from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any, Literal
from uuid import UUID

from polis.config.errors import PolisError
from polis.events.kinds import INVARIANT_VIOLATED, TICK_COMPLETED
from polis.kernel.rng import RngRegistry
from polis.store.engine import Database
from polis.store.operations import load_run_settings

Observation = tuple[int, float]
Verdict = Literal["pass", "fail", "n/a"]
GateId = Literal["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8"]


class GateError(PolisError):
    """Validity-gate evidence is missing, inconsistent, or invalid."""


@dataclass(frozen=True, slots=True)
class GateResult:
    id: GateId
    verdict: Verdict
    statistic: Mapping[str, Any]
    threshold: Mapping[str, Any]
    window: Mapping[str, int] | None
    query: str
    notes: str = ""
    shock_free: bool = False

    @property
    def gate_id(self) -> GateId:
        """Compatibility alias for the pre-C24b result shape."""

        return self.id

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EventLineage:
    seq: int
    tick: int
    kind: int
    cause_seq: int | None = None


@dataclass(frozen=True, slots=True)
class ModelFamilyEvidence:
    effect: float
    ci: tuple[float, float]
    parse_failure_rate_bp: float
    sim_awareness_rate_bp: float
    cost_usd: float
    seeds: int
    model_versions: tuple[str, ...]


def _windowed(
    values: Sequence[Observation],
    *,
    from_tick: int,
    to_tick: int,
) -> list[Observation]:
    return sorted(
        (int(tick), float(value)) for tick, value in values if from_tick <= tick <= to_tick
    )


def _log_trend(
    values: Sequence[Observation],
    *,
    ticks_per_year: int,
) -> tuple[float, float]:
    if len(values) < 3 or any(value <= 0 or not math.isfinite(value) for _, value in values):
        return math.nan, math.nan
    x = [tick / ticks_per_year for tick, _ in values]
    y = [math.log(value) for _, value in values]
    x_mean = statistics.fmean(x)
    y_mean = statistics.fmean(y)
    centred_x = [value - x_mean for value in x]
    sxx = sum(value * value for value in centred_x)
    if sxx == 0:
        return math.nan, math.nan
    beta = sum(dx * (value - y_mean) for dx, value in zip(centred_x, y, strict=True)) / sxx
    alpha = y_mean - beta * x_mean
    residuals = [value - alpha - beta * year for year, value in zip(x, y, strict=True)]
    tick_steps = [
        values[index][0] - values[index - 1][0]
        for index in range(1, len(values))
        if values[index][0] > values[index - 1][0]
    ]
    typical_step = statistics.median(tick_steps) if tick_steps else ticks_per_year
    lag = min(len(values) - 1, max(1, round(ticks_per_year / typical_step)))
    scores = [dx * residual for dx, residual in zip(centred_x, residuals, strict=True)]
    long_run_variance = sum(score * score for score in scores)
    for offset in range(1, lag + 1):
        weight = 1 - offset / (lag + 1)
        covariance = sum(
            scores[index] * scores[index - offset] for index in range(offset, len(scores))
        )
        long_run_variance += 2 * weight * covariance
    standard_error = math.sqrt(max(0.0, long_run_variance)) / sxx
    return beta, standard_error


def _annual_difference_sign_changes(
    values: Sequence[Observation],
    *,
    ticks_per_year: int,
) -> int:
    by_tick = dict(values)
    signs = [
        1 if value > by_tick[tick - ticks_per_year] else -1
        for tick, value in values
        if tick - ticks_per_year in by_tick and value != by_tick[tick - ticks_per_year]
    ]
    return sum(left != right for left, right in pairwise(signs))


def _annual_means(
    values: Sequence[Observation],
    *,
    from_tick: int,
    ticks_per_year: int,
    years: int,
) -> list[float]:
    result: list[float] = []
    for year in range(years):
        lower = from_tick + year * ticks_per_year
        upper = lower + ticks_per_year - 1
        bucket = [value for tick, value in values if lower <= tick <= upper]
        if not bucket:
            return []
        result.append(statistics.fmean(bucket))
    return result


def _annual_terminal_values(
    values: Sequence[Observation],
    *,
    from_tick: int,
    ticks_per_year: int,
    years: int,
) -> list[float]:
    result: list[float] = []
    for year in range(1, years + 1):
        upper = from_tick + year * ticks_per_year - 1
        available = [(tick, value) for tick, value in values if tick <= upper]
        if not available:
            return []
        result.append(max(available, key=lambda item: item[0])[1])
    return result


