from __future__ import annotations

from dataclasses import replace

import pytest

from polis.agents.actions.params.law import ReportCrimeParams
from polis.events.kinds import (
    ARREST_MADE,
    CRIME_REPORTED,
    INVESTIGATION_CLOSED,
    INVESTIGATION_OPENED,
    TESTIMONY_GIVEN,
)
from polis.society.law import Crime, MemoryCrimeRepository, PoliceService
from tests.law_support import clock, event, law_cfg, log, runtime, world


def _crime(
    crime_id: str,
    crime_type: str,
    *,
    amount_cents: int,
) -> Crime:
    return Crime(
        crime_id=crime_id,
        type=crime_type,  # type: ignore[arg-type]
        tick=1,
        perpetrator_id=f"ag_{crime_id}",
        victim_id="ag_victim",
        amount_cents=amount_cents,
        place_id=None,
        district_id="di_000",
        source_action_id=f"ac_{crime_id}",
        concealment=0.0,
        path="derived",
        detected=True,
        detected_tick=2,
        committed_event_seq=1,
    )


def test_evidence_strength_uses_directness_and_corroboration() -> None:
    crime = _crime("one", "fraud", amount_cents=10)
    rows = (
        event(2, 1, 5001, subjects=(crime.perpetrator_id, crime.victim_id or "")),
        event(
            3,
            1,
            TESTIMONY_GIVEN,
            subjects=(crime.perpetrator_id,),
        ),
    )
    service = PoliceService(
        log=log(),
        clock=clock(),
        runtime=runtime(),
        repo=MemoryCrimeRepository(),
        world=world(),
        cfg=law_cfg(),
        events=rows,
    )

    seqs, strength = service.evidence(crime, 10)
    assert seqs == (2, 3)
    assert strength == pytest.approx((1.0 + 0.6) * 1.2 / 6.0)


def test_queue_is_severity_ordered_budget_bounded_and_charges_at_threshold() -> None:
    repo = MemoryCrimeRepository()
    lower = _crime("lower", "theft", amount_cents=10)
    higher = _crime("higher", "assault", amount_cents=10)
    repo.add(lower)
    repo.add(higher)
    evidence = (
        event(
            2,
            1,
            5001,
            subjects=(higher.perpetrator_id, higher.victim_id or ""),
        ),
    )
    filed: list[str] = []
    service = PoliceService(
        log=log(),
        clock=clock(),
        runtime=runtime(),
        repo=repo,
        world=world(),
        cfg=law_cfg(cost_per_investigation_cents=5_000_000, charge_threshold=0.1),
        events=evidence,
        criminal_filer=lambda crime, _seqs, _strength, _tick: filed.append(crime.crime_id) or (),
    )

    events = service.process_queue(10)
    assert service.investigation_slots(10) == 1
    assert [item.kind for item in events] == [
        INVESTIGATION_OPENED,
        INVESTIGATION_CLOSED,
        ARREST_MADE,
    ]
    assert events[0].payload["crime_id"] == "higher"
    assert events[0].payload["queue_position"] == 1
    assert filed == ["higher"]


def test_description_report_resolves_the_latest_matching_crime() -> None:
    repo = MemoryCrimeRepository()
    earlier = _crime("earlier", "fraud", amount_cents=10)
    later = replace(
        _crime("later", "fraud", amount_cents=20),
        tick=2,
        perpetrator_id=earlier.perpetrator_id,
        committed_event_seq=2,
    )
    repo.add(earlier)
    repo.add(later)
    service = PoliceService(
        log=log(),
        clock=clock(),
        runtime=runtime(),
        repo=repo,
        world=world(),
        cfg=law_cfg(),
    )

    events = service.report(
        "ag_reporter",
        ReportCrimeParams(
            description="I saw a fraudulent transfer.",
            suspect_id=earlier.perpetrator_id,
            crime_type="fraud",
        ),
        3,
    )

    assert [item.kind for item in events] == [CRIME_REPORTED]
    assert events[0].payload["crime_id"] == later.crime_id
