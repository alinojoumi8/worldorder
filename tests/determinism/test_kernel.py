from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.verify import verify_batch
from polis.simulation import run_empty


@pytest.mark.asyncio
async def test_repeated_empty_runs_are_byte_identical() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 200}},
    )

    first = await run_empty(settings)
    second = await run_empty(settings)

    assert first.events == second.events
    assert first.report.chain_hash == second.report.chain_hash
    assert first.report.events == 400
    assert verify_batch(first.events).ok


@pytest.mark.asyncio
async def test_resume_does_not_diverge_from_continuous_run() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 200}},
    )

    continuous = await run_empty(settings)
    partial = await run_empty(settings, ticks=100)
    resumed = await run_empty(settings, ticks=200, resume_events=partial.events)

    assert resumed.events == continuous.events
    assert resumed.report.chain_hash == continuous.report.chain_hash
    assert verify_batch(resumed.events).ok
