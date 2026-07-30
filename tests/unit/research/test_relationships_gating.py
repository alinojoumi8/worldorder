from __future__ import annotations

from collections.abc import Callable

import pytest

from polis.research.relationships import (
    RelationshipError,
    RelationshipEvidence,
    RelationshipPreconditionError,
    RelationshipResult,
    beveridge,
    business_cycle_autocorrelation,
    okun,
    phillips,
    zipf,
)

RelationshipCall = Callable[[RelationshipEvidence], RelationshipResult]
COMMON_EVIDENCE = RelationshipEvidence(
    reflex_only_baseline_complete=True,
    mechanism_checklist_complete=True,
)


@pytest.mark.parametrize(
    ("call", "evidence", "missing"),
    [
        (
            lambda evidence: beveridge(
                [(1, 1200), (2, 1100), (3, 1000)],
                [(1, 300), (2, 400), (3, 500)],
                evidence=evidence,
            ),
            COMMON_EVIDENCE,
            "Beveridge falsification protocol",
        ),
        (
            lambda evidence: okun(
                [(1, 100), (2, 110), (3, 121)],
                [(1, 1000), (2, 900), (3, 800)],
                evidence=evidence,
            ),
            RelationshipEvidence(mechanism_checklist_complete=True),
            "reflex-only baseline",
        ),
        (
            lambda evidence: phillips(
                [(1, 1000), (2, 900), (3, 800)],
                [(1, 100), (2, 200), (3, 300)],
                evidence=evidence,
            ),
            RelationshipEvidence(reflex_only_baseline_complete=True),
            "completed MECHANISM checklist",
        ),
        (
            lambda evidence: zipf(
                [(1, 10_000), (2, 10_200)],
                evidence=evidence,
            ),
            COMMON_EVIDENCE,
            "genesis firm-size tail exponent",
        ),
        (
            lambda evidence: business_cycle_autocorrelation(
                [(1, 100), (2, 110), (3, 115), (4, 125)],
                evidence=evidence,
            ),
            RelationshipEvidence(mechanism_checklist_complete=True),
            "reflex-only baseline",
        ),
    ],
)
def test_relationships_refuse_and_name_missing_prerequisite(
    call: RelationshipCall,
    evidence: RelationshipEvidence,
    missing: str,
) -> None:
    with pytest.raises(RelationshipPreconditionError, match=missing):
        call(evidence)


def test_relationships_compute_only_after_required_evidence() -> None:
    full = RelationshipEvidence(
        reflex_only_baseline_complete=True,
        mechanism_checklist_complete=True,
        beveridge_falsification_complete=True,
        zipf_genesis_exponent_bp=10_000,
        zipf_exponent_moved=True,
    )

    beveridge_result = beveridge(
        [(1, 1200), (2, 1100), (3, 1000)],
        [(1, 300), (2, 400), (3, 500)],
        evidence=full,
    )
    okun_result = okun(
        [(1, 100), (2, 110), (3, 121), (4, 140)],
        [(1, 1200), (2, 1100), (3, 1000), (4, 850)],
        evidence=full,
    )
    phillips_result = phillips(
        [(1, 1200), (2, 1100), (3, 1000)],
        [(1, 100), (2, 200), (3, 300)],
        evidence=full,
    )
    zipf_result = zipf(
        [(1, 10_100), (2, 10_300)],
        evidence=full,
    )
    cycle_result = business_cycle_autocorrelation(
        [(1, 100), (2, 110), (3, 115), (4, 125), (5, 132)],
        evidence=full,
    )

    assert beveridge_result.value == pytest.approx(-1)
    assert okun_result.value < 0
    assert phillips_result.value == pytest.approx(-1)
    assert zipf_result.comparison == {
        "genesis_tail_exponent_bp": 10_000,
        "change_from_genesis_bp": 300,
    }
    assert cycle_result.observations == 3
    assert cycle_result.value == pytest.approx(-0.9996940763010866)


def test_relationship_input_validation_rejects_invalid_evidence_and_ticks() -> None:
    non_finite = RelationshipEvidence(
        reflex_only_baseline_complete=True,
        mechanism_checklist_complete=True,
        zipf_genesis_exponent_bp=float("nan"),
        zipf_exponent_moved=True,
    )
    with pytest.raises(
        RelationshipPreconditionError,
        match="genesis firm-size tail exponent",
    ):
        zipf([(1, 10_000)], evidence=non_finite)

    with pytest.raises(RelationshipError, match="duplicate tick 1"):
        phillips(
            [(1, 1000), (True, 900), (3, 800)],
            [(1, 100), (2, 200), (3, 300)],
            evidence=COMMON_EVIDENCE,
        )
