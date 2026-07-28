from __future__ import annotations

from polis.kernel.rng import RngRegistry
from polis.society.law import Crime, DetectionEngine, MemoryCrimeRepository
from tests.law_support import clock, law_cfg, log, runtime, world


def _crime(crime_id: str, *, crime_type: str = "theft") -> Crime:
    return Crime(
        crime_id=crime_id,
        type=crime_type,  # type: ignore[arg-type]
        tick=0,
        perpetrator_id="ag_actor",
        victim_id="ag_victim",
        amount_cents=100,
        place_id=None,
        district_id="di_000",
        source_action_id=f"ac_{crime_id}",
        concealment=0.1,
        path="explicit",
    )


def test_detection_probability_rises_with_police_budget_and_awareness() -> None:
    configured = runtime()
    configured.enact(
        "police.budget_cents",
        100_000,
        10,
        "py_low",
        1,
        enacted_tick=9,
    )
    configured.enact(
        "police.budget_cents",
        5_000_000,
        20,
        "py_high",
        2,
        enacted_tick=19,
    )
    engine = DetectionEngine(
        log=log(),
        clock=clock(),
        rng=RngRegistry(19),
        runtime=configured,
        repo=MemoryCrimeRepository(),
        world=world(),
        cfg=law_cfg(),
    )

    assert engine.p_detect(_crime("cr_low"), 10) < engine.p_detect(_crime("cr_high"), 20)
    assert engine.p_detect(_crime("cr_theft", crime_type="theft"), 20) > engine.p_detect(
        _crime("cr_inside", crime_type="insider_trading"), 20
    )


def test_daily_hazard_is_deterministic_and_remains_live_after_commission() -> None:
    def run(seed: int) -> tuple[tuple[int, int], ...]:
        repo = MemoryCrimeRepository()
        repo.add(_crime("cr_repeat"))
        engine = DetectionEngine(
            log=log(seed),
            clock=clock(),
            rng=RngRegistry(seed),
            runtime=runtime(),
            repo=repo,
            world=world(),
            cfg=law_cfg(
                detection_window_sim_days=180,
                base_detect={
                    **law_cfg().base_detect,
                    "theft": 0.98,
                },
            ),
        )
        found: list[tuple[int, int]] = []
        for tick in range(1, 181 * 24):
            found.extend((event.tick, event.kind) for event in engine.run_hazard(tick))
        return tuple(found)

    first = run(29)
    assert first
    assert first[0][0] > 0
    assert first == run(29)
