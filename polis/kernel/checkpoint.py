from __future__ import annotations

import json
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from polis.config.canon import canonical_bytes, sha256_hex


class Checkpointable(Protocol):
    name: str

    def dump(self) -> Mapping[str, Any]: ...

    def load(self, state: Mapping[str, Any]) -> None: ...


class BlobStore(Protocol):
    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/json"
    ) -> str: ...

    async def get(self, key: str) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class Checkpoint:
    run_id: UUID
    tick: int
    last_seq: int
    chain_hash: str
    uri: str
    bytes: int
    payload_hash: str


class CheckpointManager:
    def __init__(self, blobs: BlobStore, *, interval: int = 500, compress: bool = True) -> None:
        self.blobs = blobs
        self.interval = interval
        self.compress = compress
        self._records: dict[tuple[UUID, int], Checkpoint] = {}

    def due(self, tick: int) -> bool:
        return tick > 0 and tick % self.interval == 0

    async def write(
        self,
        run_id: UUID,
        tick: int,
        *,
        last_seq: int,
        chain_hash: str,
        components: Sequence[Checkpointable],
    ) -> Checkpoint:
        payload = {
            component.name: component.dump()
            for component in sorted(components, key=lambda item: item.name)
        }
        raw = canonical_bytes(payload)
        data = zlib.compress(raw) if self.compress else raw
        suffix = "json.zlib" if self.compress else "json"
        key = f"checkpoints/{run_id.hex}/{tick:012d}.{suffix}"
        uri = await self.blobs.put(key, data)
        checkpoint = Checkpoint(
            run_id,
            tick,
            last_seq,
            chain_hash,
            uri,
            len(data),
            sha256_hex(raw),
        )
        self._records[(run_id, tick)] = checkpoint
        return checkpoint

    async def restore(
        self,
        run_id: UUID,
        tick: int,
        components: Sequence[Checkpointable],
    ) -> Checkpoint:
        checkpoint = self._records[(run_id, tick)]
        key = checkpoint.uri.split("/checkpoints/", 1)[-1]
        data = await self.blobs.get(f"checkpoints/{key}")
        if data is None:
            raise ValueError("checkpoint blob is missing")
        raw = zlib.decompress(data) if self.compress else data
        if sha256_hex(raw) != checkpoint.payload_hash:
            raise ValueError("checkpoint payload hash mismatch")
        payload = json.loads(raw)
        for component in components:
            component.load(payload[component.name])
        return checkpoint
