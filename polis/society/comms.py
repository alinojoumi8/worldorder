from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol

from polis.agents.actions import (
    Action,
    ActionType,
    DuplicateHandler,
    GateFailure,
    GateResult,
    InstitutionResolver,
    InstitutionSlot,
    ResolutionContext,
    ValidatedAction,
    ValidationContext,
)
from polis.config.mechanisms import mechanism
from polis.config.settings import SocietySettings
from polis.events.kinds import (
    BROADCAST_MADE,
    CONVERSATION_CLOSED,
    CONVERSATION_OPENED,
    MESSAGE_READ,
    MESSAGE_SENT,
    SPEECH_UTTERED,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.det import det_id
from polis.kernel.rng import RngRegistry
from polis.society.graph import Interaction, SocialGraph
from polis.society.media.platform import Platform
from polis.society.protocols import BeliefChannel
from polis.world.api import World

OWN_TYPES: Final[frozenset[ActionType]] = frozenset(
    {
        ActionType.SAY,
        ActionType.DIRECT_MESSAGE,
        ActionType.BROADCAST,
        ActionType.POST,
        ActionType.REPOST,
        ActionType.LIKE,
        ActionType.COMMENT,
        ActionType.FOLLOW,
        ActionType.UNFOLLOW,
        ActionType.BEFRIEND,
    }
)


@dataclass(frozen=True, slots=True)
class AgentBrief:
    agent_id: str
    display_name: str = ""
    relationship: str | None = None


@dataclass(frozen=True, slots=True)
class Listener:
    agent_id: str
    attention: float


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str
    place_id: str
    participants: tuple[str, ...]
    topic: str | None
    turn_index: int
    last_turn_tick: int
    opener_id: str


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    sender_id: str
    recipient_id: str
    tick: int
    text: str
    topic: str | None = None
    stance_proposition: str | None = None
    stance_value: float | None = None
    read_tick: int | None = None


@dataclass(frozen=True, slots=True)
class UtteranceDelivery:
    speaker_id: str
    listener_id: str
    tick: int
    text: str


@dataclass(frozen=True, slots=True)
class SpeechSnapshot:
    place_id: str
    candidates: tuple[AgentBrief, ...]


class LedgerApi(Protocol):
    def can_pay_broadcast(
        self,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
    ) -> bool: ...

    def next_broadcast_txn_id(self, tick: int) -> str: ...

    def post_broadcast_fee(
        self,
        *,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
        txn_id: str,
        tick: int,
        cause: Event,
    ) -> str: ...


class NullLedger:
    def can_pay_broadcast(
        self,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
    ) -> bool:
        del payer_id, payee_id, amount_cents
        return True

    def next_broadcast_txn_id(self, tick: int) -> str:
        return det_id("txn", "society.broadcast.null", tick)

    def post_broadcast_fee(
        self,
        *,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
        txn_id: str,
        tick: int,
        cause: Event,
    ) -> str:
        del payer_id, payee_id, amount_cents, tick, cause
        return txn_id


class CommsRepository(Protocol):
    def add_message(self, message: Message) -> None: ...

    def messages_for(self, agent_id: str) -> tuple[Message, ...]: ...

    def mark_read(self, message_id: str, tick: int) -> Message | None: ...

    def note_dm(self, sender_id: str, recipient_id: str, sim_day: int) -> int: ...

    def dm_count(self, sender_id: str, recipient_id: str, sim_day: int) -> int: ...

    def offer_befriend(self, sender_id: str, target_id: str, tick: int) -> int | None: ...

    def add_utterance(self, delivery: UtteranceDelivery) -> None: ...

    def utterances_for(self, agent_id: str, before_tick: int) -> tuple[UtteranceDelivery, ...]: ...


class MemoryCommsRepository:
    def __init__(self) -> None:
        self._messages: dict[str, Message] = {}
        self._dm_counts: dict[tuple[str, str, int], int] = defaultdict(int)
        self._befriend: dict[tuple[str, str], int] = {}
        self._utterances: list[UtteranceDelivery] = []

    def add_message(self, message: Message) -> None:
        self._messages[message.message_id] = message

    def messages_for(self, agent_id: str) -> tuple[Message, ...]:
        return tuple(
            sorted(
                (
                    message
                    for message in self._messages.values()
                    if message.recipient_id == agent_id
                ),
                key=lambda message: (message.tick, message.message_id),
            )
        )

    def mark_read(self, message_id: str, tick: int) -> Message | None:
        message = self._messages.get(message_id)
        if message is None or message.read_tick is not None:
            return None
        message = replace(message, read_tick=tick)
        self._messages[message_id] = message
        return message

    def note_dm(self, sender_id: str, recipient_id: str, sim_day: int) -> int:
        key = (sender_id, recipient_id, sim_day)
        self._dm_counts[key] += 1
        return self._dm_counts[key]

    def dm_count(self, sender_id: str, recipient_id: str, sim_day: int) -> int:
        return self._dm_counts[(sender_id, recipient_id, sim_day)]

    def offer_befriend(self, sender_id: str, target_id: str, tick: int) -> int | None:
        reciprocal = self._befriend.get((target_id, sender_id))
        self._befriend[(sender_id, target_id)] = tick
        return reciprocal

    def add_utterance(self, delivery: UtteranceDelivery) -> None:
        self._utterances.append(delivery)

    def utterances_for(self, agent_id: str, before_tick: int) -> tuple[UtteranceDelivery, ...]:
        return tuple(
            sorted(
                (
                    row
                    for row in self._utterances
                    if row.listener_id == agent_id and row.tick < before_tick
                ),
                key=lambda row: (row.tick, row.speaker_id),
            )
        )


@mechanism(
    "comms_attention",
    entails=(
        "utterances propagate preferentially along strong ties and decay in crowded places; "
        "therefore any finding that information travels faster within cliques than across "
        "them is partly entailed. Ablate with comms_attention: uniform, which sets "
        "attention = 1 for all co-located agents."
    ),
)
def attention(
    speaker_id: str,
    listener_id: str,
    *,
    tie_strength: float,
    addressed: bool,
    occupancy: int,
    capacity: int,
    speech_id: str,
    tick: int,
    rng: RngRegistry,
    uniform: bool = False,
) -> float:
    del speaker_id, listener_id
    if uniform:
        return 1.0
    crowd = (
        0.0
        if capacity <= 0
        else 0.15 * math.log1p(max(0, occupancy - 2)) / max(math.log1p(capacity), 1e-12)
    )
    noise = -0.05 + 0.10 * rng.get("comms.attention", speech_id, tick).random()
    return min(
        1.0,
        max(
            0.0,
            0.30 + 0.50 * tie_strength + 0.20 * float(addressed) - crowd + noise,
        ),
    )


def heard_by(
    speaker_id: str,
    candidates: Sequence[AgentBrief],
    *,
    place: object,
    addressed_to: Sequence[str],
    graph: SocialGraph,
    speech_id: str,
    tick: int,
    rng: RngRegistry,
    cfg: SocietySettings,
) -> tuple[Listener, ...]:
    addressed = frozenset(addressed_to)
    ranked = sorted(
        (candidate for candidate in candidates if candidate.agent_id != speaker_id),
        key=lambda candidate: (-graph.strength(speaker_id, candidate.agent_id), candidate.agent_id),
    )[:12]
    occupancy = int(getattr(place, "occupancy", len(candidates) + 1))
    capacity = int(getattr(place, "capacity", max(occupancy, 1)))
    uniform = False
    mechanism_mode = getattr(cfg, "comms_attention", None)
    if mechanism_mode is not None:
        uniform = mechanism_mode == "uniform"
    listeners = [
        Listener(
            candidate.agent_id,
            attention(
                speaker_id,
                candidate.agent_id,
                tie_strength=graph.strength(speaker_id, candidate.agent_id),
                addressed=candidate.agent_id in addressed,
                occupancy=occupancy,
                capacity=capacity,
                speech_id=speech_id,
                tick=tick,
                rng=rng,
                uniform=uniform,
            ),
        )
        for candidate in ranked
    ]
    return tuple(
        sorted(
            (listener for listener in listeners if listener.attention >= cfg.hearing_threshold),
            key=lambda listener: listener.agent_id,
        )
    )


class ConversationTracker:
    """Event-rebuildable turn state; it never invokes cognition or an LLM."""

    def __init__(self, *, log: EventLog, clock: Clock, idle_ticks: int = 2) -> None:
        self.log = log
        self.clock = clock
        self.idle_ticks = idle_ticks
        self._active: dict[str, Conversation] = {}
        self._opened_tick: dict[str, int] = {}
        self._last_seq: dict[str, int] = {}

    def open_or_join(
        self,
        speaker_id: str,
        place_id: str,
        addressed_to: Sequence[str],
        topic: str | None,
        tick: int,
    ) -> tuple[Conversation, Event | None]:
        participants = tuple(sorted({speaker_id, *addressed_to}))
        for conversation in sorted(self._active.values(), key=lambda row: row.conversation_id):
            if conversation.place_id == place_id and set(participants).issubset(
                conversation.participants
            ):
                return conversation, None
        conversation_id = det_id("cv", "society.conversation", place_id, participants, tick)
        conversation = Conversation(
            conversation_id,
            place_id,
            participants,
            topic,
            0,
            tick,
            speaker_id,
        )
        self._active[conversation_id] = conversation
        self._opened_tick[conversation_id] = tick
        event = self.log.stage(
            NewEvent(
                CONVERSATION_OPENED,
                {
                    "conversation_id": conversation_id,
                    "place_id": place_id,
                    "participants": list(participants),
                    "opener_id": speaker_id,
                    "topic": topic,
                },
                actor_id=speaker_id,
                subject_ids=participants,
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        return conversation, event

    def get(self, conversation_id: str) -> Conversation | None:
        return self._active.get(conversation_id)

    def record_turn(
        self,
        conversation_id: str,
        speaker_id: str,
        tick: int,
        cause_seq: int | None,
    ) -> int:
        del cause_seq
        conversation = self._active[conversation_id]
        turn_index = conversation.turn_index
        participants = tuple(sorted({*conversation.participants, speaker_id}))
        self._active[conversation_id] = replace(
            conversation,
            participants=participants,
            turn_index=turn_index + 1,
            last_turn_tick=tick,
        )
        return turn_index

    def cause_seq(self, conversation_id: str) -> int | None:
        return self._last_seq.get(conversation_id)

    def note_seq(self, conversation_id: str, seq: int) -> None:
        self._last_seq[conversation_id] = seq

    def close(self, conversation_id: str, tick: int, reason: str) -> Event | None:
        conversation = self._active.pop(conversation_id, None)
        if conversation is None:
            return None
        event = self.log.stage(
            NewEvent(
                CONVERSATION_CLOSED,
                {
                    "conversation_id": conversation_id,
                    "turns": conversation.turn_index,
                    "reason": reason,
                    "duration_ticks": tick - self._opened_tick.pop(conversation_id, tick),
                    "participants": list(conversation.participants),
                },
                subject_ids=conversation.participants,
                cause_seq=self._last_seq.pop(conversation_id, None),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        return event

    def close_idle(self, tick: int, world: World) -> Sequence[Event]:
        events: list[Event] = []
        for conversation in tuple(
            sorted(self._active.values(), key=lambda row: row.conversation_id)
        ):
            reason: str | None = None
            if tick - conversation.last_turn_tick >= self.idle_ticks:
                reason = "idle"
            elif any(
                getattr(world.locations.get(agent_id), "place_id", None) != conversation.place_id
                for agent_id in conversation.participants
            ):
                reason = "dispersed"
            if reason is not None:
                event = self.close(conversation.conversation_id, tick, reason)
                if event is not None:
                    events.append(event)
        return tuple(events)

    def active_for(self, agent_id: str) -> tuple[Conversation, ...]:
        return tuple(
            sorted(
                (
                    conversation
                    for conversation in self._active.values()
                    if agent_id in conversation.participants
                ),
                key=lambda conversation: conversation.conversation_id,
            )
        )


def _param(params: object, name: str, default: Any = None) -> Any:
    if isinstance(params, Mapping):
        return params.get(name, default)
    return getattr(params, name, default)


def _speech_snapshot(observation: object) -> SpeechSnapshot | None:
    place = getattr(observation, "place", None)
    place_id = getattr(place, "place_id", None)
    if place_id is None:
        return None
    candidates: list[AgentBrief] = []
    for row in getattr(observation, "co_located", ()):
        agent_id = getattr(row, "agent_id", None)
        if agent_id is None:
            continue
        candidates.append(
            AgentBrief(
                str(agent_id),
                str(getattr(row, "display_name", "")),
                getattr(row, "relationship", None),
            )
        )
    return SpeechSnapshot(str(place_id), tuple(candidates))


def _office_holder(register: object | None, agent_id: str) -> bool:
    if register is None:
        return False
    if isinstance(register, (set, frozenset)):
        return agent_id in register
    predicate = getattr(register, "holds_public_office", None)
    return bool(predicate(agent_id)) if callable(predicate) else False


class CommunicationResolver:
    """The sole PHASE 5 communication-slot resolver.

    C17 and C20 sub-resolvers must expose the standard InstitutionResolver methods,
    use ``InstitutionSlot.COMMUNICATION``, and claim action types disjoint from this
    resolver and every other sub-resolver. Resolution concatenates this resolver's
    own events first, followed by sub-resolvers sorted by concrete class name.
    """

    slot: Final[InstitutionSlot] = InstitutionSlot.COMMUNICATION

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        graph: SocialGraph,
        platform: Platform,
        conversations: ConversationTracker,
        beliefs: BeliefChannel,
        ledger: LedgerApi,
        repo: CommsRepository,
        cfg: SocietySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.graph = graph
        self.platform = platform
        self.conversations = conversations
        self.beliefs = beliefs
        self.ledger = ledger
        self.repo = repo
        self.cfg = cfg
        self._subresolvers: tuple[InstitutionResolver, ...] = ()
        self._speech_snapshots: dict[object, tuple[int, SpeechSnapshot]] = {}
        self.handles = OWN_TYPES

    def compose(self, sub: InstitutionResolver) -> None:
        if sub.slot != self.slot:
            raise DuplicateHandler(
                f"{type(sub).__name__} occupies {sub.slot}, expected COMMUNICATION"
            )
        overlap = self.handles & sub.handles
        if overlap:
            names = ", ".join(sorted(action_type.value for action_type in overlap))
            raise DuplicateHandler(f"duplicate communication handlers: {names}")
        self._subresolvers = tuple(
            sorted((*self._subresolvers, sub), key=lambda resolver: type(resolver).__name__)
        )
        self.handles = frozenset(
            OWN_TYPES.union(
                action_type for resolver in self._subresolvers for action_type in resolver.handles
            )
        )

    def _subresolver(self, action_type: ActionType) -> InstitutionResolver | None:
        return next(
            (resolver for resolver in self._subresolvers if action_type in resolver.handles),
            None,
        )

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        sub = self._subresolver(action.type)
        if sub is not None:
            return sub.check_capability(action, ctx)
        if action.type == ActionType.DIRECT_MESSAGE:
            recipient_id = str(action.params.get("recipient_id", ""))
            follows_either_way = recipient_id in self.platform.followees(
                action.actor_id
            ) or action.actor_id in self.platform.followees(recipient_id)
            office_register = ctx.repositories.get("office_register")
            if not recipient_id or not any(
                (
                    self.graph.tie(action.actor_id, recipient_id) is not None,
                    follows_either_way,
                    _office_holder(office_register, recipient_id),
                )
            ):
                return GateFailure(
                    "capability",
                    "direct message requires a tie, follow edge, or public recipient",
                )
        if action.type == ActionType.BROADCAST:
            state = ctx.state
            observed_place = getattr(getattr(ctx.observation, "place", None), "place_id", None)
            location = self.world.locations.get(action.actor_id)
            place_id = (
                action.params.get("place_id")
                or observed_place
                or (None if location is None else getattr(location, "place_id", None))
            )
            place = None if place_id is None else self.world.place(str(place_id))
            allowed = any(
                (
                    bool(getattr(state, "holds_office", False)),
                    bool(getattr(state, "declared_candidate", False)),
                    bool(getattr(state, "outlet_employee", False)),
                    place is not None and place.owner_id == action.actor_id,
                )
            )
            if not allowed:
                return GateFailure(
                    "capability",
                    "broadcast requires a public role or venue ownership",
                )
        return None

    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        sub = self._subresolver(action.type)
        if sub is not None:
            return sub.check_locality(action, ctx)
        if action.type == ActionType.SAY:
            snapshot = _speech_snapshot(ctx.observation)
            if snapshot is None or not snapshot.candidates:
                return GateFailure("locality", "SAY requires a co-located listener")
            self._speech_snapshots[action.action_id] = (ctx.tick, snapshot)
        if action.type == ActionType.BROADCAST:
            observed_place = getattr(getattr(ctx.observation, "place", None), "place_id", None)
            requested_place = action.params.get("place_id")
            if observed_place is None or (
                requested_place is not None and requested_place != observed_place
            ):
                return GateFailure(
                    "locality",
                    "broadcast venue must be the actor's committed place",
                )
        return None

    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        sub = self._subresolver(action.type)
        if sub is not None:
            return sub.check_resources(action, ctx)
        if action.type == ActionType.DIRECT_MESSAGE:
            recipient_id = str(action.params.get("recipient_id", ""))
            count = self.repo.dm_count(action.actor_id, recipient_id, self.clock.sim_day(ctx.tick))
            if count >= self.cfg.max_dms_per_tick:
                return GateFailure("resources", "direct-message rate limit reached")
        if action.type == ActionType.BROADCAST:
            observed_place = getattr(getattr(ctx.observation, "place", None), "place_id", None)
            requested_place = action.params.get("place_id")
            place_id = requested_place or observed_place
            if place_id is not None:
                place = self.world.place(str(place_id))
                fee = 0 if place.owner_id in {None, action.actor_id} else int(place.rent_cents)
                if (
                    fee > 0
                    and place.owner_id is not None
                    and not self.ledger.can_pay_broadcast(
                        action.actor_id,
                        place.owner_id,
                        fee,
                    )
                ):
                    return GateFailure(
                        "resources",
                        "insufficient liquid balance for the broadcast venue fee",
                    )
        return None

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        self._speech_snapshots = {
            action_id: item for action_id, item in self._speech_snapshots.items() if item[0] >= tick
        }
        events: list[Event] = []
        own = sorted(
            (row for row in actions if row.action.type in OWN_TYPES),
            key=lambda row: (row.action.actor_id, str(row.action.action_id)),
        )
        for row in own:
            events.extend(self._resolve_one(row, tick, ctx))
        for resolver in self._subresolvers:
            batch = sorted(
                (row for row in actions if row.action.type in resolver.handles),
                key=lambda row: (row.action.actor_id, str(row.action.action_id)),
            )
            events.extend(resolver.resolve(batch, tick, ctx))
        events.extend(self.conversations.close_idle(tick, self.world))
        return tuple(events)

    def _resolve_one(
        self, row: ValidatedAction, tick: int, ctx: ResolutionContext
    ) -> Sequence[Event]:
        action = row.action
        params = row.validated_params
        if action.type == ActionType.SAY:
            return self._say(action, params, tick, ctx)
        if action.type == ActionType.DIRECT_MESSAGE:
            return self._dm(action, params, tick, ctx)
        if action.type == ActionType.BROADCAST:
            return self._broadcast(action, params, tick, ctx)
        if action.type == ActionType.POST:
            _, events = self.platform.publish(action.actor_id, params, tick, None)  # type: ignore[arg-type]
            return events
        if action.type == ActionType.REPOST:
            _, events = self.platform.repost(action.actor_id, params, tick)  # type: ignore[arg-type]
            return events
        if action.type == ActionType.LIKE:
            event = self.platform.engage(
                action.actor_id, str(_param(params, "post_id")), "like", tick
            )
            return () if event is None else (event,)
        if action.type == ActionType.COMMENT:
            post_id = str(_param(params, "post_id"))
            parent = self.platform.repo.post(post_id)
            if parent is None:
                return ()
            post_params = {
                "text": _param(params, "text", ""),
                "claims": _param(params, "claims", ()),
                "in_reply_to": post_id,
                "topic": parent.topic,
                "stance_proposition": parent.stance_proposition,
                "stance_value": parent.stance_value,
            }
            _, events = self.platform.publish(action.actor_id, post_params, tick, None)  # type: ignore[arg-type]
            engagement = self.platform.engage(action.actor_id, post_id, "comment", tick)
            return (*events, *((engagement,) if engagement is not None else ()))
        if action.type in {ActionType.FOLLOW, ActionType.UNFOLLOW}:
            target = _param(params, "followee_id") or _param(params, "target_id")
            if target is None:
                return ()
            event = (
                self.platform.follow(action.actor_id, str(target), "feed", tick)
                if action.type == ActionType.FOLLOW
                else self.platform.unfollow(action.actor_id, str(target), "unfollow", tick)
            )
            if event is None:
                return ()
            events = [event]
            if (
                action.type == ActionType.FOLLOW
                and self.graph.tie(action.actor_id, str(target)) is None
            ):
                formed = self.graph.form(
                    action.actor_id,
                    str(target),
                    "acquaintance",
                    "platform",
                    tick,
                )
                if formed is not None:
                    events.append(formed)
            return tuple(events)
        if action.type == ActionType.BEFRIEND:
            target = str(_param(params, "target_id"))
            prior = self.repo.offer_befriend(action.actor_id, target, tick)
            event = (
                self.graph.form(action.actor_id, target, "acquaintance", "befriend", tick)
                if self.graph.tie(action.actor_id, target) is None
                else None
            )
            result = [] if event is None else [event]
            if (
                prior is not None
                and tick - prior
                <= self.cfg.befriend_window_sim_days * self.clock.profile.ticks_per_sim_day
            ):
                current = self.graph.tie(action.actor_id, target, "acquaintance")
                if current is not None:
                    result.append(
                        self.graph.transition(
                            current,
                            "friend",
                            "reciprocal_befriend",
                            tick,
                        )
                    )
            return tuple(result)
        return ()

    def _say(
        self, action: Action, params: object, tick: int, ctx: ResolutionContext
    ) -> Sequence[Event]:
        cached = self._speech_snapshots.pop(action.action_id, None)
        snapshot = None if cached is None or cached[0] != tick else cached[1]
        if snapshot is None:
            observations = ctx.repositories.get("observations")
            observation = (
                observations.get(action.actor_id) if isinstance(observations, Mapping) else None
            )
            snapshot = _speech_snapshot(observation)
        if snapshot is None:
            return ()
        place_id = snapshot.place_id
        place = self.world.place(place_id)
        addressed = tuple(_param(params, "addressed_to", ()))
        to_id = _param(params, "to_id")
        if to_id is not None:
            addressed = tuple(sorted({*addressed, str(to_id)}))
        conversation_id = _param(params, "conversation_id")
        opened: Event | None = None
        if conversation_id is None or self.conversations.get(str(conversation_id)) is None:
            conversation, opened = self.conversations.open_or_join(
                action.actor_id,
                place_id,
                addressed,
                _param(params, "topic"),
                tick,
            )
            conversation_id = conversation.conversation_id
        speech_id = det_id("sp", "society.speech", action.action_id)
        listeners = heard_by(
            action.actor_id,
            snapshot.candidates,
            place=place,
            addressed_to=addressed,
            graph=self.graph,
            speech_id=speech_id,
            tick=tick,
            rng=self.rng,
            cfg=self.cfg,
        )
        cause_seq = self.conversations.cause_seq(str(conversation_id))
        turn_index = self.conversations.record_turn(
            str(conversation_id), action.actor_id, tick, cause_seq
        )
        utterance = ctx.emit(
            NewEvent(
                SPEECH_UTTERED,
                {
                    "speaker_id": action.actor_id,
                    "place_id": place_id,
                    "text": str(_param(params, "text", "")),
                    "addressed_to": list(addressed),
                    "heard_by": [
                        {"agent_id": listener.agent_id, "attention": listener.attention}
                        for listener in listeners
                    ],
                    "topic": _param(params, "topic"),
                    "stance_proposition": _param(params, "stance_proposition"),
                    "stance_value": _param(params, "stance_value"),
                    "conversation_id": conversation_id,
                    "turn_index": turn_index,
                    "closing": bool(_param(params, "closing", False)),
                    "claims": list(_param(params, "claims", ())),
                },
                actor_id=action.actor_id,
                subject_ids=tuple(
                    sorted({action.actor_id, *(listener.agent_id for listener in listeners)})
                ),
                cause_seq=cause_seq,
            )
        )
        self.conversations.note_seq(str(conversation_id), utterance.seq)
        for listener in listeners:
            self.repo.add_utterance(
                UtteranceDelivery(
                    action.actor_id,
                    listener.agent_id,
                    tick,
                    str(_param(params, "text", "")),
                )
            )
            self.graph.stage_interaction(
                Interaction(
                    action.actor_id,
                    listener.agent_id,
                    "conversation",
                    listener.attention,
                )
            )
        events: list[Event] = []
        if opened is not None:
            events.append(opened)
        events.append(utterance)
        proposition = _param(params, "stance_proposition")
        stance_value = _param(params, "stance_value")
        if proposition is not None and stance_value is not None:
            for listener in listeners:
                belief_event = self.beliefs.apply_social(
                    listener.agent_id,
                    str(proposition),
                    float(stance_value),
                    action.actor_id,
                    tick,
                )
                if belief_event is not None:
                    events.append(belief_event)
        if bool(_param(params, "closing", False)):
            closed = self.conversations.close(str(conversation_id), tick, "closed")
            if closed is not None:
                events.append(closed)
        return tuple(events)

    def _dm(
        self, action: Action, params: object, tick: int, ctx: ResolutionContext
    ) -> Sequence[Event]:
        recipient_id = str(_param(params, "recipient_id"))
        message_id = det_id("msg", "society.message", action.action_id)
        event = ctx.emit(
            NewEvent(
                MESSAGE_SENT,
                {
                    "message_id": message_id,
                    "sender_id": action.actor_id,
                    "recipient_id": recipient_id,
                    "text": str(_param(params, "text", "")),
                    "in_reply_to": _param(params, "in_reply_to"),
                    "topic": _param(params, "topic"),
                    "stance_proposition": _param(params, "stance_proposition"),
                    "stance_value": _param(params, "stance_value"),
                    "claims": list(_param(params, "claims", ())),
                },
                actor_id=action.actor_id,
                subject_ids=(action.actor_id, recipient_id),
            )
        )
        self.repo.add_message(
            Message(
                message_id,
                action.actor_id,
                recipient_id,
                tick,
                str(_param(params, "text", "")),
                _param(params, "topic"),
                _param(params, "stance_proposition"),
                _param(params, "stance_value"),
            )
        )
        self.repo.note_dm(action.actor_id, recipient_id, self.clock.sim_day(tick))
        self.graph.stage_interaction(Interaction(action.actor_id, recipient_id, "dm"))
        return (event,)

    def mark_message_read(
        self,
        message_id: str,
        reader_id: str,
        tick: int,
        *,
        entered_memory: bool,
        ctx: ResolutionContext,
    ) -> Event | None:
        visible = next(
            (
                message
                for message in self.repo.messages_for(reader_id)
                if message.message_id == message_id
            ),
            None,
        )
        if visible is None:
            return None
        message = self.repo.mark_read(message_id, tick)
        if message is None:
            return None
        return ctx.emit(
            NewEvent(
                MESSAGE_READ,
                {
                    "message_id": message_id,
                    "reader_id": reader_id,
                    "latency_ticks": tick - message.tick,
                    "entered_memory": entered_memory,
                },
                actor_id=reader_id,
                subject_ids=(reader_id, message.sender_id),
            )
        )

    def _broadcast(
        self, action: Action, params: object, tick: int, ctx: ResolutionContext
    ) -> Sequence[Event]:
        requested_place = _param(params, "place_id")
        location = (
            None if requested_place is not None else self.world.locations.get(action.actor_id)
        )
        resolved_place = requested_place or (
            None if location is None else getattr(location, "place_id", None)
        )
        if resolved_place is None:
            return ()
        place_id = str(resolved_place)
        place = self.world.place(place_id)
        audience = tuple(
            sorted(
                agent_id
                for agent_id in self.world.occupancy(place_id)
                if agent_id != action.actor_id
            )
        )
        fee = 0 if place.owner_id in {None, action.actor_id} else int(place.rent_cents)
        txn_id: str | None = None
        if fee > 0 and place.owner_id is not None:
            txn_id = self.ledger.next_broadcast_txn_id(tick)
        event = ctx.emit(
            NewEvent(
                BROADCAST_MADE,
                {
                    "broadcaster_id": action.actor_id,
                    "place_id": place_id,
                    "text": str(_param(params, "text", "")),
                    "topic": _param(params, "topic"),
                    "audience_ids": list(audience),
                    "audience_size": len(audience),
                    "venue_fee_cents": fee,
                    "txn_id": txn_id,
                    "stance_proposition": _param(params, "stance_proposition"),
                    "stance_value": _param(params, "stance_value"),
                },
                actor_id=action.actor_id,
                subject_ids=tuple(sorted({action.actor_id, *audience})),
            )
        )
        if fee > 0 and place.owner_id is not None and txn_id is not None:
            posted = self.ledger.post_broadcast_fee(
                payer_id=action.actor_id,
                payee_id=place.owner_id,
                amount_cents=fee,
                txn_id=txn_id,
                tick=tick,
                cause=event,
            )
            if posted != txn_id:
                raise RuntimeError("broadcast ledger transaction ordinal diverged")
        for listener_id in audience:
            self.repo.add_utterance(
                UtteranceDelivery(
                    action.actor_id,
                    listener_id,
                    tick,
                    str(_param(params, "text", "")),
                )
            )
        return (event,)

    def options_for(
        self, action_type: ActionType, ctx: ValidationContext
    ) -> tuple[Mapping[str, Any], ...]:
        sub = self._subresolver(action_type)
        if sub is not None:
            return sub.options_for(action_type, ctx)
        if action_type in {ActionType.SAY, ActionType.BROADCAST}:
            location = self.world.locations.get(str(getattr(ctx.state, "agent_id", "")))
            if location is not None:
                return ({"place_id": location.place_id},)
        return ()


__all__ = [
    "OWN_TYPES",
    "AgentBrief",
    "CommsRepository",
    "CommunicationResolver",
    "Conversation",
    "ConversationTracker",
    "LedgerApi",
    "Listener",
    "MemoryCommsRepository",
    "Message",
    "NullLedger",
    "UtteranceDelivery",
    "attention",
    "heard_by",
]
