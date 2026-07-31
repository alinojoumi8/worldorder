from __future__ import annotations

from polis.research.gates import evaluate_v3, evaluate_v4


def test_v3_and_v4_apply_independent_subcheck_tolerances() -> None:
    twenty = [(tick, 1_000.0) for tick in range(1, 21)]
    v3 = evaluate_v3(
        {
            "wealth_share.top1": [(0, 9_500), *twenty[:-1], (20, 9_500), (21, 9_500)],
            "unemployment_rate": twenty,
            "exchange.zero_trade_streak": [(tick, 0) for tick, _ in twenty],
            "active_firms": [(tick, 5) for tick, _ in twenty],
            "agents.zero_transactions_30d_share": twenty,
        },
        last_tick=20,
    )
    v4 = evaluate_v4(
        {
            "sys.action.entropy_norm": [
                (0, 0.10),
                *((tick, 0.50) for tick in range(1, 10)),
                (10, 0.10),
                (11, 0.10),
            ],
            "sys.action.js_divergence_mean": [(tick, 0.20) for tick in range(1, 11)],
            "sys.text.distinct3": [(tick, 6_000) for tick in range(1, 11)],
            "sys.text.embed_cos_mean": [(tick, 0.70) for tick in range(1, 11)],
        },
        last_tick=10,
    )

    assert v3.verdict == "pass"
    assert v3.statistic["wealth_share.top1"]["evaluations"] == 20
    assert v3.statistic["wealth_share.top1"]["failure_share"] == 0.05
    assert v4.verdict == "pass"
    assert v4.statistic["sys.action.entropy_norm"]["evaluations"] == 10
    assert v4.statistic["sys.action.entropy_norm"]["failure_share"] == 0.10
    assert v4.threshold["failure_share_max"] == 0.10


def test_v4_reports_actions_diverse_when_language_collapses() -> None:
    observations = range(1, 11)
    result = evaluate_v4(
        {
            "sys.action.entropy_norm": [(tick, 0.50) for tick in observations],
            "sys.action.js_divergence_mean": [(tick, 0.20) for tick in observations],
            "sys.text.distinct3": [(tick, 2_000) for tick in observations],
            "sys.text.embed_cos_mean": [(tick, 0.95) for tick in observations],
        },
        last_tick=10,
    )

    assert result.verdict == "fail"
    assert result.statistic["sys.action.entropy_norm"]["pass"]
    assert result.statistic["sys.action.js_divergence_mean"]["pass"]
    assert not result.statistic["sys.text.distinct3"]["pass"]
    assert not result.statistic["sys.text.embed_cos_mean"]["pass"]
