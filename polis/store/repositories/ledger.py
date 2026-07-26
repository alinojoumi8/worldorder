from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from polis.store.engine import Database


class LedgerRepository:
    """Exclusive persistence path for ledger accounts and entries."""

    def __init__(self, db: Database, run_id: UUID) -> None:
        self.db = db
        self.run_id = run_id

    async def flush(
        self,
        accounts: Sequence[Mapping[str, Any]],
        entries: Sequence[Mapping[str, Any]],
    ) -> None:
        async with self.db.txn() as connection, connection.cursor() as cursor:
            if accounts:
                await cursor.executemany(
                    """
                    INSERT INTO ledger_accounts(
                        run_id,account_id,owner_id,owner_type,account_type,currency,
                        balance_cents,opened_tick,closed_tick
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(run_id,account_id) DO UPDATE SET
                        balance_cents=EXCLUDED.balance_cents,
                        closed_tick=EXCLUDED.closed_tick
                    """,
                    [
                        (
                            self.run_id,
                            row["account_id"],
                            row["owner_id"],
                            row["owner_type"],
                            row["account_type"],
                            row["currency"],
                            row["balance_cents"],
                            row["opened_tick"],
                            row["closed_tick"],
                        )
                        for row in accounts
                    ],
                )
            if entries:
                await cursor.executemany(
                    """
                    INSERT INTO ledger_entries(
                        run_id,txn_id,tick,account_id,direction,amount_cents,reason,event_seq
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(run_id,txn_id,account_id,direction,reason) DO NOTHING
                    """,
                    [
                        (
                            self.run_id,
                            row["txn_id"],
                            row["tick"],
                            row["account_id"],
                            row["direction"],
                            row["amount_cents"],
                            row["reason"],
                            row["event_seq"],
                        )
                        for row in entries
                    ],
                )

    async def reconcile_balances(self) -> dict[str, int]:
        rows = await self.db.fetch(
            """
            SELECT a.account_id,
                   a.balance_cents - COALESCE(SUM(e.direction * e.amount_cents),0) AS delta
            FROM ledger_accounts a
            LEFT JOIN ledger_entries e
              ON e.run_id=a.run_id AND e.account_id=a.account_id
            WHERE a.run_id=%s
            GROUP BY a.account_id,a.balance_cents
            HAVING a.balance_cents <> COALESCE(SUM(e.direction * e.amount_cents),0)
            ORDER BY a.account_id
            """,
            (self.run_id,),
        )
        return {str(row["account_id"]): int(row["delta"]) for row in rows}
