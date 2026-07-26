from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from polis.config.errors import PolisError
from polis.config.settings import StoreSettings


class StoreError(PolisError):
    """Persistence layer failure."""


class MigrationMismatch(StoreError):
    """The database migration head does not match the application."""


class WriteForbidden(StoreError):
    """A reader or unauthorized module attempted a write."""


@dataclass(frozen=True, slots=True)
class HealthReport:
    ok: bool
    server_version: int
    extensions: frozenset[str]
    alembic_head: str
    pool_in_use: int
    pool_size: int


class Database:
    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[Any]],
        *,
        role: Literal["engine", "reader"],
    ) -> None:
        self.pool = pool
        self.role = role

    @classmethod
    async def open(
        cls,
        settings: StoreSettings,
        *,
        role: Literal["engine", "reader"] = "engine",
        application_name: str = "polis-engine",
    ) -> Database:
        dsn = settings.reader_dsn if role == "reader" and settings.reader_dsn else settings.dsn
        pool: AsyncConnectionPool[AsyncConnection[Any]] = AsyncConnectionPool(
            conninfo=dsn,
            min_size=settings.pool_min,
            max_size=settings.pool_max,
            kwargs={
                "autocommit": False,
                "row_factory": dict_row,
                "application_name": application_name,
            },
            open=False,
        )
        await pool.open(wait=True)
        return cls(pool, role=role)

    @asynccontextmanager
    async def conn(self) -> AsyncIterator[AsyncConnection[Any]]:
        async with self.pool.connection() as connection:
            yield connection

    @asynccontextmanager
    async def txn(self) -> AsyncIterator[AsyncConnection[Any]]:
        async with self.pool.connection() as connection, connection.transaction():
            if self.role == "reader":
                await connection.execute("SET LOCAL TRANSACTION READ ONLY")
            yield connection

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        if self.role == "reader" and sql.lstrip().split(maxsplit=1)[0].upper() not in {
            "SELECT",
            "SHOW",
            "EXPLAIN",
            "WITH",
        }:
            raise WriteForbidden("reader database rejects mutation statements")
        async with self.txn() as connection:
            await connection.execute(sql, params)

    async def fetch(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        async with self.conn() as connection:
            cursor = await connection.execute(sql, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def health(self) -> HealthReport:
        rows = await self.fetch("SELECT current_setting('server_version_num')::int AS version")
        extensions = await self.fetch(
            "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')"
        )
        heads = await self.fetch(
            "SELECT version_num FROM alembic_version ORDER BY version_num DESC LIMIT 1"
        )
        return HealthReport(
            ok=True,
            server_version=int(rows[0]["version"]),
            extensions=frozenset(str(row["extname"]) for row in extensions),
            alembic_head=str(heads[0]["version_num"]) if heads else "",
            pool_in_use=0,
            pool_size=self.pool.max_size,
        )

    async def close(self) -> None:
        await self.pool.close()
