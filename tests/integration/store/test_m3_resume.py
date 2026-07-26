from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.simulation import run_id_for
from polis.store.engine import Database
from polis.store.living_city import run_persistent
from polis.store.operations import (
    replay_stored_run,
    resume_stored_run,
    verify_stored_run,
)
from polis.store.repositories.events import EventRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_m3_checkpoint_resume_replays_exactly() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={"run": {"name": "m3-resume-integration"}},
    )
    run_id = run_id_for(settings)
    db = await Database.open(settings.store, role="engine")
    await db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
    await db.close()
    try:
        original = await run_persistent(settings)
        db = await Database.open(settings.store, role="engine")
        rows = await db.fetch(
            """
            SELECT seq,hash FROM events
            WHERE run_id=%s AND tick=6 AND kind=1003
            ORDER BY seq DESC LIMIT 1
            """,
            (run_id,),
        )
        cut_seq = int(rows[0]["seq"])
        await EventRepository(db, run_id).delete_after_seq(cut_seq)
        await db.execute(
            """
            UPDATE runs SET status='running',last_tick=6,terminal_hash=%s,ended_at=NULL
            WHERE run_id=%s
            """,
            (str(rows[0]["hash"]), run_id),
        )
        await db.close()

        resumed = await resume_stored_run(settings, run_id)
        verified = await verify_stored_run(settings, run_id)
        replayed = await replay_stored_run(settings, run_id)

        assert resumed.from_tick == 6
        assert resumed.to_tick == 12
        assert resumed.terminal_hash == original.report.chain_hash
        assert verified.ok
        assert replayed.exact
    finally:
        db = await Database.open(settings.store, role="engine")
        await db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        await db.close()
