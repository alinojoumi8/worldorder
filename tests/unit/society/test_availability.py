from uuid import UUID

from polis.config.settings import SocietySettings
from polis.events.kinds import SPEECH_UTTERED
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import NewEvent
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.graph import MemoryGraphRepository, SocialGraph, Tie
from polis.society.media.news import AvailabilityIndex


class Memories:
    def __init__(self) -> None:
        self.rows: set[tuple[str, int]] = set()

    def holds_memory_of(self, agent_id: str, event_seq: int) -> bool:
        return (agent_id, event_seq) in self.rows

    def holders_of(self, event_seq: int) -> frozenset[str]:
        return frozenset(agent for agent, seq in self.rows if seq == event_seq)

    def retrieve_recent_texts(self, agent_id: str, tick: int, n: int) -> tuple[str, ...]:
        del agent_id, tick, n
        return ()


def test_private_event_requires_witness_source_or_document() -> None:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=20), MemoryEventSink())
    graph_repo = MemoryGraphRepository()
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=RngRegistry(20),
        repo=graph_repo,
        cfg=SocietySettings(),
    )
    memories = Memories()
    event = log.stage(
        NewEvent(
            SPEECH_UTTERED,
            {
                "speaker_id": "ag_source",
                "place_id": "pl_private",
                "text": "private",
                "addressed_to": [],
                "heard_by": [],
                "topic": None,
                "stance_proposition": None,
                "stance_value": None,
                "conversation_id": "cv_one",
                "turn_index": 0,
                "closing": False,
                "claims": [],
            },
        ),
        tick=1,
        sim_time=clock.sim_time_at(1),
    )
    availability = AvailabilityIndex(
        log=log,
        clock=clock,
        memories=memories,
        graph=graph,
        public_kinds=frozenset(),
        message_exchange=lambda *_: True,
    )
    assert not availability.available_to("ag_reporter", event, 1)

    memories.rows.add(("ag_reporter", event.seq))
    assert availability.channel("ag_reporter", event, 1) == "witness"
    memories.rows.clear()

    graph_repo.put(
        Tie(
            "ag_reporter",
            "ag_source",
            "friend",
            0.8,
            0.3,
            0.8,
            0,
            None,
            0,
        )
    )
    memories.rows.add(("ag_source", event.seq))
    assert availability.cultivate("ag_reporter", "ag_source", "msg_answer", 2) is not None
    assert availability.channel("ag_reporter", event, 2) == "source"


def test_public_and_document_channels() -> None:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=21), MemoryEventSink())
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=RngRegistry(21),
        repo=MemoryGraphRepository(),
        cfg=SocietySettings(),
    )
    memories = Memories()
    event = log.stage(
        NewEvent(
            SPEECH_UTTERED,
            {
                "speaker_id": "ag_source",
                "place_id": "pl_square",
                "text": "public",
                "addressed_to": [],
                "heard_by": [],
                "topic": None,
                "stance_proposition": None,
                "stance_value": None,
                "conversation_id": "cv_two",
                "turn_index": 0,
                "closing": False,
                "claims": [],
            },
        ),
        tick=1,
        sim_time=clock.sim_time_at(1),
    )
    public = AvailabilityIndex(
        log=log,
        clock=clock,
        memories=memories,
        graph=graph,
        public_kinds=frozenset({SPEECH_UTTERED}),
    )
    assert public.channel("ag_reporter", event, 1) == "public"
    document = AvailabilityIndex(
        log=log,
        clock=clock,
        memories=memories,
        graph=graph,
        public_kinds=frozenset(),
        document_access=lambda *_: True,
    )
    assert document.channel("ag_reporter", event, 1) == "document"
