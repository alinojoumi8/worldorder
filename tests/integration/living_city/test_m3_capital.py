from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.kinds import KIND_REGISTRY
from polis.living_city import run_living_city

M3_GOLDEN_100_HASH = "97c67255c5ba6ed12fb019f10bb67b6922373ff7b3ff0a7fc44061b7cfba9cab"


@pytest.mark.asyncio
async def test_m3_acceptance_path_is_complete_and_deterministic() -> None:
    settings = load_settings(Path("configs/m3-smoke.yaml"))
    first = await run_living_city(settings)
    second = await run_living_city(settings)

    assert first.report.status == second.report.status == "completed"
    assert first.report.last_tick == second.report.last_tick == 12
    assert first.report.chain_hash == second.report.chain_hash
    assert first.events == second.events
    assert first.economy is not None
    assert second.economy is not None
    assert first.economy.dump() == second.economy.dump()

    counts = Counter(KIND_REGISTRY[event.kind].name for event in first.events)
    for kind in (
        "STARTUP_FOUNDED",
        "ROUND_CLOSED",
        "IPO_COMPLETED",
        "TRADE_EXECUTED",
        "ACQUISITION_COMPLETED",
        "DIVIDEND_PAID",
        "BANKRUPTCY_DISCHARGED",
    ):
        assert counts[kind] > 0
    assert first.metrics.series("market_index")


@pytest.mark.asyncio
async def test_non_fixture_mechanical_market_bootstraps_and_trades_deterministically() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={
            "run": {"ticks": 10},
            "ventures": {"acceptance_fixture": False},
            "exchange": {
                "bootstrap_listing_day": 1,
                "zero_intelligence_participation_bp": 10_000,
            },
            "mechanisms": {"exchange_zero_intelligence_trader": "seeded_reservation_orders"},
        },
    )
    first = await run_living_city(settings)
    second = await run_living_city(settings)

    assert first.report.chain_hash == second.report.chain_hash
    assert first.economy is not None
    assert second.economy is not None
    assert sorted(first.economy.exchange.securities) == ["POLS"]
    assert first.economy.exchange.trades
    assert first.economy.exchange.dump() == second.economy.exchange.dump()


@pytest.mark.asyncio
async def test_frozen_m3_100_tick_golden_covers_every_leg_family() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={"run": {"ticks": 100}},
    )
    result = await run_living_city(settings)

    assert result.report.status == "completed"
    assert result.report.chain_hash == M3_GOLDEN_100_HASH
    counts = Counter(KIND_REGISTRY[event.kind].name for event in result.events)
    for kind in (
        "WAGE_PAID",
        "GOODS_PURCHASED",
        "TRADE_EXECUTED",
        "LOAN_PAYMENT_MADE",
    ):
        assert counts[kind] > 0
