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

    def collect(
        self,
        *,
        tick: int,
        as_of_seq: int,
        population: AgentPopulation,
        routing: RoutingResult,
        memory: MemoryStore,
        world: World,
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
        batch = tuple(
            MetricPoint(tick, metric_id, round(values[metric_id], 8), as_of_seq)
            for metric_id in sorted(METRICS)
        )
        self.points.extend(batch)
        return batch

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
