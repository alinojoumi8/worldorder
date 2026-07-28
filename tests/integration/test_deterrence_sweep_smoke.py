from __future__ import annotations

from polis.kernel.rng import RngRegistry
from polis.society.law import Crime, DetectionEngine, MemoryCrimeRepository
from tests.law_support import clock, law_cfg, log, runtime, world


def _cell(budget_cents: int, seed: int) -> dict[str, object]:
    configured = runtime()
    configured.enact(
        "police.budget_cents",
        budget_cents,
        1,
        f"py_budget_{budget_cents}",
        1,
        enacted_tick=0,
    )
    repo = MemoryCrimeRepository()
    for index in range(20):
        crime_type = "theft" if index < 10 else "insider_trading"
        repo.add(
            Crime(
                f"cr_{index:02}",
                crime_type,
                0,
                f"ag_{index:02}",
                "ag_victim",
                100,
                None,
                "ds_00",
                f"ac_{index:02}",
                0.0,
                "derived",
            )
        )
    engine = DetectionEngine(
        log=log(seed),
        clock=clock(),
        rng=RngRegistry(seed),
        runtime=configured,
        repo=repo,
        world=world(),
        cfg=law_cfg(
            base_detect={
                **law_cfg().base_detect,
                "theft": 0.98,
                "insider_trading": 0.98,
            }
        ),
    )
    for tick in range(1, 401):
        engine.run_hazard(tick)
    rows = repo.all()
    return {
        "crime.committed_rate": len(rows) / 20,
        "crime.detected_rate": sum(item.detected for item in rows) / 20,
        "type_share": {
            crime_type: sum(item.type == crime_type for item in rows) / len(rows)
            for crime_type in ("theft", "insider_trading")
        },
    }


def test_two_budget_cells_report_committed_detected_and_type_share() -> None:
    low = [_cell(100_000, seed) for seed in (1, 2, 3)]
    high = [_cell(50_000_000, seed) for seed in (1, 2, 3)]

    assert all(row["crime.committed_rate"] == 1.0 for row in (*low, *high))
    assert sum(float(row["crime.detected_rate"]) for row in high) >= sum(
        float(row["crime.detected_rate"]) for row in low
    )
    assert all(row["type_share"] == {"theft": 0.5, "insider_trading": 0.5} for row in (*low, *high))
