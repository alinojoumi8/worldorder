from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.simulation import run_id_for
from polis.store.engine import Database
from polis.store.living_city import run_persistent
from polis.store.operations import rebuild_stored_run


async def _demography_snapshot(db: Database, run_id) -> tuple[list[dict], list[dict]]:
    households = await db.fetch(
        """
        SELECT household_id,formed_at_tick,dissolved_at_tick,home_place_id,member_ids,
               head_agent_id,tenure,rent_cents
        FROM households WHERE run_id=%s ORDER BY household_id
        """,
        (run_id,),
    )
    agents = await db.fetch(
        """
        SELECT agent_id,household_id,mother_id,father_id,generation,
               died_at_tick,death_cause,home_place_id
        FROM agents WHERE run_id=%s ORDER BY agent_id
        """,
        (run_id,),
    )
    return [dict(row) for row in households], [dict(row) for row in agents]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rebuild_reproduces_demography_owned_columns() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={"run": {"name": "demography-rebuild-integration"}},
    )
    run_id = run_id_for(settings)
    db = await Database.open(settings.store, role="engine")
    await db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
    await db.close()
    try:
        await run_persistent(settings)
        db = await Database.open(settings.store, role="engine")
        before = await _demography_snapshot(db, run_id)
        await db.close()

        report = await rebuild_stored_run(settings, run_id)

        db = await Database.open(settings.store, role="engine")
        after = await _demography_snapshot(db, run_id)
        await db.close()
        assert report.exact
        assert before[0]
        assert before == after
    finally:
        db = await Database.open(settings.store, role="engine")
        await db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        await db.close()
