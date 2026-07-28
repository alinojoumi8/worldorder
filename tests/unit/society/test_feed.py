from uuid import UUID

from polis.config.settings import SocietySettings
from polis.events.kinds import POST_ENGAGED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.graph import MemoryGraphRepository, SocialGraph
from polis.society.media.feed import EngagementModel, Features, FeedService
from polis.society.media.platform import MemoryPlatformRepository, Platform, Post
from polis.society.protocols import NullBeliefChannel


class PolarisingBeliefs(NullBeliefChannel):
    def value(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 0.4

    def predict_delta(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        channel: str,
    ) -> float:
        del agent_id, proposition, target, source_id, channel
        return 0.1


def fixture() -> tuple[Clock, EventLog, SocialGraph, Platform]:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=3), MemoryEventSink())
    cfg = SocietySettings()
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=RngRegistry(3),
        repo=MemoryGraphRepository(),
        cfg=cfg,
    )
    repo = MemoryPlatformRepository()
    platform = Platform(log=log, clock=clock, repo=repo, graph=graph, cfg=cfg)
    for index in range(40):
        author_id = f"author_{index:02}"
        repo.put_post(
            Post(
                post_id=f"po_{index:02}",
                author_id=author_id,
                tick=100 - index // 2,
                text=f"post {index}",
                topic="policy",
                stance_proposition="tax",
                stance_value=0.8,
                in_reply_to=None,
                repost_of=None,
                root_post_id=f"po_{index:02}",
                claims=(),
                reach=0,
            )
        )
        platform.follow("reader", author_id, "feed", 0)
    return clock, log, graph, platform


def test_all_rankers_are_swappable_and_return_one_feed_slice() -> None:
    clock, log, graph, platform = fixture()
    cfg = SocietySettings()
    outputs: dict[str, tuple[Post, ...]] = {}
    for algorithm in ("chronological", "engagement", "random", "adversarial"):
        beliefs = PolarisingBeliefs()
        service = FeedService(
            algorithm=algorithm,
            platform=platform,
            graph=graph,
            beliefs=beliefs,
            model=EngagementModel(),
            rng=RngRegistry(99),
            clock=clock,
            log=log,
            cfg=cfg,
        )
        outputs[algorithm] = service.build("reader", 100)[0]

    assert {len(posts) for posts in outputs.values()} == {15}
    chronological = outputs["chronological"]
    assert list(chronological) == sorted(chronological, key=lambda post: (-post.tick, post.post_id))
    assert outputs["random"] != chronological


def test_engagement_refit_is_deterministic_and_keeps_feature_order() -> None:
    features = Features(0.9, 0.2, 0.8, 0.5, 0.1, -0.4, 0.7, 0.4, 0.0, 0.9, 0.0)
    assert features.vector() == (
        1.0,
        0.5,
        0.8,
        0.2,
        0.9,
        0.1,
        0.7,
        0.0,
        0.4,
        0.9,
        0.0,
    )
    rows = [
        ("a", "p1", features, True),
        ("b", "p2", features, False),
    ]
    first = EngagementModel()
    second = EngagementModel()

    assert first.refit(rows, 1) == second.refit(tuple(reversed(rows)), 1)
    assert len(first.beta) == 11


def test_build_all_freezes_features_before_writing_any_same_tick_views() -> None:
    clock, log, graph, platform = fixture()
    for reader_id in ("reader_1", "reader_2"):
        for index in range(40):
            platform.follow(reader_id, f"author_{index:02}", "feed", 0)
    service = FeedService(
        algorithm="engagement",
        platform=platform,
        graph=graph,
        beliefs=NullBeliefChannel(),
        model=EngagementModel(),
        rng=RngRegistry(101),
        clock=clock,
        log=log,
        cfg=SocietySettings(),
    )

    result = service.build_all(("reader_2", "reader_1"), 100)
    impressions = service.impressions_for_refit(clock.sim_day(100))
    view_events = [
        event
        for event in log.staged()
        if event.kind == POST_ENGAGED and event.payload["type"] == "view"
    ]

    assert {agent_id: len(posts) for agent_id, posts in result.items()} == {
        "reader_1": 15,
        "reader_2": 15,
    }
    assert {row[2].pop for row in impressions} == {0.0}
    assert len(view_events) == 30
