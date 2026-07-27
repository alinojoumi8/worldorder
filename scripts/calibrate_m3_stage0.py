from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from polis.config.canon import canonical_bytes, sha256_hex
from polis.config.mechanisms import mechanism_manifest
from polis.config.settings import Settings, config_hash, load_settings
from polis.events.kinds import INVARIANT_VIOLATED
from polis.living_city import run_living_city
from polis.research.gates import GateResult, evaluate_v2, evaluate_v3
from scripts.validate_m3_multiseed import (
    DEFAULT_SEEDS,
    _derived_v3_series,
    _GateEventSink,
    _git_sha,
    _metric_series,
    _posthoc_closure,
    write_json,
)

PROTOCOL_ID = "m3-stage0-mechanical-v1"
REQUIRED_GATES = ("V2", "V3")
PROTOCOL_YEARS = 2


def _series_summary(
    series: dict[str, list[tuple[int, float]]],
) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for name, observations in sorted(series.items()):
        values = [value for _tick, value in observations]
        if not values:
            continue
        summary[name] = {
            "evaluations": len(values),
            "minimum": min(values),
            "mean": round(sum(values) / len(values), 6),
            "maximum": max(values),
            "first": values[0],
            "last": values[-1],
        }
    return summary


def _stage0_ineligibility_reasons(settings: Settings) -> list[str]:
    reasons: list[str] = []
    if settings.population.initial_agents < 1_000:
        reasons.append("population_below_1000")
    if settings.ventures.acceptance_fixture:
        reasons.append("development_acceptance_fixture")
    if not settings.ablations.reflex_only:
        reasons.append("not_reflex_only")
    if any(provider.kind != "stub" for provider in settings.llm.providers.values()):
        reasons.append("non_stub_provider")
    return reasons


async def run_seed(
    config: Path,
    *,
    seed: int,
    years: int,
    output: Path,
) -> dict[str, Any]:
    base = load_settings(config)
    ticks = years * base.clock.days_per_sim_year * base.clock.ticks_per_sim_day
    settings = load_settings(
        config,
        overrides={
            "run": {
                "name": f"m3-stage0-seed-{seed}",
                "seed": seed,
                "ticks": ticks,
                "checkpoint_interval": ticks,
            }
        },
    )
    started = time.perf_counter()
    result = await run_living_city(
        settings,
        sink=_GateEventSink(),
        collect_events=False,
    )
    elapsed = time.perf_counter() - started
    series = _metric_series(result)
    series.update(_derived_v3_series(result, settings))
    ticks_checked, posthoc_violating_ticks, final_checks = _posthoc_closure(result)
    runtime_violations = Counter(
        str(event.payload["invariant_id"])
        for event in result.events
        if event.kind == INVARIANT_VIOLATED
    )
    gates: tuple[GateResult, ...] = (
        evaluate_v2(
            last_tick=result.report.last_tick,
            ticks_checked=ticks_checked,
            invariant_violations=runtime_violations,
            posthoc_violating_ticks=posthoc_violating_ticks,
            final_ledger_checks=final_checks,
        ),
        evaluate_v3(series, last_tick=result.report.last_tick),
    )
    eligibility_reasons = _stage0_ineligibility_reasons(settings)
    report = {
        "protocol_id": PROTOCOL_ID,
        "seed": seed,
        "run_id": str(result.report.run_id),
        "code_git_sha": _git_sha(),
        "config": str(config),
        "base_config_hash": config_hash(base),
        "run_config_hash": config_hash(settings),
        "config_class": (
            "mechanical_calibration" if not eligibility_reasons else "engineering_diagnostic"
        ),
        "stage0_ineligibility_reasons": eligibility_reasons,
        "population": settings.population.initial_agents,
        "years": years,
        "ticks": result.report.ticks,
        "last_tick": result.report.last_tick,
        "events": result.report.events,
        "terminal_hash": result.report.chain_hash,
        "status": result.report.status,
        "halt_reason": result.report.halt_reason,
        "elapsed_seconds": round(elapsed, 3),
        "ticks_per_second": round(result.report.ticks / elapsed, 3),
        "series_summary": _series_summary(series),
        "gates": {gate.gate_id: gate.as_dict() for gate in gates},
        "gate_verdict": "pass" if all(gate.verdict == "pass" for gate in gates) else "fail",
    }
    write_json(output, report)
    return report


