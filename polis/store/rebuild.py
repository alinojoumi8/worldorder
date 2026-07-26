from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from polis.config.canon import canonical_bytes, sha256_hex
from polis.events.reader import EventQuery
from polis.store.engine import Database
from polis.store.projections.base import ProjectionRouter
from polis.store.repositories.events import EventRepository


@dataclass(frozen=True, slots=True)
class RebuildReport:
    run_id: UUID
    events_replayed: int
    from_tick: int
    to_tick: int
    rows_written: Mapping[str, int]
    duration_s: float
    ok: bool


async def rebuild(
    db: Database,
    run_id: UUID,
    *,
    from_tick: int = 0,
    batch: int = 5_000,
    progress: Callable[[int], None] | None = None,
) -> RebuildReport:
    repository = EventRepository(db, run_id)
    router = ProjectionRouter(db, run_id)
    tables = await router.truncate_all()
    events = [
        event
        async for event in repository.scan(EventQuery(run_id, from_tick=from_tick, order="seq"))
    ]
    for offset in range(0, len(events), batch):
        await router.apply_batch(events[offset : offset + batch])
        if progress is not None:
            progress(min(offset + batch, len(events)))
    return RebuildReport(
        run_id=run_id,
        events_replayed=len(events),
        from_tick=from_tick,
        to_tick=max((event.tick for event in events), default=from_tick),
        rows_written={table: -1 for table in tables},
        duration_s=0.0,
        ok=True,
    )


async def snapshot_projections(
    db: Database,
    run_id: UUID,
    tables: Sequence[str] | None = None,
) -> dict[str, str]:
    selected = tables or ("agents", "memories", "beliefs", "metrics")
    result: dict[str, str] = {}
    for table in selected:
        rows = await db.fetch(f"SELECT * FROM {table} WHERE run_id=%s ORDER BY 1,2", (run_id,))
        result[table] = sha256_hex(canonical_bytes(rows))
    return result
