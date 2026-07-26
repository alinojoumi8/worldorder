from pathlib import Path

import pytest

from polis.store.blobs import LocalBlobStore
from polis.store.engine import StoreError


@pytest.mark.asyncio
async def test_local_blob_round_trip(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    uri = await store.put("runs/one/checkpoint.json", b'{"tick": 1}')
    assert uri.startswith("file:")
    assert await store.get("runs/one/checkpoint.json") == b'{"tick": 1}'
    assert await store.exists("runs/one/checkpoint.json")
    await store.delete("runs/one/checkpoint.json")
    assert await store.get("runs/one/checkpoint.json") is None


@pytest.mark.asyncio
async def test_blob_key_cannot_escape_root(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    with pytest.raises(StoreError):
        await store.put("../escape", b"x")
