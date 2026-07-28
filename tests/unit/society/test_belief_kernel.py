from pathlib import Path
from uuid import UUID

import pytest

from polis.config.settings import BeliefSettings, SocietySettings, load_settings
from polis.events.kinds import BELIEF_DRIFT_APPLIED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.beliefs import (
    Belief,
    BeliefEngine,
    BeliefUpdate,
    MemoryBeliefRepository,
    update_kernel,
)
from polis.society.graph import MemoryGraphRepository, SocialGraph, Tie


def engine(
    *,
    belief_cfg: BeliefSettings | None = None,
) -> tuple[BeliefEngine, MemoryBeliefRepository, EventLog]:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=17), MemoryEventSink())
    graph_repo = MemoryGraphRepository()
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=RngRegistry(17),
        repo=graph_repo,
        cfg=SocietySettings(),
    )
    graph_repo.put(Tie("ag_a", "ag_b", "friend", 0.8, 0.0, 0.2, 0, None, 0))
    repo = MemoryBeliefRepository(
        entities={
            "agent": frozenset({"ag_a", "ag_b"}),
            "outlet": frozenset({"ol_one"}),
        }
    )
    return (
        BeliefEngine(
            log=log,
            clock=clock,
            rng=RngRegistry(17),
            repo=repo,
            graph=graph,
            cfg=belief_cfg or BeliefSettings(),
        ),
        repo,
        log,
    )


def test_predict_delta_matches_apply_and_entrenchment_boundaries() -> None:
    beliefs, repo, _ = engine()
    repo.put(
        Belief(
            "ag_a",
            "policy.tax.progressivity",
            -0.8,
            0.8,
            "inherited",
            None,
            0,
        )
    )
    predicted = beliefs.predict_delta("ag_a", "policy.tax.progressivity", 0.8, "ag_b", "social")
    event = beliefs.apply_social("ag_a", "policy.tax.progressivity", 0.8, "ag_b", 1)
    assert event is not None
    assert event.kind == BELIEF_DRIFT_APPLIED
    assert beliefs.value("ag_a", "policy.tax.progressivity") + 0.8 == pytest.approx(predicted)
    _, _, entrenched = update_kernel(-0.8, 0.8, 0.8, 0.2, 0.1, BeliefSettings())
    assert entrenched
    _, _, boundary = update_kernel(-0.6, 0.6, 0.0, 0.2, 0.1, BeliefSettings())
    assert not boundary


def test_social_ablation_and_experience_policy_guard() -> None:
    beliefs, _, log = engine(belief_cfg=BeliefSettings(social_influence_off=True))
    assert beliefs.apply_social("ag_a", "policy.tax.progressivity", 1.0, "ag_b", 1) is None
    assert not [event for event in log.staged() if event.kind == BELIEF_DRIFT_APPLIED]
    with pytest.raises(AssertionError):
        beliefs.apply_experience(
            "ag_a",
            999,
            {"proposition": "policy.tax.progressivity", "target": 1.0},
            1,
        )


def test_top_level_belief_ablations_are_wired_into_belief_settings() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "ablations": {
                "social_influence_off": True,
                "backfire_off": True,
            }
        },
    )
    assert settings.beliefs.social_influence_off
    assert settings.beliefs.backfire_off


def test_self_serving_outlet_trust_update_is_dampened() -> None:
    beliefs, _, _ = engine()
    applied = beliefs.apply_llm_belief_updates(
        "ag_a",
        1,
        (
            BeliefUpdate(
                "trust.outlet.ol_one",
                1.0,
                0.8,
                "article:ol_one:ar_one",
            ),
        ),
        None,
    )
    assert applied == 1
    assert beliefs.value("ag_a", "trust.outlet.ol_one") == pytest.approx(0.675)
