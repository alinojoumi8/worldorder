from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from polis.config.settings import Settings, load_settings
from polis.events.kinds import (
    BANKRUPTCY_FILED,
    FIRM_DISSOLVED,
    FIRM_FOUNDED,
    FIRM_STATUS_CHANGED,
    INTEGRATION_COMPLETED,
    INVARIANT_VIOLATED,
    SESSION_CLOSED,
)
from polis.events.log import MemoryEventSink
from polis.events.types import Event
from polis.living_city import LivingCityResult, run_living_city
from polis.research.gates import GateResult, Observation, evaluate_v1, evaluate_v2, evaluate_v3
from scripts.calibrate_m1 import write_json

DEFAULT_SEEDS = (2026072701, 2026072702, 2026072703, 2026072704, 2026072705)
_GATE_EVENT_KINDS = frozenset(
    {
        BANKRUPTCY_FILED,
        FIRM_DISSOLVED,
        FIRM_FOUNDED,
        FIRM_STATUS_CHANGED,
        INTEGRATION_COMPLETED,
        INVARIANT_VIOLATED,
        SESSION_CLOSED,
    }
)


class _GateEventSink(MemoryEventSink):
    """Retain only events required by V2/V3 while the log still hashes every event."""

    async def append(self, events: Sequence[Event]) -> None:
        self.events.extend(event for event in events if event.kind in _GATE_EVENT_KINDS)


def _completed_duration(
    *,
    status: str,
    ticks: int,
    last_tick: int,
    expected_ticks: int,
) -> bool:
    return status == "completed" and ticks == expected_ticks and last_tick == expected_ticks


def _metric_series(result: LivingCityResult) -> dict[str, list[Observation]]:
    metrics = {point.metric for point in result.metrics.points}
    return {
        metric: [(point.tick, float(point.value)) for point in result.metrics.series(metric)]
        for metric in sorted(metrics)
    }


