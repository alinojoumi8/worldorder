from uuid import UUID

import pytest

from polis.config.settings import SocietySettings
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.graph import MemoryGraphRepository, SocialGraph
from polis.society.media.platform import MemoryPlatformRepository, Platform, Post


def test_structural_virality_uses_mean_pairwise_tree_distance() -> None:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=4), MemoryEventSink())
    cfg = SocietySettings()
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=RngRegistry(4),
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
    posts = (
        Post("root", "a", 0, "", None, None, None, None, None, "root", (), 0),
        Post("child", "b", 1, "", None, None, None, None, "root", "root", (), 0),
        Post(
            "grandchild",
            "c",
            2,
            "",
            None,
            None,
            None,
            None,
            "child",
            "root",
            (),
            0,
        ),
    )
    for post in posts:
        platform.cascades.note(post, post.tick)

    assert platform.cascades.structural_virality("root") == pytest.approx(4 / 3)
