from __future__ import annotations

from typing import cast

import pytest

from polis.events.kinds import (
    ARREST_MADE,
    COUNSEL_RETAINED,
    EVIDENCE_ADMITTED,
    INCARCERATION_STARTED,
    INVESTIGATION_CLOSED,
    INVESTIGATION_OPENED,
    JUDGMENT_RENDERED,
    SUIT_FILED,
    TRIAL_HELD,
)
from polis.society.law import (
    CourtService,
    Crime,
    Incarceration,
    MemoryCourtRepository,
    MemoryCrimeRepository,
    PenaltyService,
    PoliceService,
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
    world,
)


@pytest.mark.asyncio
async def test_crime_to_investigation_court_penalty_and_custody_pipeline() -> None:
    event_log = log()
    configured_clock = clock()
    configured_world = world()
    configured_runtime = runtime()
    cfg = law_cfg(charge_threshold=0.45)
    crimes = MemoryCrimeRepository()
    crime = Crime(
        "cr_one",
        "fraud",
        1,
        "ag_defendant",
        "ag_victim",
        1_000,
        None,
        "ds_00",
        "ac_one",
        0.0,
        "derived",
        detected=True,
        detected_tick=2,
        committed_event_seq=1,
    )
    crimes.add(crime)
    evidence = tuple(
        event(
            seq,
            1,
            5001,
            subjects=("ag_defendant", "ag_victim"),
        )
        for seq in range(2, 6)
    )
    cases = MemoryCourtRepository()
    ledger = RecordingLawLedger(
        {
            "government": 10_000,
            "ag_defendant": 5_000,
            "ag_lawyer": 0,
            "ag_victim": 0,
        }
    )
    court = CourtService(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        repo=cases,
        crimes=crimes,
        ledger=ledger,
        offices=cast(OfficeRegister, Offices()),
        memories=Memories(),
        checker=checker(event_log),
        cfg=cfg,
        events=evidence,
        wealth_percentile=lambda _agent_id: 0.0,
        available_lawyers=lambda _tick: (("ag_lawyer", 200, 0.8),),
        skill_law=lambda _agent_id: 0.8,
    )
    incarceration = Incarceration(
        log=event_log,
        clock=configured_clock,
        world=configured_world,
        runtime=configured_runtime,
        cfg=cfg,
    )
    penalties = PenaltyService(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        ledger=ledger,
        cases=cases,
        crimes=crimes,
        cfg=cfg,
        incarceration=incarceration,
    )
    court.penalties = penalties
    police = PoliceService(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        repo=crimes,
        world=configured_world,
        cfg=cfg,
        events=evidence,
        criminal_filer=court.file_criminal,
    )

    investigation_events = police.process_queue(10)
    court_events = await court.hold_session(11)
    kinds = [item.kind for item in (*investigation_events, *court_events)]

    assert kinds[:4] == [
        INVESTIGATION_OPENED,
        INVESTIGATION_CLOSED,
        ARREST_MADE,
        SUIT_FILED,
    ]
    assert COUNSEL_RETAINED in kinds
    assert EVIDENCE_ADMITTED in kinds
    assert TRIAL_HELD in kinds
    assert JUDGMENT_RENDERED in kinds
    assert INCARCERATION_STARTED in kinds
    judgment = next(item for item in court_events if item.kind == JUDGMENT_RENDERED)
    assert judgment.payload["origin"] == "bench"
    assert judgment.payload["verdict"] == "guilty"
    assert cases.get(str(judgment.payload["case_id"])).status == "resolved"  # type: ignore[union-attr]
