from __future__ import annotations

from collections.abc import Callable, Sequence

from polis.config.mechanisms import mechanism
from polis.config.settings import FirmMarkupSettings, Settings
from polis.economy.goods import sector_skus
from polis.economy.money import allocate, bp, round_to_tick
from polis.economy.production import production_output_micro
from polis.economy.state import EconomyState, FirmState, InventoryState
from polis.events.kinds import (
    CAPITAL_DEPRECIATED,
    INVENTORY_WRITTEN_OFF,
    PRICE_SET,
    PRODUCTION_RUN,
    PRODUCTIVITY_UPDATED,
)
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry

Emit = Callable[[NewEvent], Event]


def add_inventory(inventory: InventoryState, quantity: int, unit_cost_cents: int) -> None:
    if quantity < 0 or unit_cost_cents < 0:
        raise ValueError("inventory quantity and unit cost must be non-negative")
    if quantity == 0:
        return
    old_value = inventory.quantity * inventory.unit_cost_cents
    new_value = quantity * unit_cost_cents
    inventory.quantity += quantity
    inventory.unit_cost_cents = (old_value + new_value) // inventory.quantity


@mechanism(
    "price_setting",
    entails=(
        "Mechanical prices move opposite to inventory and remain at or above unit cost; "
        "this does not determine aggregate demand or the aggregate price level."
    ),
    config_key="mechanisms.price_setting",
)
def markup_price(
    inventory: InventoryState,
    settings: FirmMarkupSettings,
) -> tuple[int, int, int]:
    inventory_days = (
        10_000 * inventory.quantity // max(1, inventory.units_sold_28d // 28)
        if inventory.units_sold_28d
        else 1_000_000_000
    )
    markup = inventory.markup_bp
    if inventory_days < settings.target_low_bp:
        markup += settings.step_bp
    elif inventory_days > settings.target_high_bp:
        markup -= settings.step_bp
    inventory.markup_bp = max(0, min(settings.max_bp, markup))
    new_price = max(
        1,
        round_to_tick(
            inventory.unit_cost_cents * (10_000 + inventory.markup_bp) // 10_000,
            1,
        ),
    )
    return new_price, inventory.markup_bp, inventory_days


@mechanism(
    "firms.productivity_drift",
    entails=(
        "Firm productivity follows a bounded random walk with a small positive "
        "learning-by-doing drift at positive utilisation."
    ),
    config_key="firms.productivity_sigma_bp",
)
def update_productivity_bp(
    firm: FirmState,
    rng: RngRegistry,
    *,
    tick: int,
    learning_bp: int,
    sigma_bp: int,
    bounds: tuple[int, int],
    utilised: bool,
) -> int:
    shock = rng.get("firms.productivity", firm.firm_id, tick).randint(-sigma_bp, sigma_bp)
    drift = learning_bp if utilised else 0
    return max(bounds[0], min(bounds[1], firm.productivity_bp + drift + shock))


