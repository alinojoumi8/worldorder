from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from polis.events.types import Event
from polis.observatory.live import RedisEphemeralPublisher


def event(kind: int, tick: int) -> Event:
    return Event(
        seq=-1,
        run_id=UUID("10000000-0000-0000-0000-000000000001"),
        tick=tick,
        sim_time=datetime(2026, 1, 1, tzinfo=UTC),
        kind=kind,
        actor_id=None,
        subject_ids=(),
        cause_seq=None,
        payload={"value": kind},
        sig=None,
        prev_hash="",
        hash="",
    )


@pytest.mark.asyncio
async def test_ephemeral_events_are_coalesced_into_one_tick_frame() -> None:
    publisher = RedisEphemeralPublisher(
        "redis://127.0.0.1:6379/0",
        UUID("10000000-0000-0000-0000-000000000001"),
    )
    try:
        await publisher.publish([event(90050, 12), event(90051, 12)])
        frame = publisher.queue.get_nowait()
        publisher.queue.task_done()
        assert frame["op"] == "batch"
        assert frame["tick"] == 12
        assert [item["kind"] for item in frame["frames"]] == [90050, 90051]
        assert publisher.queue.empty()
    finally:
        await publisher.client.aclose()


@pytest.mark.asyncio
async def test_empty_ephemeral_batch_is_not_published() -> None:
    publisher = RedisEphemeralPublisher(
        "redis://127.0.0.1:6379/0",
        UUID("10000000-0000-0000-0000-000000000001"),
    )
    try:
        await publisher.publish([])
        assert publisher.queue.empty()
    finally:
        await publisher.client.aclose()


@pytest.mark.asyncio
async def test_pending_live_state_keeps_latest_frame_per_kind() -> None:
    publisher = RedisEphemeralPublisher(
        "redis://127.0.0.1:6379/0",
        UUID("10000000-0000-0000-0000-000000000001"),
    )
    try:
        await publisher.publish([event(90050, 12), event(90051, 12)])
        await publisher.publish([event(90050, 13)])
        frame = publisher.queue.get_nowait()
        publisher.queue.task_done()
        by_kind = {item["kind"]: item for item in frame["frames"]}
        assert frame["tick"] == 13
        assert by_kind[90050]["tick"] == 13
        assert by_kind[90051]["tick"] == 12
        assert publisher.dropped == 1
    finally:
        await publisher.client.aclose()
