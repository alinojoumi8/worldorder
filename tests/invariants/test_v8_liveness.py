from __future__ import annotations

from polis.research.gates import evaluate_v8


def test_v8_fails_only_above_threshold_and_reports_invalidation_tag() -> None:
    at_threshold = evaluate_v8(
        {"agent-a": 0.05},
        external_miss_rate_max=0.05,
        run_tags=(),
    )
    breached = evaluate_v8(
        {"agent-a": 0.051, "agent-b": 0.0},
        external_miss_rate_max=0.05,
        run_tags=("invalid_for_cross_agent_comparison",),
    )

    assert at_threshold.verdict == "pass"
    assert breached.verdict == "fail"
    assert breached.statistic["offending_agent_ids"] == ["agent-a"]
    assert breached.statistic["invalidation_tag_present"] is True
