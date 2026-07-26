from __future__ import annotations

import math

from polis.research.gates import evaluate_v1, evaluate_v2, evaluate_v3


def test_v1_passes_bounded_cyclical_five_year_series() -> None:
    year = 360
    days = range(1, 5 * year + 1)
    levels = [(tick, 10_000 * math.exp(0.04 * math.sin(2 * math.pi * tick / 240))) for tick in days]
    unemployment = [(tick, 1000.0 + 50 * math.sin(2 * math.pi * tick / year)) for tick in days]
    gdp = [
        (tick, 1_000_000 * math.exp(0.03 * math.sin(2 * math.pi * tick / year)))
        for tick in range(year // 4, 5 * year + 1, year // 4)
    ]

    result = evaluate_v1(
        {
            "cpi": levels,
            "market_index": levels,
            "unemployment_rate": unemployment,
            "gdp_real": gdp,
        },
        ticks_per_year=year,
        last_tick=5 * year,
    )

    assert result.verdict == "pass"
    assert result.statistic["cpi"]["annual_difference_sign_changes"] >= 4


def test_v1_fails_when_a_required_series_does_not_cover_the_window() -> None:
    year = 360
    constant = [(tick, 10_000.0) for tick in range(1, 5 * year + 1)]

    result = evaluate_v1(
        {
            "cpi": constant,
            "market_index": constant[:-1],
            "unemployment_rate": [(tick, 1000.0) for tick, _ in constant],
            "gdp_real": [(tick, 1_000_000.0) for tick in range(90, 1801, 90)],
        },
        ticks_per_year=year,
        last_tick=5 * year,
    )

    assert result.verdict == "fail"
    assert result.statistic["market_index"]["covered"] is False


def test_v2_requires_runtime_posthoc_and_tick_coverage() -> None:
    passed = evaluate_v2(
        last_tick=1800,
        ticks_checked=1801,
        invariant_violations={},
        posthoc_violating_ticks=(),
        final_ledger_checks={"global": 0, "materialisation": 0, "base_money": 0, "deposits": 0},
    )
    failed = evaluate_v2(
        last_tick=1800,
        ticks_checked=1800,
        invariant_violations={"INV-MONEY": 1},
        posthoc_violating_ticks=(42,),
        final_ledger_checks={"global": 0},
    )

    assert passed.verdict == "pass"
    assert failed.verdict == "fail"


def test_v3_allows_at_most_five_percent_failures() -> None:
    observations = [(tick, 1000.0) for tick in range(1, 21)]
    one_failure = [*observations[:-1], (20, 9500.0)]
    result = evaluate_v3(
        {
            "wealth_share.top1": one_failure,
            "unemployment_rate": observations,
            "exchange.zero_trade_streak": [(tick, 0.0) for tick, _ in observations],
            "active_firms": [(tick, 5.0) for tick, _ in observations],
            "agents.zero_transactions_30d_share": observations,
        },
        last_tick=20,
    )

    assert result.verdict == "pass"
    assert result.statistic["wealth_share.top1"]["failure_share"] == 0.05


def test_v3_fails_missing_or_degenerate_series() -> None:
    result = evaluate_v3(
        {
            "wealth_share.top1": [(1, math.nan)],
            "unemployment_rate": [(1, 10_000.0)],
            "active_firms": [(1, 4.0)],
        },
        last_tick=1,
    )

    assert result.verdict == "fail"
    assert result.statistic["exchange.zero_trade_streak"]["evaluations"] == 0
