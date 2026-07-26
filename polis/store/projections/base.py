from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from uuid import UUID

from psycopg import AsyncConnection

from polis.events.types import Event
from polis.store.engine import Database, StoreError

PROTECTED_TABLES: Final = frozenset(
    {"events", "runs", "llm_calls", "completion_cache", "checkpoints"}
)


class Projection(Protocol):
    name: str
    tables: tuple[str, ...]
    handles: frozenset[int]

    async def apply(self, ctx: ProjectionContext, event: Event) -> None: ...

    async def truncate(self, ctx: ProjectionContext) -> None: ...


@dataclass(slots=True)
class ProjectionContext:
    db: Database
    run_id: UUID
    conn: AsyncConnection[Any]
    buffer: dict[str, list[Sequence[Any]]] = field(default_factory=dict)


PROJECTION_REGISTRY: Final[dict[str, Projection]] = {}


def register_projection(projection: Projection) -> None:
    if projection.name in PROJECTION_REGISTRY:
        raise StoreError(f"duplicate projection: {projection.name}")
    protected = set(projection.tables) & PROTECTED_TABLES
    if protected:
        raise StoreError(f"projection {projection.name} claims protected tables: {protected}")
    owned = {table for existing in PROJECTION_REGISTRY.values() for table in existing.tables}
    overlap = owned & set(projection.tables)
    if overlap:
        raise StoreError(f"projection table ownership conflict: {overlap}")
    PROJECTION_REGISTRY[projection.name] = projection


class ProjectionRouter:
    def __init__(
        self,
        db: Database,
        run_id: UUID,
        projections: Sequence[Projection] | None = None,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.projections = tuple(projections or PROJECTION_REGISTRY.values())
        self._by_kind: dict[int, list[Projection]] = {}
        for projection in self.projections:
            for kind in projection.handles:
                self._by_kind.setdefault(kind, []).append(projection)

    async def apply_batch(
        self,
        events: Sequence[Event],
        conn: AsyncConnection[Any] | None = None,
    ) -> None:
        if conn is None:
            async with self.db.txn() as connection:
                await self.apply_batch(events, connection)
            return
        ctx = ProjectionContext(self.db, self.run_id, conn)
        for event in sorted(events, key=lambda item: item.seq):
            for projection in self._by_kind.get(event.kind, ()):
                await projection.apply(ctx, event)

    async def truncate_all(self) -> list[str]:
        async with self.db.txn() as connection:
            ctx = ProjectionContext(self.db, self.run_id, connection)
            for projection in self.projections:
                await projection.truncate(ctx)
        return sorted({table for projection in self.projections for table in projection.tables})


async def snapshot_table(
    db: Database, run_id: UUID, table: str, *, order_by: str
) -> list[dict[str, Any]]:
    if table in PROTECTED_TABLES:
        raise StoreError(f"refusing to snapshot protected source table: {table}")
    return await db.fetch(f"SELECT * FROM {table} WHERE run_id=%s ORDER BY {order_by}", (run_id,))
