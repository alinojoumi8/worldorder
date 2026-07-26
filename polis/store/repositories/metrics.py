from __future__ import annotations

from collections.abc import Mapping, Sequence

from polis.store.repositories.base import Repository


class MetricRepository(Repository):
    async def write(self, tick: int, values: Mapping[str, float], *, as_of_seq: int) -> None:
        if not values:
            return
        async with self.db.txn() as connection, connection.cursor() as cursor:
            await cursor.executemany(
                """
                INSERT INTO metrics(run_id,tick,metric,value,as_of_seq)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (run_id,tick,metric) DO UPDATE SET
                    value=EXCLUDED.value, as_of_seq=EXCLUDED.as_of_seq
                """,
                [
                    (self.run_id, tick, metric, value, as_of_seq)
                    for metric, value in sorted(values.items())
                ],
            )

    async def series(
        self, metric: str, *, from_tick: int = 0, to_tick: int | None = None
    ) -> list[tuple[int, float]]:
        clause = " AND tick<=%s" if to_tick is not None else ""
        params = (
            (self.run_id, metric, from_tick, to_tick)
            if to_tick is not None
            else (self.run_id, metric, from_tick)
        )
        rows = await self.db.fetch(
            "SELECT tick,value FROM metrics "
            "WHERE run_id=%s AND metric=%s AND tick>=%s"
            f"{clause} ORDER BY tick",
            params,
        )
        return [(int(row["tick"]), float(row["value"])) for row in rows]

    async def latest(self, metrics: Sequence[str]) -> dict[str, float]:
        rows = await self.db.fetch(
            """
            SELECT DISTINCT ON (metric) metric,value
            FROM metrics WHERE run_id=%s AND metric=ANY(%s)
            ORDER BY metric,tick DESC
            """,
            (self.run_id, list(metrics)),
        )
        return {str(row["metric"]): float(row["value"]) for row in rows}
