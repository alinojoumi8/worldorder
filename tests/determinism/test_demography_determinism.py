from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.kinds import (
    BELIEF_PRIORS_INHERITED,
    BEREAVEMENT_APPLIED,
    CHILD_COST_CHARGED,
    CONCEPTION,
    COURTSHIP_ENDED,
    COURTSHIP_STARTED,
    ESTATE_CLOSED,
    ESTATE_DEBTS_SETTLED,
    ESTATE_DISTRIBUTED,
    ESTATE_OPENED,
    HOUSEHOLD_DISSOLVED,
    HOUSEHOLD_FORMED,
    HOUSEHOLD_JOINED,
    HOUSEHOLD_LEFT,
    MIGRATION_IN,
    MIGRATION_OUT,
    PREGNANCY_ENDED,
    STATE_CARE_STARTED,
    UNION_DISSOLVED,
    UNION_FORMED,
)
from polis.living_city import run_living_city

DEMOGRAPHY_EVENT_KINDS = frozenset(
    {
        COURTSHIP_STARTED,
        COURTSHIP_ENDED,
        UNION_FORMED,
        UNION_DISSOLVED,
        HOUSEHOLD_FORMED,
        HOUSEHOLD_JOINED,
        HOUSEHOLD_LEFT,
        HOUSEHOLD_DISSOLVED,
        CONCEPTION,
        PREGNANCY_ENDED,
        CHILD_COST_CHARGED,
        STATE_CARE_STARTED,
        BELIEF_PRIORS_INHERITED,
        MIGRATION_IN,
        MIGRATION_OUT,
        ESTATE_OPENED,
        ESTATE_DEBTS_SETTLED,
        ESTATE_DISTRIBUTED,
        ESTATE_CLOSED,
        BEREAVEMENT_APPLIED,
    }
)


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
            if event.kind in DEMOGRAPHY_EVENT_KINDS
        )

    first_events = demography_events(first)
    assert first_events
    assert first_events == demography_events(second)
