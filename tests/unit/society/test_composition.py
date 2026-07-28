from types import SimpleNamespace
from uuid import UUID

import pytest

from polis.agents.actions import (
    ActionType,
    DuplicateHandler,
    InstitutionSlot,
    LegalityVerdict,
    ResolutionContext,
    ValidatedAction,
    ValidationContext,
    make_action,
)
from polis.agents.actions.params.speech import SayParams
from polis.config.settings import SocietySettings
from polis.events.kinds import (
    CONVERSATION_CLOSED,
    CONVERSATION_OPENED,
    SPEECH_UTTERED,
)
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import NewEvent
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.comms import (
    CommunicationResolver,
    ConversationTracker,
    MemoryCommsRepository,
    NullLedger,
    UtteranceDelivery,
)
from polis.society.graph import MemoryGraphRepository, SocialGraph
from polis.society.protocols import NullBeliefChannel


class Place:
    capacity = 8
    owner_id = None
    rent_cents = 0


class Location:
    place_id = "pl_square"


class CoLocatedWorld:
    def __init__(self) -> None:
        self.locations = {"ag_a": Location(), "ag_b": Location()}

    def place(self, place_id: str) -> Place:
        assert place_id == "pl_square"
        return Place()

    def occupancy(self, place_id: str) -> tuple[str, ...]:
        assert place_id == "pl_square"
        return ("ag_a", "ag_b")


class SubResolver:
    slot = InstitutionSlot.COMMUNICATION
    handles = frozenset({ActionType.COURT})


class WrongSlot(SubResolver):
    slot = InstitutionSlot.LAW


class Duplicate(SubResolver):
    handles = frozenset({ActionType.SAY})


def resolver() -> CommunicationResolver:
    log = EventLog(UUID(int=5), MemoryEventSink())
    clock = Clock(PROFILES["microscope"])
    cfg = SocietySettings()
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=RngRegistry(5),
        repo=MemoryGraphRepository(),
        cfg=cfg,
    )
    return CommunicationResolver(
        log=log,
        clock=clock,
        rng=RngRegistry(5),
        world=object(),  # type: ignore[arg-type]
        graph=graph,
        platform=object(),  # type: ignore[arg-type]
        conversations=ConversationTracker(log=log, clock=clock),
        beliefs=NullBeliefChannel(),
        ledger=NullLedger(),
        repo=MemoryCommsRepository(),
        cfg=cfg,
    )


def test_compose_rejects_wrong_slots_and_duplicate_types() -> None:
    communications = resolver()
    with pytest.raises(DuplicateHandler):
        communications.compose(WrongSlot())  # type: ignore[arg-type]
    with pytest.raises(DuplicateHandler):
        communications.compose(Duplicate())  # type: ignore[arg-type]


def test_say_locality_uses_the_committed_observation_not_live_occupancy() -> None:
    communications = resolver()
    action = make_action(
        actor_id="ag_a",
        tick=1,
        action_type=ActionType.SAY,
        params={"text": "hello"},
    )
    observation = SimpleNamespace(
        place=SimpleNamespace(place_id="pl_square"),
        co_located=(),
    )

    failure = communications.check_locality(
        action,
        ValidationContext(observation, object(), 1),
    )

    assert failure is not None
    assert failure.reason == "locality"


def test_utterance_delivery_is_visible_only_after_its_speech_tick() -> None:
    repo = MemoryCommsRepository()
    repo.add_utterance(UtteranceDelivery("ag_a", "ag_b", 8, "not before the boundary"))

    assert repo.utterances_for("ag_b", 8) == ()
    assert len(repo.utterances_for("ag_b", 9)) == 1


def test_four_turn_conversation_has_one_open_and_a_complete_cause_chain() -> None:
    log = EventLog(UUID(int=7), MemoryEventSink())
    clock = Clock(PROFILES["microscope"])
    cfg = SocietySettings(hearing_threshold=0)
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=RngRegistry(7),
        repo=MemoryGraphRepository(),
        cfg=cfg,
    )
    conversations = ConversationTracker(log=log, clock=clock)
    communications = CommunicationResolver(
        log=log,
        clock=clock,
        rng=RngRegistry(7),
        world=CoLocatedWorld(),  # type: ignore[arg-type]
        graph=graph,
        platform=object(),  # type: ignore[arg-type]
        conversations=conversations,
        beliefs=NullBeliefChannel(),
        ledger=NullLedger(),
        repo=MemoryCommsRepository(),
        cfg=cfg,
    )

    returned = []
    observations = {
        "ag_a": SimpleNamespace(
            place=SimpleNamespace(place_id="pl_square"),
            co_located=(SimpleNamespace(agent_id="ag_b"),),
        ),
        "ag_b": SimpleNamespace(
            place=SimpleNamespace(place_id="pl_square"),
            co_located=(SimpleNamespace(agent_id="ag_a"),),
        ),
    }
    for tick, actor_id, listener_id in (
        (0, "ag_a", "ag_b"),
        (1, "ag_b", "ag_a"),
        (2, "ag_a", "ag_b"),
        (3, "ag_b", "ag_a"),
    ):
        params = SayParams(text=f"turn-{tick}", addressed_to=(listener_id,))
        action = make_action(
            actor_id=actor_id,
            tick=tick,
            action_type=ActionType.SAY,
            params=params.model_dump(mode="json"),
        )

        def emit(draft: NewEvent, *, current_tick: int = tick):  # type: ignore[no-untyped-def]
            return log.stage(
                draft,
                tick=current_tick,
                sim_time=clock.sim_time_at(current_tick),
            )

        returned.extend(
            communications.resolve(
                (ValidatedAction(action, params, LegalityVerdict(False), 0),),
                tick,
                ResolutionContext(
                    emit=emit,
                    repositories={"observations": observations},
                ),
            )
        )

    returned.extend(conversations.close_idle(5, CoLocatedWorld()))  # type: ignore[arg-type]
    opens = [event for event in returned if event.kind == CONVERSATION_OPENED]
    turns = [event for event in returned if event.kind == SPEECH_UTTERED]
    closes = [event for event in returned if event.kind == CONVERSATION_CLOSED]

    assert len(opens) == 1
    assert [event.payload["turn_index"] for event in turns] == [0, 1, 2, 3]
    assert turns[0].cause_seq is None
    assert [event.cause_seq for event in turns[1:]] == [event.seq for event in turns[:-1]]
    assert len(closes) == 1
    assert closes[0].cause_seq == turns[-1].seq
    assert closes[0].payload["turns"] == 4
