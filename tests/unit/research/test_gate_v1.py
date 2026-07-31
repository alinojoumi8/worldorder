from __future__ import annotations

import math

from polis.research.gates import EventLineage, evaluate_v1, longest_shock_free_window


def _v1_series(last_tick: int, year: int) -> dict[str, list[tuple[int, float]]]:
    levels = [
        (tick, 10_000 * math.exp(0.04 * math.sin(2 * math.pi * tick / 7)))
        for tick in range(1, last_tick + 1)
    ]
    return {
        "cpi": levels,
        "market_index": levels,
        "unemployment_rate": [
            (tick, 1_000 + 40 * math.sin(2 * math.pi * tick / year))
            for tick in range(1, last_tick + 1)
        ],
        "gdp_real": [
            (tick, 1_000_000 * math.exp(0.02 * math.sin(2 * math.pi * tick / year)))
            for tick in range(year, last_tick + 1, year)
        ],
    }


def test_v1_uses_longest_window_after_walking_research_ancestry() -> None:
    lineage = (
        EventLineage(seq=10, tick=20, kind=99_000),
        EventLineage(seq=11, tick=20, kind=6_001, cause_seq=10),
        EventLineage(seq=12, tick=21, kind=6_002, cause_seq=11),
        EventLineage(seq=13, tick=40, kind=99_071),
    )

    window = longest_shock_free_window(lineage, last_tick=80)
    result = evaluate_v1(
        _v1_series(80, 10),
        ticks_per_year=10,
        last_tick=80,
        event_lineage=lineage,
    )

    assert window == {"from_tick": 22, "to_tick": 80}
    assert result.window == window
    assert result.shock_free
    assert result.verdict == "pass"


def test_v1_returns_na_when_no_shock_free_window_reaches_five_years() -> None:
    lineage = (
        EventLineage(seq=1, tick=10, kind=99_000),
        EventLineage(seq=2, tick=11, kind=6_001, cause_seq=1),
    )

    result = evaluate_v1(
        _v1_series(60, 10),
        ticks_per_year=10,
        last_tick=60,
        event_lineage=lineage,
    )

    assert result.verdict == "n/a"
    assert result.window == {"from_tick": 12, "to_tick": 60}
    assert result.statistic == {"required_ticks": 50, "observed_ticks": 49}


def test_v1_marks_an_all_contaminated_fallback_as_not_shock_free() -> None:
    lineage = (
        EventLineage(seq=1, tick=0, kind=99_001),
        EventLineage(seq=2, tick=1, kind=2_001, cause_seq=1),
        EventLineage(seq=3, tick=2, kind=2_001, cause_seq=2),
        EventLineage(seq=4, tick=3, kind=2_001, cause_seq=3),
        EventLineage(seq=5, tick=4, kind=2_001, cause_seq=4),
        EventLineage(seq=6, tick=5, kind=2_001, cause_seq=5),
    )

    result = evaluate_v1(
        _v1_series(5, 1),
        ticks_per_year=1,
        last_tick=5,
        event_lineage=lineage,
    )

    assert result.verdict == "n/a"
    assert result.statistic == {"required_ticks": 5, "observed_ticks": 0}
    assert result.shock_free is False
