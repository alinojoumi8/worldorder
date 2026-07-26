from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from polis.agents.cognition.salience import RoutingResult
from polis.agents.memory import MemoryStore
from polis.agents.state import AgentPopulation
from polis.config.metric_catalogue import METRICS as METRICS
from polis.config.metric_catalogue import (
    UNAVAILABLE_M1_METRICS as UNAVAILABLE_M1_METRICS,
)
from polis.config.metric_catalogue import MetricDefinition as MetricDefinition
from polis.config.metric_catalogue import catalogue_manifest as catalogue_manifest
from polis.config.settings import Settings
from polis.economy.state import EconomyState
from polis.research.economy_metrics import economy_metric_values
from polis.world.api import World


@dataclass(frozen=True, slots=True)
class MetricPoint:
    tick: int
    metric: str
    value: float
    as_of_seq: int


class MetricCollector:
    name = "metrics"

    def __init__(self) -> None:
        self.points: list[MetricPoint] = []
        self._series: dict[str, list[MetricPoint]] = {}

    def collect(
        self,
        *,
        tick: int,
        as_of_seq: int,
        population: AgentPopulation,
        routing: RoutingResult,
        memory: MemoryStore,
        world: World,
        economy: EconomyState | None = None,
        settings: Settings | None = None,
    ) -> tuple[MetricPoint, ...]:
        alive = population.alive()
        salience = sorted(score.score for score in routing.scores.values())
        action_counts = population.action_type_counts()
        action_total = sum(action_counts.values())
        entropy = (
            -sum(
                (count / action_total) * math.log(count / action_total)
                for count in action_counts.values()
                if count
            )
            if action_total
            else 0.0
        )
        skill_values = [value for agent in alive for value in agent.skills.values()]
        values = {
            "city.population": float(len(alive)),
            "city.wellbeing_mean": statistics.fmean(agent.wellbeing for agent in alive)
            if alive
            else 0.0,
            "sys.cognition.deliberate_share": (
                10_000 * routing.n_deliberate / len(alive) if alive else 0.0
            ),
            "sys.cognition.reflect_share": (
                10_000 * routing.n_reflect / len(alive) if alive else 0.0
            ),
            "sys.cognition.salience_cutoff": routing.cutoff,
            "sys.cognition.salience_p50": (statistics.median(salience) if salience else 0.0),
            "sys.cognition.salience_p90": (
                salience[min(len(salience) - 1, int(len(salience) * 0.9))] if salience else 0.0
            ),
            "sys.memory.count": float(len(memory)),
            "sys.actions.entropy": entropy,
            "sys.actions.unique": float(len(action_counts)),
            "education.mean_skill": (statistics.fmean(skill_values) if skill_values else 0.0),
            "world.occupied_places": float(
                sum(bool(world.occupancy(place.place_id)) for place in world.places)
            ),
        }
        if economy is not None:
            if settings is None:
                raise ValueError("economy metrics require resolved settings")
            quarter = settings.clock.days_per_sim_year * settings.clock.ticks_per_sim_day // 4
            year = settings.clock.days_per_sim_year * settings.clock.ticks_per_sim_day
            quarter_due = quarter > 0 and tick % quarter == 0
            week = 7 * settings.clock.ticks_per_sim_day
            week_due = week > 0 and tick % week == 0
            previous_inventory = (
                self._value_at_or_before(
                    "inventory_value_cents",
                    tick - quarter,
                )
                if quarter_due
                else None
            )
            inventory_year_ago = (
                self._value_at_or_before(
                    "inventory_value_cents",
                    tick - year,
                )
                if quarter_due
                else None
            )
            if tick == year and inventory_year_ago is None:
                inventory_year_ago = float(economy.initial_inventory_value_cents)
            credit_year_ago = (
                self._value_at_or_before(
                    "credit_outstanding_cents",
                    tick - year,
                )
                if week_due
                else None
            )
            values.update(
                economy_metric_values(
                    settings,
                    population,
                    economy,
                    tick=tick,
                    previous_inventory_cents=(
                        int(previous_inventory) if previous_inventory is not None else None
                    ),
                    inventory_year_ago_cents=(
                        int(inventory_year_ago) if inventory_year_ago is not None else None
                    ),
                    credit_year_ago_cents=(
                        int(credit_year_ago) if credit_year_ago is not None else None
                    ),
                )
            )
        unknown = set(values) - set(METRICS)
        if unknown:
            raise RuntimeError(f"unregistered metrics: {sorted(unknown)}")
        batch = tuple(
            MetricPoint(tick, metric_id, round(values[metric_id], 8), as_of_seq)
            for metric_id in sorted(values)
        )
        self.points.extend(batch)
        for point in batch:
            self._series.setdefault(point.metric, []).append(point)
        return batch

    def _value_at_or_before(self, metric: str, tick: int) -> float | None:
        series = self._series.get(metric, ())
        low = 0
        high = len(series)
        while low < high:
            middle = (low + high) // 2
            if series[middle].tick <= tick:
                low = middle + 1
            else:
                high = middle
        return series[low - 1].value if low else None

    def latest(self) -> dict[str, MetricPoint]:
        result: dict[str, MetricPoint] = {}
        for point in self.points:
            result[point.metric] = point
        return result

    def series(self, metric: str) -> tuple[MetricPoint, ...]:
        if metric not in METRICS:
            raise KeyError(metric)
        return tuple(point for point in self.points if point.metric == metric)

    def dump(self) -> Mapping[str, Any]:
        return {"points": [asdict(point) for point in self.points]}

    def load(self, state: Mapping[str, Any]) -> None:
        self.points = [MetricPoint(**row) for row in state["points"]]
        self._series = {}
        for point in self.points:
            self._series.setdefault(point.metric, []).append(point)
