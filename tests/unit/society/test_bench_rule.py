from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from polis.events.kinds import JUDGMENT_RENDERED
from polis.llm.router import LLMRouter
from polis.society.law import (
    CourtCase,
    CourtService,
    Crime,
    MemoryCourtRepository,
    MemoryCrimeRepository,
    Range,
    bench_verdict,
)
from polis.society.polity import OfficeRegister
from tests.law_support import (
    Memories,
    Offices,
    RecordingLawLedger,
    checker,
    clock,
    law_cfg,
    log,
    runtime,
)


def test_bench_rule_is_monotone_in_evidence_strength() -> None:
    outcomes = [bench_verdict(strength, 0.6, 10, 100) for strength in (0.0, 0.59, 0.6, 0.8, 1.0)]

    assert [convicted for convicted, _penalty in outcomes] == [
        False,
        False,
        True,
        True,
        True,
    ]
    assert [penalty for convicted, penalty in outcomes if convicted] == sorted(
        penalty for convicted, penalty in outcomes if convicted
    )


def test_bench_rule_handles_a_one_hundred_percent_threshold() -> None:
    assert bench_verdict(1.0, 1.0, 10, 100) == (True, 10)


class FailingRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, *args: object, **kwargs: object) -> Any:
        del args, kwargs
        self.calls += 1
        return SimpleNamespace(parsed_ok=False, parsed=None, call_id="call_failed")


@pytest.mark.asyncio
async def test_llm_failure_retries_then_uses_the_bench_rule() -> None:
    event_log = log()
    failed = FailingRouter()
    service = CourtService(
        log=event_log,
        clock=clock(),
        runtime=runtime(),
        repo=MemoryCourtRepository(),
        crimes=MemoryCrimeRepository(),
        ledger=RecordingLawLedger(),
        offices=cast(OfficeRegister, Offices()),
        memories=Memories(),
        checker=checker(event_log),
        cfg=law_cfg(),
        router=cast(LLMRouter, failed),
    )
    case = CourtCase(
        "ca_retry",
        "criminal",
        "government",
        "ag_defendant",
        None,
        "perjury",
        0,
        1,
        evidence_strength=0.9,
    )

    judgment = await service._decide(
        case,
        Range(10, 100, 30, 300),
        "ag_judge",
        10,
    )

    assert failed.calls == 3
    assert judgment.origin == "bench"


@pytest.mark.asyncio
async def test_missing_judge_provider_falls_back_to_bench_and_records_nominal() -> None:
    event_log = log()
    cases = MemoryCourtRepository()
    crimes = MemoryCrimeRepository()
    crime = Crime(
        "cr_one",
        "contract_breach",
        1,
        "ag_defendant",
        "ag_plaintiff",
        0,
        None,
        "di_000",
        "ac_one",
        0.0,
        "derived",
    )
    crimes.add(crime)
    cases.add(
        CourtCase(
            "ca_one",
            "criminal",
            "government",
            "ag_defendant",
            crime.crime_id,
            crime.type,
            0,
            1,
            admitted_event_seqs=(7,),
            evidence_strength=0.9,
        )
    )
    service = CourtService(
        log=event_log,
        clock=clock(),
        runtime=runtime(),
        repo=cases,
        crimes=crimes,
        ledger=RecordingLawLedger(),
        offices=cast(OfficeRegister, Offices()),
        memories=Memories(),
        checker=checker(event_log),
        cfg=law_cfg(),
        router=None,
    )

    events = await service.hold_session(10)
    rendered = next(item for item in events if item.kind == JUDGMENT_RENDERED)

    assert rendered.payload["origin"] == "bench"
    assert rendered.payload["verdict"] == "guilty"
    assert rendered.payload["nominal"] is True
    assert service.bench_rule(0.9, "criminal", Range(0, 0, 0, 0)).origin == "bench"
