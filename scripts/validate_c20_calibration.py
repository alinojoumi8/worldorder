from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from polis.config.settings import load_settings
from polis.events.kinds import (
    AGENT_BORN,
    MIGRATION_IN,
    MIGRATION_OUT,
    TICK_COMPLETED,
)
from polis.events.types import Event
from polis.living_city import run_living_city


class DemographyCountingSink:
    def __init__(self, *, ticks_per_year: int, progress_ticks: int) -> None:
        self.ticks_per_year = ticks_per_year
        self.progress_ticks = progress_ticks
        self.counts: Counter[int] = Counter()
        self.yearly: defaultdict[int, Counter[int]] = defaultdict(Counter)
        self._last_progress = -1

    async def append(self, events: Sequence[Event]) -> None:
        for event in events:
            self.counts[event.kind] += 1
            if event.kind in {AGENT_BORN, MIGRATION_IN, MIGRATION_OUT}:
                self.yearly[event.tick // self.ticks_per_year][event.kind] += 1
            if (
                event.kind == TICK_COMPLETED
                and self.progress_ticks > 0
                and event.tick % self.progress_ticks == 0
                and event.tick != self._last_progress
            ):
                self._last_progress = event.tick
                print(f"progress_tick={event.tick}", flush=True)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run the C20 long-horizon calibration.")
    value.add_argument("--config", type=Path, default=Path("configs/m3-smoke.yaml"))
    value.add_argument("--agents", type=int, default=300)
    value.add_argument("--years", type=int, default=3)
    value.add_argument("--progress-ticks", type=int, default=100)
    return value


async def calibrate(args: argparse.Namespace) -> dict[str, object]:
    base = load_settings(args.config)
    ticks_per_year = base.clock.days_per_sim_year * base.clock.ticks_per_sim_day
    ticks = args.years * ticks_per_year
    settings = load_settings(
        args.config,
        overrides={
            "run": {
                "name": f"c20-calibration-{args.agents}x{args.years}",
                "ticks": ticks,
                "scale": args.agents,
                "retention": "metrics_only",
            },
            "population": {"initial_agents": args.agents},
            "ventures": {"acceptance_fixture": False},
        },
    )
    sink = DemographyCountingSink(
        ticks_per_year=ticks_per_year,
        progress_ticks=args.progress_ticks,
    )
    started = time.perf_counter()
    result = await run_living_city(
        settings,
        sink=sink,
        collect_events=False,
    )
    deliberate = result.metrics.series("sys.cognition.deliberate_share")
    yearly: list[dict[str, int | float | None]] = []
    share_means: list[float] = []
    birth_counts: list[int] = []
    for year in range(args.years):
        values = [
            point.value
            for point in deliberate
            if year * ticks_per_year <= point.tick < (year + 1) * ticks_per_year
        ]
        share_mean = sum(values) / len(values) if values else None
        births = sink.yearly[year][AGENT_BORN]
        if share_mean is not None:
            share_means.append(share_mean)
            birth_counts.append(births)
        yearly.append(
            {
                "year": year + 1,
                "births": births,
                "migration_in": sink.yearly[year][MIGRATION_IN],
                "migration_out": sink.yearly[year][MIGRATION_OUT],
                "deliberate_share_mean": share_mean,
            }
        )
    correlation = (
        statistics.correlation(share_means, birth_counts)
        if len(share_means) == args.years
        and len(set(share_means)) > 1
        and len(set(birth_counts)) > 1
        else None
    )
    return {
        "status": result.report.status,
        "halt_reason": result.report.halt_reason,
        "ticks_completed": result.report.ticks,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "initial_population": args.agents,
        "final_alive": len(result.population.alive()),
        "births": sink.counts[AGENT_BORN],
        "migration_in": sink.counts[MIGRATION_IN],
        "migration_out": sink.counts[MIGRATION_OUT],
        "yearly": yearly,
        "birth_deliberate_correlation": correlation,
    }


def main() -> None:
    args = parser().parse_args()
    if args.agents <= 0 or args.years <= 0:
        raise SystemExit("--agents and --years must be positive")
    print(json.dumps(asyncio.run(calibrate(args)), sort_keys=True))


if __name__ == "__main__":
    main()
