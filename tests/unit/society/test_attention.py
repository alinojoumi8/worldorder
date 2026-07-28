import math
from types import SimpleNamespace
from uuid import UUID

from polis.config.settings import SocietySettings
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.comms import AgentBrief, attention, heard_by
from polis.society.graph import MemoryGraphRepository, SocialGraph


def graph() -> SocialGraph:
    return SocialGraph(
        log=EventLog(UUID(int=1), MemoryEventSink()),
        clock=Clock(PROFILES["microscope"]),
        rng=RngRegistry(1),
        repo=MemoryGraphRepository(),
        cfg=SocietySettings(),
    )


def test_attention_matches_the_spec_formula() -> None:
    rng = RngRegistry(91)
    expected_rng = RngRegistry(91)
    noise = -0.05 + 0.10 * expected_rng.get("comms.attention", "sp_1", 4).random()
    expected = 0.30 + 0.50 * 0.6 + 0.20 - 0.15 * math.log1p(8) / math.log1p(20) + noise

    actual = attention(
        "a",
        "b",
        tie_strength=0.6,
        addressed=True,
        occupancy=10,
        capacity=20,
        speech_id="sp_1",
        tick=4,
        rng=rng,
    )

    assert actual == expected


def test_heard_by_caps_sorts_and_supports_uniform_attention() -> None:
    candidates = [AgentBrief(f"ag_{index:02}") for index in range(20, 0, -1)]
    listeners = heard_by(
        "speaker",
        candidates,
        place=SimpleNamespace(occupancy=21, capacity=30),
        addressed_to=(),
        graph=graph(),
        speech_id="sp_uniform",
        tick=1,
        rng=RngRegistry(7),
        cfg=SocietySettings(comms_attention="uniform"),
    )

    assert len(listeners) == 12
    assert [listener.agent_id for listener in listeners] == sorted(
        listener.agent_id for listener in listeners
    )
    assert {listener.attention for listener in listeners} == {1.0}
