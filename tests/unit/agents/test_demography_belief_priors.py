from __future__ import annotations

from dataclasses import replace

import pytest

from polis.events.kinds import AGENT_BORN, BELIEF_PRIORS_INHERITED
from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_birth_is_single_path_and_inherits_no_fact_proposition() -> None:
    result = await demography_result()
    assert result.demography is not None
    runtime = result.demography
    fertility = runtime.institution.fertility
    adults = sorted(
        (agent for agent in result.population.alive() if 20 <= agent.age_years <= 35),
        key=lambda row: row.agent_id,
    )
    mother, father = adults[:2]
    runtime.graph.form(mother.agent_id, father.agent_id, "partner", "test", 2)
    fertility.conceive(mother.agent_id, father.agent_id, 2, hazard=1.0, draw=0.0)
    fertility.pregnancies[mother.agent_id] = replace(
        fertility.pregnancies[mother.agent_id],
        due_tick=3,
    )

    events = fertility.advance(3)
    born = tuple(event for event in events if event.kind == AGENT_BORN)
    inherited = next(event for event in events if event.kind == BELIEF_PRIORS_INHERITED)

    assert len(born) == 1
    child_id = str(born[0].payload["agent_id"])
    assert child_id.removeprefix("ag_").isdigit()
    assert result.population[child_id].generation == max(mother.generation, father.generation) + 1
    assert not any(
        str(row["proposition"]).startswith("fact.") for row in inherited.payload["propositions"]
    )
    assert runtime.graph.strength(mother.agent_id, child_id) > 0
