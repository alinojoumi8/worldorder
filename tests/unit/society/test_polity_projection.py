from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from polis.events.kinds import (
    CAMPAIGN_SPEND,
    CANDIDACY_ANNOUNCED,
    ELECTION_CALLED,
    ELECTION_RESOLVED,
    PARTY_FOUNDED,
    POLICY_ENACTED,
    POLICY_REPEALED,
    VOTE_CAST,
)
from polis.events.types import Event
from polis.society.projections import PolityProjection


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))


def _event(seq: int, kind: int, payload: dict[str, object]) -> Event:
    return Event(
        seq,
        UUID(int=1825),
        seq,
        datetime(2026, 1, 1, tzinfo=UTC),
        kind,
        None,
        (),
        None,
        payload,
        None,
        "0" * 64,
        f"{seq:064x}",
    )


@pytest.mark.asyncio
async def test_polity_projection_covers_canonical_rebuild_tables() -> None:
    conn = RecordingConnection()
    ctx = SimpleNamespace(run_id=UUID(int=1825), conn=conn)
    events = (
        _event(
            1,
            PARTY_FOUNDED,
            {
                "party_id": "pt_one",
                "name": "Civic",
                "platform": {"tax": 0.5},
            },
        ),
        _event(
            2,
            ELECTION_CALLED,
            {
                "election_id": "el_one",
                "office": "president",
                "seats": 1,
                "called_tick": 2,
                "voting_tick": 10,
                "campaign_ends_tick": 9,
                "electorate_size": 2,
                "method": "plurality",
            },
        ),
        _event(
            3,
            CANDIDACY_ANNOUNCED,
            {
                "candidacy_id": "ca_one",
                "election_id": "el_one",
                "agent_id": "ag_one",
                "party_id": "pt_one",
                "platform": {"tax": 0.5},
            },
        ),
        _event(
            4,
            CAMPAIGN_SPEND,
            {"candidacy_id": "ca_one", "amount_cents": 100},
        ),
        _event(
            5,
            VOTE_CAST,
            {
                "election_id": "el_one",
                "voter_id": "ag_voter",
                "candidacy_id": "ca_one",
                "ranking": [],
                "approvals": [],
                "origin": "reflex",
                "utility": {"congruence": 1.0},
            },
        ),
        _event(
            6,
            ELECTION_RESOLVED,
            {
                "election_id": "el_one",
                "winner_ids": ["ca_one"],
                "turnout": 0.5,
                "margin": 1.0,
                "tallies": {"ca_one": 1},
                "rounds": [],
                "n_deliberate": 0,
                "n_reflex": 1,
                "fitted_omega": {},
                "holdout_accuracy": 0.0,
                "first_election_prior": True,
            },
        ),
        _event(
            7,
            POLICY_ENACTED,
            {
                "policy_id": "py_one",
                "parameter": "tax.vat_bp",
                "old_value": 1_000,
                "new_value": 1_500,
                "effective_tick": 8,
                "enacted_by": "council",
                "vote_margin": 0.4,
                "proposal_seq": 1,
            },
        ),
        _event(
            8,
            POLICY_REPEALED,
            {"policy_id": "py_two", "repealed_policy_id": "py_one"},
        ),
    )
    projection = PolityProjection()

    for event in events:
        await projection.apply(ctx, event)  # type: ignore[arg-type]

    sql = "\n".join(query for query, _params in conn.calls)
    assert all(
        table in sql for table in ("parties", "elections", "candidacies", "votes", "policies")
    )
    assert any("spend_cents=spend_cents+" in query for query, _params in conn.calls)
    assert any("repealed_tick" in query for query, _params in conn.calls)
    assert projection.tables == (
        "parties",
        "elections",
        "candidacies",
        "votes",
        "policies",
    )
