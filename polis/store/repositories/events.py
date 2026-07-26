from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from polis.config.canon import canonical_json
from polis.events.reader import EventQuery
from polis.events.types import Event
from polis.store.partition import PartitionManager
from polis.store.repositories.base import Repository

EVENT_COLUMNS = (
    "seq,run_id,tick,sim_time,kind,actor_id,subject_ids,cause_seq,payload,sig,prev_hash,hash"
)


def _event(row: dict[str, Any]) -> Event:
    return Event(
        seq=int(row["seq"]),
        run_id=row["run_id"],
        tick=int(row["tick"]),
        sim_time=row["sim_time"],
        kind=int(row["kind"]),
        actor_id=row["actor_id"],
        subject_ids=tuple(row["subject_ids"]),
        cause_seq=row["cause_seq"],
        payload=row["payload"],
        sig=row["sig"],
        prev_hash=str(row["prev_hash"]),
        hash=str(row["hash"]),
    )


class EventRepository(Repository):
    def __init__(self, db: Any, run_id: UUID) -> None:
        super().__init__(db, run_id)
        self.partitions = PartitionManager(db)

    async def append(self, events: Sequence[Event]) -> None:
        if not events:
            return
        if any(event.run_id != self.run_id for event in events):
            raise ValueError("event batch contains a foreign run_id")
        await self.partitions.ensure_tick_partition(
            self.run_id, max(event.tick for event in events)
        )
        async with (
            self.db.txn() as connection,
            connection.cursor() as cursor,
            cursor.copy(f"COPY events ({EVENT_COLUMNS}) FROM STDIN") as copy,
        ):
            for event in events:
                await copy.write_row(
                    (
                        event.seq,
                        event.run_id,
                        event.tick,
                        event.sim_time,
                        event.kind,
                        event.actor_id,
                        list(event.subject_ids),
                        event.cause_seq,
                        Jsonb(event.payload, dumps=canonical_json),
                        event.sig,
                        event.prev_hash,
                        event.hash,
                    )
                )

    async def get(self, run_id: UUID, seq: int) -> Event | None:
        rows = await self.db.fetch(
            f"SELECT {EVENT_COLUMNS} FROM events WHERE run_id=%s AND seq=%s",
            (run_id, seq),
        )
        return _event(rows[0]) if rows else None

    def scan(self, query: EventQuery) -> AsyncIterator[Event]:
        async def generate() -> AsyncIterator[Event]:
            clauses = ["run_id=%s"]
            params: list[Any] = [query.run_id]
            if query.kinds:
                clauses.append("kind = ANY(%s)")
                params.append(list(query.kinds))
            if query.kind_range:
                clauses.append("kind BETWEEN %s AND %s")
                params.extend(query.kind_range)
            for column, value, operator in (
                ("actor_id", query.actor_id, "="),
                ("tick", query.from_tick, ">="),
                ("tick", query.to_tick, "<="),
                ("seq", query.from_seq, ">="),
                ("seq", query.to_seq, "<="),
            ):
                if value is not None:
                    clauses.append(f"{column} {operator} %s")
                    params.append(value)
            if query.subject_id is not None:
                clauses.append("%s = ANY(subject_ids)")
                params.append(query.subject_id)
            order = "DESC" if query.order == "seq_desc" else "ASC"
            limit = ""
            if query.limit is not None:
                limit = " LIMIT %s"
                params.append(query.limit)
            rows = await self.db.fetch(
                f"SELECT {EVENT_COLUMNS} FROM events "
                f"WHERE {' AND '.join(clauses)} ORDER BY seq {order}{limit}",
                params,
            )
            for row in rows:
                yield _event(row)

        return generate()

    async def count(self, query: EventQuery) -> int:
        count = 0
        async for _ in self.scan(query):
            count += 1
        return count

    async def last(self, run_id: UUID) -> Event | None:
        rows = await self.db.fetch(
            f"SELECT {EVENT_COLUMNS} FROM events WHERE run_id=%s ORDER BY seq DESC LIMIT 1",
            (run_id,),
        )
        return _event(rows[0]) if rows else None

    async def by_cause(self, run_id: UUID, cause_seq: int) -> list[Event]:
        rows = await self.db.fetch(
            f"SELECT {EVENT_COLUMNS} FROM events WHERE run_id=%s AND cause_seq=%s ORDER BY seq",
            (run_id, cause_seq),
        )
        return [_event(row) for row in rows]

    async def max_seq(self) -> int:
        rows = await self.db.fetch(
            "SELECT COALESCE(MAX(seq), 0) AS value FROM events WHERE run_id=%s",
            (self.run_id,),
        )
        return int(rows[0]["value"])

    async def last_complete_tick(self) -> int:
        rows = await self.db.fetch(
            "SELECT COALESCE(MAX(tick), -1) AS value FROM events WHERE run_id=%s AND kind=1003",
            (self.run_id,),
        )
        return int(rows[0]["value"])

    async def delete_after_seq(self, seq: int) -> int:
        async with self.db.txn() as connection:
            cursor = await connection.execute(
                "DELETE FROM events WHERE run_id=%s AND seq>%s",
                (self.run_id, seq),
            )
            return cursor.rowcount or 0
