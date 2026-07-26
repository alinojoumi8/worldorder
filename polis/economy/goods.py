from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isqrt
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from polis.agents.actions.types import Action, ActionType, make_action
from polis.agents.state import AgentPopulation
from polis.agents.types import AgentState
from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.ledger import Leg, parse_account_id
from polis.economy.money import allocate, bp, bp_ceil, mint
from polis.economy.production import production_output_micro
from polis.economy.state import (
    BasketState,
    DurableState,
    EconomyState,
    GoodsTransactionState,
    InventoryState,
    SkuState,
)
from polis.events.kinds import (
    CPI_COMPUTED,
    DURABLE_EXPIRED,
    GOODS_PURCHASED,
    INFLATION_COMPUTED,
    NEED_SATISFIED,
    PURCHASE_FAILED,
)
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.world.api import World

Category = Literal["food", "housing", "goods", "services", "luxury", "health"]
Emit = Callable[[NewEvent], Event]
CATEGORIES: tuple[Category, ...] = (
    "food",
    "housing",
    "goods",
    "services",
    "luxury",
    "health",
)


@dataclass(frozen=True, slots=True)
class SellerQuote:
    firm_id: str
    sku: str
    price_cents: int
    qty_available: int
    district_id: str
    distance_bands: int


@dataclass(frozen=True, slots=True)
class PurchaseBreakdown:
    gross_cents: int
    sales_tax_cents: int
    subsidy_cents: int
    paid_cents: int


@dataclass(frozen=True, slots=True)
class BudgetPlan:
    household_id: str
    horizon_ticks: int
    committed_cents: int
    disposable_cents: int
    buffer_cents: int
    savings_share_bp: int
    spend_by_sku_cents: Mapping[str, int]
    savings_cents: int


@dataclass(frozen=True, slots=True)
class GoodsContext:
    settings: Settings
    population: AgentPopulation
    world: World
    economy: EconomyState
    rng: RngRegistry


def load_skus(
    path: str | Path = "configs/skus.yaml",
    *,
    ticks_per_sim_day: int = 1,
) -> dict[str, SkuState]:
    catalogue_path = Path(path)
    if not catalogue_path.is_absolute():
        catalogue_path = Path(__file__).resolve().parents[2] / catalogue_path
    payload = yaml.safe_load(catalogue_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("skus"), Mapping):
        raise ValueError("SKU catalogue must contain a skus mapping")
    result: dict[str, SkuState] = {}
    for sku, raw in sorted(cast(Mapping[str, Any], payload["skus"]).items()):
        if not isinstance(raw, Mapping):
            raise ValueError(f"SKU {sku} must be a mapping")
        category = str(raw.get("category", ""))
        if category not in CATEGORIES:
            raise ValueError(f"SKU {sku} has unknown category {category}")
        restore = raw.get("need_restore_bp", {})
        sectors = raw.get("sectors", ())
        if not isinstance(restore, Mapping):
            raise ValueError(f"SKU {sku} need_restore_bp must be a mapping")
        if not isinstance(sectors, Sequence) or isinstance(sectors, (str, bytes)):
            raise ValueError(f"SKU {sku} sectors must be a sequence")
        durable_days = raw.get("durable_life_days")
        result[str(sku)] = SkuState(
            sku=str(sku),
            category=category,
            is_necessity=bool(raw.get("necessity", False)),
            base_utility_bp=int(raw.get("utility_bp", 0)),
            perishable_bp_per_day=int(raw.get("perishable_bp_per_day", 0)),
            durable_life_ticks=(
                int(durable_days) * ticks_per_sim_day if durable_days is not None else None
            ),
            is_service=bool(raw.get("service", False)),
            is_capital=bool(raw.get("capital", False)),
            need_restore_bp={str(need): int(value) for need, value in restore.items()},
            gamma_units_per_year=int(raw.get("gamma_units_per_year", 0)),
            beta_bp=int(raw.get("beta_bp", 0)),
            sectors=tuple(str(sector) for sector in sectors),
            yield_units=int(raw.get("yield_units", 1)),
        )
    if len(result) != 23:
        raise ValueError(f"SKU catalogue must contain 23 rows, found {len(result)}")
    return result


