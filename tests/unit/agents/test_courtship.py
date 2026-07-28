from __future__ import annotations

import pytest

from polis.agents.actions import ActionType
from polis.events.kinds import COURTSHIP_ENDED, COURTSHIP_STARTED, UNION_FORMED
from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_union_requires_mutual_courtship_and_two_proposals() -> None:
    result = await demography_result()
    assert result.demography is not None
    registry = result.demography.courtships
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda row: row.agent_id,
    )
    a, b = adults[:2]

    registry.court(a.agent_id, b.agent_id, 2)
    assert registry.propose_union(a.agent_id, b.agent_id, 2) == ()
    registry.court(b.agent_id, a.agent_id, 2)
    assert registry.propose_union(a.agent_id, b.agent_id, 2) == ()
    events = registry.propose_union(b.agent_id, a.agent_id, 2)

    assert sum(event.kind == UNION_FORMED for event in events) == 1
    assert result.demography.graph.live_partner(a.agent_id) == b.agent_id


@pytest.mark.asyncio
async def test_one_sided_courtship_expires_without_auto_pairing() -> None:
    result = await demography_result()
    assert result.demography is not None
    registry = result.demography.courtships
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda row: row.agent_id,
    )
    a, b = adults[:2]
    registry.court(a.agent_id, b.agent_id, 2)
    expiry = (
        2 + registry.cfg.courtship_window_sim_days * registry.clock.profile.ticks_per_sim_day + 1
    )
    events = registry.expire(expiry)
    assert any(event.kind == COURTSHIP_ENDED for event in events)
    assert result.demography.graph.live_partner(a.agent_id) is None


@pytest.mark.asyncio
async def test_repeated_courtship_updates_intent_without_duplicate_start_event() -> None:
    result = await demography_result()
    assert result.demography is not None
    registry = result.demography.courtships
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda row: row.agent_id,
    )
    a, b = adults[:2]

    first = registry.court(a.agent_id, b.agent_id, 2)
    repeated = registry.court(a.agent_id, b.agent_id, 3)

    assert [event.kind for event in first] == [COURTSHIP_STARTED]
    assert repeated == ()
    assert registry.rows[registry._key(a.agent_id, b.agent_id)].latest[a.agent_id] == 3


@pytest.mark.asyncio
async def test_queued_courtship_is_dropped_when_target_is_no_longer_alive() -> None:
    result = await demography_result()
    assert result.demography is not None
    registry = result.demography.courtships
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda row: row.agent_id,
    )
    a, b = adults[:2]
    registry.queue(ActionType.COURT, a.agent_id, b.agent_id)
    b.alive = False

    assert registry.process(2) == ()
    assert registry.rows == {}
