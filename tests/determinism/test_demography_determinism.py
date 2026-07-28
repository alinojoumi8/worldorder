from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.living_city import run_living_city


@pytest.mark.determinism
@pytest.mark.asyncio
async def test_demography_event_sequence_is_seed_replayable() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={"run": {"ticks": 31}},
    )

    first = await run_living_city(settings)
    second = await run_living_city(settings)

    def demography_events(result):
        return tuple(
            (
                event.kind,
                event.tick,
                event.actor_id,
                event.subject_ids,
                event.payload,
            )
            for event in result.events
            if 15_000 <= event.kind <= 15_999 or 2_005 <= event.kind <= 2_009 or event.kind == 2_051
        )

    first_events = demography_events(first)
    assert first_events
    assert first_events == demography_events(second)
