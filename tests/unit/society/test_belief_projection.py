from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from polis.events.kinds import BELIEF_DRIFT_APPLIED, MIGRATION_IN
from polis.events.types import Event
from polis.society.projections import BeliefsProjection
from tests.unit.society.test_belief_kernel import engine


class RecordingConnection:
    def __init__(self) -> None:
        self.query = ""
        self.params: tuple[object, ...] = ()

    async def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.query = query
        self.params = params


@pytest.mark.asyncio
async def test_drift_projection_upserts_missing_fact_with_fact_bounds() -> None:
    run_id = UUID(int=28)
    conn = RecordingConnection()
    ctx = SimpleNamespace(run_id=run_id, conn=conn)
    event = Event(
        1,
        run_id,
        3,
        datetime(2025, 1, 1, tzinfo=UTC),
        BELIEF_DRIFT_APPLIED,
        "ag_one",
        ("ag_one",),
        None,
        {
            "agent_id": "ag_one",
            "channel": "media",
            "updates": [
                {
                    "proposition": "fact.firm.fm_one.solvent",
                    "d_value": -1.0,
                    "d_confidence": 0.1,
                }
            ],
        },
        None,
        "0" * 64,
        "1" * 64,
    )
    await BeliefsProjection().apply(ctx, event)  # type: ignore[arg-type]
    assert "INSERT INTO beliefs" in conn.query
    assert "ON CONFLICT" in conn.query
    assert conn.params[3] == 0.0
    assert conn.params[4] == pytest.approx(0.35)
    assert conn.params[8:10] == (0.0, 1.0)


@pytest.mark.asyncio
async def test_migration_projection_replays_belief_priors() -> None:
    run_id = UUID(int=29)
    beliefs, repo, _ = engine()
    source_ref = "migration:coh_30"
    beliefs.apply_priors(
        "ag_new",
        (("policy.tax.progressivity", 0.25, 0.4),),
        tick=30,
        source_ref=source_ref,
    )
    live = repo.get("ag_new", "policy.tax.progressivity")
    assert live is not None
    conn = RecordingConnection()
    ctx = SimpleNamespace(run_id=run_id, conn=conn)
    event = Event(
        2,
        run_id,
        30,
        datetime(2025, 1, 31, tzinfo=UTC),
        MIGRATION_IN,
        "ag_new",
        ("ag_new",),
        None,
        {
            "agent_id": "ag_new",
            "cohort_id": "coh_30",
            "origin_profile": {},
            "arrival_wealth_cents": 0,
            "skills": {},
            "belief_priors": [
                {
                    "proposition": "policy.tax.progressivity",
                    "value": 0.25,
                    "confidence": 0.4,
                }
            ],
            "home_place_id": "pl_home",
        },
        None,
        "1" * 64,
        "2" * 64,
    )

    await BeliefsProjection().apply(ctx, event)  # type: ignore[arg-type]

    assert "INSERT INTO beliefs" in conn.query
    assert conn.params[1:5] == ("ag_new", "policy.tax.progressivity", 0.25, 0.4)
    assert conn.params[6:8] == ("migration", live.source_ref)
