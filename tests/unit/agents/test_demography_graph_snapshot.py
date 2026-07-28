from __future__ import annotations

import pytest

from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_graph_snapshot_restores_ties_without_replacing_collaborators() -> None:
    result = await demography_result()
    assert result.demography is not None
    port = result.demography.institution.estate.graph
    graph = port.graph
    collaborators = (graph.log, graph.clock, graph.rng, graph.repo, graph.cfg)
    snapshot = port.dump()
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda row: row.agent_id,
    )
    a, b = adults[:2]
    port.form(a.agent_id, b.agent_id, "friend", "test", 2)
    assert port.strength(a.agent_id, b.agent_id) > 0

    port.load(snapshot)

    assert (graph.log, graph.clock, graph.rng, graph.repo, graph.cfg) == collaborators
    assert port.strength(a.agent_id, b.agent_id) == 0
