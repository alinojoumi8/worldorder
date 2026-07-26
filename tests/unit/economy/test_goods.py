from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from polis.agents.genesis import generate_agents
from polis.config.settings import Settings, load_settings
from polis.economy.genesis import create_economy
from polis.economy.goods import (
    GoodsContext,
    cpi_bp,
    load_skus,
    plan_budget,
    purchase_legs,
    visible_sellers,
)
from polis.economy.state import EconomyState, GoodsTransactionState
from polis.events.kinds import CPI_COMPUTED, GOODS_PURCHASED, PURCHASE_FAILED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.rng import RngRegistry
from polis.living_city import run_living_city
from polis.simulation import run_id_for
from polis.world.api import World
from polis.world.generator import generate_world

ROOT = Path(__file__).resolve().parents[3]


def settings() -> Settings:
    return load_settings(ROOT / "configs" / "m2-smoke.yaml")


def economy_fixture() -> tuple[Settings, World, object, EconomyState, RngRegistry]:
    configured = settings()
    rng = RngRegistry(configured.run.seed)
    world = generate_world(configured.world, rng)
    population = generate_agents(configured.population, world, rng)
    log = EventLog(run_id_for(configured), MemoryEventSink())
    result = create_economy(
        configured,
        population,
        world,
        rng,
        run_id_for(configured),
        emit=lambda draft: log.stage(
            draft,
            tick=0,
            sim_time=datetime(2025, 1, 1),
        ),
    )
    return configured, world, population, result.state, rng


def test_seed_catalogue_has_twenty_three_skus_and_fixed_base_cpi() -> None:
    configured, _world, _population, economy, _rng = economy_fixture()
    catalogue = load_skus(
        ROOT / "configs" / "skus.yaml",
        ticks_per_sim_day=configured.clock.ticks_per_sim_day,
    )

    assert len(catalogue) == 23
    assert sum(row.is_capital for row in catalogue.values()) == 3
    assert economy.basket is not None
    assert economy.cpi_history_bp[0] == 10_000
    assert economy.basket.version == 1


def test_visible_sellers_is_bounded_and_purchase_legs_close_with_ceiling_tax() -> None:
    configured, world, population, economy, rng = economy_fixture()
    context = GoodsContext(configured, population, world, economy, rng)
    buyer = next(iter(population))
    quotes = visible_sellers(buyer, "fd_staple", 1, ctx=context)

    assert 0 < len(quotes) <= configured.goods.search_k
    assert all(row.qty_available > 0 for row in quotes)
    seller = quotes[0]
    legs, breakdown = purchase_legs(
        buyer.agent_id,
        seller.firm_id,
        seller.sku,
        3,
        251,
        ctx=context,
    )
    assert breakdown.gross_cents == 753
    assert breakdown.sales_tax_cents == 61
    assert breakdown.paid_cents == 814
    assert sum(leg.direction * leg.amount_cents for leg in legs) == 0


def test_linear_expenditure_budget_allocates_every_cent() -> None:
    configured, _world, _population, economy, _rng = economy_fixture()
    prices = {
        sku: inventory.price_cents
        for inventory in economy.inventory.values()
        for sku in (inventory.sku,)
    }
    plan = plan_budget(
        "hh_fixture",
        liquid_cents=2_000_000,
        committed_cents=200_000,
        monthly_expenses_cents=500_000,
        prices_cents=prices,
        skus=economy.skus,
        settings=configured,
    )

    assert sum(plan.spend_by_sku_cents.values()) + plan.savings_cents == plan.disposable_cents
    assert plan.disposable_cents == 1_700_000
    assert all(value >= 0 for value in plan.spend_by_sku_cents.values())


def test_fixed_basket_cpi_tracks_known_ten_percent_price_shift() -> None:
    configured, world, population, economy, rng = economy_fixture()
    assert economy.basket is not None
    economy.basket.base_prices_cents = {sku: 100 for sku in economy.basket.base_prices_cents}
    for ordinal, (sku, base_price) in enumerate(sorted(economy.basket.base_prices_cents.items())):
        economy.goods_transactions.append(
            GoodsTransactionState(
                f"gds_fixture_{ordinal}",
                f"00000000-0000-0000-0000-{ordinal:012d}",
                1,
                "ag_fixture",
                next(iter(economy.firms)),
                sku,
                100,
                base_price * 11_000 // 10_000,
                base_price * 100,
                0,
                0,
            )
        )
    context = GoodsContext(configured, population, world, economy, rng)

    assert abs(cpi_bp(1, ctx=context) - 11_000) <= 1


@pytest.mark.asyncio
async def test_consumption_loop_purchases_rations_and_keeps_money_closed() -> None:
    result = await run_living_city(settings(), ticks=30)
    economy = result.economy
    assert economy is not None
    purchases = [event for event in result.events if event.kind == GOODS_PURCHASED]
    failures = [event for event in result.events if event.kind == PURCHASE_FAILED]
    cpi_events = [event for event in result.events if event.kind == CPI_COMPUTED]

    assert result.report.status == "completed"
    assert purchases
    assert failures
    assert len(cpi_events) == 30
    assert economy.goods_transactions
    assert all(row.quantity >= 0 for row in economy.inventory.values())
    assert sum(row.qty for row in economy.goods_transactions) == sum(
        int(event.payload["qty"]) for event in purchases
    )
    assert economy.ledger.global_balance_cents() == 0
    assert economy.ledger.materialisation_imbalance_cents() == 0
    assert all(value == 0 for value in economy.ledger.deposit_imbalances().values())
