from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.kinds import KIND_REGISTRY
from polis.living_city import run_living_city

M3_GOLDEN_100_HASH = "a26b0f5c453bed7a95a1120be70b3197b74a57db9f46b483f97b9c850af9119d"


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
