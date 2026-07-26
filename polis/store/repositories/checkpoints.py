from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from polis.store.repositories.base import Repository


class CheckpointRepository(Repository):
    async def put(
        self,
        *,
        tick: int,
        last_seq: int,
        chain_hash: str,
        uri: str,
        bytes_: int,
        created_at: datetime,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO checkpoints(run_id,tick,last_seq,chain_hash,uri,bytes,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,tick) DO UPDATE SET
                last_seq=EXCLUDED.last_seq, chain_hash=EXCLUDED.chain_hash,
                uri=EXCLUDED.uri, bytes=EXCLUDED.bytes, created_at=EXCLUDED.created_at
            """,
            (
                self.run_id,
                tick,
                last_seq,
                chain_hash,
                uri,
                bytes_,
                created_at,
            ),
        )

    async def latest(self, *, at_or_before: int | None = None) -> Mapping[str, Any] | None:
        clause = " AND tick<=%s" if at_or_before is not None else ""
        params: tuple[Any, ...] = (
            (self.run_id, at_or_before) if at_or_before is not None else (self.run_id,)
        )
        rows = await self.db.fetch(
            f"SELECT * FROM checkpoints WHERE run_id=%s{clause} ORDER BY tick DESC LIMIT 1",
            params,
        )
        return rows[0] if rows else None

    async def list(self) -> list[dict[str, Any]]:
        return await self.db.fetch(
            "SELECT * FROM checkpoints WHERE run_id=%s ORDER BY tick",
            (self.run_id,),
        )
