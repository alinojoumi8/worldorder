from __future__ import annotations

from polis.agents.genesis import generate_agents
from polis.agents.memory import MemoryStore, ReflectionInsight
from polis.config.settings import MemorySettings, PopulationSettings, WorldSettings
from polis.kernel.rng import RngRegistry
from polis.world.generator import generate_world


def _agent():
    world = generate_world(
        WorldSettings(width=30, height=30, districts=3, places_per_district=6),
        RngRegistry(31),
    )
    population = generate_agents(
        PopulationSettings(initial_agents=2),
        world,
        RngRegistry(31),
    )
    return population["ag_0000"]


def test_retrieval_is_ranked_bounded_and_updates_access() -> None:
    agent = _agent()
    store = MemoryStore(MemorySettings(retrieval_k=2))
    first = store.write(
        agent_id=agent.agent_id,
        tick=1,
        type="observation",
        text="I studied engineering at the university.",
        importance=0.8,
    )
    store.write(
        agent_id=agent.agent_id,
        tick=2,
        type="observation",
        text="The park was quiet after lunch.",
        importance=0.2,
    )
    store.write(
        agent_id=agent.agent_id,
        tick=3,
        type="plan",
        text="I want to improve my engineering skill.",
        importance=0.7,
    )

    result = store.retrieve(agent.agent_id, "engineering study", tick=10)

    assert len(result) == 2
    assert [row.rank for row in result] == [1, 2]
    assert first.access_count <= 1
    assert all(row.memory_id.startswith("mem_0000_") for row in result)


def test_reflection_drops_unsupported_citations() -> None:
    agent = _agent()
    store = MemoryStore(MemorySettings(reflection_threshold=0.1))
    source = store.maybe_write_observation(
        agent,
        tick=1,
        text="I completed a difficult lesson.",
        salience=0.9,
    )
    assert source is not None
    assert store.reflection_due(agent, tick=24)

    rows = store.apply_reflection(
        agent,
        tick=24,
        insights=(
            ReflectionInsight("Learning rewards patience.", (source.memory_id,), 0.8),
            ReflectionInsight("Unsupported claim.", ("mem_missing",), 0.9),
        ),
        identity_summary="I am a patient learner.",
    )

    assert len(rows) == 1
    assert rows[0].parent_memory_ids == (source.memory_id,)
    assert agent.identity_summary == "I am a patient learner."
    assert not store.reflection_due(agent, tick=25)
