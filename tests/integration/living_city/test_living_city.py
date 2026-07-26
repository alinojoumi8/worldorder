from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.verify import verify_batch
from polis.living_city import run_living_city

GOLDEN_100_HASH = "a8c9b11e10d556688029547bedc42dc6fb11a4409aed13b4ac6c0eb18368e694"


@pytest.mark.determinism
@pytest.mark.asyncio
async def test_frozen_50_agent_100_tick_golden_run() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 100}},
    )

    result = await run_living_city(settings)

    assert result.report.chain_hash == GOLDEN_100_HASH
    assert result.report.events == 11_424
    assert len(result.events) == 11_477
    assert verify_batch(result.events).ok


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fast_50_agent_500_tick_smoke() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))

    result = await run_living_city(settings, collect_events=False)

    latest = result.metrics.latest()
    assert result.report.ticks == 500
    assert result.report.status == "completed"
    assert result.population.population() == 50
    assert latest["sys.actions.unique"].value >= 2
    assert 500 <= latest["sys.cognition.deliberate_share"].value <= 1_000
    assert latest["city.wellbeing_mean"].value > 0
