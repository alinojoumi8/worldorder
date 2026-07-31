from __future__ import annotations

from dataclasses import replace

import pytest

from polis.research.gates import (
    GateError,
    ModelFamilyEvidence,
    evaluate_v5,
    evaluate_v6,
    evaluate_v7,
)


def test_v5_is_deterministic_and_detects_the_rng_namespace_cv_trap() -> None:
    effects = [1.0 + index * 0.05 for index in range(20)]
    first = evaluate_v5(
        effects,
        metric_id="polarisation.index",
        bootstrap_resamples=200,
    )
    second = evaluate_v5(
        effects,
        metric_id="polarisation.index",
        bootstrap_resamples=200,
    )
    trapped = evaluate_v5(
        [1.0] * 20,
        metric_id="polarisation.index",
        bootstrap_resamples=200,
    )

    assert first == second
    assert first.verdict == "pass"
    assert trapped.verdict == "fail"
    assert "RNG namespace" in trapped.notes


def test_v6_applies_sign_ci_rules_and_refuses_mixed_versions() -> None:
    passed = evaluate_v6(
        base_effect=1.0,
        base_ci=(0.5, 1.5),
        paraphrase_effect=0.9,
        paraphrase_ci=(0.4, 1.4),
        base_model_versions=("v1",),
        paraphrase_model_versions=("v1",),
    )

    assert passed.verdict == "pass"
    with pytest.raises(GateError, match="refuses to pool model_versions"):
        evaluate_v6(
            base_effect=1.0,
            base_ci=(0.5, 1.5),
            paraphrase_effect=0.9,
            paraphrase_ci=(0.4, 1.4),
            base_model_versions=("v1", "v2"),
            paraphrase_model_versions=("v1",),
        )


def test_v7_checks_family_count_seeds_parse_failures_and_versions() -> None:
    evidence = {
        "family-a": ModelFamilyEvidence(
            effect=1.0,
            ci=(0.5, 1.5),
            parse_failure_rate_bp=100,
            sim_awareness_rate_bp=50,
            cost_usd=5,
            seeds=10,
            model_versions=("a1",),
        ),
        "family-b": ModelFamilyEvidence(
            effect=1.2,
            ci=(0.7, 1.6),
            parse_failure_rate_bp=200,
            sim_awareness_rate_bp=60,
            cost_usd=6,
            seeds=12,
            model_versions=("b1",),
        ),
    }

    assert evaluate_v7(evidence).verdict == "pass"
    with pytest.raises(GateError, match="refuses to pool model_versions"):
        evaluate_v7(
            {
                **evidence,
                "family-b": replace(
                    evidence["family-b"],
                    model_versions=("b1", "b2"),
                ),
            }
        )
    with pytest.raises(GateError, match="parse_failure_rate_bp"):
        evaluate_v7(
            {
                **evidence,
                "family-b": replace(
                    evidence["family-b"],
                    parse_failure_rate_bp=-1,
                ),
            }
        )


def test_v7_preserves_significance_without_an_arbitrary_reference_family() -> None:
    common = {
        "a-includes-zero": ModelFamilyEvidence(
            effect=1.0,
            ci=(-0.1, 1.5),
            parse_failure_rate_bp=100,
            sim_awareness_rate_bp=50,
            cost_usd=5,
            seeds=10,
            model_versions=("a1",),
        ),
        "b-excludes-zero": ModelFamilyEvidence(
            effect=1.2,
            ci=(0.1, 1.6),
            parse_failure_rate_bp=200,
            sim_awareness_rate_bp=60,
            cost_usd=6,
            seeds=12,
            model_versions=("b1",),
        ),
    }

    result = evaluate_v7(common)

    assert result.verdict == "fail"
    assert result.statistic["checks"]["preserve_exclusion_of_zero"] is False
