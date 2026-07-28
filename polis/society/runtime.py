from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from polis.agents.actions import (
    InstitutionResolver,
    ResolutionContext,
    ResolverRegistry,
    ValidatedAction,
)
from polis.config.settings import SocietySettings
from polis.events.log import EventLog
from polis.events.types import Event
from polis.kernel.clock import Clock
from polis.kernel.rng import RngRegistry
from polis.society.comms import (
    CommunicationResolver,
    ConversationTracker,
    LedgerApi,
    MemoryCommsRepository,
    Message,
    UtteranceDelivery,
)
from polis.society.graph import ContactLedger, MemoryGraphRepository, SocialGraph
from polis.society.media.feed import EngagementModel, FeedService, PostBrief
from polis.society.media.platform import MemoryPlatformRepository, Platform
from polis.society.protocols import BeliefChannel
from polis.world.api import World


@dataclass(frozen=True, slots=True)
class SocietyPerception:
    inbox: Mapping[str, tuple[Message, ...]]
    feed: Mapping[str, tuple[PostBrief, ...]]
    utterances: Mapping[str, tuple[UtteranceDelivery, ...]]


class SocietyRuntime:
    """C16 phase composition, isolated from the legacy M1-M3 engine."""

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        beliefs: BeliefChannel,
        ledger: LedgerApi,
        cfg: SocietySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.cfg = cfg
        self.comms_repo = MemoryCommsRepository()
        self.graph = SocialGraph(
            log=log,
            clock=clock,
            rng=rng,
            repo=MemoryGraphRepository(),
            cfg=cfg,
        )
        self.platform = Platform(
            log=log,
            clock=clock,
            repo=MemoryPlatformRepository(),
            graph=self.graph,
            cfg=cfg,
        )
        self.conversations = ConversationTracker(
            log=log,
            clock=clock,
            idle_ticks=cfg.conversation_idle_ticks,
        )
        self.model = EngagementModel(
            eta=cfg.feed.engagement.eta,
            passes=cfg.feed.engagement.passes,
            n0=cfg.feed.engagement.n0,
            beta_prior=cfg.feed.engagement.beta_prior,
        )
        self.feed = FeedService(
            algorithm=cfg.feed_algorithm,
            platform=self.platform,
            graph=self.graph,
            beliefs=beliefs,
            model=self.model,
            rng=rng,
            clock=clock,
            log=log,
            cfg=cfg,
        )
        self.communication = CommunicationResolver(
            log=log,
            clock=clock,
            rng=rng,
            world=world,
            graph=self.graph,
            platform=self.platform,
            conversations=self.conversations,
            beliefs=beliefs,
            ledger=ledger,
            repo=self.comms_repo,
            cfg=cfg,
        )
        self.registry = ResolverRegistry()
        self.registry.register(cast(InstitutionResolver, self.communication))
        self.contacts = ContactLedger(ticks_per_sim_day=clock.profile.ticks_per_sim_day)

    def phase1(
        self,
        agent_ids: Sequence[str],
        tick: int,
        ctx: ResolutionContext,
        *,
        feed_off: bool = False,
    ) -> SocietyPerception:
        ordered = tuple(sorted(set(agent_ids)))
        feeds = {} if feed_off else self.feed.build_all(ordered, tick)
        inbox: dict[str, tuple[Message, ...]] = {}
        utterances: dict[str, tuple[UtteranceDelivery, ...]] = {}
        for agent_id in ordered:
            unread = tuple(
                message
                for message in self.comms_repo.messages_for(agent_id)
                if message.tick < tick and message.read_tick is None
            )[:10]
            inbox[agent_id] = unread
            for message in unread:
                self.communication.mark_message_read(
                    message.message_id,
                    agent_id,
                    tick,
                    entered_memory=False,
                    ctx=ctx,
                )
            utterances[agent_id] = self.comms_repo.utterances_for(agent_id, tick)
        return SocietyPerception(inbox, feeds, utterances)

    def phase5(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        events = list(self.communication.resolve(actions, tick, ctx))
        for place in sorted(self.world.places, key=lambda item: item.place_id):
            self.contacts.record(
                place.place_id,
                self.world.occupancy(place.place_id),
                tick,
            )
        events.extend(self.graph.apply_tick(tick, self.contacts))
        return tuple(events)

    def phase7(self, tick: int) -> Sequence[Event]:
        events = list(self.platform.cascades.close_due(tick))
        if self.clock.starts_new("day", tick) and tick > 0:
            previous_day = self.clock.sim_day(tick) - 1
            self.model.refit(self.feed.impressions_for_refit(previous_day), tick)
        if self.clock.starts_new("week", tick):
            events.append(self.graph.snapshot(tick))
        return tuple(events)


__all__ = ["SocietyPerception", "SocietyRuntime"]
