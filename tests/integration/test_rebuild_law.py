from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from polis.events.kinds import (
    COUNSEL_RETAINED,
    CRIME_COMMITTED,
    CRIME_DETECTED,
    CRIME_REPORTED,
    EVIDENCE_ADMITTED,
    JUDGMENT_RENDERED,
    SUIT_FILED,
)
from polis.events.types import Event
from polis.society.projections import LawProjection


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((" ".join(query.split()), params))


def _event(seq: int, kind: int, payload: dict[str, object]) -> Event:
    return Event(
        seq,
        UUID(int=19),
        seq,
        datetime(2100, 1, 1, tzinfo=UTC),
        kind,
        None,
        (),
        None,
        payload,
        None,
        "0" * 64,
        f"{seq:064x}",
    )


def _law_events() -> tuple[Event, ...]:
    return (
        _event(
            1,
            CRIME_COMMITTED,
            {
                "crime_id": "cr_one",
                "type": "fraud",
                "perpetrator_id": "ag_defendant",
                "victim_id": "ag_victim",
                "amount_cents": 500,
                "place_id": "pl_office_0001",
                "district_id": "ds_00",
                "source_action_id": "ac_one",
                "concealment": 0.2,
                "path": "derived",
            },
        ),
        _event(2, CRIME_DETECTED, {"crime_id": "cr_one"}),
        _event(
            3,
            CRIME_REPORTED,
            {"crime_id": "cr_one", "reporter_id": "ag_victim"},
        ),
        _event(
            4,
            SUIT_FILED,
            {
                "case_id": "ca_one",
                "type": "criminal",
                "plaintiff_id": "government",
                "defendant_id": "ag_defendant",
                "crime_id": "cr_one",
                "cause_of_action": "fraud",
                "claim_cents": 500,
                "evidence_event_seqs": [1],
            },
        ),
        _event(
            5,
            COUNSEL_RETAINED,
            {
                "case_id": "ca_one",
                "side": "defence",
                "counsel_id": "ag_lawyer",
            },
        ),
        _event(
            6,
            EVIDENCE_ADMITTED,
            {"case_id": "ca_one", "admitted_seqs": [1]},
        ),
        _event(
            7,
            JUDGMENT_RENDERED,
            {
                "case_id": "ca_one",
                "judge_id": "ag_judge",
                "verdict": "guilty",
                "fine_cents": 100,
                "sentence_ticks": 10,
                "damages_cents": 0,
                "restitution_cents": 500,
            },
        ),
    )


async def _replay() -> list[tuple[str, tuple[object, ...]]]:
    conn = RecordingConnection()
    ctx = SimpleNamespace(run_id=UUID(int=19), conn=conn)
    projection = LawProjection()
    for item in _law_events():
        await projection.apply(ctx, item)  # type: ignore[arg-type]
    return conn.calls


@pytest.mark.asyncio
async def test_rebuild_replays_crimes_cases_and_conviction_column_exactly() -> None:
    first = await _replay()
    second = await _replay()
    sql = "\n".join(query for query, _params in first)

    assert first == second
    assert "INSERT INTO crimes" in sql
    assert "INSERT INTO court_cases" in sql
    assert "criminal_record=criminal_record+1" in sql
    assert LawProjection.tables == ("crimes", "court_cases")
