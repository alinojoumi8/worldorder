from uuid import UUID

from polis.agents.actions.params.media import PostParams
from polis.config.settings import SocietySettings
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.graph import (
    ContactLedger,
    Interaction,
    MemoryGraphRepository,
    SocialGraph,
)
from polis.society.media.feed import EngagementModel, FeedService
from polis.society.media.platform import MemoryPlatformRepository, Platform
from polis.society.protocols import NullBeliefChannel


def run_once() -> tuple[tuple[int, object, str], ...]:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=16), MemoryEventSink())
    cfg = SocietySettings()
    rng = RngRegistry(16)
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=rng,
        repo=MemoryGraphRepository(),
        cfg=cfg,
    )
    platform = Platform(
        log=log,
        clock=clock,
        repo=MemoryPlatformRepository(),
        graph=graph,
        cfg=cfg,
    )
    for index in range(20):
        author_id = f"author_{index:02}"
        platform.follow("reader", author_id, "feed", 0)
        platform.publish(
            author_id,
            PostParams(
                text=f"post-{index}",
                topic="policy",
                stance_proposition="tax",
                stance_value=(index - 10) / 10,
            ),
            0,
            None,
        )
    graph.stage_interaction(Interaction("reader", "author_00", "dm"))
    graph.apply_tick(1, ContactLedger())
    FeedService(
        algorithm="engagement",
        platform=platform,
        graph=graph,
        beliefs=NullBeliefChannel(),
        model=EngagementModel(),
        rng=rng,
        clock=clock,
        log=log,
        cfg=cfg,
    ).build_all(("reader",), 1)
    return tuple((event.kind, event.payload, event.hash) for event in log.staged())


def test_society_event_sequence_and_feed_are_byte_deterministic() -> None:
    assert run_once() == run_once()
