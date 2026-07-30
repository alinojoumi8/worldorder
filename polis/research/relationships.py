from __future__ import annotations

import math
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final, Literal

from polis.config.errors import PolisError

RelationshipId = Literal[
    "beveridge",
    "okun",
    "phillips",
    "zipf",
    "business_cycle_autocorrelation",
]
Series = Mapping[int, float] | Sequence[tuple[int, float]]


class RelationshipError(PolisError):
    """A relationship cannot be evaluated from the supplied evidence or series."""


class RelationshipPreconditionError(RelationshipError):
    def __init__(self, relationship: RelationshipId, missing: Sequence[str]) -> None:
        self.relationship = relationship
        self.missing = tuple(missing)
        joined = ", ".join(self.missing)
        super().__init__(f"{relationship}: missing prerequisite(s): {joined}")


@dataclass(frozen=True, slots=True)
class RelationshipEvidence:
    """Evidence that must exist before a cross-metric relationship may be claimed."""

    reflex_only_baseline_complete: bool = False
    mechanism_checklist_complete: bool = False
    beveridge_falsification_complete: bool = False
    zipf_genesis_exponent_bp: float | None = None
    zipf_exponent_moved: bool = False


@dataclass(frozen=True, slots=True)
class RelationshipResult:
    relationship: RelationshipId
    statistic: str
    value: float
    observations: int
    inputs: tuple[str, ...]
    prerequisites: tuple[str, ...]
    comparison: Mapping[str, float]


_COMMON_PREREQUISITES: Final = (
    "reflex-only baseline",
    "completed MECHANISM checklist",
)


def _require(relationship: RelationshipId, evidence: RelationshipEvidence) -> tuple[str, ...]:
    missing: list[str] = []
    satisfied: list[str] = []
    if evidence.reflex_only_baseline_complete:
        satisfied.append(_COMMON_PREREQUISITES[0])
    else:
        missing.append(_COMMON_PREREQUISITES[0])
    if evidence.mechanism_checklist_complete:
        satisfied.append(_COMMON_PREREQUISITES[1])
    else:
        missing.append(_COMMON_PREREQUISITES[1])
    if relationship == "beveridge":
        label = "Beveridge falsification protocol"
        if evidence.beveridge_falsification_complete:
            satisfied.append(label)
        else:
            missing.append(label)
    if relationship == "zipf":
        if evidence.zipf_genesis_exponent_bp is None or not math.isfinite(
            evidence.zipf_genesis_exponent_bp
        ):
            missing.append("genesis firm-size tail exponent")
        else:
            satisfied.append("genesis firm-size tail exponent")
        if evidence.zipf_exponent_moved:
            satisfied.append("evidence that the firm-size tail exponent moved")
        else:
            missing.append("evidence that the firm-size tail exponent moved")
    if missing:
        raise RelationshipPreconditionError(relationship, missing)
    return tuple(satisfied)


def _points(series: Series, *, name: str) -> dict[int, float]:
    pairs = series.items() if isinstance(series, Mapping) else series
    result: dict[int, float] = {}
    for tick, value in pairs:
        try:
            normalized_tick = operator.index(tick)
        except TypeError as error:
            raise RelationshipError(f"{name}: tick must be an integer: {tick!r}") from error
        if normalized_tick in result:
            raise RelationshipError(f"{name}: duplicate tick {normalized_tick}")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise RelationshipError(f"{name}: non-finite value at tick {normalized_tick}")
        result[normalized_tick] = numeric
    if not result:
        raise RelationshipError(f"{name}: series is empty")
    return result


def _aligned(
    left: Series,
    right: Series,
    *,
    names: tuple[str, str],
) -> tuple[list[float], list[float]]:
    left_points = _points(left, name=names[0])
    right_points = _points(right, name=names[1])
    ticks = sorted(left_points.keys() & right_points.keys())
    if len(ticks) < 3:
        raise RelationshipError(
            f"{names[0]} and {names[1]} need at least 3 observations at matching ticks"
        )
    return (
        [left_points[tick] for tick in ticks],
        [right_points[tick] for tick in ticks],
    )


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    left_delta = [value - left_mean for value in left]
    right_delta = [value - right_mean for value in right]
    denominator = math.sqrt(
        math.fsum(value * value for value in left_delta)
        * math.fsum(value * value for value in right_delta)
    )
    if denominator == 0:
        raise RelationshipError("relationship is undefined for a constant series")
    return (
        math.fsum(
            left_value * right_value
            for left_value, right_value in zip(left_delta, right_delta, strict=True)
        )
        / denominator
    )