def _derived_v3_series(
    result: LivingCityResult,
    settings: Settings,
) -> dict[str, list[Observation]]:
    economy = result.economy
    if economy is None:
        raise ValueError("M3 calibration requires the economy")
    ticks_per_day = settings.clock.ticks_per_sim_day
    week = 7 * ticks_per_day
    transaction_window = 30 * ticks_per_day
    adults = tuple(
        sorted(
            agent.agent_id for agent in result.population if agent.alive and agent.age_years >= 18
        )
    )
    adult_set = frozenset(adults)
    owner_by_account = {
        account.account_id: account.owner_id for account in economy.ledger.accounts()
    }
    entries_by_tick: dict[int, list[Any]] = defaultdict(list)
    for entry in economy.ledger.entries():
        entries_by_tick[entry.tick].append(entry)
    events_by_tick: dict[int, list[Any]] = defaultdict(list)
    for event in result.events:
        events_by_tick[event.tick].append(event)
    acquisition_targets = {
        deal_id: deal.target_id for deal_id, deal in economy.ventures.acquisitions.items()
    }

    wealth = dict.fromkeys(adults, 0)
    last_transaction = dict.fromkeys(adults, -transaction_window)
    active_firms: set[str] = set()
    zero_trade_streak = 0
    series: dict[str, list[Observation]] = {
        "wealth_share.top1": [],
        "active_firms": [],
        "agents.zero_transactions_30d_share": [],
        "exchange.zero_trade_streak": [],
    }
    for tick in range(result.report.last_tick + 1):
        transacting: set[str] = set()
        for entry in entries_by_tick.get(tick, ()):
            owner_id = owner_by_account[entry.account_id]
            if owner_id in adult_set:
                wealth[owner_id] += entry.direction * entry.amount_cents
                if tick > 0:
                    transacting.add(owner_id)
        for owner_id in transacting:
            last_transaction[owner_id] = tick

        for event in events_by_tick.get(tick, ()):
            payload = event.payload
            if event.kind == FIRM_FOUNDED:
                active_firms.add(str(payload["firm_id"]))
            elif event.kind == FIRM_DISSOLVED:
                active_firms.discard(str(payload["firm_id"]))
            elif event.kind == FIRM_STATUS_CHANGED:
                firm_id = str(payload["firm_id"])
                if payload["to"] == "active":
                    active_firms.add(firm_id)
                else:
                    active_firms.discard(firm_id)
            elif event.kind == BANKRUPTCY_FILED and payload["entity_type"] == "firm":
                active_firms.discard(str(payload["entity_id"]))
            elif event.kind == INTEGRATION_COMPLETED:
                target_id = acquisition_targets.get(str(payload["deal_id"]))
                if target_id is not None:
                    active_firms.discard(target_id)
            elif event.kind == SESSION_CLOSED:
                zero_trade_streak = zero_trade_streak + 1 if int(payload["trades_n"]) == 0 else 0
                series["exchange.zero_trade_streak"].append((tick, float(zero_trade_streak)))

        if tick == 0 or tick % ticks_per_day != 0:
            continue
        ordered_wealth = sorted(wealth.values())
        total_wealth = sum(ordered_wealth)
        top_one_n = max(1, (len(ordered_wealth) + 99) // 100)
        top_one_share = (
            10_000 * sum(ordered_wealth[-top_one_n:]) // total_wealth
            if ordered_wealth and total_wealth > 0
            else math.nan
        )
        series["wealth_share.top1"].append((tick, float(top_one_share)))
        series["active_firms"].append((tick, float(len(active_firms))))
        if tick % week == 0:
            window_start = tick - transaction_window + 1
            inactive = sum(last_transaction[agent_id] < window_start for agent_id in adults)
            inactive_share = 10_000 * inactive // max(1, len(adults))
            series["agents.zero_transactions_30d_share"].append((tick, float(inactive_share)))
    return series


def _posthoc_closure(
    result: LivingCityResult,
) -> tuple[int, list[int], dict[str, int]]:
    economy = result.economy
    if economy is None:
        raise ValueError("M3 calibration requires the economy")
    ledger = economy.ledger
    accounts = {account.account_id: account for account in ledger.accounts()}
    entries_by_tick: dict[int, list[Any]] = defaultdict(list)
    for entry in ledger.entries():
        entries_by_tick[entry.tick].append(entry)
    balances = dict.fromkeys(accounts, 0)
    deposit_banks = tuple(
        sorted(
            account.owner_id
            for account in accounts.values()
            if account.code == "dpl" and account.owner_id != "bk_cb"
        )
    )
    violating_ticks: list[int] = []
    for tick in range(result.report.last_tick + 1):
        for entry in entries_by_tick.get(tick, ()):
            balances[entry.account_id] += entry.direction * entry.amount_cents
        global_imbalance = sum(balances.values())
        base_money_imbalance = sum(
            balances[account.account_id]
            for account in accounts.values()
            if account.code == "cash"
            or (account.code == "res" and account.owner_id != "bk_cb")
            or (
                account.code == "dep"
                and account.owner_id == "gv_treasury"
                and account.bank_id == "bk_cb"
            )
            or (account.code == "iss" and account.owner_id == "bk_cb")
        )
        deposit_imbalance = sum(
            abs(
                sum(
                    balances[account.account_id]
                    for account in accounts.values()
                    if account.code in {"dep", "esc"} and account.bank_id == bank_id
                )
                + sum(
                    balances[account.account_id]
                    for account in accounts.values()
                    if account.code == "dpl" and account.owner_id == bank_id
                )
            )
            for bank_id in deposit_banks
        )
        if global_imbalance or base_money_imbalance or deposit_imbalance:
            violating_ticks.append(tick)
    materialisation = sum(
        abs(balances[account_id] - account.balance_cents)
        for account_id, account in accounts.items()
    )
    final_checks = {
        "global": ledger.global_balance_cents(),
        "materialisation": max(materialisation, ledger.materialisation_imbalance_cents()),
        "base_money": ledger.base_money_imbalance_cents(),
        "deposits": sum(abs(value) for value in ledger.deposit_imbalances().values()),
    }
    return result.report.last_tick + 1, violating_ticks, final_checks


def _eligibility_reasons(settings: Settings) -> list[str]:
    reasons: list[str] = []
    if settings.population.initial_agents < 1000:
        reasons.append("population_below_1000")
    if settings.ventures.acceptance_fixture:
        reasons.append("development_acceptance_fixture")
    if settings.ablations.reflex_only:
        reasons.append("reflex_only_ablation")
    if all(provider.kind == "stub" for provider in settings.llm.providers.values()):
        reasons.append("stub_only_provider")
    return reasons


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
    ).strip()


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
                "name": f"m3-stage3-seed-{seed}",
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
    duration_complete = _completed_duration(
        status=result.report.status,
        ticks=result.report.ticks,
        last_tick=result.report.last_tick,
        expected_ticks=ticks,
    )
    ticks_per_year = settings.clock.days_per_sim_year * settings.clock.ticks_per_sim_day
    gates: tuple[GateResult, ...] = (
        evaluate_v1(
            series,
            ticks_per_year=ticks_per_year,
            last_tick=result.report.last_tick,
            years=years,
        ),
        evaluate_v2(
            last_tick=result.report.last_tick,
            ticks_checked=ticks_checked,
            invariant_violations=runtime_violations,
            posthoc_violating_ticks=posthoc_violating_ticks,
            final_ledger_checks=final_checks,
        ),
        evaluate_v3(series, last_tick=result.report.last_tick),
    )
    eligibility_reasons = _eligibility_reasons(settings)
    report = {
        "seed": seed,
        "run_id": str(result.report.run_id),
        "code_git_sha": _git_sha(),
        "config": str(config),
        "config_class": (
            "research_eligible" if not eligibility_reasons else "engineering_diagnostic"
        ),
        "research_ineligibility_reasons": eligibility_reasons,
        "population": settings.population.initial_agents,
        "years": years,
        "ticks": result.report.ticks,
        "expected_ticks": ticks,
        "duration_complete": duration_complete,
        "last_tick": result.report.last_tick,
        "events": result.report.events,
        "terminal_hash": result.report.chain_hash,
        "status": result.report.status,
        "halt_reason": result.report.halt_reason,
        "invariant_violations": [
            {"tick": event.tick, **dict(event.payload)}
            for event in result.events
            if event.kind == INVARIANT_VIOLATED
        ],
        "elapsed_seconds": round(elapsed, 3),
        "ticks_per_second": round(result.report.ticks / elapsed, 3),
        "gates": {gate.gate_id: gate.as_dict() for gate in gates},
        "gate_verdict": (
            "pass"
            if duration_complete and all(gate.verdict == "pass" for gate in gates)
            else "fail"
        ),
    }
    write_json(output, report)
    return report


