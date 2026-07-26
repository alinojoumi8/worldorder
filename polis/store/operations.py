from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import yaml

from polis.config.runtime_time import utc_now_naive
from polis.config.settings import Settings
from polis.events.reader import EventQuery
from polis.events.verify import ChainReport, verify_run
from polis.living_city import run_living_city
from polis.store.engine import Database, StoreError
from polis.store.living_city import write_living_city_projections
from polis.store.repositories.events import EventRepository
from polis.store.repositories.runs import RunRepository


@dataclass(frozen=True, slots=True)
class ReplayReport:
    run_id: UUID
    stored_events: int
    replayed_events: int
    stored_hash: str
    replayed_hash: str
    exact: bool


@dataclass(frozen=True, slots=True)
class ResumeReport:
    run_id: UUID
    from_tick: int
    to_tick: int
    appended_events: int
    terminal_hash: str


async def load_run_settings(db: Database, run_id: UUID) -> Settings:
    record = await RunRepository(db).get(run_id)
    if record is None:
        raise StoreError(f"run not found: {run_id}")
    value = yaml.safe_load(record.config_yaml)
    # Runs written before config serialization used aliases retain ``schema_``.
    # Normalize that one historic shape so stored runs remain replayable.
    for route in value.get("llm", {}).get("routing", {}).values():
        if "schema_" in route:
            route["schema"] = route.pop("schema_")
    return Settings.model_validate(value)


async def verify_stored_run(settings: Settings, run_id: UUID) -> ChainReport:
    db = await Database.open(settings.store, role="reader")
    try:
        return await verify_run(EventRepository(db, run_id), run_id)
    finally:
        await db.close()


async def replay_stored_run(settings: Settings, run_id: UUID) -> ReplayReport:
    reader = await Database.open(settings.store, role="reader")
    try:
        rows = await reader.fetch(
            """
            SELECT terminal_hash,
                   (SELECT count(*) FROM events e WHERE e.run_id=r.run_id) AS event_count
            FROM runs r WHERE run_id=%s
            """,
            (run_id,),
        )
        if not rows:
            raise StoreError(f"run not found: {run_id}")
        stored_hash = str(rows[0]["terminal_hash"] or "")
        stored_events = int(rows[0]["event_count"])
    finally:
        await reader.close()
    replay = await run_living_city(settings)
    return ReplayReport(
        run_id,
        stored_events,
        len(replay.events),
        stored_hash,
        replay.report.chain_hash,
        stored_events == len(replay.events) and stored_hash == replay.report.chain_hash,
    )


async def rebuild_stored_run(settings: Settings, run_id: UUID) -> ReplayReport:
    replay = await run_living_city(settings)
    db = await Database.open(settings.store, role="engine")
    try:
        rows = await db.fetch(
            """
            SELECT terminal_hash,
                   (SELECT count(*) FROM events e WHERE e.run_id=r.run_id) AS event_count
            FROM runs r WHERE run_id=%s
            """,
            (run_id,),
        )
        if not rows:
            raise StoreError(f"run not found: {run_id}")
        stored_hash = str(rows[0]["terminal_hash"] or "")
        stored_events = int(rows[0]["event_count"])
        exact = stored_hash == replay.report.chain_hash and stored_events == len(replay.events)
        if not exact:
            raise StoreError("projection rebuild refused because replay is not exact")
        await write_living_city_projections(
            db,
            replay,
            replace=True,
            cache_mode=settings.llm.cache.mode,
        )
        return ReplayReport(
            run_id,
            stored_events,
            len(replay.events),
            stored_hash,
            replay.report.chain_hash,
            True,
        )
    finally:
        await db.close()


async def resume_stored_run(settings: Settings, run_id: UUID) -> ResumeReport:
    replay = await run_living_city(settings)
    db = await Database.open(settings.store, role="engine")
    repository = EventRepository(db, run_id)
    try:
        rows = await db.fetch(
            """
            SELECT COALESCE(MAX(seq),0) AS seq,COALESCE(MAX(tick),0) AS tick
            FROM events WHERE run_id=%s AND kind=1003
            """,
            (run_id,),
        )
        complete_seq = int(rows[0]["seq"])
        from_tick = int(rows[0]["tick"])
        await repository.delete_after_seq(complete_seq)
        stored = [
            event
            async for event in repository.scan(EventQuery(run_id, to_seq=complete_seq, order="seq"))
        ]
        expected_prefix = replay.events[: len(stored)]
        if len(stored) != len(expected_prefix) or any(
            stored_event.hash != expected_event.hash
            for stored_event, expected_event in zip(stored, expected_prefix, strict=True)
        ):
            raise StoreError("resume refused because stored events diverge from replay")
        tail = replay.events[len(stored) :]
        batch_size = 5_000
        for offset in range(0, len(tail), batch_size):
            await repository.append(tail[offset : offset + batch_size])
        await write_living_city_projections(
            db,
            replay,
            replace=True,
            cache_mode=settings.llm.cache.mode,
        )
        await db.execute(
            """
            UPDATE runs SET status='completed',ended_at=%s,last_tick=%s,
                            terminal_hash=%s
            WHERE run_id=%s
            """,
            (
                utc_now_naive(),
                replay.report.last_tick,
                replay.report.chain_hash,
                run_id,
            ),
        )
        return ResumeReport(
            run_id,
            from_tick,
            replay.report.last_tick,
            len(tail),
            replay.report.chain_hash,
        )
    finally:
        await db.close()
