from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Literal

from polis.config.canon import canonical_bytes, sha256_hex

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