def longest_shock_free_window(
    events: Sequence[EventLineage],
    *,
    last_tick: int,
) -> Mapping[str, int] | None:
    """Return the earliest longest interval without a 99xxx causal ancestor."""

    if last_tick < 1:
        return None
    contaminated_sequences: set[int] = set()
    contaminated_ticks: set[int] = set()
    for event in sorted(events, key=lambda item: item.seq):
        is_research_root = 99_000 <= event.kind <= 99_999
        descends_from_research = (
            event.cause_seq is not None and event.cause_seq in contaminated_sequences
        )
        if is_research_root:
            contaminated_sequences.add(event.seq)
        if descends_from_research:
            contaminated_sequences.add(event.seq)
            if 1 <= event.tick <= last_tick:
                contaminated_ticks.add(event.tick)

    best: tuple[int, int] | None = None
    start = 1
    for blocked_tick in sorted(contaminated_ticks):
        if start <= blocked_tick - 1:
            candidate = (start, blocked_tick - 1)
            if best is None or candidate[1] - candidate[0] > best[1] - best[0]:
                best = candidate
        start = blocked_tick + 1
    if start <= last_tick:
        candidate = (start, last_tick)
        if best is None or candidate[1] - candidate[0] > best[1] - best[0]:
            best = candidate
    if best is None:
        return None
    return {"from_tick": best[0], "to_tick": best[1]}