def _slope(explanatory: Sequence[float], response: Sequence[float]) -> float:
    explanatory_mean = math.fsum(explanatory) / len(explanatory)
    response_mean = math.fsum(response) / len(response)
    denominator = math.fsum(
        (value - explanatory_mean) * (value - explanatory_mean) for value in explanatory
    )
    if denominator == 0:
        raise RelationshipError("slope is undefined for a constant explanatory series")
    return (
        math.fsum(
            (x_value - explanatory_mean) * (y_value - response_mean)
            for x_value, y_value in zip(explanatory, response, strict=True)
        )
        / denominator
    )


def _correlation_result(
    relationship: RelationshipId,
    left: Series,
    right: Series,
    *,
    names: tuple[str, str],
    evidence: RelationshipEvidence,
) -> RelationshipResult:
    prerequisites = _require(relationship, evidence)
    left_values, right_values = _aligned(left, right, names=names)
    return RelationshipResult(
        relationship=relationship,
        statistic="pearson_correlation",
        value=_pearson(left_values, right_values),
        observations=len(left_values),
        inputs=names,
        prerequisites=prerequisites,
        comparison={},
    )


def beveridge(
    unemployment_rate: Series,
    vacancy_rate: Series,
    *,
    evidence: RelationshipEvidence,
) -> RelationshipResult:
    return _correlation_result(
        "beveridge",
        unemployment_rate,
        vacancy_rate,
        names=("unemployment_rate", "vacancy_rate"),
        evidence=evidence,
    )


def okun(
    gdp_real: Series,
    unemployment_rate: Series,
    *,
    evidence: RelationshipEvidence,
) -> RelationshipResult:
    prerequisites = _require("okun", evidence)
    gdp_values, unemployment_values = _aligned(
        gdp_real,
        unemployment_rate,
        names=("gdp_real", "unemployment_rate"),
    )
    if any(value <= 0 for value in gdp_values):
        raise RelationshipError("gdp_real must be positive for log differences")
    gdp_growth = [
        math.log(current) - math.log(previous) for previous, current in pairwise(gdp_values)
    ]
    unemployment_change = [
        current - previous for previous, current in pairwise(unemployment_values)
    ]
    return RelationshipResult(
        relationship="okun",
        statistic="ols_slope_unemployment_change_on_log_gdp_change",
        value=_slope(gdp_growth, unemployment_change),
        observations=len(gdp_growth),
        inputs=("gdp_real", "unemployment_rate"),
        prerequisites=prerequisites,
        comparison={},
    )


def phillips(
    unemployment_rate: Series,
    inflation_yoy: Series,
    *,
    evidence: RelationshipEvidence,
) -> RelationshipResult:
    return _correlation_result(
        "phillips",
        unemployment_rate,
        inflation_yoy,
        names=("unemployment_rate", "inflation_yoy"),
        evidence=evidence,
    )


def zipf(
    firm_size_tail_bp: Series,
    *,
    evidence: RelationshipEvidence,
) -> RelationshipResult:
    prerequisites = _require("zipf", evidence)
    values = sorted(_points(firm_size_tail_bp, name="firm_size_tail_bp").items())
    measured = values[-1][1]
    genesis = evidence.zipf_genesis_exponent_bp
    assert genesis is not None
    return RelationshipResult(
        relationship="zipf",
        statistic="terminal_hill_tail_exponent_bp",
        value=measured,
        observations=len(values),
        inputs=("firm_size_tail_bp",),
        prerequisites=prerequisites,
        comparison={
            "genesis_tail_exponent_bp": genesis,
            "change_from_genesis_bp": measured - genesis,
        },
    )


def business_cycle_autocorrelation(
    gdp_real: Series,
    *,
    evidence: RelationshipEvidence,
) -> RelationshipResult:
    prerequisites = _require("business_cycle_autocorrelation", evidence)
    values = [value for _, value in sorted(_points(gdp_real, name="gdp_real").items())]
    if len(values) < 4:
        raise RelationshipError("gdp_real needs at least 4 observations for lag-1 autocorrelation")
    if any(value <= 0 for value in values):
        raise RelationshipError("gdp_real must be positive for log differences")
    growth = [math.log(current) - math.log(previous) for previous, current in pairwise(values)]
    value = _pearson(growth[:-1], growth[1:])
    return RelationshipResult(
        relationship="business_cycle_autocorrelation",
        statistic="lag1_autocorrelation_log_gdp_growth",
        value=value,
        observations=len(growth) - 1,
        inputs=("gdp_real",),
        prerequisites=prerequisites,
        comparison={},
    )
