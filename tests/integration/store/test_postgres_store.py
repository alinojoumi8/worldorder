from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from polis.config.settings import load_settings
from polis.events.kinds import RUN_STARTED, TICK_COMPLETED
from polis.events.log import EventLog
from polis.events.reader import EventQuery
from polis.events.types import NewEvent
from polis.events.verify import verify_batch
from polis.llm.cache import EMPTY_COMPLETION_CACHE_MANIFEST_HASH
from polis.store.engine import Database, WriteForbidden
from polis.store.repositories.events import EventRepository
from polis.store.repositories.runs import RunRecord, RunRepository

RUN_ID = UUID("10000000-0000-0000-0000-000000000003")
CODE_GIT_SHA = "a" * 40
UNMATCHED_EXTERNAL_AGENT_ID = "ag_" + "a" * 64
RUN_STARTED_PAYLOAD = {
    "config_hash": "hash",
    "prompt_manifest": {},
    "model_manifest": {},
    "code_git_sha": CODE_GIT_SHA,
    "master_seed": 1,
    "completion_cache_manifest_hash": EMPTY_COMPLETION_CACHE_MANIFEST_HASH,
    "mechanism_manifest": {},
    "metric_manifest": {},
    "kind_registry_hash": "c" * 64,
    "clock_profile": "test",
    "scale": 1,
}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_event_round_trip_and_reader_role() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    db = await Database.open(settings.store)
    try:
        await db.execute("DELETE FROM runs WHERE run_id=%s", (RUN_ID,))
        runs = RunRepository(db)
        await runs.create(
            RunRecord(
                run_id=RUN_ID,
                name="store-test",
                config_yaml="{}",
                config_hash="hash",
                master_seed=1,
                prompt_manifest={},
                model_manifest={},
                metric_manifest={},
                mechanism_manifest={},
                completion_cache_manifest={},
                completion_cache_manifest_hash=EMPTY_COMPLETION_CACHE_MANIFEST_HASH,
                ablations={},
                scale=1,
                code_git_sha=CODE_GIT_SHA,
                started_at=datetime(2026, 1, 1),
                status="running",
            )
        )
        stored_run = await runs.get(RUN_ID)
        assert stored_run is not None
        assert stored_run.completion_cache_manifest == {}
        assert stored_run.completion_cache_manifest_hash == EMPTY_COMPLETION_CACHE_MANIFEST_HASH
        view_rows = await db.fetch(
            "SELECT pg_get_viewdef('v_agent_control'::regclass, true) AS definition"
        )
        assert len(view_rows) == 1
        assert "x.agent_id IS NOT NULL" in str(view_rows[0]["definition"])
        await db.execute(
            """
            INSERT INTO agents(
                run_id,agent_id,born_tick,age_years,district_id,place_id,state,
                as_of_tick,as_of_seq,home_place_id,kind
            ) VALUES(%s,%s,0,20,'dt_test','pl_test','{}'::jsonb,0,0,'pl_test','external')
            """,
            (RUN_ID, UNMATCHED_EXTERNAL_AGENT_ID),
        )
        unmatched = await db.fetch(
            """
            SELECT driver FROM v_agent_control
            WHERE run_id=%s AND agent_id=%s
            """,
            (RUN_ID, UNMATCHED_EXTERNAL_AGENT_ID),
        )
        assert len(unmatched) == 1
        assert unmatched[0]["driver"] == "native"
        events = EventRepository(db, RUN_ID)
        log = EventLog(RUN_ID, events)
        first = log.stage(
            NewEvent(RUN_STARTED, RUN_STARTED_PAYLOAD),
            tick=0,
            sim_time=datetime(2026, 1, 1),
        )
        log.stage(
            NewEvent(
                TICK_COMPLETED,
                {"tick": 1, "event_count": 1, "cost": str(Decimal("0"))},
                cause_seq=first.seq,
            ),
            tick=1,
            sim_time=datetime(2026, 1, 1),
        )
        await log.commit(1)
        stored = [event async for event in events.scan(EventQuery(RUN_ID, order="seq"))]
        assert len(stored) == 2
        assert verify_batch(stored).ok
        assert await events.last_complete_tick() == 1
    finally:
        await db.close()

    reader = await Database.open(settings.store, role="reader")
    try:
        rows = await reader.fetch("SELECT count(*) AS n FROM events WHERE run_id=%s", (RUN_ID,))
        assert rows[0]["n"] == 2
        with pytest.raises(WriteForbidden):
            await reader.execute("DELETE FROM events WHERE run_id=%s", (RUN_ID,))
    finally:
        await reader.close()
