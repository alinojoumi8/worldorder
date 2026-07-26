from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from polis.config.canon import canonical_bytes
from polis.config.errors import PolisError
from polis.llm.providers.base import CompletionResponse, SamplingParams


class CacheRenderMismatch(PolisError):
    """Cached content was rendered from different prompt text."""


class CacheMissInReplay(PolisError):
    """Replay mode requires a cache record that does not exist."""


class CacheVersionMismatch(PolisError):
    """A persistent completion was written by another cache schema."""


def cache_key(
    *,
    provider: str,
    model: str,
    model_version: str | None,
    prompt_template_hash: str,
    prompt_variables: Mapping[str, Any],
    sampling: SamplingParams,
    schema_hash: str | None,
    call_seed: int,
) -> str:
    return hashlib.sha256(
        canonical_bytes(
            {
                "provider": provider,
                "model": model,
                "model_version": model_version,
                "template_hash": prompt_template_hash,
                "variables": prompt_variables,
                "sampling": {
                    "temperature": sampling.temperature,
                    "top_p": sampling.top_p,
                    "max_tokens": sampling.max_tokens,
                    "stop": sampling.stop,
                    "seed": sampling.seed,
                },
                "schema_hash": schema_hash,
                "call_seed": call_seed,
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheRecord:
    key: str
    rendered_hash: str
    response: CompletionResponse
    cost_usd: Decimal = Decimal(0)


class CompletionCache:
    def __init__(
        self,
        *,
        mode: Literal["live", "replay", "hybrid"],
        l0_entries: int = 50_000,
        verify_render: bool = True,
        path: str | None = None,
        namespace: str = "default",
        schema_version: int = 1,
        strict_version: bool = True,
    ) -> None:
        self.mode = mode
        self.l0_entries = l0_entries
        self.verify_render = verify_render
        self.schema_version = schema_version
        self.strict_version = strict_version
        self._records: OrderedDict[str, CacheRecord] = OrderedDict()
        self._replay_accesses: dict[str, int] = {}
        self.hits = 0
        self.misses = 0
        self._database: sqlite3.Connection | None = None
        self._pending_writes = 0
        if path is not None:
            if not path.startswith("file://"):
                raise ValueError("M1 completion cache supports file:// storage")
            raw_path = path.removeprefix("file://")
            if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
                raw_path = raw_path[1:]
            root = Path(raw_path)
            cache_dir = root / namespace
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._database = sqlite3.connect(cache_dir / "completions.sqlite3")
            self._database.execute(
                """
                CREATE TABLE IF NOT EXISTS completions (
                    key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    rendered_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    cost_usd TEXT NOT NULL
                )
                """
            )
            if mode != "replay":
                # Each authoritative execution starts cold. It then leaves an exact,
                # run-scoped cache that an offline replay can consume.
                self._database.execute("DELETE FROM completions")
            self._database.commit()

    def _remember(self, record: CacheRecord) -> None:
        self._records[record.key] = record
        self._records.move_to_end(record.key)
        while len(self._records) > self.l0_entries:
            self._records.popitem(last=False)

    def _load_persistent(self, key: str) -> CacheRecord | None:
        if self._database is None:
            return None
        row = self._database.execute(
            """
            SELECT schema_version,rendered_hash,response_json,cost_usd
            FROM completions WHERE key=?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        version, rendered_hash, response_json, cost_usd = row
        if int(version) != self.schema_version:
            if self.strict_version:
                raise CacheVersionMismatch(
                    f"cache record {key} is schema {version}, expected {self.schema_version}"
                )
            return None
        response = CompletionResponse(**json.loads(str(response_json)))
        return CacheRecord(key, str(rendered_hash), response, Decimal(str(cost_usd)))

    async def get(self, key: str, *, rendered_hash: str) -> CacheRecord | None:
        record = self._records.get(key)
        if record is None and self.mode == "replay":
            record = self._load_persistent(key)
            if record is not None:
                self._remember(record)
        if record is None:
            self.misses += 1
            if self.mode == "replay":
                raise CacheMissInReplay(key)
            return None
        if self.verify_render and record.rendered_hash != rendered_hash:
            raise CacheRenderMismatch(key)
        self._records.move_to_end(key)
        if self.mode == "replay":
            self._replay_accesses[key] = self._replay_accesses.get(key, 0) + 1
        self.hits += 1
        return record

    def reported_hit(self, key: str) -> bool:
        if self.mode == "replay":
            return self._replay_accesses.get(key, 0) > 1
        return True

    async def put(self, record: CacheRecord) -> None:
        if self.mode == "replay":
            return
        self._remember(record)
        if self._database is not None:
            self._database.execute(
                """
                INSERT INTO completions(
                    key,schema_version,rendered_hash,response_json,cost_usd
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    rendered_hash=excluded.rendered_hash,
                    response_json=excluded.response_json,
                    cost_usd=excluded.cost_usd
                """,
                (
                    record.key,
                    self.schema_version,
                    record.rendered_hash,
                    json.dumps(asdict(record.response), sort_keys=True, separators=(",", ":")),
                    str(record.cost_usd),
                ),
            )
            self._pending_writes += 1
            if self._pending_writes >= 128:
                self._database.commit()
                self._pending_writes = 0

    def snapshot(self) -> dict[str, CacheRecord]:
        return dict(self._records)

    def restore(self, records: Mapping[str, CacheRecord]) -> None:
        self._records = OrderedDict(sorted(records.items()))

    async def close(self) -> None:
        if self._database is not None:
            self._database.commit()
            self._database.close()
            self._database = None
