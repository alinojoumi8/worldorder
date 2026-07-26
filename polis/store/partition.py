from __future__ import annotations

import re
from typing import Final
from uuid import UUID

from psycopg import sql

from polis.store.engine import Database, StoreError

PARTITIONED_TABLES: Final = ("events", "memories")
TICK_BUCKET: Final = 100_000
IDENT_RE: Final = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def validate_ident(name: str) -> str:
    if not IDENT_RE.fullmatch(name):
        raise StoreError(f"unsafe SQL identifier: {name!r}")
    return name


def run_suffix(run_id: UUID) -> str:
    return run_id.hex


def partition_name(table: str, run_id: UUID, bucket: int | None = None) -> str:
    prefix = {"events": "ev", "memories": "mem"}.get(table, table)
    suffix = f"_{bucket}" if bucket is not None else ""
    return validate_ident(f"{prefix}_{run_suffix(run_id)}{suffix}")


class PartitionManager:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._known: set[str] = set()

    async def ensure_run_partitions(self, run_id: UUID) -> list[str]:
        created: list[str] = []
        async with self.db.txn() as connection:
            event_parent = partition_name("events", run_id)
            memory_partition = partition_name("memories", run_id)
            statements = [
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {} PARTITION OF events "
                    "FOR VALUES IN ({}) PARTITION BY RANGE (tick)"
                ).format(sql.Identifier(event_parent), sql.Literal(run_id)),
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {} PARTITION OF memories FOR VALUES IN ({})"
                ).format(sql.Identifier(memory_partition), sql.Literal(run_id)),
            ]
            for name, statement in zip((event_parent, memory_partition), statements, strict=True):
                if name not in self._known:
                    await connection.execute(statement)
                    await connection.execute(
                        sql.SQL("GRANT SELECT ON {} TO polis_reader").format(sql.Identifier(name))
                    )
                    self._known.add(name)
                    created.append(name)
        return created

    async def ensure_tick_partition(self, run_id: UUID, tick: int) -> str:
        await self.ensure_run_partitions(run_id)
        bucket = tick // TICK_BUCKET
        name = partition_name("events", run_id, bucket)
        if name in self._known:
            return name
        start = bucket * TICK_BUCKET
        end = start + TICK_BUCKET
        parent = partition_name("events", run_id)
        async with self.db.txn() as connection:
            await connection.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {} PARTITION OF {} FOR VALUES FROM ({}) TO ({})"
                ).format(
                    sql.Identifier(name),
                    sql.Identifier(parent),
                    sql.Literal(start),
                    sql.Literal(end),
                )
            )
            await connection.execute(
                sql.SQL("GRANT SELECT ON {} TO polis_reader").format(sql.Identifier(name))
            )
        self._known.add(name)
        return name
