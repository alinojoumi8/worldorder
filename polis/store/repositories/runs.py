from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from polis.store.engine import Database


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: UUID
    name: str
    config_yaml: str
    config_hash: str
    master_seed: int
    prompt_manifest: Mapping[str, Any]
    model_manifest: Mapping[str, Any]
    metric_manifest: Mapping[str, Any]
    mechanism_manifest: Mapping[str, Any]
    ablations: Mapping[str, Any]
    scale: int
    code_git_sha: str
    started_at: datetime
    status: str
    parent_run_id: UUID | None = None
    sweep_id: UUID | None = None
    tags: tuple[str, ...] = ()


class RunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, record: RunRecord) -> None:
        await self.db.execute(
            """
            INSERT INTO runs (
                run_id,name,config_yaml,config_hash,master_seed,prompt_manifest,
                model_manifest,metric_manifest,mechanism_manifest,ablations,scale,
                code_git_sha,started_at,status,parent_run_id,sweep_id,tags
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                record.run_id,
                record.name,
                record.config_yaml,
                record.config_hash,
                record.master_seed,
                Jsonb(record.prompt_manifest),
                Jsonb(record.model_manifest),
                Jsonb(record.metric_manifest),
                Jsonb(record.mechanism_manifest),
                Jsonb(record.ablations),
                record.scale,
                record.code_git_sha,
                record.started_at,
                record.status,
                record.parent_run_id,
                record.sweep_id,
                list(record.tags),
            ),
        )

    async def get(self, run_id: UUID) -> RunRecord | None:
        rows = await self.db.fetch("SELECT * FROM runs WHERE run_id=%s", (run_id,))
        if not rows:
            return None
        row = rows[0]
        return RunRecord(
            run_id=row["run_id"],
            name=row["name"],
            config_yaml=row["config_yaml"],
            config_hash=row["config_hash"],
            master_seed=row["master_seed"],
            prompt_manifest=row["prompt_manifest"],
            model_manifest=row["model_manifest"],
            metric_manifest=row["metric_manifest"],
            mechanism_manifest=row["mechanism_manifest"],
            ablations=row["ablations"],
            scale=row["scale"],
            code_git_sha=row["code_git_sha"],
            started_at=row["started_at"],
            status=row["status"],
            parent_run_id=row["parent_run_id"],
            sweep_id=row["sweep_id"],
            tags=tuple(row["tags"]),
        )

    async def update_progress(
        self,
        run_id: UUID,
        *,
        last_tick: int,
        total_llm_calls: int,
        total_tokens_in: int,
        total_tokens_out: int,
        total_cost_usd: Decimal,
    ) -> None:
        del total_llm_calls, total_tokens_in, total_tokens_out, total_cost_usd
        await self.db.execute("UPDATE runs SET last_tick=%s WHERE run_id=%s", (last_tick, run_id))

    async def finish(
        self,
        run_id: UUID,
        *,
        status: str,
        ended_at: datetime,
        halt_reason: str | None = None,
    ) -> None:
        await self.db.execute(
            "UPDATE runs SET status=%s, ended_at=%s, halt_reason=%s WHERE run_id=%s",
            (status, ended_at, halt_reason, run_id),
        )

    async def list(
        self, *, sweep_id: UUID | None = None, tags: Sequence[str] = ()
    ) -> list[RunRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if sweep_id is not None:
            clauses.append("sweep_id=%s")
            params.append(sweep_id)
        if tags:
            clauses.append("tags @> %s")
            params.append(list(tags))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.db.fetch(
            f"SELECT run_id FROM runs{where} ORDER BY started_at DESC", params
        )
        records = [await self.get(row["run_id"]) for row in rows]
        return [record for record in records if record is not None]