def evaluate_v1(
    series: Mapping[str, Sequence[Observation]],
    *,
    ticks_per_year: int,
    last_tick: int,
    years: int = 5,
    event_lineage: Sequence[EventLineage] = (),
) -> GateResult:
    required_ticks = years * ticks_per_year
    longest_window = longest_shock_free_window(event_lineage, last_tick=last_tick)
    shock_free = longest_window is not None or last_tick < 1
    if longest_window is None:
        window = {"from_tick": 1, "to_tick": max(0, last_tick)}
    else:
        window = dict(longest_window)
    from_tick = window["from_tick"]
    to_tick = window["to_tick"]
    thresholds = {
        "level_ratio_min": round(math.exp(-1), 8),
        "level_ratio_max": round(math.e, 8),
        "level_log_slope_abs_max": 0.15,
        "annual_difference_sign_changes_min": 4,
        "unemployment_annual_mean_min_bp": 200,
        "unemployment_annual_mean_max_bp": 3000,
        "unemployment_annual_change_abs_max_bp": 1500,
        "gdp_log_slope_abs_max": 0.20,
        "gdp_large_same_sign_consecutive_years_max": 2,
    }
    observed_ticks = max(0, to_tick - from_tick + 1) if shock_free else 0
    if observed_ticks < required_ticks:
        return GateResult(
            id="V1",
            verdict="n/a",
            statistic={"required_ticks": required_ticks, "observed_ticks": observed_ticks},
            threshold=thresholds,
            window=window,
            query=(
                "Longest contiguous tick interval with no event whose causal ancestry "
                "contains a kind in 99000..99999."
            ),
            notes="No contiguous five-sim-year shock-free window is available.",
            shock_free=shock_free,
        )

    statistics_by_series: dict[str, Any] = {}
    checks: list[bool] = []
    for metric in ("cpi", "market_index"):
        values = _windowed(series.get(metric, ()), from_tick=from_tick, to_tick=to_tick)
        covered = bool(values) and values[0][0] <= from_tick and values[-1][0] >= to_tick
        ratio = values[-1][1] / values[0][1] if covered and values[0][1] > 0 else math.nan
        slope, newey_west_se = _log_trend(values, ticks_per_year=ticks_per_year)
        sign_changes = _annual_difference_sign_changes(
            values,
            ticks_per_year=ticks_per_year,
        )
        metric_checks = {
            "coverage": covered,
            "ratio": math.isfinite(ratio) and math.exp(-1) <= ratio <= math.e,
            "slope": math.isfinite(slope) and abs(slope) < 0.15,
            "sign_changes": sign_changes >= 4,
        }
        checks.extend(metric_checks.values())
        statistics_by_series[metric] = {
            "observations": len(values),
            "covered": covered,
            "terminal_initial_ratio": round(ratio, 8) if math.isfinite(ratio) else None,
            "log_slope_per_year": round(slope, 8) if math.isfinite(slope) else None,
            "newey_west_se_lag_one_year": (
                round(newey_west_se, 8) if math.isfinite(newey_west_se) else None
            ),
            "annual_difference_sign_changes": sign_changes,
            "checks": metric_checks,
        }

    unemployment = _windowed(
        series.get("unemployment_rate", ()),
        from_tick=from_tick,
        to_tick=to_tick,
    )
    unemployment_means = _annual_means(
        unemployment,
        from_tick=from_tick,
        ticks_per_year=ticks_per_year,
        years=years,
    )
    unemployment_changes = [right - left for left, right in pairwise(unemployment_means)]
    unemployment_checks = {
        "five_annual_means": len(unemployment_means) == years,
        "annual_means_bounded": bool(unemployment_means)
        and all(200 <= value <= 3000 for value in unemployment_means),
        "annual_changes_bounded": bool(unemployment_means)
        and all(abs(value) <= 1500 for value in unemployment_changes),
    }
    checks.extend(unemployment_checks.values())
    statistics_by_series["unemployment_rate"] = {
        "observations": len(unemployment),
        "annual_means_bp": [round(value, 8) for value in unemployment_means],
        "annual_changes_bp": [round(value, 8) for value in unemployment_changes],
        "checks": unemployment_checks,
    }

    gdp = _windowed(series.get("gdp_real", ()), from_tick=from_tick, to_tick=to_tick)
    gdp_slope, _ = _log_trend(gdp, ticks_per_year=ticks_per_year)
    gdp_annual = _annual_terminal_values(
        gdp,
        from_tick=from_tick,
        ticks_per_year=ticks_per_year,
        years=years,
    )
    gdp_changes = [
        (right - left) / left if left > 0 else math.nan for left, right in pairwise(gdp_annual)
    ]
    large_run = 0
    largest_run = 0
    prior_sign = 0
    for change in gdp_changes:
        sign = 1 if change > 0.20 else -1 if change < -0.20 else 0
        large_run = large_run + 1 if sign and sign == prior_sign else int(bool(sign))
        largest_run = max(largest_run, large_run)
        prior_sign = sign
    gdp_checks = {
        "five_annual_levels": len(gdp_annual) == years,
        "positive_levels": bool(gdp) and all(value > 0 for _, value in gdp),
        "slope": math.isfinite(gdp_slope) and abs(gdp_slope) < 0.20,
        "no_three_year_absorbing_run": largest_run <= 2,
    }
    checks.extend(gdp_checks.values())
    statistics_by_series["gdp_real"] = {
        "observations": len(gdp),
        "annual_terminal_cents": [round(value, 8) for value in gdp_annual],
        "annual_changes": [
            round(value, 8) if math.isfinite(value) else None for value in gdp_changes
        ],
        "log_slope_per_year": round(gdp_slope, 8) if math.isfinite(gdp_slope) else None,
        "largest_same_sign_change_run_over_20_percent": largest_run,
        "checks": gdp_checks,
    }
    return GateResult(
        id="V1",
        verdict="pass" if all(checks) else "fail",
        statistic=statistics_by_series,
        threshold=thresholds,
        window=window,
        query=(
            "Metric series restricted to the longest interval with no 99xxx causal "
            "ancestor; tests follow research specification section 2.3 V1."
        ),
        shock_free=shock_free,
    )


def evaluate_v2(
    *,
    last_tick: int,
    ticks_checked: int,
    invariant_violations: Mapping[str, int],
    posthoc_violating_ticks: Sequence[int],
    final_ledger_checks: Mapping[str, int],
) -> GateResult:
    relevant_violations = {
        invariant_id: int(invariant_violations.get(invariant_id, 0))
        for invariant_id in ("INV-MONEY", "INV-LEDGER")
    }
    checks = {
        "zero_runtime_violations": not any(relevant_violations.values()),
        "zero_posthoc_violations": not posthoc_violating_ticks,
        "final_ledger_exact": not any(final_ledger_checks.values()),
        "checked_every_tick": ticks_checked == last_tick + 1,
    }
    return GateResult(
        id="V2",
        verdict="pass" if all(checks.values()) else "fail",
        statistic={
            "runtime_violations": relevant_violations,
            "posthoc_violating_ticks": list(posthoc_violating_ticks),
            "final_ledger_checks_cents": dict(final_ledger_checks),
            "ticks_checked": ticks_checked,
            "expected_ticks_checked": last_tick + 1,
            "checks": checks,
        },
        threshold={
            "runtime_violations_max": 0,
            "posthoc_violating_ticks_max": 0,
            "final_ledger_imbalance_cents": 0,
            "ticks_checked": last_tick + 1,
        },
        window={"from_tick": 0, "to_tick": last_tick},
        query=(
            "Runtime INV-MONEY/INV-LEDGER violations plus post-hoc ledger-entry "
            "closure, materialised balances, and TICK_COMPLETED coverage."
        ),
    )


