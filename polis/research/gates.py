from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any, Literal

Observation = tuple[int, float]
Verdict = Literal["pass", "fail", "n/a"]


@dataclass(frozen=True, slots=True)
class GateResult:
    gate_id: str
    verdict: Verdict
    statistic: Mapping[str, Any]
    threshold: Mapping[str, Any]
    window: Mapping[str, int]
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def evaluate_v1(
    series: Mapping[str, Sequence[Observation]],
    *,
    ticks_per_year: int,
    last_tick: int,
    years: int = 5,
) -> GateResult:
    required_ticks = years * ticks_per_year
    from_tick = max(1, last_tick - required_ticks + 1)
    window = {"from_tick": from_tick, "to_tick": last_tick}
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
    if last_tick < required_ticks:
        return GateResult(
            "V1",
            "n/a",
            {"required_ticks": required_ticks, "observed_ticks": last_tick},
            thresholds,
            window,
            "No contiguous five-sim-year window is available.",
        )

    statistics_by_series: dict[str, Any] = {}
    checks: list[bool] = []
    for metric in ("cpi", "market_index"):
        values = _windowed(series.get(metric, ()), from_tick=from_tick, to_tick=last_tick)
        covered = bool(values) and values[0][0] <= from_tick and values[-1][0] >= last_tick
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
        to_tick=last_tick,
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

    gdp = _windowed(series.get("gdp_real", ()), from_tick=from_tick, to_tick=last_tick)
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
        "V1",
        "pass" if all(checks) else "fail",
        statistics_by_series,
        thresholds,
        window,
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
        "V2",
        "pass" if all(checks.values()) else "fail",
        {
            "runtime_violations": relevant_violations,
            "posthoc_violating_ticks": list(posthoc_violating_ticks),
            "final_ledger_checks_cents": dict(final_ledger_checks),
            "ticks_checked": ticks_checked,
            "expected_ticks_checked": last_tick + 1,
            "checks": checks,
        },
        {
            "runtime_violations_max": 0,
            "posthoc_violating_ticks_max": 0,
            "final_ledger_imbalance_cents": 0,
            "ticks_checked": last_tick + 1,
        },
        {"from_tick": 0, "to_tick": last_tick},
    )


def _failure_summary(
    values: Sequence[Observation],
    predicate: Callable[[float], bool],
) -> dict[str, Any]:
    failures = sum(not predicate(value) for _, value in values)
    evaluations = len(values)
    failure_share = failures / evaluations if evaluations else 1.0
    return {
        "evaluations": evaluations,
        "failures": failures if evaluations else 1,
        "failure_share": round(failure_share, 8),
        "pass": evaluations > 0 and failure_share <= 0.05,
    }


def evaluate_v3(
    series: Mapping[str, Sequence[Observation]],
    *,
    last_tick: int,
) -> GateResult:
    checks = {
        "wealth_share.top1": _failure_summary(
            series.get("wealth_share.top1", ()),
            lambda value: math.isfinite(value) and value < 9000,
        ),
        "unemployment_rate": _failure_summary(
            series.get("unemployment_rate", ()),
            lambda value: math.isfinite(value) and 50 < value < 6000,
        ),
        "exchange.zero_trade_streak": _failure_summary(
            series.get("exchange.zero_trade_streak", ()),
            lambda value: math.isfinite(value) and value <= 3,
        ),
        "active_firms": _failure_summary(
            series.get("active_firms", ()),
            lambda value: math.isfinite(value) and value >= 5,
        ),
        "agents.zero_transactions_30d_share": _failure_summary(
            series.get("agents.zero_transactions_30d_share", ()),
            lambda value: math.isfinite(value) and value < 5000,
        ),
    }
    return GateResult(
        "V3",
        "pass" if all(check["pass"] for check in checks.values()) else "fail",
        checks,
        {
            "wealth_share.top1_max_bp_exclusive": 9000,
            "unemployment_rate_bp_exclusive": [50, 6000],
            "exchange_zero_trade_streak_max": 3,
            "active_firms_min": 5,
            "agents_zero_transactions_30d_share_max_bp_exclusive": 5000,
            "failure_share_max": 0.05,
        },
        {"from_tick": 1, "to_tick": last_tick},
    )
