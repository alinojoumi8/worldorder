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


def summarize(
    config: Path,
    *,
    seeds: tuple[int, ...],
    ticks: int,
    workers: int,
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    reports.sort(key=lambda report: int(report["seed"]))
    throughput = [float(report["ticks_per_second"]) for report in reports]
    late_ratio = [float(report["throughput_profile"]["late_to_early_ratio"]) for report in reports]
    wellbeing_final = [float(report["wellbeing"]["final"]) for report in reports]
    wellbeing_slope = [
        float(report["wellbeing"]["last_200_slope_per_1000_ticks"]) for report in reports
    ]
    memories = [float(report["memory"]["per_agent_mean"]) for report in reports]
    memory_write_rates = [float(report["memory"]["writes_per_agent_tick"]) for report in reports]
    memory_cap_ticks = [float(report["memory"]["projected_mean_cap_tick"]) for report in reports]
    entropy = [float(report["v4_action_entropy_normalized"]["mean"]) for report in reports]
    need_names = tuple(reports[0]["wellbeing"]["final_need_means"])
    final_need_means = {
        name: range_summary(
            [float(report["wellbeing"]["final_need_means"][name]) for report in reports]
        )
        for name in need_names
    }
    gates = {
        "all_seed_acceptance_gates_pass": all(report["status"] == "passed" for report in reports),
        "all_runs_complete_without_halt": all(report["halt_reason"] is None for report in reports),
        "all_populations_remain_1000": all(report["population"] == 1_000 for report in reports),
        "all_memory_counts_below_cap": all(
            report["memory"]["per_agent_maximum"] < report["memory"]["configured_maximum"]
            for report in reports
        ),
        "late_throughput_retains_at_least_90_percent": min(late_ratio) >= 0.90,
        "memory_writes_below_0_01_per_agent_tick": max(memory_write_rates) <= 0.01,
        "wellbeing_tail_decline_at_most_5_per_1000_ticks": min(wellbeing_slope) >= -5.0,
        "cross_seed_final_wellbeing_spread_at_most_2": (max(wellbeing_final) - min(wellbeing_final))
        <= 2.0,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "config": str(config),
        "ticks_per_seed": ticks,
        "seeds": list(seeds),
        "workers": workers,
        "throughput_note": (
            "Concurrent-process measurements are conservative and include worker contention."
            if workers > 1
            else "Single-process measurement."
        ),
        "throughput_ticks_per_second": range_summary(throughput),
        "late_to_early_throughput_ratio": range_summary(late_ratio),
        "final_wellbeing": range_summary(wellbeing_final),
        "final_need_means": final_need_means,
        "wellbeing_last_200_slope_per_1000_ticks": range_summary(wellbeing_slope),
        "memories_per_agent": range_summary(memories),
        "memory_writes_per_agent_tick": range_summary(memory_write_rates),
        "projected_mean_memory_cap_tick": range_summary(memory_cap_ticks),
        "normalized_action_entropy": range_summary(entropy),
        "gates": gates,
        "runs": reports,
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
    aggregate = summarize(
        config,
        seeds=seeds,
        ticks=ticks,
        workers=workers,
        reports=reports,
    )
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
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Rebuild the aggregate from completed per-seed reports without rerunning.",
    )
    parser.add_argument(
        "--promote-first-seed",
        type=Path,
        help="Also write the first seed report to this acceptance-artifact path.",
    )
    args = parser.parse_args()
    seeds = tuple(args.seeds)
    if args.reuse_existing:
        run_dir = args.output.parent / "m1-multiseed-runs"
        reports = [
            json.loads((run_dir / f"seed-{seed}.json").read_text(encoding="utf-8"))
            for seed in seeds
        ]
        report = summarize(
            args.config,
            seeds=seeds,
            ticks=args.ticks,
            workers=args.workers,
            reports=reports,
        )
        write_json(args.output, report)
    else:
        report = validate(
            args.config,
            args.output,
            seeds=seeds,
            ticks=args.ticks,
            workers=args.workers,
        )
    if args.promote_first_seed is not None:
        write_json(args.promote_first_seed, report["runs"][0])
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