def _failure_summary(
    values: Sequence[Observation],
    predicate: Callable[[float], bool],
    *,
    failure_share_max: float,
) -> dict[str, Any]:
    failures = sum(not predicate(value) for _, value in values)
    evaluations = len(values)
    failure_share = failures / evaluations if evaluations else 1.0
    return {
        "evaluations": evaluations,
        "failures": failures if evaluations else 1,
        "failure_share": round(failure_share, 8),
        "pass": evaluations > 0 and failure_share <= failure_share_max,
    }


def evaluate_v3(
    series: Mapping[str, Sequence[Observation]],
    *,
    last_tick: int,
) -> GateResult:
    checks = {
        "wealth_share.top1": _failure_summary(
            _windowed(
                series.get("wealth_share.top1", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value < 9000,
            failure_share_max=0.05,
        ),
        "unemployment_rate": _failure_summary(
            _windowed(
                series.get("unemployment_rate", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and 50 < value < 6000,
            failure_share_max=0.05,
        ),
        "exchange.zero_trade_streak": _failure_summary(
            _windowed(
                series.get("exchange.zero_trade_streak", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value <= 3,
            failure_share_max=0.05,
        ),
        "active_firms": _failure_summary(
            _windowed(
                series.get("active_firms", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value >= 5,
            failure_share_max=0.05,
        ),
        "agents.zero_transactions_30d_share": _failure_summary(
            _windowed(
                series.get("agents.zero_transactions_30d_share", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value < 5000,
            failure_share_max=0.05,
        ),
    }
    return GateResult(
        id="V3",
        verdict="pass" if all(check["pass"] for check in checks.values()) else "fail",
        statistic=checks,
        threshold={
            "wealth_share.top1_max_bp_exclusive": 9000,
            "unemployment_rate_bp_exclusive": [50, 6000],
            "exchange_zero_trade_streak_max": 3,
            "active_firms_min": 5,
            "agents_zero_transactions_30d_share_max_bp_exclusive": 5000,
            "failure_share_max": 0.05,
        },
        window={"from_tick": 1, "to_tick": last_tick},
        query="Per-series failure shares over stored metric observations for V3.",
    )


def evaluate_v4(
    series: Mapping[str, Sequence[Observation]],
    *,
    last_tick: int,
) -> GateResult:
    checks = {
        "sys.action.entropy_norm": _failure_summary(
            _windowed(
                series.get("sys.action.entropy_norm", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value >= 0.35,
            failure_share_max=0.10,
        ),
        "sys.action.js_divergence_mean": _failure_summary(
            _windowed(
                series.get("sys.action.js_divergence_mean", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value >= 0.10,
            failure_share_max=0.10,
        ),
        "sys.text.distinct3": _failure_summary(
            _windowed(
                series.get("sys.text.distinct3", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value >= 5_500,
            failure_share_max=0.10,
        ),
        "sys.text.embed_cos_mean": _failure_summary(
            _windowed(
                series.get("sys.text.embed_cos_mean", ()),
                from_tick=1,
                to_tick=last_tick,
            ),
            lambda value: math.isfinite(value) and value <= 0.85,
            failure_share_max=0.10,
        ),
    }
    return GateResult(
        id="V4",
        verdict="pass" if all(check["pass"] for check in checks.values()) else "fail",
        statistic=checks,
        threshold={
            "action_entropy_norm_min": 0.35,
            "action_js_divergence_mean_min": 0.10,
            "text_distinct3_min_bp": 5_500,
            "text_embed_cos_mean_max": 0.85,
            "failure_share_max": 0.10,
        },
        window={"from_tick": 1, "to_tick": last_tick},
        query="Per-sub-check behavioural-diversity failure shares over stored metrics.",
    )


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise GateError("percentile requires at least one observation")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bootstrap_ci(
    effects: Sequence[float],
    *,
    metric_id: str,
    resamples: int,
    master_seed: int,
) -> tuple[float, float]:
    rng = RngRegistry(master_seed).get("research.bootstrap", metric_id, 0)
    means = [statistics.fmean(rng.choice(effects) for _ in effects) for _ in range(resamples)]
    return _percentile(means, 0.025), _percentile(means, 0.975)


def _validate_ci(ci: tuple[float, float], *, name: str) -> None:
    if len(ci) != 2 or not all(math.isfinite(value) for value in ci) or ci[0] > ci[1]:
        raise GateError(f"{name}: confidence interval must be finite and ordered")


def _ci_excludes_zero(ci: tuple[float, float]) -> bool:
    return ci[1] < 0 or ci[0] > 0


def evaluate_v5(
    effects_by_seed: Sequence[float],
    *,
    metric_id: str,
    bootstrap_resamples: int = 10_000,
    master_seed: int = 0,
) -> GateResult:
    effects = tuple(float(value) for value in effects_by_seed)
    if any(not math.isfinite(value) for value in effects):
        raise GateError("V5 effects must all be finite")
    if bootstrap_resamples < 1:
        raise GateError("V5 bootstrap_resamples must be positive")
    mean_effect = statistics.fmean(effects) if effects else math.nan
    mean_sign = _sign(mean_effect) if math.isfinite(mean_effect) else 0
    sign_agreement = (
        sum(_sign(value) == mean_sign for value in effects) / len(effects)
        if effects and mean_sign
        else 0.0
    )
    ci = (
        _bootstrap_ci(
            effects,
            metric_id=metric_id,
            resamples=bootstrap_resamples,
            master_seed=master_seed,
        )
        if effects
        else (math.nan, math.nan)
    )
    standard_deviation = statistics.stdev(effects) if len(effects) >= 2 else math.nan
    between_seed_cv = (
        standard_deviation / abs(mean_effect)
        if math.isfinite(standard_deviation) and mean_effect != 0
        else math.inf
    )
    checks = {
        "minimum_seeds": len(effects) >= 20,
        "sign_agreement": sign_agreement >= 0.80,
        "ci_excludes_zero": all(math.isfinite(value) for value in ci) and _ci_excludes_zero(ci),
        "between_seed_cv": between_seed_cv > 0.01,
    }
    notes = (
        "between_seed_cv <= 0.01; likely missing tick or entity_id in an RNG namespace."
        if math.isfinite(between_seed_cv) and between_seed_cv <= 0.01
        else ""
    )
    return GateResult(
        id="V5",
        verdict="pass" if all(checks.values()) else "fail",
        statistic={
            "metric_id": metric_id,
            "seeds": len(effects),
            "mean_effect": round(mean_effect, 12) if math.isfinite(mean_effect) else None,
            "sign_agreement": round(sign_agreement, 8),
            "ci_95": [round(value, 12) if math.isfinite(value) else None for value in ci],
            "between_seed_cv": (
                round(between_seed_cv, 8) if math.isfinite(between_seed_cv) else None
            ),
            "bootstrap_resamples": bootstrap_resamples,
            "checks": checks,
        },
        threshold={
            "seeds_min": 20,
            "sign_agreement_min": 0.80,
            "ci_excludes_zero": True,
            "between_seed_cv_min_exclusive": 0.01,
        },
        window=None,
        query=(
            "Seed-paired headline effects with deterministic percentile bootstrap "
            "namespace research.bootstrap."
        ),
        notes=notes,
    )


def _single_version(versions: Sequence[str], *, label: str) -> str:
    normalized = sorted({value.strip() for value in versions if value.strip()})
    if len(normalized) != 1:
        rendered = ", ".join(normalized) if normalized else "none"
        raise GateError(f"{label}: refuses to pool model_versions; observed {rendered}")
    return normalized[0]


def evaluate_v6(
    *,
    base_effect: float,
    base_ci: tuple[float, float],
    paraphrase_effect: float,
    paraphrase_ci: tuple[float, float],
    base_model_versions: Sequence[str],
    paraphrase_model_versions: Sequence[str],
) -> GateResult:
    if not math.isfinite(base_effect) or not math.isfinite(paraphrase_effect):
        raise GateError("V6 effects must be finite")
    _validate_ci(base_ci, name="base")
    _validate_ci(paraphrase_ci, name="paraphrase")
    base_version = _single_version(base_model_versions, label="base")
    paraphrase_version = _single_version(
        paraphrase_model_versions,
        label="paraphrase",
    )
    checks = {
        "same_sign": _sign(base_effect) != 0 and _sign(base_effect) == _sign(paraphrase_effect),
        "confidence_intervals_overlap": max(base_ci[0], paraphrase_ci[0])
        <= min(base_ci[1], paraphrase_ci[1]),
        "paraphrase_significant_when_base_is": not _ci_excludes_zero(base_ci)
        or _ci_excludes_zero(paraphrase_ci),
    }
    ratio = paraphrase_effect / base_effect if base_effect != 0 else math.nan
    return GateResult(
        id="V6",
        verdict="pass" if all(checks.values()) else "fail",
        statistic={
            "base_effect": base_effect,
            "paraphrase_effect": paraphrase_effect,
            "paraphrase_base_ratio": ratio if math.isfinite(ratio) else None,
            "base_ci": list(base_ci),
            "paraphrase_ci": list(paraphrase_ci),
            "base_model_version": base_version,
            "paraphrase_model_version": paraphrase_version,
            "checks": checks,
        },
        threshold={
            "same_sign": True,
            "confidence_intervals_overlap": True,
            "preserve_exclusion_of_zero": True,
        },
        window=None,
        query="Pre-registered base and paraphrase headline cells over seed-paired effects.",
    )


def evaluate_v7(families: Mapping[str, ModelFamilyEvidence]) -> GateResult:
    if len(families) < 2:
        raise GateError("V7 requires at least two model families")
    normalized: dict[str, ModelFamilyEvidence] = {}
    for family, evidence in sorted(families.items()):
        if not family.strip():
            raise GateError("V7 model family names must not be empty")
        if not all(
            math.isfinite(value)
            for value in (
                evidence.effect,
                evidence.parse_failure_rate_bp,
                evidence.sim_awareness_rate_bp,
                evidence.cost_usd,
            )
        ):
            raise GateError(f"{family}: V7 evidence must be finite")
        if not 0 <= evidence.parse_failure_rate_bp <= 10_000:
            raise GateError(f"{family}: parse_failure_rate_bp must be in [0,10000]")
        if not 0 <= evidence.sim_awareness_rate_bp <= 10_000:
            raise GateError(f"{family}: sim_awareness_rate_bp must be in [0,10000]")
        if evidence.cost_usd < 0:
            raise GateError(f"{family}: cost_usd must not be negative")
        _validate_ci(evidence.ci, name=family)
        _single_version(evidence.model_versions, label=family)
        normalized[family] = evidence

    signs = {_sign(evidence.effect) for evidence in normalized.values()}
    common_lower = max(evidence.ci[0] for evidence in normalized.values())
    common_upper = min(evidence.ci[1] for evidence in normalized.values())
    any_exclude_zero = any(_ci_excludes_zero(evidence.ci) for evidence in normalized.values())
    all_exclude_zero = all(_ci_excludes_zero(evidence.ci) for evidence in normalized.values())
    checks = {
        "model_families": len(normalized) >= 2,
        "seeds_per_family": all(evidence.seeds >= 10 for evidence in normalized.values()),
        "same_nonzero_sign": len(signs) == 1 and 0 not in signs,
        "confidence_intervals_overlap": common_lower <= common_upper,
        "preserve_exclusion_of_zero": not any_exclude_zero or all_exclude_zero,
        "parse_failure_rate": all(
            evidence.parse_failure_rate_bp < 500 for evidence in normalized.values()
        ),
    }
    return GateResult(
        id="V7",
        verdict="pass" if all(checks.values()) else "fail",
        statistic={
            "families": {
                family: {
                    "effect": evidence.effect,
                    "ci": list(evidence.ci),
                    "parse_failure_rate_bp": evidence.parse_failure_rate_bp,
                    "sim_awareness_rate_bp": evidence.sim_awareness_rate_bp,
                    "cost_usd": evidence.cost_usd,
                    "seeds": evidence.seeds,
                    "model_version": _single_version(
                        evidence.model_versions,
                        label=family,
                    ),
                }
                for family, evidence in normalized.items()
            },
            "common_ci_intersection": [common_lower, common_upper],
            "checks": checks,
        },
        threshold={
            "model_families_min": 2,
            "seeds_per_family_min": 10,
            "same_sign": True,
            "confidence_intervals_overlap": True,
            "parse_failure_rate_bp_max_exclusive": 500,
            "mixed_model_versions": False,
        },
        window=None,
        query="Pre-registered headline effects grouped by model family and exact model version.",
    )


def evaluate_v8(
    miss_rates: Mapping[str, float],
    *,
    external_miss_rate_max: float,
    run_tags: Sequence[str] = (),
) -> GateResult:
    if not 0 <= external_miss_rate_max <= 1:
        raise GateError("external_miss_rate_max must be in [0,1]")
    normalized: dict[str, float] = {}
    for agent_id, value in sorted(miss_rates.items()):
        numeric = float(value)
        if not agent_id.strip() or not math.isfinite(numeric) or not 0 <= numeric <= 1:
            raise GateError(f"invalid V8 miss rate for {agent_id!r}")
        normalized[agent_id] = numeric
    offending = {
        agent_id: value for agent_id, value in normalized.items() if value > external_miss_rate_max
    }
    tag_present = "invalid_for_cross_agent_comparison" in set(run_tags)
    return GateResult(
        id="V8",
        verdict="fail" if offending else "pass",
        statistic={
            "miss_rates": normalized,
            "offending_agent_ids": sorted(offending),
            "maximum_miss_rate": max(normalized.values(), default=0.0),
            "invalidation_tag_present": tag_present,
        },
        threshold={
            "external_miss_rate_max": external_miss_rate_max,
            "comparison": "strictly_greater_than",
        },
        window=None,
        query=(
            "external_agents.deadlines_missed / ticks_driven for operator-driven "
            "agents with ticks_driven > 0."
        ),
        notes=(
            "V8 breached but invalid_for_cross_agent_comparison tag is missing."
            if offending and not tag_present
            else ""
        ),
    )


def run_gate_results(
    *,
    v1: GateResult,
    v2: GateResult,
    v3: GateResult,
    v4: GateResult,
) -> tuple[GateResult, ...]:
    if v2.id != "V2":
        raise GateError("run gate suppression requires a V2 result")
    if v2.verdict == "fail":
        return (v2,)
    return (v1, v2, v3, v4)


def gate_report(
    results: Sequence[GateResult],
    *,
    run_id: UUID,
    code_git_sha: str,
    invariants: Mapping[str, Any],
) -> dict[str, Any]:
    ordered = sorted(results, key=lambda result: int(result.id[1:]))
    blocking = [result.id for result in ordered if result.verdict == "fail"]
    return {
        "run_id": str(run_id),
        "code_git_sha": code_git_sha,
        "gates": [result.as_dict() for result in ordered],
        "invariants": {key: invariants[key] for key in sorted(invariants)},
        "verdict": "fail" if blocking else "pass",
        "blocking_failures": blocking,
    }


def gate_report_bytes(report: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


async def gate_arena(db: Database, run_id: UUID) -> GateResult:
    rows = await db.fetch(
        "SELECT tags FROM runs WHERE run_id=%s",
        (run_id,),
    )
    if not rows:
        raise GateError(f"run not found: {run_id}")
    settings = await load_run_settings(db, run_id)
    agents = await db.fetch(
        """
        SELECT agent_id,deadlines_missed,ticks_driven
        FROM external_agents
        WHERE run_id=%s AND ticks_driven>0
        ORDER BY agent_id
        """,
        (run_id,),
    )
    miss_rates = {
        str(row["agent_id"]): int(row["deadlines_missed"]) / int(row["ticks_driven"])
        for row in agents
    }
    return evaluate_v8(
        miss_rates,
        external_miss_rate_max=settings.research.gates.external_miss_rate_max,
        run_tags=tuple(str(value) for value in (rows[0]["tags"] or ())),
    )


async def gate_run(db: Database, run_id: UUID) -> Mapping[str, Any]:
    run_rows = await db.fetch(
        "SELECT name,status,last_tick,code_git_sha FROM runs WHERE run_id=%s",
        (run_id,),
    )
    if not run_rows:
        raise GateError(f"run not found: {run_id}")
    run = run_rows[0]
    missing = [field for field in ("last_tick", "code_git_sha") if run[field] is None]
    if missing:
        raise GateError(
            f"run {run['name']!r} has status {run['status']!r} and missing {', '.join(missing)}"
        )
    last_tick = int(run["last_tick"])
    settings = await load_run_settings(db, run_id)
    metric_ids = (
        "cpi",
        "market_index",
        "unemployment_rate",
        "gdp_real",
        "wealth_share.top1",
        "exchange.zero_trade_streak",
        "active_firms",
        "agents.zero_transactions_30d_share",
        "sys.action.entropy_norm",
        "sys.action.js_divergence_mean",
        "sys.text.distinct3",
        "sys.text.embed_cos_mean",
    )
    metric_rows = await db.fetch(
        """
        SELECT metric,tick,value FROM metrics
        WHERE run_id=%s AND metric=ANY(%s)
        ORDER BY metric,tick
        """,
        (run_id, list(metric_ids)),
    )
    series: dict[str, list[Observation]] = {metric_id: [] for metric_id in metric_ids}
    for row in metric_rows:
        series[str(row["metric"])].append((int(row["tick"]), float(row["value"])))

    contaminated_event_rows = await db.fetch(
        """
        WITH RECURSIVE contaminated AS (
            SELECT seq,tick,kind,cause_seq
            FROM events
            WHERE run_id=%s AND kind BETWEEN 99000 AND 99999
          UNION
            SELECT child.seq,child.tick,child.kind,child.cause_seq
            FROM events child
            JOIN contaminated parent ON child.cause_seq=parent.seq
            WHERE child.run_id=%s
        )
        SELECT seq,tick,kind,cause_seq FROM contaminated ORDER BY seq
        """,
        (run_id, run_id),
    )
    lineage = tuple(
        EventLineage(
            seq=int(row["seq"]),
            tick=int(row["tick"]),
            kind=int(row["kind"]),
            cause_seq=int(row["cause_seq"]) if row["cause_seq"] is not None else None,
        )
        for row in contaminated_event_rows
    )
    invariant_rows = await db.fetch(
        """
        SELECT payload->>'invariant_id' AS invariant_id,COUNT(*)::bigint AS violations
        FROM events WHERE run_id=%s AND kind=%s
          AND payload->>'invariant_id' IS NOT NULL
        GROUP BY payload->>'invariant_id'
        ORDER BY payload->>'invariant_id'
        """,
        (run_id, INVARIANT_VIOLATED),
    )
    invariant_violations = {
        str(row["invariant_id"]): int(row["violations"]) for row in invariant_rows
    }
    completed_rows = await db.fetch(
        "SELECT COUNT(DISTINCT tick)::bigint AS ticks FROM events WHERE run_id=%s AND kind=%s",
        (run_id, TICK_COMPLETED),
    )
    ticks_checked = int(completed_rows[0]["ticks"])
    violating_rows = await db.fetch(
        """
        SELECT tick FROM ledger_entries WHERE run_id=%s
        GROUP BY tick HAVING SUM(direction*amount_cents)<>0
        ORDER BY tick
        """,
        (run_id,),
    )
    final_rows = await db.fetch(
        """
        WITH entry_balances AS (
            SELECT account_id,SUM(direction*amount_cents)::bigint AS entry_balance
            FROM ledger_entries WHERE run_id=%s GROUP BY account_id
        )
        SELECT
          COALESCE(SUM(a.balance_cents),0)::bigint AS global,
          COALESCE(SUM(ABS(a.balance_cents-COALESCE(e.entry_balance,0))),0)::bigint
            AS materialisation
        FROM ledger_accounts a LEFT JOIN entry_balances e USING(account_id)
        WHERE a.run_id=%s
        """,
        (run_id, run_id),
    )
    final_ledger_checks = {
        "global": int(final_rows[0]["global"]),
        "materialisation": int(final_rows[0]["materialisation"]),
    }
    v1 = evaluate_v1(
        series,
        ticks_per_year=(settings.clock.ticks_per_sim_day * settings.clock.days_per_sim_year),
        last_tick=last_tick,
        event_lineage=lineage,
    )
    v2 = evaluate_v2(
        last_tick=last_tick,
        ticks_checked=ticks_checked,
        invariant_violations=invariant_violations,
        posthoc_violating_ticks=tuple(int(row["tick"]) for row in violating_rows),
        final_ledger_checks=final_ledger_checks,
    )
    v3 = evaluate_v3(series, last_tick=last_tick)
    v4 = evaluate_v4(series, last_tick=last_tick)
    invariants = {
        invariant_id: {
            "violations": count,
            "ticks_checked": ticks_checked,
        }
        for invariant_id, count in invariant_violations.items()
    }
    for invariant_id in ("INV-MONEY", "INV-LEDGER"):
        invariants.setdefault(
            invariant_id,
            {"violations": 0, "ticks_checked": ticks_checked},
        )
    return gate_report(
        run_gate_results(v1=v1, v2=v2, v3=v3, v4=v4),
        run_id=run_id,
        code_git_sha=str(run["code_git_sha"]),
        invariants=invariants,
    )
