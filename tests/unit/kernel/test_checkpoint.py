from __future__ import annotations

from uuid import UUID

import pytest

from polis.kernel.checkpoint import CheckpointManager
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.store.blobs import LocalBlobStore


@pytest.mark.asyncio
async def test_checkpoint_roundtrip_restores_component_state(tmp_path) -> None:
    run_id = UUID(int=17)
    clock = Clock(PROFILES["chronicle"])
    rng = RngRegistry(99)
    manager = CheckpointManager(LocalBlobStore(tmp_path), interval=5)

    for _ in range(5):
        clock.advance()
    checkpoint = await manager.write(
        run_id,
        5,
        last_seq=11,
        chain_hash="ab" * 32,
        components=(clock, rng),
    )
    clock.advance()
    restored = await manager.restore(run_id, 5, (clock, rng))

    assert manager.due(5)
    assert restored == checkpoint
    assert clock.tick == 5
