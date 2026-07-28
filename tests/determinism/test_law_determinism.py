from __future__ import annotations

from polis.agents.actions import ActionType, ValidationContext, make_action
from polis.agents.actions.params.law import CommitCrimeParams
from polis.kernel.rng import RngRegistry
from polis.society.law import (
    DetectionEngine,
    LawLegalityOracle,
    MemoryCrimeRepository,
    MnpiIndex,
    ObligationIndex,
)
from tests.law_support import Memories, checker, clock, law_cfg, log, runtime, world


def _run() -> tuple[tuple[int, int, object, str], ...]:
    event_log = log(29)
    configured_clock = clock()
    configured_runtime = runtime()
    cfg = law_cfg(
        base_detect={
            **law_cfg().base_detect,
            "theft": 0.98,
        }
    )
    repo = MemoryCrimeRepository()
    memories = Memories()
    oracle = LawLegalityOracle(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        mnpi=MnpiIndex(
            memories=memories,
            cfg=cfg,
            clock=configured_clock,
            events=(),
        ),
        obligations=ObligationIndex(),
        checker=checker(event_log),
        memories=memories,
        repo=repo,
        cfg=cfg,
    )
    action = make_action(
        actor_id="ag_actor",
        tick=1,
        action_type=ActionType.COMMIT_CRIME,
        params={
            "crime_type": "theft",
            "victim_id": "ag_victim",
            "amount_cents": 100,
        },
    )
    oracle.assess(
        action,
        CommitCrimeParams.model_validate(action.params),
        ValidationContext(observation=object(), state=object(), tick=1),
    )
    detection = DetectionEngine(
        log=event_log,
        clock=configured_clock,
        rng=RngRegistry(29),
        runtime=configured_runtime,
        repo=repo,
        world=world(),
        cfg=cfg,
    )
    for tick in range(2, 181 * 24):
        detection.run_hazard(tick)
    return tuple((item.tick, item.kind, item.payload, item.hash) for item in event_log.staged())


def test_same_seed_reproduces_the_complete_law_event_sequence() -> None:
    first = _run()
    assert len(first) >= 3
    assert first == _run()
