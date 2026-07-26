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
from polis.store.engine import Database, WriteForbidden
from polis.store.repositories.events import EventRepository
from polis.store.repositories.runs import RunRecord, RunRepository

RUN_ID = UUID("10000000-0000-0000-0000-000000000003")


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
                ablations={},
                scale=1,
                code_git_sha="test",
                started_at=datetime(2026, 1, 1),
                status="running",
            )
        )
        events = EventRepository(db, RUN_ID)
        log = EventLog(RUN_ID, events)
        first = log.stage(
            NewEvent(RUN_STARTED, {"config_hash": "hash", "seed": 1}),
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
