from __future__ import annotations

from dataclasses import replace

import pytest

from polis.economy.invariants import check_money
from polis.events.kinds import AGENT_BORN
from tests.demography_support import demography_result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_birth_path_supports_three_living_generations() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    runtime = result.demography
    fertility = runtime.institution.fertility
    adults = sorted(
        (agent for agent in result.population.alive() if 20 <= agent.age_years <= 35),
        key=lambda agent: agent.agent_id,
    )
    first_parent, second_parent, later_partner = adults[:3]
    runtime.graph.form(
        first_parent.agent_id,
        second_parent.agent_id,
        "partner",
        "test",
        2,
    )
    fertility.conceive(
        first_parent.agent_id,
        second_parent.agent_id,
        2,
        hazard=1.0,
        draw=0.0,
    )
    fertility.pregnancies[first_parent.agent_id] = replace(
        fertility.pregnancies[first_parent.agent_id],
        due_tick=3,
    )
    first_birth = fertility.advance(3)
    child_id = str(
        next(event for event in first_birth if event.kind == AGENT_BORN).payload["agent_id"]
    )
    child = result.population[child_id]
    child.age_years = 25.0
    runtime.graph.form(child_id, later_partner.agent_id, "partner", "test", 4)
    fertility.conceive(
        child_id,
        later_partner.agent_id,
        4,
        hazard=1.0,
        draw=0.0,
    )
    fertility.pregnancies[child_id] = replace(
        fertility.pregnancies[child_id],
        due_tick=5,
    )

    second_birth = fertility.advance(5)
    grandchild_id = str(
        next(event for event in second_birth if event.kind == AGENT_BORN).payload["agent_id"]
    )

    assert result.population[child_id].generation == 1
    assert result.population[grandchild_id].generation == 2
    assert {agent.generation for agent in result.population.alive()} >= {0, 1, 2}
    assert check_money(result.economy.ledger).invariant_id == "INV-MONEY"