def _aggregate(
    reports: list[dict[str, Any]],
    *,
    config: Path,
    years: int,
) -> dict[str, Any]:
    base = load_settings(config)
    all_eligible = all(not report["stage0_ineligibility_reasons"] for report in reports)
    all_gates_pass = all(report["gate_verdict"] == "pass" for report in reports)
    base_config_hash = config_hash(base)
    code_git_shas = sorted({str(report["code_git_sha"]) for report in reports})
    seed_roster_complete = [report["seed"] for report in reports] == list(DEFAULT_SEEDS)
    duration_complete = years == PROTOCOL_YEARS
    provenance_consistent = len(code_git_shas) == 1 and all(
        (
            report["protocol_id"] == PROTOCOL_ID
            and report["base_config_hash"] == base_config_hash
            and set(report["gates"]) == set(REQUIRED_GATES)
        )
        for report in reports
    )
    mechanisms = mechanism_manifest(base)
    return {
        "status": (
            "mechanical_calibration_passed"
            if (
                all_eligible
                and all_gates_pass
                and provenance_consistent
                and seed_roster_complete
                and duration_complete
            )
            else "mechanical_calibration_failed"
        ),
        "stage0_accepted": (
            all_eligible
            and all_gates_pass
            and provenance_consistent
            and seed_roster_complete
            and duration_complete
        ),
        "config": str(config),
        "years_per_seed": years,
        "seeds": [report["seed"] for report in reports],
        "provenance": {
            "protocol_id": PROTOCOL_ID,
            "required_gates": list(REQUIRED_GATES),
            "required_seeds": list(DEFAULT_SEEDS),
            "required_years_per_seed": PROTOCOL_YEARS,
            "base_config_hash": base_config_hash,
            "code_git_shas": code_git_shas,
            "run_config_hashes": {
                str(report["seed"]): report["run_config_hash"] for report in reports
            },
            "terminal_hashes": {str(report["seed"]): report["terminal_hash"] for report in reports},
            "mechanism_manifest_hash": sha256_hex(canonical_bytes(mechanisms)),
            "mechanisms": mechanisms,
            "consistent": provenance_consistent,
            "seed_roster_complete": seed_roster_complete,
            "duration_complete": duration_complete,
        },
        "all_runs_stage0_eligible": all_eligible,
        "all_v2_v3_pass": all_gates_pass,
        "gate_matrix": {
            str(report["seed"]): {
                gate_id: gate["verdict"] for gate_id, gate in report["gates"].items()
            }
            for report in reports
        },
        "stage0_ineligibility_reasons": sorted(
            {reason for report in reports for reason in report["stage0_ineligibility_reasons"]}
        ),
        "throughput_ticks_per_second": {
            "minimum": min(report["ticks_per_second"] for report in reports),
            "mean": round(
                sum(report["ticks_per_second"] for report in reports) / len(reports),
                3,
            ),
            "maximum": max(report["ticks_per_second"] for report in reports),
        },
        "runs": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deterministic M3 mechanical calibration diagnostic"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/m3-calibration-stage0.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/validation/m3-stage0-multiseed.json"),
    )
    parser.add_argument("--years", type=int, default=PROTOCOL_YEARS)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Rebuild the aggregate from completed per-seed reports without rerunning.",
    )
    parser.add_argument(
        "--allow-gate-failures",
        action="store_true",
        help="Return success after recording diagnostic failures.",
    )
    args = parser.parse_args()
    if args.years < 1:
        raise SystemExit("stage-0 calibration requires at least one simulated year")
    run_dir = args.output.parent / "m3-stage0-runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.reuse_existing:
        reports = [
            json.loads((run_dir / f"seed-{seed}.json").read_text(encoding="utf-8"))
            for seed in args.seeds
        ]
    else:
        reports = [
            asyncio.run(
                run_seed(
                    args.config,
                    seed=seed,
                    years=args.years,
                    output=run_dir / f"seed-{seed}.json",
                )
            )
            for seed in args.seeds
        ]
    aggregate = _aggregate(reports, config=args.config, years=args.years)
    write_json(args.output, aggregate)
    print(json.dumps(aggregate, sort_keys=True))
    if not aggregate["stage0_accepted"] and not args.allow_gate_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
