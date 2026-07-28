from dataclasses import dataclass
from uuid import UUID

import pytest

from polis.config.settings import SocietySettings
from polis.events.kinds import TIE_ENDED, TIE_TYPE_CHANGED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.graph import (
    ContactLedger,
    Interaction,
    MemoryGraphRepository,
    SocialGraph,
    Tie,
    formation_multiplier,
)


def make_graph(profile: str, repo: MemoryGraphRepository) -> SocialGraph:
    return SocialGraph(
        log=EventLog(UUID(int=2), MemoryEventSink()),
        clock=Clock(PROFILES[profile]),
        rng=RngRegistry(2),
        repo=repo,
        cfg=SocietySettings(),
    )


def test_tie_canonicalisation_and_clock_invariant_daily_decay() -> None:
    microscope_repo = MemoryGraphRepository()
    chronicle_repo = MemoryGraphRepository()
    microscope = make_graph("microscope", microscope_repo)
    chronicle = make_graph("chronicle", chronicle_repo)
    microscope.form("z", "a", "friend", "befriend", 0)
    chronicle.form("z", "a", "friend", "befriend", 0)

    microscope.apply_tick(24, ContactLedger())
    chronicle.apply_tick(1, ContactLedger(ticks_per_sim_day=1))

    microscope_tie = microscope.tie("a", "z", "friend")
    chronicle_tie = chronicle.tie("a", "z", "friend")
    assert microscope_tie is not None
    assert chronicle_tie is not None
    assert microscope_tie.a_id == "a"
    assert microscope_tie.strength == pytest.approx(chronicle_tie.strength)


def test_decay_is_incremental_instead_of_reapplying_the_full_idle_period() -> None:
    stepped_repo = MemoryGraphRepository()
    single_repo = MemoryGraphRepository()
    stepped = make_graph("microscope", stepped_repo)
    single = make_graph("microscope", single_repo)
    stepped.form("a", "b", "friend", "befriend", 0)
    single.form("a", "b", "friend", "befriend", 0)

    for tick in range(1, 25):
        stepped.apply_tick(tick, ContactLedger())
    single.apply_tick(24, ContactLedger())

    stepped_tie = stepped.tie("a", "b", "friend")
    single_tie = single.tie("a", "b", "friend")
    assert stepped_tie is not None
    assert single_tie is not None
    assert stepped_tie.strength == pytest.approx(single_tie.strength)


def test_logged_direct_message_forms_a_tie_before_applying_dynamics() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    graph.stage_interaction(Interaction("sender", "recipient", "dm"))

    events = graph.apply_tick(1, ContactLedger())
    tie = graph.tie("sender", "recipient", "acquaintance")

    assert tie is not None
    assert tie.strength > 0.08
    assert events[0].payload["context"] == "dm"


def test_network_snapshot_computes_triangle_clustering() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    graph.form("a", "b", "friend", "befriend", 0)
    graph.form("b", "c", "friend", "befriend", 0)
    graph.form("a", "c", "friend", "befriend", 0)

    snapshot = graph.snapshot(1)

    assert snapshot.payload["n_nodes"] == 3
    assert snapshot.payload["n_edges"] == 3
    assert snapshot.payload["mean_degree"] == 2
    assert snapshot.payload["clustering_global"] == 1
    assert snapshot.payload["clustering_avg_local"] == 1
    assert snapshot.payload["modularity"] is None
    assert snapshot.payload["n_communities"] is None
    assert snapshot.payload["assortativity_wealth"] is None


@dataclass
class Profile:
    traits: dict[str, float]


def test_homophily_is_exactly_disabled_at_zero() -> None:
    a = Profile({"openness": 0.8, "agreeableness": 0.6})
    b = Profile({"openness": 0.8, "agreeableness": 0.6})

    assert formation_multiplier(a, b, 0.0) == 1.0
    assert formation_multiplier(a, b, 0.5) > 1.0


def test_pair_lookup_is_symmetric_and_does_not_scan_all_ties() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    graph.form("z", "a", "friend", "befriend", 0)

    assert graph.tie("z", "a", "friend") == graph.tie("a", "z", "friend")
    assert repo.for_pair("z", "a") == repo.for_pair("a", "z")


def test_transition_emits_event_and_preserves_existing_target_tie() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    graph.form("a", "b", "acquaintance", "colocation", 0)
    graph.form("a", "b", "friend", "befriend", 1)
    acquaintance = graph.tie("a", "b", "acquaintance")
    friend = graph.tie("a", "b", "friend")
    assert acquaintance is not None
    assert friend is not None

    event = graph.transition(acquaintance, "friend", "test", 2)

    assert event.kind == TIE_ENDED
    assert event.payload["reason"] == "merged_into_friend"
    assert graph.tie("a", "b", "acquaintance") is None
    assert graph.tie("a", "b", "friend") == friend


def test_transition_without_target_emits_type_change() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    graph.form("a", "b", "acquaintance", "befriend", 0)
    acquaintance = graph.tie("a", "b", "acquaintance")
    assert acquaintance is not None

    event = graph.transition(acquaintance, "friend", "test", 1)

    assert event.kind == TIE_TYPE_CHANGED
    assert graph.tie("a", "b", "acquaintance") is None
    assert graph.tie("a", "b", "friend") is not None


def test_transition_rules_cover_conflict_recovery_and_decay() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    friend = Tie("a", "b", "friend", 0.5, -0.4, 0.5, 0, None, 0)
    repo.put(friend)

    conflict = graph._transition(friend, 1)
    rival = graph.tie("a", "b", "rival")
    assert conflict is not None
    assert conflict.kind == TIE_TYPE_CHANGED
    assert rival is not None

    recovering = Tie("c", "d", "rival", 0.5, 0.2, 0.5, 0, None, 0)
    repo.put(recovering)
    assert graph._transition(recovering, 1) is None
    recovered = graph._transition(recovering, 1 + 30 * 24)
    assert recovered is not None
    assert recovered.kind == TIE_TYPE_CHANGED
    assert graph.tie("c", "d", "acquaintance") is not None

    decayed = Tie("e", "f", "acquaintance", 0.01, 0.0, 0.5, 0, None, 0)
    repo.put(decayed)
    ended = graph._transition(decayed, 2)
    assert ended is not None
    assert ended.kind == TIE_ENDED
    assert graph.tie("e", "f", "acquaintance") is None


def test_end_all_for_closes_every_live_tie_for_agent() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    graph.form("a", "b", "friend", "befriend", 0)
    graph.form("a", "c", "acquaintance", "colocation", 0)

    events = graph.end_all_for("a", "death", 1)

    assert len(events) == 2
    assert {event.kind for event in events} == {TIE_ENDED}
    assert graph.neighbours("a") == ()


def test_strong_ties_deduplicates_counterparts_across_tie_types() -> None:
    repo = MemoryGraphRepository()
    graph = make_graph("microscope", repo)
    repo.put(Tie("a", "b", "friend", 0.8, 0.2, 0.8, 0, None, 0))
    repo.put(Tie("a", "b", "colleague", 0.7, 0.1, 0.7, 0, None, 0))

    assert graph.strong_ties("a", 0.5) == ("b",)
