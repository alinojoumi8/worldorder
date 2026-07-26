from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from scripts.calibrate_m1 import calibrate, write_json

DEFAULT_SEEDS = (2026072601, 2026072602, 2026072603)


def run_seed(config: Path, output: Path, ticks: int, seed: int) -> dict[str, Any]:
    report = asyncio.run(calibrate(config, output, ticks=ticks, seed=seed))
    write_json(output, report)
    return report


def range_summary(values: list[float]) -> dict[str, float]:
    return {
        "minimum": round(min(values), 6),
        "mean": round(statistics.fmean(values), 6),
        "maximum": round(max(values), 6),
    }


def validate(
    config: Path,
    output: Path,
    *,
    seeds: tuple[int, ...],
    ticks: int,
    workers: int,
) -> dict[str, Any]:
    run_dir = output.parent / "m1-multiseed-runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                run_seed,
                config,
                run_dir / f"seed-{seed}.json",
                ticks,
                seed,
            )
            for seed in seeds
        ]
        reports = [future.result() for future in futures]
    reports.sort(key=lambda report: int(report["seed"]))

    throughput = [float(report["ticks_per_second"]) for report in reports]
    late_ratio = [float(report["throughput_profile"]["late_to_early_ratio"]) for report in reports]
    wellbeing_final = [float(report["wellbeing"]["final"]) for report in reports]
    wellbeing_slope = [
        float(report["wellbeing"]["last_200_slope_per_1000_ticks"]) for report in reports
    ]
    memories = [float(report["memory"]["per_agent_mean"]) for report in reports]
    entropy = [float(report["v4_action_entropy_normalized"]["mean"]) for report in reports]
    gates = {
        "all_seed_acceptance_gates_pass": all(report["status"] == "passed" for report in reports),
        "all_runs_complete_without_halt": all(report["halt_reason"] is None for report in reports),
        "all_populations_remain_1000": all(report["population"] == 1_000 for report in reports),
        "all_memory_counts_below_cap": all(
            report["memory"]["per_agent_maximum"] < report["memory"]["configured_maximum"]
            for report in reports
        ),
    }
    aggregate = {
        "status": "passed" if all(gates.values()) else "failed",
        "config": str(config),
        "ticks_per_seed": ticks,
        "seeds": list(seeds),
        "workers": workers,
        "throughput_ticks_per_second": range_summary(throughput),
        "late_to_early_throughput_ratio": range_summary(late_ratio),
        "final_wellbeing": range_summary(wellbeing_final),
        "wellbeing_last_200_slope_per_1000_ticks": range_summary(wellbeing_slope),
        "memories_per_agent": range_summary(memories),
        "normalized_action_entropy": range_summary(entropy),
        "gates": gates,
        "runs": reports,
    }
    write_json(output, aggregate)
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reproducible multi-seed M1 validation")
    parser.add_argument("--config", type=Path, default=Path("configs/calibration-m1.yaml"))
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/validation/m1-multiseed.json")
    )
    parser.add_argument("--ticks", type=int, default=2_000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    args = parser.parse_args()
    report = validate(
        args.config,
        args.output,
        seeds=tuple(args.seeds),
        ticks=args.ticks,
        workers=args.workers,
    )
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
