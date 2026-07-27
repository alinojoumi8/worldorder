from __future__ import annotations

import pytest

from polis.llm.providers.base import ProviderRateLimited
from polis.llm.quota import SlidingWindowQuota


@pytest.mark.asyncio
async def test_sliding_window_quota_persists_across_instances(tmp_path) -> None:
    current = 100.0
    path = f"file://{(tmp_path / 'quota.sqlite3').as_posix()}"
    first = SlidingWindowQuota(path, now=lambda: current)
    second = SlidingWindowQuota(path, now=lambda: current)

    await first.reserve("minimax-key", limit=2, window_seconds=10)
    await second.reserve("minimax-key", limit=2, window_seconds=10)
    with pytest.raises(ProviderRateLimited) as raised:
        await first.reserve("minimax-key", limit=2, window_seconds=10)
    assert raised.value.retry_after_s == 10

    current = 111.0
    await first.reserve("minimax-key", limit=2, window_seconds=10)


@pytest.mark.asyncio
async def test_sliding_window_quota_is_scoped(tmp_path) -> None:
    path = f"file://{(tmp_path / 'quota.sqlite3').as_posix()}"
    quota = SlidingWindowQuota(path, now=lambda: 100.0)
    await quota.reserve("first", limit=1, window_seconds=10)
    await quota.reserve("second", limit=1, window_seconds=10)
