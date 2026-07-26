from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from polis.config.canon import canonical_bytes
from polis.config.errors import PolisError
from polis.llm.providers.base import CompletionResponse, SamplingParams


class CacheRenderMismatch(PolisError):
    """Cached content was rendered from different prompt text."""


class CacheMissInReplay(PolisError):
    """Replay mode requires a cache record that does not exist."""


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


class CompletionCache:
    def __init__(
        self,
        *,
        mode: Literal["live", "replay", "hybrid"],
        l0_entries: int = 50_000,
        verify_render: bool = True,
    ) -> None:
        self.mode = mode
        self.l0_entries = l0_entries
        self.verify_render = verify_render
        self._records: OrderedDict[str, CacheRecord] = OrderedDict()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str, *, rendered_hash: str) -> CacheRecord | None:
        record = self._records.get(key)
        if record is None:
            self.misses += 1
            if self.mode == "replay":
                raise CacheMissInReplay(key)
            return None
        if self.verify_render and record.rendered_hash != rendered_hash:
            raise CacheRenderMismatch(key)
        self._records.move_to_end(key)
        self.hits += 1
        return record

    async def put(self, record: CacheRecord) -> None:
        if self.mode == "replay":
            return
        self._records[record.key] = record
        self._records.move_to_end(record.key)
        while len(self._records) > self.l0_entries:
            self._records.popitem(last=False)

    def snapshot(self) -> dict[str, CacheRecord]:
        return dict(self._records)

    def restore(self, records: Mapping[str, CacheRecord]) -> None:
        self._records = OrderedDict(sorted(records.items()))
