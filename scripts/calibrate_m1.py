from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from polis.config.settings import load_settings
from polis.events.kinds import LIVE_TICK
from polis.events.types import Event
from polis.living_city import run_living_city


class ProgressSink:
    def __init__(self, output: Path, total_ticks: int) -> None:
        self.output = output
        self.total_ticks = total_ticks
        self.started_ns = time.perf_counter_ns()

    async def publish(self, events: Sequence[Event]) -> None:
        ticks = [event.tick for event in events if event.kind == LIVE_TICK]
        if not ticks:
            return
        tick = max(ticks)
        if tick % 25 != 0 and tick != self.total_ticks:
            return
        elapsed_s = (time.perf_counter_ns() - self.started_ns) / 1_000_000_000
        write_json(
            self.output,
            {
                "status": "running",
                "tick": tick,
                "total_ticks": self.total_ticks,
                "progress_pct": round(100 * tick / self.total_ticks, 2),
                "elapsed_s": round(elapsed_s, 3),
                "ticks_per_second": round(tick / elapsed_s, 3),
            },
        )


def percentile(values: list[float], proportion: float) -> float:
    if not values:
        return 0.0
    index = round((len(values) - 1) * proportion)
    return sorted(values)[index]


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def calibrate(
    config: Path,
    output: Path,
    *,
    ticks: int | None = None,
) -> dict[str, Any]:
    settings = load_settings(
        config,
        overrides={"run": {"ticks": ticks}} if ticks is not None else None,
    )
    progress = ProgressSink(output, settings.run.ticks)
    started_ns = time.perf_counter_ns()
    result = await run_living_city(
        settings,
        ephemeral_sink=progress,
        collect_events=False,
    )
    elapsed_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000
    deliberate = [
        float(point.value)
        for point in result.metrics.series("sys.cognition.deliberate_share")
        if point.tick > 1
    ]
    raw_entropy = [
        float(point.value)
        for point in result.metrics.series("sys.actions.entropy")
        if point.tick > 1
    ]
    action_unique = [
        int(point.value) for point in result.metrics.series("sys.actions.unique") if point.tick > 1
    ]
    legal_action_types = 6
    normalized_entropy = [value / math.log(legal_action_types) for value in raw_entropy]
    v4_pass_share = sum(value >= 0.35 for value in normalized_entropy) / len(normalized_entropy)
    wellbeing = [
        float(point.value)
        for point in result.metrics.series("city.wellbeing_mean")
        if point.tick > 1
    ]
    throughput = settings.run.ticks / elapsed_s
    invariants_pass = (
        result.report.status == "completed"
        and result.report.halt_reason is None
        and result.population.population() == settings.population.initial_agents
    )
    gates = {
        "throughput_at_least_1_tick_s": throughput >= 1.0,
        "deliberate_share_near_7_percent": 650 <= statistics.mean(deliberate) <= 750,
        "v4_entropy_floor_on_90_percent_ticks": v4_pass_share >= 0.90,
        "multiple_action_types": min(action_unique) >= 2,
        "invariants_and_population": invariants_pass,
        "nonzero_wellbeing": min(wellbeing) > 0,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "config": str(config),
        "run_id": str(result.report.run_id),
        "seed": settings.run.seed,
        "population": result.population.population(),
        "ticks": result.report.ticks,
        "elapsed_s": round(elapsed_s, 3),
        "ticks_per_second": round(throughput, 3),
        "events_hashed": result.report.events,
        "terminal_hash": result.report.chain_hash,
        "halt_reason": result.report.halt_reason,
        "deliberate_share_bp": {
            "mean": round(statistics.mean(deliberate), 3),
            "p05": round(percentile(deliberate, 0.05), 3),
            "p50": round(percentile(deliberate, 0.50), 3),
            "p95": round(percentile(deliberate, 0.95), 3),
        },
        "v4_action_entropy_normalized": {
            "mean": round(statistics.mean(normalized_entropy), 6),
            "minimum": round(min(normalized_entropy), 6),
            "pass_share": round(v4_pass_share, 6),
            "threshold": 0.35,
        },
        "action_types_per_tick": {
            "minimum": min(action_unique),
            "maximum": max(action_unique),
        },
        "wellbeing": {
            "initial": wellbeing[0],
            "final": wellbeing[-1],
            "minimum": min(wellbeing),
        },
        "retained_memories": len(result.memory),
        "sampled_cognition_traces": len(result.traces),
        "gates": gates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and report the M1 acceptance calibration")
    parser.add_argument("--config", type=Path, default=Path("configs/calibration-m1.yaml"))
    parser.add_argument("--ticks", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/acceptance/m1-calibration.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(calibrate(args.config, args.output, ticks=args.ticks))
    write_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
