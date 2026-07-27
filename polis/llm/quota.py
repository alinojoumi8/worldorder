from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from polis.llm.providers.base import ProviderRateLimited

RUN_QUOTA_WINDOW_SECONDS = 604_800


def quota_path(value: str) -> Path:
    raw = value.removeprefix("file://")
    return Path(raw).expanduser().resolve()


class SlidingWindowQuota:
    def __init__(self, path: str, *, now: Callable[[], float] = time.time) -> None:
        self.path = quota_path(path)
        self.now = now

    async def reserve(self, scope: str, *, limit: int, window_seconds: int) -> None:
        await asyncio.to_thread(self._reserve, scope, limit, window_seconds)

    async def count(self, scope: str, *, window_seconds: int) -> int:
        return await asyncio.to_thread(self._count, scope, window_seconds)

    def _count(self, scope: str, window_seconds: int) -> int:
        if not self.path.is_file():
            return 0
        cutoff = self.now() - window_seconds
        with sqlite3.connect(self.path, timeout=30) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM provider_call_reservations
                WHERE scope = ? AND reserved_at > ?
                """,
                (scope, cutoff),
            ).fetchone()
        if row is None:
            raise RuntimeError("quota count query returned no row")
        return int(row[0])

    def _reserve(self, scope: str, limit: int, window_seconds: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        current = self.now()
        cutoff = current - window_seconds
        with sqlite3.connect(self.path, timeout=30, isolation_level=None) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_call_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    reserved_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_provider_call_reservations_scope_time
                ON provider_call_reservations(scope, reserved_at)
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM provider_call_reservations WHERE reserved_at <= ?",
                    (cutoff,),
                )
                count_row = connection.execute(
                    """
                        SELECT COUNT(*) FROM provider_call_reservations
                        WHERE scope = ?
                        """,
                    (scope,),
                ).fetchone()
                if count_row is None:
                    raise RuntimeError("quota count query returned no row")
                count = int(count_row[0])
                if count >= limit:
                    oldest_row = connection.execute(
                        """
                            SELECT MIN(reserved_at) FROM provider_call_reservations
                            WHERE scope = ?
                            """,
                        (scope,),
                    ).fetchone()
                    if oldest_row is None or oldest_row[0] is None:
                        raise RuntimeError("quota oldest reservation query returned no row")
                    oldest = float(oldest_row[0])
                    connection.execute("ROLLBACK")
                    retry_after = max(1.0, oldest + window_seconds - current)
                    raise ProviderRateLimited(
                        f"provider call window exhausted for {scope!r}",
                        retry_after_s=retry_after,
                    )
                connection.execute(
                    """
                    INSERT INTO provider_call_reservations
                        (reservation_id, scope, reserved_at)
                    VALUES (?, ?, ?)
                    """,
                    (str(uuid4()), scope, current),
                )
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
