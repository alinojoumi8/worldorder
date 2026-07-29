from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.verify import verify_batch
from polis.living_city import run_living_city
from polis.run_identity import build_run_identity

GOLDEN_100_HASH = "7aee6c8647dcf502eb437ff7975fb7acc3f862f84c7eb5dbd04420f3c95bbc1f"


@pytest.mark.determinism
@pytest.mark.asyncio
async def test_frozen_50_agent_100_tick_golden_run() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 100}},
    )

    result = await run_living_city(
        settings,
        run_identity=build_run_identity(settings, code_git_sha="0" * 40),
    )

    assert result.report.chain_hash == GOLDEN_100_HASH
    assert result.report.events == 10_332
    assert len(result.events) == 10_385
    assert len(result.memory) == 16
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_metrics_only_retention_keeps_only_sampled_cognition_traces() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 10, "retention": "metrics_only"}},
    )

    result = await run_living_city(settings, collect_events=False)

    assert 0 < len(result.traces) < 25
