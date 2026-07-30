from __future__ import annotations

import math
from uuid import UUID

import pytest

from polis.research.gates import (
    GateId,
    GateResult,
    Verdict,
    gate_report,
    gate_report_bytes,
    run_gate_results,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000024")


def _result(gate_id: GateId, verdict: Verdict) -> GateResult:
    return GateResult(
        id=gate_id,
        verdict=verdict,
        statistic={},
        threshold={},
        window=None,
        query="fixture",
    )


def test_v2_failure_suppresses_other_run_gates() -> None:
    v1 = _result("V1", "pass")
    v2 = _result("V2", "fail")
    v3 = _result("V3", "pass")
    v4 = _result("V4", "pass")

    assert run_gate_results(v1=v1, v2=v2, v3=v3, v4=v4) == (v2,)


def test_gate_report_is_byte_identical_for_same_run_and_code_sha() -> None:
    results = (_result("V1", "pass"), _result("V2", "pass"))
    first = gate_report(
        results,
        run_id=RUN_ID,
        code_git_sha="a" * 40,
        invariants={"INV-MONEY": {"violations": 0, "ticks_checked": 10}},
    )
    second = gate_report(
        tuple(reversed(results)),
        run_id=RUN_ID,
        code_git_sha="a" * 40,
        invariants={"INV-MONEY": {"ticks_checked": 10, "violations": 0}},
    )

    assert gate_report_bytes(first) == gate_report_bytes(second)
    assert b"evaluated_at" not in gate_report_bytes(first)


def test_gate_report_rejects_non_finite_json_numbers() -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        gate_report_bytes({"statistic": {"value": math.nan}})
