from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.simulation import run_id_for
from polis.store.engine import Database
from polis.store.living_city import run_persistent
from polis.store.operations import replay_stored_run, resume_stored_run, verify_stored_run
from polis.store.repositories.events import EventRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_persistent_run_resumes_from_last_complete_tick_without_divergence() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "run": {
                "name": "resume-integration",
                "seed": 8_100_004,
                "ticks": 4,
                "scale": 8,
            },
            "population": {"initial_agents": 8},
        },
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
            WHERE run_id=%s AND tick=2 AND kind=1003
            ORDER BY seq DESC LIMIT 1
            """,
            (run_id,),
        )
        cut_seq = int(rows[0]["seq"])
        await EventRepository(db, run_id).delete_after_seq(cut_seq)
        await db.execute(
            """
            UPDATE runs SET status='running',last_tick=2,terminal_hash=%s,ended_at=NULL
            WHERE run_id=%s
            """,
            (str(rows[0]["hash"]), run_id),
        )
        await db.close()

        resumed = await resume_stored_run(settings, run_id)
        verified = await verify_stored_run(settings, run_id)
        replayed = await replay_stored_run(settings, run_id)

        assert resumed.from_tick == 2
        assert resumed.to_tick == 4
        assert resumed.appended_events > 0
        assert resumed.terminal_hash == original.report.chain_hash
        assert verified.ok
        assert replayed.exact
    finally:
        db = await Database.open(settings.store, role="engine")
        await db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        await db.close()
