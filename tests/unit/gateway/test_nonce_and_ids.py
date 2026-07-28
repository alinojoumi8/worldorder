from __future__ import annotations

from uuid import UUID

import pytest

from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.verify import ActionIdLRU, NonceStore


def test_nonce_is_strictly_increasing_and_resynchronisable() -> None:
    store = NonceStore()
    assert store.next_nonce("ag_0000000000000000") == 0

    store.accept("ag_0000000000000000", 4, 10)

    assert store.next_nonce("ag_0000000000000000") == 5
    with pytest.raises(ProtocolError) as caught:
        store.accept("ag_0000000000000000", 4, 11)
    assert caught.value.code is ErrorCode.NONCE_REUSED


def test_action_id_lru_rejects_duplicates_and_evicts_oldest() -> None:
    seen = ActionIdLRU(2)
    first, second, third = UUID(int=1), UUID(int=2), UUID(int=3)
    seen.accept(first)
    seen.accept(second)

    with pytest.raises(ProtocolError) as caught:
        seen.check(first)
    assert caught.value.code is ErrorCode.DUPLICATE_ACTION_ID

    seen.accept(third)
    seen.check(first)
    assert len(seen) == 2
