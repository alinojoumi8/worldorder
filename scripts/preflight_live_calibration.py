from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from polis.config.settings import load_settings
from polis.living_city import LivingCityResult, run_living_city
from polis.llm.providers.base import ProviderRateLimited, ProviderTransient
from polis.llm.quota import RUN_QUOTA_WINDOW_SECONDS, SlidingWindowQuota
from polis.simulation import run_id_for

LiveRunner = Callable[..., Awaitable[LivingCityResult]]


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def call_rows(result: LivingCityResult) -> list[dict[str, object]]:
    return [trace.response for trace in result.traces.values() if trace.response is not None]


def action_counts(result: LivingCityResult) -> Counter[str]:
    return Counter(
        str(trace.action.get("type", "not_recorded"))
        for trace in result.traces.values()
        if trace.response is not None and trace.action is not None
    )


async def run_resumable(
    settings,
    *,
    attempts: int,
    retry_delay_seconds: float,
    concurrency_overrides: dict[str, int],
    runner: LiveRunner = run_living_city,
) -> LivingCityResult:
    for attempt in range(1, attempts + 1):
        try:
            return await runner(
                settings,
                collect_events=False,
                lane_concurrency_overrides=concurrency_overrides,
            )
        except ProviderRateLimited as exc:
            if attempt >= attempts or exc.retry_after_s > 60:
                raise
            delay = max(retry_delay_seconds, exc.retry_after_s)
            print(
                f"live calibration rate limited; cached resume "
                f"{attempt + 1}/{attempts} in {delay:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)
        except ProviderTransient:
            if attempt >= attempts:
                raise
            print(
                f"live calibration transient failure; cached resume "
                f"{attempt + 1}/{attempts} in {retry_delay_seconds:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(retry_delay_seconds)
    raise RuntimeError("live calibration resume loop exhausted")


async def preflight(
    config: Path,
    *,
    ticks: int,
    max_calls: int,
    concurrency: int,
    cache_path: str,
    resume_attempts: int = 20,
    resume_delay_seconds: float = 15,
) -> dict[str, Any]:
    base_settings = load_settings(config)
    run_name = "live-minimax-m3-preflight-1k" if ticks == 1 else "live-minimax-m3-pilot-1k"
    cognition_calls = base_settings.llm.budget.lines["cognition"].calls_per_tick
    cognition_tokens = base_settings.llm.budget.lines["cognition"].tokens_per_tick
    if ticks == 1 and max_calls > cognition_calls:
        cognition_calls = max_calls
        cognition_tokens = max(
            cognition_tokens,
            max_calls * base_settings.llm.est_tokens_per_call,
        )
    settings = load_settings(
        config,
        overrides={
            "run": {
                "name": run_name,
                "ticks": ticks,
                "checkpoint_interval": ticks,
                "retention": "full",
            },
            "llm": {
                "budget": {
                    "lines": {
                        "cognition": {
                            "calls_per_tick": cognition_calls,
                            "tokens_per_tick": cognition_tokens,
                        }
                    },
                    "max_calls_per_run": max_calls,
                },
                "cache": {"mode": "hybrid", "path": cache_path},
            },
            "salience": {"cognition_sample_rate": 1.0},
        },
    )
    live = await run_resumable(
        settings,
        attempts=resume_attempts,
        retry_delay_seconds=resume_delay_seconds,
        concurrency_overrides={"reasoning": concurrency},
    )
    replay = await run_living_city(
        settings,
        collect_events=False,
        cache_mode="replay",
    )
    calls = call_rows(live)
    actions = action_counts(live)
    call_count = len(calls)
    parsed_calls = sum(bool(row.get("parsed_ok")) for row in calls)
    repair_attempts = sum(int(row.get("repair_attempts", 0)) for row in calls)
    logical_attempts = call_count + repair_attempts
    lane_name = settings.llm.routing["DELIBERATE"].lane
    quota = SlidingWindowQuota(settings.llm.providers[lane_name].quota_path)
    wire_calls = await quota.count(
        f"polis-run:{run_id_for(settings)}",
        window_seconds=RUN_QUOTA_WINDOW_SECONDS,
    )
    latencies = [int(row.get("latency_ms", 0)) for row in calls]
    total_cost = sum((Decimal(str(row.get("cost_usd", "0"))) for row in calls), Decimal(0))
    null_actions = actions["NULL_ACTION"]
    gates = {
        "live_completed": live.report.status == "completed",
        "replay_completed": replay.report.status == "completed",
        "offline_replay_exact": live.report.chain_hash == replay.report.chain_hash,
        "call_limit_respected": wire_calls <= max_calls,
        "provider_calls_recorded": call_count > 0,
        "schema_valid_rate": parsed_calls / call_count >= 0.98 if call_count else False,
        "null_action_rate": null_actions / call_count <= 0.10 if call_count else False,
        "action_diversity": len(actions) >= 3,
        "cost_limit": total_cost <= settings.llm.budget.usd_per_run,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "config": str(config),
        "ticks": ticks,
        "population": settings.population.initial_agents,
        "provider": "minimax",
        "model": settings.llm.routing["DELIBERATE"].model,
        "concurrency": concurrency,
        "max_calls_per_run": max_calls,
        "calls": call_count,
        "logical_attempts": logical_attempts,
        "wire_calls": wire_calls,
        "repair_attempts": repair_attempts,
        "parsed_calls": parsed_calls,
        "parsed_rate": parsed_calls / call_count if call_count else 0,
        "action_counts": dict(sorted(actions.items())),
        "null_action_rate": null_actions / call_count if call_count else 0,
        "tokens_in": sum(int(row.get("tokens_in", 0)) for row in calls),
        "tokens_out": sum(int(row.get("tokens_out", 0)) for row in calls),
        "cost_usd": str(total_cost),
        "latency_ms": {
            "min": min(latencies, default=0),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies, default=0),
        },
        "live_chain_hash": live.report.chain_hash,
        "replay_chain_hash": replay.report.chain_hash,
        "gates": gates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a bounded 1,000-agent live-provider calibration preflight"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/live-minimax-m3-pilot.yaml"),
    )
    parser.add_argument("--ticks", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--resume-attempts", type=int, default=20)
    parser.add_argument("--resume-delay-seconds", type=float, default=15)
    parser.add_argument(
        "--cache-path",
        default="file://./.cache/live-minimax-m3-preflight",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/acceptance/live-minimax-m3-preflight.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(
        preflight(
            args.config,
            ticks=args.ticks,
            max_calls=args.max_calls,
            concurrency=args.concurrency,
            cache_path=args.cache_path,
            resume_attempts=args.resume_attempts,
            resume_delay_seconds=args.resume_delay_seconds,
        )
    )
    write_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
