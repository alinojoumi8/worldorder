from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal

from polis.agents.cognition.salience import RoutingResult
from polis.agents.memory import MemoryStore
from polis.agents.state import AgentPopulation
from polis.config.canon import canonical_bytes, sha256_hex
from polis.world.api import World

Cadence = Literal["tick", "sim_day", "sim_week", "sim_month", "sim_year"]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    unit: str
    cadence: Cadence
    definition: str
    research_questions: tuple[str, ...] = ()

    @property
    def definition_hash(self) -> str:
        return sha256_hex(canonical_bytes(asdict(self)))


@dataclass(frozen=True, slots=True)
class MetricPoint:
    tick: int
    metric: str
    value: float
    as_of_seq: int


def _definition(
    metric_id: str,
    unit: str,
    definition: str,
    *,
    cadence: Cadence = "tick",
    research_questions: tuple[str, ...] = (),
) -> MetricDefinition:
    return MetricDefinition(metric_id, unit, cadence, definition, research_questions)


METRICS: Final[dict[str, MetricDefinition]] = {
    item.metric_id: item
    for item in (
        _definition("city.population", "agents", "Count of living citizens."),
        _definition(
            "city.wellbeing_mean",
            "index_0_100",
            "Arithmetic mean of living citizen wellbeing.",
        ),
        _definition(
            "sys.cognition.deliberate_share",
            "bp",
            "10,000 times deliberate routed citizens divided by awake citizens.",
            research_questions=("T8", "T9"),
        ),
        _definition(
            "sys.cognition.reflect_share",
            "bp",
            "10,000 times reflection routed citizens divided by awake citizens.",
        ),
        _definition(
            "sys.cognition.salience_cutoff",
            "dimensionless_float",
            "Minimum salience among deliberate-routed citizens.",
            research_questions=("T8",),
        ),
        _definition(
            "sys.cognition.salience_p50",
            "dimensionless_float",
            "Median salience score among awake citizens.",
            research_questions=("T8",),
        ),
        _definition(
            "sys.cognition.salience_p90",
            "dimensionless_float",
            "90th percentile salience score among awake citizens.",
            research_questions=("T8",),
        ),
        _definition(
            "sys.memory.count",
            "memories",
            "Count of retained memory records.",
        ),
        _definition(
            "sys.actions.entropy",
            "nats",
            "Shannon entropy over resolved M1 action types in the tick.",
        ),
        _definition(
            "sys.actions.unique",
            "action_types",
            "Count of distinct resolved M1 action types in the tick.",
        ),
        _definition(
            "education.mean_skill",
            "dimensionless_float",
            "Mean skill level over living citizens and the closed skill vocabulary.",
            cadence="sim_day",
        ),
        _definition(
            "world.occupied_places",
            "places",
            "Count of places with at least one citizen after movement resolution.",
        ),
    )
}

UNAVAILABLE_M1_METRICS: Final = frozenset(
    {
        "gdp_nominal",
        "unemployment_rate",
        "cpi",
        "gini_wealth",
        "gini_income",
        "median_wage",
        "market_index",
    }
)


def catalogue_manifest() -> dict[str, str]:
    return {
        metric_id: definition.definition_hash for metric_id, definition in sorted(METRICS.items())
    }


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