def _aggregate(
    reports: Sequence[Mapping[str, Any]],
    *,
    config: Path,
    years: int,
) -> dict[str, Any]:
    ordered = sorted(reports, key=lambda report: int(report["seed"]))
    gate_matrix = {
        str(report["seed"]): {
            gate_id: gate["verdict"] for gate_id, gate in cast_mapping(report["gates"]).items()
        }
        for report in ordered
    }
    all_gate_pass = all(report["gate_verdict"] == "pass" for report in ordered)
    all_research_eligible = all(report["config_class"] == "research_eligible" for report in ordered)
    throughput = [float(report["ticks_per_second"]) for report in ordered]
    return {
        "status": (
            "research_passed"
            if all_gate_pass and all_research_eligible
            else "engineering_diagnostic_passed"
            if all_gate_pass
            else "engineering_diagnostic_failed"
        ),
        "research_accepted": all_gate_pass and all_research_eligible,
        "config": str(config),
        "years_per_seed": years,
        "seeds": [int(report["seed"]) for report in ordered],
        "gate_matrix": gate_matrix,
        "all_v1_v2_v3_pass": all_gate_pass,
        "all_runs_research_eligible": all_research_eligible,
        "research_ineligibility_reasons": sorted(
            {
                str(reason)
                for report in ordered
                for reason in report["research_ineligibility_reasons"]
            }
        ),
        "throughput_ticks_per_second": {
            "minimum": round(min(throughput), 3),
            "mean": round(statistics.fmean(throughput), 3),
            "maximum": round(max(throughput), 3),
        },
        "runs": list(ordered),
    }


def cast_mapping(value: object) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("gate report must be a mapping")
    return value  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the five-seed, five-year M3 V1-V3 gate")
    parser.add_argument("--config", type=Path, default=Path("configs/m3-smoke.yaml"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/validation/m3-stage3-multiseed.json"),
    )
    parser.add_argument("--years", type=int, default=5)
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
    if args.years != 5:
        raise SystemExit("V1 requires exactly five simulated years")
    run_dir = args.output.parent / "m3-stage3-runs"
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
    if not aggregate["research_accepted"] and not args.allow_gate_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
