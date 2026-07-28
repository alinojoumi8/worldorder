from __future__ import annotations

from typing import cast

from polis.society.law import (
    CourtCase,
    CourtService,
    Crime,
    MemoryCourtRepository,
    MemoryCrimeRepository,
    Range,
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


def _service() -> tuple[CourtService, MemoryCourtRepository, MemoryCrimeRepository]:
    event_log = log()
    cases = MemoryCourtRepository()
    crimes = MemoryCrimeRepository()
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
    )
    return service, cases, crimes


def test_judge_output_is_clamped_and_non_admitted_findings_are_dropped() -> None:
    service, cases, crimes = _service()
    crime = Crime(
        "cr_one",
        "fraud",
        1,
        "ag_defendant",
        "ag_victim",
        50,
        None,
        "di_000",
        "ac_one",
        0.1,
        "derived",
    )
    crimes.add(crime)
    case = CourtCase(
        "ca_one",
        "criminal",
        "government",
        "ag_defendant",
        crime.crime_id,
        "fraud",
        100,
        1,
        admitted_event_seqs=(7,),
    )
    cases.add(case)
    parsed = {
        "verdict": "guilty",
        "findings": ["Event 7 proves the transfer.", "Event 999 is decisive."],
        "penalty": {
            "fine_cents": 999,
            "sentence_ticks": 999,
            "damages_cents": 999,
            "restitution_cents": 999,
            "disqualification_ticks": 5,
        },
    }

    judgment = service._clamp_judgment(
        case,
        Range(10, 20, 30, 40),
        parsed,
        "call_one",
    )

    assert judgment is not None
    assert (
        judgment.fine_cents,
        judgment.sentence_ticks,
        judgment.damages_cents,
        judgment.restitution_cents,
    ) == (20, 40, 100, 50)
    assert judgment.findings == ("Event 7 proves the transfer.",)
    assert set(judgment.clamped) == {
        "fine_cents",
        "sentence_ticks",
        "damages_cents",
        "restitution_cents",
        "finding_non_admitted",
    }


def test_verdict_case_type_mismatch_requires_repair() -> None:
    service, _cases, _crimes = _service()
    civil = CourtCase(
        "ca_civil",
        "civil",
        "ag_plaintiff",
        "ag_defendant",
        None,
        "negligence",
        100,
        1,
    )

    assert (
        service._clamp_judgment(
            civil,
            Range(0, 0, 0, 0),
            {"verdict": "guilty", "findings": [], "penalty": {}},
            "call_one",
        )
        is None
    )