class FirmEngine:
    def __init__(self, settings: Settings, economy: EconomyState, rng: RngRegistry) -> None:
        self.settings = settings
        self.economy = economy
        self.rng = rng

    def run_daily(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        if tick % self.settings.clock.ticks_per_sim_day != 0:
            return ()
        events: list[Event] = []
        for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
            if firm.status != "active":
                continue
            events.extend(self._produce(firm, tick, emit))
            events.extend(self._update_productivity(firm, tick, emit))
        events.extend(self._spoil(tick, emit))
        if self._weekly(tick):
            events.extend(self._set_prices(emit))
        if self._monthly(tick):
            events.extend(self._depreciate(emit))
        return tuple(events)

    def _produce(self, firm: FirmState, tick: int, emit: Emit) -> tuple[Event, ...]:
        employments = [
            row
            for row in self.economy.employments.values()
            if row.firm_id == firm.firm_id
            and row.last_worked_tick == tick
            and row.ended_tick is None
        ]
        labour_bp = sum(row.last_effective_labour_bp for row in employments)
        if labour_bp <= 0:
            return ()
        skus = sector_skus(self.economy, firm.sector)
        if not skus:
            return ()
        labour_allocations = split_labour_by_revenue(
            labour_bp,
            tuple((sku.sku, 1) for sku in skus),
        )
        daily_wage_cost = sum(row.wage_cents // 14 for row in employments)
        wage_allocations = allocate(
            daily_wage_cost,
            tuple((sku.sku, max(1, labour_allocations[sku.sku])) for sku in skus),
        )
        events: list[Event] = []
        for sku in skus:
            inventory_id = f"{firm.firm_id}:{sku.sku}"
            inventory = self.economy.inventory.setdefault(
                inventory_id,
                InventoryState(
                    firm.firm_id,
                    sku.sku,
                    price_cents=max(
                        1,
                        self.settings.economy.median_wage_cents // 24 // 20,
                    ),
                    markup_bp=self.settings.firms.markup.initial_bp,
                ),
            )
            sku_labour = labour_allocations[sku.sku]
            output_micro = production_output_micro(
                productivity_bp=firm.productivity_bp,
                capital_cents=firm.capital_cents,
                capital_ref_cents=self.settings.firms.capital_ref_cents,
                effective_labour_bp=sku_labour,
                beta_capital_bp=self.settings.firms.beta_capital_bp,
                yield_units=sku.yield_units,
            )
            total_micro = inventory.carry_micro + output_micro
            units = total_micro // 1_000_000
            inventory.carry_micro = total_micro % 1_000_000
            unit_cost = wage_allocations[sku.sku] // max(1, units)
            if units:
                add_inventory(inventory, units, max(1, unit_cost))
                firm.cumulative_output_units += units
            events.append(
                emit(
                    NewEvent(
                        PRODUCTION_RUN,
                        {
                            "firm_id": firm.firm_id,
                            "sku": sku.sku,
                            "labour_bp": sku_labour,
                            "capital_cents_used": firm.capital_cents,
                            "productivity_bp": firm.productivity_bp,
                            "output_micro": output_micro,
                            "units_produced": units,
                            "unit_cost_cents": max(1, unit_cost),
                            "carry_micro_after": inventory.carry_micro,
                        },
                        actor_id=firm.firm_id,
                    )
                )
            )
        return tuple(events)

    def _update_productivity(self, firm: FirmState, tick: int, emit: Emit) -> tuple[Event, ...]:
        old = firm.productivity_bp
        new = update_productivity_bp(
            firm,
            self.rng,
            tick=tick,
            learning_bp=self.settings.firms.learning_bp_per_day,
            sigma_bp=self.settings.firms.productivity_sigma_bp,
            bounds=self.settings.firms.productivity_bounds_bp,
            utilised=any(
                row.firm_id == firm.firm_id and row.last_worked_tick == tick
                for row in self.economy.employments.values()
            ),
        )
        firm.productivity_bp = new
        if new == old:
            return ()
        return (
            emit(
                NewEvent(
                    PRODUCTIVITY_UPDATED,
                    {
                        "firm_id": firm.firm_id,
                        "from_bp": old,
                        "to_bp": new,
                        "cause": "learning" if new > old else "shock",
                    },
                    actor_id=firm.firm_id,
                )
            ),
        )

    def _set_prices(self, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for inventory in sorted(
            self.economy.inventory.values(),
            key=lambda row: (row.firm_id, row.sku),
        ):
            old = inventory.price_cents
            new, markup, inventory_days = markup_price(inventory, self.settings.firms.markup)
            inventory.price_cents = new
            if new != old:
                events.append(
                    emit(
                        NewEvent(
                            PRICE_SET,
                            {
                                "firm_id": inventory.firm_id,
                                "sku": inventory.sku,
                                "from_cents": old,
                                "to_cents": new,
                                "rule": "markup",
                                "markup_bp": markup,
                                "inventory_days": inventory_days,
                            },
                            actor_id=inventory.firm_id,
                        )
                    )
                )
        return tuple(events)

    def _depreciate(self, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        monthly_rate = self.settings.firms.depreciation_bp_per_year // 12
        for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
            old = firm.capital_cents
            firm.capital_cents = max(0, old - bp(old, monthly_rate))
            if firm.capital_cents != old:
                events.append(
                    emit(
                        NewEvent(
                            CAPITAL_DEPRECIATED,
                            {
                                "firm_id": firm.firm_id,
                                "from_cents": old,
                                "to_cents": firm.capital_cents,
                                "rate_bp": monthly_rate,
                            },
                            actor_id=firm.firm_id,
                        )
                    )
                )
        return tuple(events)

    def _spoil(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for inventory in sorted(
            self.economy.inventory.values(),
            key=lambda row: (row.firm_id, row.sku),
        ):
            sku = self.economy.skus.get(inventory.sku)
            if sku is None or sku.perishable_bp_per_day <= 0 or inventory.quantity <= 0:
                continue
            rate = sku.perishable_bp_per_day
            raw = inventory.quantity * rate
            spoiled = raw // 10_000
            remainder = raw % 10_000
            spoilage_draw = self.rng.get("firms.spoilage", inventory.firm_id, tick).randint(
                0, 9_999
            )
            if spoilage_draw < remainder:
                spoiled += 1
            spoiled = min(inventory.quantity, spoiled)
            if not spoiled:
                continue
            inventory.quantity -= spoiled
            events.append(
                emit(
                    NewEvent(
                        INVENTORY_WRITTEN_OFF,
                        {
                            "firm_id": inventory.firm_id,
                            "sku": inventory.sku,
                            "units": spoiled,
                            "unit_cost_cents": inventory.unit_cost_cents,
                            "value_cents": spoiled * inventory.unit_cost_cents,
                            "reason": "spoilage",
                        },
                        actor_id=inventory.firm_id,
                    )
                )
            )
        return tuple(events)

    def _weekly(self, tick: int) -> bool:
        return tick % (7 * self.settings.clock.ticks_per_sim_day) == 0

    def _monthly(self, tick: int) -> bool:
        return tick % (30 * self.settings.clock.ticks_per_sim_day) == 0


def split_labour_by_revenue(
    labour_bp: int,
    revenue_weights: Sequence[tuple[str, int]],
) -> dict[str, int]:
    if labour_bp < 0:
        raise ValueError("labour_bp must be non-negative")
    if not revenue_weights:
        return {}
    total = sum(max(0, value) for _sku, value in revenue_weights)
    if total == 0:
        total = len(revenue_weights)
        revenue_weights = tuple((sku, 1) for sku, _value in revenue_weights)
    base = {sku: labour_bp * max(0, weight) // total for sku, weight in revenue_weights}
    remainder = labour_bp - sum(base.values())
    order = sorted(
        revenue_weights,
        key=lambda row: (-(labour_bp * max(0, row[1]) % total), row[0]),
    )
    for sku, _weight in order[:remainder]:
        base[sku] += 1
    return base
