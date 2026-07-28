from __future__ import annotations

from typing import cast

from polis.events.kinds import ARREST_MADE
from polis.society.law import (
    CourtCase,
    CourtService,
    MemoryCourtRepository,
    MemoryCrimeRepository,
)
from polis.society.polity import OfficeRegister
from tests.law_support import (
    Memories,
    Offices,
    RecordingLawLedger,
    checker,
    clock,
    event,
    law_cfg,
    log,
    runtime,
)


def test_admissibility_routes_exclusions_and_counsel_surfacing() -> None:
    case = CourtCase(
        "ca_one",
        "criminal",
        "government",
        "ag_defendant",
        "cr_one",
        "fraud",
        1_000,
        1,
        evidence_event_seqs=(1, 2, 3, 4, 5, 6),
        plaintiff_counsel_id="ag_lawyer",
        witness_ids=("ag_witness",),
    )
    repo = MemoryCourtRepository()
    repo.add(case)
    rows = (
        event(1, 1, 5001, subjects=("ag_defendant",)),
        event(2, 1, 4001, subjects=("ag_defendant",)),
        event(3, 1, 11010, subjects=("ag_defendant",)),
        event(4, 1, ARREST_MADE, subjects=("ag_defendant",)),
        event(5, 1, 4001, subjects=("ag_other",)),
    )
    event_log = log()
    service = CourtService(
        log=event_log,
        clock=clock(),
        runtime=runtime(),
        repo=repo,
        crimes=MemoryCrimeRepository(),
        ledger=RecordingLawLedger(),
        offices=cast(OfficeRegister, Offices()),
        memories=Memories({("ag_witness", 2)}),
        checker=checker(event_log),
        cfg=law_cfg(),
        events=rows,
        skill_law=lambda _agent_id: 1.0,
    )

    admitted, admitted_event = service.admit_evidence("ca_one", 10)

    assert admitted == (1, 2, 3, 4)
    assert admitted_event.payload["excluded_seqs"] == [5, 6]
    assert admitted_event.payload["excluded_reasons"] == [
        "no_party_subject",
        "missing",
    ]
    assert admitted_event.payload["surfaced_by_counsel"] == 11
    assert repo.get("ca_one").evidence_strength == 1.0  # type: ignore[union-attr]