def sector_skus(economy: EconomyState, sector: str) -> tuple[SkuState, ...]:
    return tuple(
        row
        for row in sorted(economy.skus.values(), key=lambda item: item.sku)
        if sector in row.sectors
    )


def seed_goods_state(settings: Settings, economy: EconomyState) -> BasketState:
    economy.skus = load_skus(
        settings.goods.catalogue_path,
        ticks_per_sim_day=settings.clock.ticks_per_sim_day,
    )
    for firm in sorted(economy.firms.values(), key=lambda row: row.firm_id):
        rows = sector_skus(economy, firm.sector)
        if not rows:
            continue
        target_headcount = max(1, firm.target_headcount)
        expected_labour_bp = target_headcount * settings.firms.seed_effective_labour_bp_per_worker
        labour_allocations = allocate(
            expected_labour_bp,
            tuple((sku.sku, 1) for sku in rows),
        )
        daily_payroll_cents = settings.economy.median_wage_cents * target_headcount // 24 // 14
        wage_allocations = allocate(
            daily_payroll_cents,
            tuple((sku.sku, max(1, labour_allocations[sku.sku])) for sku in rows),
        )
        for sku in rows:
            output_micro = production_output_micro(
                productivity_bp=firm.productivity_bp,
                capital_cents=firm.capital_cents,
                capital_ref_cents=settings.firms.capital_ref_cents,
                effective_labour_bp=labour_allocations[sku.sku],
                beta_capital_bp=settings.firms.beta_capital_bp,
                yield_units=sku.yield_units,
            )
            daily_units = max(1, output_micro // 1_000_000)
            quantity = daily_units * settings.goods.initial_inventory_days
            unit_cost = max(1, wage_allocations[sku.sku] // daily_units)
            price = max(
                1,
                unit_cost * (10_000 + settings.firms.markup.initial_bp) // 10_000,
            )
            economy.inventory[f"{firm.firm_id}:{sku.sku}"] = InventoryState(
                firm.firm_id,
                sku.sku,
                quantity=quantity,
                unit_cost_cents=unit_cost,
                price_cents=price,
                markup_bp=settings.firms.markup.initial_bp,
            )
    base_prices: dict[str, int] = {}
    quantities: dict[str, int] = {}
    for sku in sorted(economy.skus.values(), key=lambda row: row.sku):
        quotes = [
            row for row in economy.inventory.values() if row.sku == sku.sku and row.quantity > 0
        ]
        if not quotes or sku.is_capital:
            continue
        total_qty = sum(row.quantity for row in quotes)
        posted_price = sum(row.price_cents * row.quantity for row in quotes) // total_qty
        base_tax = (
            0
            if sku.is_necessity and settings.treasury.tax.exempt_necessities
            else bp_ceil(posted_price, settings.treasury.tax.sales_bp)
        )
        base_subsidy = (
            bp(posted_price, settings.treasury.spend.health_subsidy_bp)
            if sku.category == "health"
            else 0
        )
        base_prices[sku.sku] = max(1, posted_price + base_tax - base_subsidy)
        quantities[sku.sku] = max(
            1,
            sku.gamma_units_per_year
            or sku.beta_bp
            * max(1, settings.economy.median_wage_cents // max(1, base_prices[sku.sku]))
            // 10_000,
        )
    economy.basket = BasketState(1, quantities, base_prices, 0)
    economy.cpi_history_bp[0] = 10_000
    economy.cpi_core_history_bp[0] = 10_000
    economy.cpi_fisher_history_bp[0] = 10_000
    for category in CATEGORIES:
        economy.cpi_category_history_bp.setdefault(category, {})[0] = 10_000
    economy.initial_inventory_value_cents = sum(
        row.quantity * row.unit_cost_cents for row in economy.inventory.values()
    )
    return economy.basket


def _district_distance(world: World, first: str, second: str) -> int:
    a = world.district(first).bbox
    b = world.district(second).bbox
    a_x = a[0] + a[2]
    a_y = a[1] + a[3]
    b_x = b[0] + b[2]
    b_y = b[1] + b[3]
    return (abs(a_x - b_x) + abs(a_y - b_y)) // 2


@mechanism(
    "goods.search_slice",
    entails=(
        "A buyer transacts only with sellers in a distance-then-price slice. Price "
        "dispersion and spatial inequality can persist, but their level is not implied."
    ),
    config_key="mechanisms.goods_search_slice",
)
def visible_sellers(
    agent: AgentState,
    sku: str,
    tick: int,
    *,
    ctx: GoodsContext,
    inventories: Sequence[InventoryState] | None = None,
) -> tuple[SellerQuote, ...]:
    location = ctx.world.locations[agent.agent_id]
    place_id = location.place_id or agent.home_place_id
    buyer_district = ctx.world.place(place_id).district_id
    rows: list[SellerQuote] = []
    for inventory in inventories if inventories is not None else ctx.economy.inventory.values():
        if inventory.sku != sku or inventory.quantity <= 0:
            continue
        firm = ctx.economy.firms[inventory.firm_id]
        seller_district = ctx.world.place(firm.place_id).district_id
        rows.append(
            SellerQuote(
                inventory.firm_id,
                sku,
                inventory.price_cents,
                inventory.quantity,
                seller_district,
                _district_distance(ctx.world, buyer_district, seller_district),
            )
        )
    if not rows:
        return ()
    within_radius = [
        row for row in rows if row.distance_bands <= ctx.settings.goods.search_radius_districts
    ]
    candidates = within_radius or rows if len(rows) == 1 else within_radius
    bands: dict[int, list[SellerQuote]] = {}
    for row in candidates:
        bands.setdefault(row.distance_bands, []).append(row)
    stream = ctx.rng.get("goods.search", agent.agent_id, tick)
    ordered: list[SellerQuote] = []
    for distance, band in sorted(bands.items()):
        del distance
        stable_band = sorted(band, key=lambda row: row.firm_id)
        stream.shuffle(stable_band)
        stable_band.sort(key=lambda row: row.price_cents)
        ordered.extend(stable_band)
    return tuple(ordered[: ctx.settings.goods.search_k])


def _deposit_account(economy: EconomyState, owner_id: str) -> str:
    for account_id in economy.ledger.accounts_of(owner_id):
        code, _owner, _bank, _ref = parse_account_id(account_id)
        if code == "dep" and economy.ledger.is_open(account_id):
            return account_id
    raise RuntimeError(f"owner {owner_id} has no open deposit")


def _combine_legs(legs: Sequence[Leg]) -> tuple[Leg, ...]:
    totals: dict[tuple[str, int, str], int] = {}
    for leg in legs:
        key = (leg.account_id, leg.direction, leg.reason)
        totals[key] = totals.get(key, 0) + leg.amount_cents
    return tuple(
        Leg(account_id, direction, amount, reason)
        for (account_id, direction, reason), amount in sorted(totals.items())
        if amount
    )


def purchase_legs(
    buyer_id: str,
    seller_firm_id: str,
    sku: str,
    qty: int,
    unit_price_cents: int,
    *,
    ctx: GoodsContext,
) -> tuple[tuple[Leg, ...], PurchaseBreakdown]:
    if qty <= 0 or unit_price_cents <= 0:
        raise ValueError("purchase quantity and unit price must be positive")
    sku_row = ctx.economy.skus[sku]
    gross = qty * unit_price_cents
    taxable = not (sku_row.is_necessity and ctx.settings.treasury.tax.exempt_necessities)
    sales_tax = bp_ceil(gross, ctx.settings.treasury.tax.sales_bp) if taxable else 0
    subsidy = (
        bp(gross, ctx.settings.treasury.spend.health_subsidy_bp)
        if sku_row.category == "health"
        else 0
    )
    buyer_payment = gross + sales_tax - subsidy
    buyer_deposit = _deposit_account(ctx.economy, buyer_id)
    seller_deposit = _deposit_account(ctx.economy, seller_firm_id)
    treasury_deposit = _deposit_account(ctx.economy, "gv_treasury")
    legs: list[Leg] = []
    seller_from_buyer = gross - subsidy
    if seller_from_buyer:
        legs.extend(
            ctx.economy.ledger.transfer(
                buyer_deposit,
                seller_deposit,
                seller_from_buyer,
                "purchase",
            )
        )
    if sales_tax:
        legs.extend(
            ctx.economy.ledger.transfer(
                buyer_deposit,
                treasury_deposit,
                sales_tax,
                "tax",
            )
        )
    if subsidy:
        legs.extend(
            ctx.economy.ledger.transfer(
                treasury_deposit,
                seller_deposit,
                subsidy,
                "purchase",
            )
        )
    return _combine_legs(legs), PurchaseBreakdown(
        gross,
        sales_tax,
        subsidy,
        buyer_payment,
    )


@mechanism(
    "consumption_rule",
    entails=(
        "Necessities have income elasticity below one and luxuries above one by "
        "construction; an Engel curve is not a finding. Marginal shares are constant "
        "above subsistence."
    ),
    config_key="mechanisms.consumption_rule",
)
def plan_budget(
    household_id: str,
    *,
    liquid_cents: int,
    committed_cents: int,
    monthly_expenses_cents: int,
    prices_cents: Mapping[str, int],
    skus: Mapping[str, SkuState],
    settings: Settings,
) -> BudgetPlan:
    buffer_cents = bp(monthly_expenses_cents, settings.goods.consumption.buffer_bp)
    disposable = max(0, liquid_cents - committed_cents - buffer_cents)
    necessities = [
        sku
        for sku in skus.values()
        if sku.is_necessity and sku.gamma_units_per_year > 0 and sku.sku in prices_cents
    ]
    floors = {
        sku.sku: prices_cents[sku.sku] * max(1, sku.gamma_units_per_year // 12)
        for sku in necessities
    }
    subsistence = sum(floors.values())
    if disposable < subsistence and floors:
        spend = allocate(
            disposable,
            [(sku, amount) for sku, amount in sorted(floors.items())],
        )
        return BudgetPlan(
            household_id,
            30 * settings.clock.ticks_per_sim_day,
            committed_cents,
            disposable,
            buffer_cents,
            settings.goods.consumption.savings_share_bp,
            spend,
            0,
        )
    supernumerary = max(0, disposable - subsistence)
    savings = bp(supernumerary, settings.goods.consumption.savings_share_bp)
    spendable = supernumerary - savings
    weights = [
        (sku.sku, sku.beta_bp)
        for sku in skus.values()
        if sku.beta_bp > 0 and sku.sku in prices_cents
    ]
    marginal = allocate(spendable, weights) if spendable and weights else {}
    spend = {
        sku: floors.get(sku, 0) + marginal.get(sku, 0)
        for sku in sorted(set(floors) | set(marginal))
    }
    unallocated = disposable - savings - sum(spend.values())
    savings += unallocated
    return BudgetPlan(
        household_id,
        30 * settings.clock.ticks_per_sim_day,
        committed_cents,
        disposable,
        buffer_cents,
        settings.goods.consumption.savings_share_bp,
        spend,
        savings,
    )


def transaction_price_cents(
    sku: str,
    tick: int,
    window_ticks: int,
    *,
    economy: EconomyState,
) -> tuple[int, bool]:
    if economy.goods_price_qty_by_tick or economy.goods_last_price_cents:
        included_ticks = tuple(
            row_tick
            for row_tick in economy.goods_price_qty_by_tick
            if tick - window_ticks < row_tick <= tick
        )
        total_qty = sum(
            economy.goods_price_qty_by_tick[row_tick].get(sku, 0) for row_tick in included_ticks
        )
        if total_qty:
            total_value = sum(
                economy.goods_price_value_by_tick[row_tick].get(sku, 0)
                for row_tick in included_ticks
            )
            return total_value // total_qty, False
        if economy.basket is None:
            raise RuntimeError("CPI basket is not fixed")
        return economy.goods_last_price_cents.get(
            sku,
            economy.basket.base_prices_cents[sku],
        ), True
    rows = [
        row
        for row in economy.goods_transactions
        if row.sku == sku and tick - window_ticks < row.tick <= tick
    ]
    total_qty = sum(row.qty for row in rows)
    if total_qty:
        return (
            sum(row.unit_price_cents * row.qty for row in rows) // total_qty,
            False,
        )
    if economy.basket is None:
        raise RuntimeError("CPI basket is not fixed")
    previous = [row for row in economy.goods_transactions if row.sku == sku and row.tick <= tick]
    if previous:
        latest_tick = max(row.tick for row in previous)
        latest = [row for row in previous if row.tick == latest_tick]
        latest_qty = sum(row.qty for row in latest)
        return (
            sum(row.unit_price_cents * row.qty for row in latest) // latest_qty,
            True,
        )
    return economy.basket.base_prices_cents[sku], True


def _index_for(
    skus: Sequence[str],
    prices: Mapping[str, int],
    basket: BasketState,
) -> int:
    numerator = sum(basket.quantities[sku] * prices[sku] for sku in skus)
    denominator = sum(basket.quantities[sku] * basket.base_prices_cents[sku] for sku in skus)
    return 10_000 * numerator // max(1, denominator)


def cpi_bp(tick: int, *, ctx: GoodsContext) -> int:
    basket = ctx.economy.basket
    if basket is None:
        raise RuntimeError("CPI basket is not fixed")
    window = ctx.settings.goods.cpi_window_days * ctx.settings.clock.ticks_per_sim_day
    prices = {
        sku: transaction_price_cents(sku, tick, window, economy=ctx.economy)[0]
        for sku in basket.quantities
    }
    return _index_for(tuple(basket.quantities), prices, basket)


class GoodsEngine:
    def __init__(
        self,
        settings: Settings,
        population: AgentPopulation,
        world: World,
        economy: EconomyState,
        rng: RngRegistry,
    ) -> None:
        self.ctx = GoodsContext(settings, population, world, economy, rng)
        self._transaction_tick = -1
        self._transaction_ordinal = 0

    @property
    def economy(self) -> EconomyState:
        return self.ctx.economy

    def mechanical_actions(self, tick: int) -> tuple[Action, ...]:
        if tick % self.ctx.settings.clock.ticks_per_sim_day != 0:
            return ()
        self._expire_sales_window(tick)
        inventories_by_sku: dict[str, list[InventoryState]] = {}
        for inventory in self.economy.inventory.values():
            if inventory.quantity > 0:
                inventories_by_sku.setdefault(inventory.sku, []).append(inventory)
        actions: list[Action] = []
        for agent in self.ctx.population:
            if not agent.alive or agent.employment_status in {"child", "dead"}:
                continue
            selected = 0
            for sku in self._purchase_choices(agent):
                quantity = self._quantity_due(agent, sku, tick)
                if quantity <= 0:
                    continue
                sellers = visible_sellers(
                    agent,
                    sku.sku,
                    tick,
                    ctx=self.ctx,
                    inventories=inventories_by_sku.get(sku.sku, ()),
                )
                if not sellers:
                    continue
                seller = sellers[0]
                total_price = quantity * (
                    seller.price_cents
                    + bp_ceil(
                        seller.price_cents,
                        self.ctx.settings.treasury.tax.sales_bp,
                    )
                )
                if self.ctx.economy.ledger.liquid(agent.agent_id) < total_price:
                    continue
                actions.append(
                    make_action(
                        actor_id=agent.agent_id,
                        tick=tick,
                        action_type=ActionType.BUY_GOOD,
                        params={
                            "sku": sku.sku,
                            "qty": quantity,
                            "seller_firm_id": seller.firm_id,
                            "max_unit_price_cents": seller.price_cents,
                        },
                        origin="scripted",
                        reasoning="MechanicalPolicy linear expenditure purchase",
                    )
                )
                selected += 1
                if selected >= self.ctx.settings.goods.max_purchases_per_agent_per_day:
                    break
        return tuple(actions)

    def _quantity_due(self, agent: AgentState, sku: SkuState, tick: int) -> int:
        annual_units = sku.gamma_units_per_year
        if annual_units <= 0:
            return 0
        ticks_per_year = (
            self.ctx.settings.clock.days_per_sim_year * self.ctx.settings.clock.ticks_per_sim_day
        )
        phase = (
            self.ctx.rng.seed_for("goods.due", f"{agent.agent_id}:{sku.sku}", 0) % ticks_per_year
        )
        before = ((tick - 1) * annual_units + phase) // ticks_per_year
        after = (tick * annual_units + phase) // ticks_per_year
        return after - before

    def _purchase_choices(self, agent: AgentState) -> tuple[SkuState, ...]:
        preferred_categories: tuple[str, ...]
        if agent.needs.hunger < 0.75:
            preferred_categories = ("food", "health", "housing", "goods", "services")
        elif agent.health < 0.7:
            preferred_categories = ("health", "food", "housing", "goods", "services")
        elif agent.needs.security < 0.6:
            preferred_categories = ("housing", "goods", "food", "services", "health")
        else:
            preferred_categories = ("food", "goods", "housing", "services", "health")
        rank = {category: ordinal for ordinal, category in enumerate(preferred_categories)}
        return tuple(
            sorted(
                (
                    sku
                    for sku in self.economy.skus.values()
                    if sku.is_necessity and not sku.is_capital
                ),
                key=lambda sku: (rank.get(sku.category, 99), -sku.base_utility_bp, sku.sku),
            )
        )

    def resolve(self, actions: Sequence[Action], tick: int, emit: Emit) -> tuple[Event, ...]:
        if tick != self._transaction_tick:
            self._transaction_tick = tick
            self._transaction_ordinal = 0
        groups: dict[tuple[str, str], list[Action]] = {}
        for action in actions:
            if action.type != ActionType.BUY_GOOD:
                continue
            key = (str(action.params["seller_firm_id"]), str(action.params["sku"]))
            groups.setdefault(key, []).append(action)
        events: list[Event] = []
        for (firm_id, sku), group in sorted(groups.items()):
            inventory = self.economy.inventory.get(f"{firm_id}:{sku}")
            initial_stock = inventory.quantity if inventory is not None else 0
            ordered = sorted(group, key=lambda row: self._buyer_signature(row.actor_id))
            self.ctx.rng.get("goods.service", f"{firm_id}:{sku}", tick).shuffle(ordered)
            for action in ordered:
                events.extend(self._purchase(action, inventory, initial_stock, tick, emit))
        return tuple(events)

    def _purchase(
        self,
        action: Action,
        inventory: InventoryState | None,
        initial_stock: int,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        sku = str(action.params["sku"])
        qty_requested = min(
            int(action.params["qty"]),
            self.ctx.settings.goods.purchase_max_qty,
        )
        seller_id = str(action.params["seller_firm_id"])
        if inventory is None or initial_stock == 0:
            return (self._failed(action.actor_id, sku, qty_requested, "stockout", emit),)
        if inventory.quantity <= 0:
            return (self._failed(action.actor_id, sku, qty_requested, "rationed", emit),)
        if inventory.price_cents > int(action.params["max_unit_price_cents"]):
            return (self._failed(action.actor_id, sku, qty_requested, "price_above_cap", emit),)
        fill = min(qty_requested, inventory.quantity)
        legs, breakdown = purchase_legs(
            action.actor_id,
            seller_id,
            sku,
            fill,
            inventory.price_cents,
            ctx=self.ctx,
        )
        if self.economy.ledger.liquid(action.actor_id) < breakdown.paid_cents:
            return (self._failed(action.actor_id, sku, qty_requested, "unaffordable", emit),)
        goods_txn_id = mint(
            "gds",
            tick,
            self._transaction_ordinal,
        )
        expected = self.economy.ledger.next_txn_id(tick)
        purchase_event = emit(
            NewEvent(
                GOODS_PURCHASED,
                {
                    "txn_id": goods_txn_id,
                    "buyer_id": action.actor_id,
                    "seller_firm_id": seller_id,
                    "sku": sku,
                    "qty": fill,
                    "unit_price_cents": inventory.price_cents,
                    "gross_cents": breakdown.gross_cents,
                    "sales_tax_cents": breakdown.sales_tax_cents,
                    "subsidy_cents": breakdown.subsidy_cents,
                    "ledger_txn_id": str(expected),
                },
                actor_id=action.actor_id,
                subject_ids=(seller_id,),
            )
        )
        ledger_txn_id = self.economy.ledger.post_transaction(
            legs,
            tick=tick,
            cause=purchase_event,
        )
        if ledger_txn_id != expected:
            raise RuntimeError("goods transaction ordinal diverged")
        inventory.quantity -= fill
        effective_unit_price = max(1, breakdown.paid_cents // fill)
        self.economy.goods_transactions.append(
            GoodsTransactionState(
                goods_txn_id,
                str(ledger_txn_id),
                tick,
                action.actor_id,
                seller_id,
                sku,
                fill,
                effective_unit_price,
                breakdown.gross_cents,
                breakdown.sales_tax_cents,
                breakdown.subsidy_cents,
            )
        )
        self._transaction_ordinal += 1
        firm = self.economy.firms[seller_id]
        firm.cumulative_revenue_cents += breakdown.gross_cents
        inventory_id = f"{seller_id}:{sku}"
        sales = self.economy.goods_sales_by_tick.setdefault(tick, {})
        sales[inventory_id] = sales.get(inventory_id, 0) + fill
        inventory.units_sold_28d += fill
        price_qty = self.economy.goods_price_qty_by_tick.setdefault(tick, {})
        price_value = self.economy.goods_price_value_by_tick.setdefault(tick, {})
        price_qty[sku] = price_qty.get(sku, 0) + fill
        price_value[sku] = price_value.get(sku, 0) + effective_unit_price * fill
        self.economy.goods_last_price_cents[sku] = price_value[sku] // price_qty[sku]
        events: list[Event] = [purchase_event]
        events.extend(self._consume(action.actor_id, sku, fill, tick, emit))
        return tuple(events)

    def _expire_sales_window(self, tick: int) -> None:
        cutoff = tick - 28 * self.ctx.settings.clock.ticks_per_sim_day
        expired_ticks = tuple(
            sold_tick
            for sold_tick in sorted(self.economy.goods_sales_by_tick)
            if sold_tick <= cutoff
        )
        for sold_tick in expired_ticks:
            for inventory_id, qty in self.economy.goods_sales_by_tick.pop(sold_tick).items():
                inventory = self.economy.inventory.get(inventory_id)
                if inventory is not None:
                    inventory.units_sold_28d = max(0, inventory.units_sold_28d - qty)
        price_cutoff = (
            tick
            - self.ctx.settings.goods.cpi_window_days * self.ctx.settings.clock.ticks_per_sim_day
        )
        for sold_tick in tuple(
            row_tick
            for row_tick in sorted(self.economy.goods_price_qty_by_tick)
            if row_tick <= price_cutoff
        ):
            self.economy.goods_price_qty_by_tick.pop(sold_tick, None)
            self.economy.goods_price_value_by_tick.pop(sold_tick, None)

    def _failed(
        self,
        buyer_id: str,
        sku: str,
        qty: int,
        reason: str,
        emit: Emit,
    ) -> Event:
        return emit(
            NewEvent(
                PURCHASE_FAILED,
                {"buyer_id": buyer_id, "sku": sku, "qty": qty, "reason": reason},
                actor_id=buyer_id,
            )
        )

    def _consume(
        self,
        agent_id: str,
        sku: str,
        qty: int,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        agent = self.ctx.population[agent_id]
        row = self.economy.skus[sku]
        events: list[Event] = []
        if row.durable_life_ticks is not None:
            durable_id = mint("dur", tick, len(self.economy.durables))
            self.economy.durables[durable_id] = DurableState(
                durable_id,
                agent_id,
                sku,
                tick,
                row.durable_life_ticks,
                qty,
            )
        for need, restore_bp in sorted(row.need_restore_bp.items()):
            if need == "health":
                before = round(agent.health * 10_000)
                agent.health = min(1.0, agent.health + qty * restore_bp / 10_000)
                after = round(agent.health * 10_000)
            else:
                before = round(getattr(agent.needs, need) * 10_000)
                agent.restore(need, qty * restore_bp / 10_000)
                after = round(getattr(agent.needs, need) * 10_000)
            events.append(
                emit(
                    NewEvent(
                        NEED_SATISFIED,
                        {
                            "agent_id": agent_id,
                            "need": need,
                            "sku": sku,
                            "from_bp": before,
                            "to_bp": after,
                        },
                        actor_id=agent_id,
                    )
                )
            )
        return tuple(events)

    def expire_durables(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for durable_id, durable in sorted(tuple(self.economy.durables.items())):
            if durable.acquired_tick + durable.life_ticks > tick:
                continue
            del self.economy.durables[durable_id]
            events.append(
                emit(
                    NewEvent(
                        DURABLE_EXPIRED,
                        {
                            "agent_id": durable.agent_id,
                            "sku": durable.sku,
                            "acquired_tick": durable.acquired_tick,
                            "life_ticks": durable.life_ticks,
                        },
                        actor_id=durable.agent_id,
                    )
                )
            )
        return tuple(events)

    def compute_cpi(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        basket = self.economy.basket
        if basket is None:
            return ()
        window = self.ctx.settings.goods.cpi_window_days * self.ctx.settings.clock.ticks_per_sim_day
        prices: dict[str, int] = {}
        carried: list[str] = []
        for sku in basket.quantities:
            price, was_carried = transaction_price_cents(
                sku,
                tick,
                window,
                economy=self.economy,
            )
            prices[sku] = price
            if was_carried:
                carried.append(sku)
        index = _index_for(tuple(basket.quantities), prices, basket)
        categories: dict[str, int] = {}
        for category in CATEGORIES:
            category_skus = tuple(
                sku for sku in basket.quantities if self.economy.skus[sku].category == category
            )
            if category_skus:
                categories[category] = _index_for(category_skus, prices, basket)
                self.economy.cpi_category_history_bp.setdefault(category, {})[tick] = categories[
                    category
                ]
        core_skus = tuple(
            sku
            for sku in basket.quantities
            if self.economy.skus[sku].category not in {"food", "health"}
        )
        core = _index_for(core_skus, prices, basket) if core_skus else index
        quantities = {
            sku: sum(
                values.get(sku, 0)
                for row_tick, values in self.economy.goods_price_qty_by_tick.items()
                if tick - window < row_tick <= tick
            )
            for sku in basket.quantities
        }
        paasche_denominator = sum(
            quantities[sku] * basket.base_prices_cents[sku] for sku in basket.quantities
        )
        paasche = (
            10_000
            * sum(quantities[sku] * prices[sku] for sku in basket.quantities)
            // paasche_denominator
            if paasche_denominator
            else index
        )
        fisher = isqrt(index * paasche)
        self.economy.cpi_history_bp[tick] = index
        self.economy.cpi_core_history_bp[tick] = core
        self.economy.cpi_fisher_history_bp[tick] = fisher
        year_ticks = (
            self.ctx.settings.clock.days_per_sim_year * self.ctx.settings.clock.ticks_per_sim_day
        )
        prior_year = self.economy.cpi_history_bp.get(tick - year_ticks)
        yoy = 10_000 * index // prior_year - 10_000 if prior_year else 0
        prior_month = self.economy.cpi_history_bp.get(tick - window)
        mom = (10_000 * index // prior_month - 10_000) * 12 if prior_month else 0
        cpi_event = emit(
            NewEvent(
                CPI_COMPUTED,
                {
                    "basket_version": basket.version,
                    "index_bp": index,
                    "category_index_bp": categories,
                    "carried_forward_skus": carried,
                    "window_ticks": window,
                    "fisher_bp": fisher,
                },
            )
        )
        inflation_event = emit(
            NewEvent(
                INFLATION_COMPUTED,
                {"yoy_bp": yoy, "mom_annualised_bp": mom, "core_bp": core},
            )
        )
        return cpi_event, inflation_event

    def _buyer_signature(self, agent_id: str) -> tuple[object, ...]:
        agent = self.ctx.population[agent_id]
        return (
            tuple(round(value * 10_000) for _skill, value in sorted(agent.skills.items())),
            round(agent.age_years * 100),
            tuple(sorted(agent.traits.as_dict().items())),
        )
