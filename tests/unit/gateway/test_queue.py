from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any
from uuid import UUID

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.queue import (
    DrainedAction,
    GatewayQueue,
    ObservationPublisher,
    RedisActionDrain,
    RedisLifecycleDrain,
    ReplayActionDrain,
)


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[bytes]] = defaultdict(list)
        self.values: dict[str, bytes] = {}
        self.expiry: dict[str, int] = {}
        self.delay = 0.0

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Any:
        del numkeys
        if self.delay:
            await asyncio.sleep(self.delay)
        key = str(keys_and_args[0])
        if "POLIS_PUSH_CAPPED" in script:
            cap = int(keys_and_args[1])
            if len(self.lists[key]) >= cap:
                return -1
            value = keys_and_args[2]
            assert isinstance(value, bytes)
            self.lists[key].append(value)
            self.expiry[key] = int(keys_and_args[3])
            return len(self.lists[key])
        if "POLIS_CLAIM_BATCH" in script:
            processing_key = str(keys_and_args[1])
            if processing_key not in self.lists and key in self.lists:
                self.lists[processing_key] = self.lists.pop(key)
                if key in self.expiry:
                    self.expiry[processing_key] = self.expiry.pop(key)
                self.expiry[processing_key] = int(keys_and_args[2])
            return list(self.lists.get(processing_key, ()))
        if "POLIS_PEEK_BATCH" in script:
            return list(self.lists.get(key, ()))
        if "POLIS_ACK_BATCH_WITH_QUARANTINE" in script:
            if key not in self.lists:
                return 0
            quarantine_key = str(keys_and_args[1])
            self.lists[quarantine_key].extend(keys_and_args[3:])
            self.expiry[quarantine_key] = int(keys_and_args[2])
            self.lists.pop(key, None)
            self.expiry.pop(key, None)
            return 1
        if "POLIS_ACK_BATCH" in script:
            existed = key in self.lists
            self.lists.pop(key, None)
            self.expiry.pop(key, None)
            return int(existed)
        values = list(self.lists[key])
        self.lists.pop(key, None)
        return values

    async def rpush(self, key: str, *values: object) -> int:
        for value in values:
            assert isinstance(value, bytes)
            self.lists[key].append(value)
        return len(self.lists[key])

    async def get(self, key: str) -> bytes | None:
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.values.get(key)

    async def set(self, key: str, value: bytes, *, ex: int) -> bool:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.values[key] = value
        self.expiry[key] = ex
        return True

    async def llen(self, key: str) -> int:
        if self.delay:
            await asyncio.sleep(self.delay)
        return len(self.lists[key])


class UnavailableRedis(FakeRedis):
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Any:
        del script, numkeys, keys_and_args
        raise RedisConnectionError("unavailable")

    async def get(self, key: str) -> bytes | None:
        del key
        raise RedisConnectionError("unavailable")

    async def llen(self, key: str) -> int:
        del key
        raise RedisConnectionError("unavailable")


def _action(agent: str, action_id: str, nonce: int = 0) -> DrainedAction:
    return DrainedAction(
        agent,
        action_id,
        5,
        nonce,
        "NULL_ACTION",
        {},
        None,
        None,
        {},
        "0" * 128,
        "ses_1",
        100,
    )


async def test_queue_caps_atomically_and_drain_order_is_deterministic() -> None:
    redis = FakeRedis()
    run_id = UUID(int=1)
    queue = GatewayQueue(redis, run_id, max_queued=2)
    drain = RedisActionDrain(redis, run_id)

    assert await queue.push_action(5, _action("ag_bbbbbbbbbbbbbbbb", "z")) == 1
    assert await queue.push_action(5, _action("ag_aaaaaaaaaaaaaaaa", "a")) == 2
    assert redis.expiry[f"polis:act:{run_id}:5"] == 3_600
    with pytest.raises(ProtocolError) as caught:
        await queue.push_action(5, _action("ag_cccccccccccccccc", "c"))
    assert caught.value.code is ErrorCode.QUEUE_FULL

    records = await drain.drain(5, timeout_ms=100)
    assert [record.agent_id for record in records] == [
        "ag_aaaaaaaaaaaaaaaa",
        "ag_bbbbbbbbbbbbbbbb",
    ]
    assert await drain.drain(5, timeout_ms=100) == records
    assert await drain.ack(5)
    assert await drain.drain(5, timeout_ms=100) == ()


async def test_action_drain_preserves_malformed_claim_until_original_ttl(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = FakeRedis()
    run_id = UUID(int=11)
    queue = GatewayQueue(redis, run_id, max_queued=3)
    drain = RedisActionDrain(redis, run_id, ttl_s=17)
    await queue.push_action(5, _action("ag_bbbbbbbbbbbbbbbb", "z"))
    redis.lists[f"polis:act:{run_id}:5"].append(b"{")
    await queue.push_action(5, _action("ag_aaaaaaaaaaaaaaaa", "a"))

    queued = await drain.queued_agent_ids(5)
    records = await drain.drain(5, timeout_ms=100)

    assert queued == frozenset({"ag_aaaaaaaaaaaaaaaa", "ag_bbbbbbbbbbbbbbbb"})
    assert [record.action_id for record in records] == ["a", "z"]
    assert "malformed action queue record ignored while peeking" in caplog.text
    assert "malformed action queue record ignored" in caplog.text
    processing_key = f"polis:act-processing:{run_id}:5"
    assert processing_key in redis.lists
    assert redis.expiry[processing_key] == 17
    redis.expiry[processing_key] = 5
    assert await drain.drain(5, timeout_ms=100) == records
    assert redis.expiry[processing_key] == 5
    assert await drain.ack(5) is True
    assert processing_key not in redis.lists
    quarantine_key = f"polis:act-quarantine:{run_id}:5"
    assert redis.lists[quarantine_key] == [b"{"]
    assert redis.expiry[quarantine_key] == 17
    assert await drain.drain(5, timeout_ms=100) == ()


async def test_action_ack_refuses_a_changed_processing_batch() -> None:
    redis = FakeRedis()
    run_id = UUID(int=12)
    queue = GatewayQueue(redis, run_id, max_queued=2)
    drain = RedisActionDrain(redis, run_id)
    await queue.push_action(5, _action("ag_aaaaaaaaaaaaaaaa", "a"))
    await drain.drain(5, timeout_ms=100)
    processing_key = f"polis:act-processing:{run_id}:5"
    redis.lists[processing_key].append(b"changed-after-delivery")

    assert await drain.ack(5) is False
    assert processing_key in redis.lists


async def test_observation_is_byte_passthrough_with_ttl() -> None:
    redis = FakeRedis()
    run_id = UUID(int=2)
    publisher = ObservationPublisher(redis, run_id, ttl_s=9)
    queue = GatewayQueue(redis, run_id, max_queued=1)
    blob = b'{"tick":4,"visible":"same bytes"}'

    assert await publisher.publish(4, "ag_0000000000000000", blob)
    assert await queue.read_observation(4, "ag_0000000000000000") == blob
    key = f"polis:obs:{run_id}:4:ag_0000000000000000"
    assert redis.expiry[key] == 9


async def test_drain_timeout_is_degraded_and_claimed_actions_are_recoverable() -> None:
    redis = FakeRedis()
    run_id = UUID(int=3)
    queue = GatewayQueue(redis, run_id, max_queued=1)
    drain = RedisActionDrain(redis, run_id)
    await queue.push_action(5, _action("ag_aaaaaaaaaaaaaaaa", "a"))

    class AmbiguousTimeoutRedis(FakeRedis):
        async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Any:
            delay = self.delay
            self.delay = 0
            try:
                result = await super().eval(script, numkeys, *keys_and_args)
            finally:
                self.delay = delay
            if "POLIS_CLAIM_BATCH" in script and delay:
                await asyncio.sleep(delay)
            return result

    ambiguous = AmbiguousTimeoutRedis()
    ambiguous.lists = redis.lists
    ambiguous.expiry = redis.expiry
    ambiguous.delay = 0.05
    drain = RedisActionDrain(ambiguous, run_id)

    with pytest.raises(OSError):
        await drain.drain(5, timeout_ms=1)
    assert await drain.ack(5) is False
    assert f"polis:act-processing:{run_id}:5" in ambiguous.lists
    ambiguous.delay = 0
    recovered = await drain.drain(5, timeout_ms=100)
    assert [record.action_id for record in recovered] == ["a"]
    assert await drain.ack(5)


async def test_replay_drain_ignores_recorded_arrival_order() -> None:
    records = (
        _action("ag_bbbbbbbbbbbbbbbb", "z"),
        _action("ag_aaaaaaaaaaaaaaaa", "a"),
    )
    drain = ReplayActionDrain({5: records})

    first = await drain.drain(5, timeout_ms=1)
    second = await drain.drain(5, timeout_ms=999)

    assert first == second
    assert first[0].agent_id == "ag_aaaaaaaaaaaaaaaa"


async def test_gateway_queue_maps_redis_outage_to_uniform_degraded_results() -> None:
    queue = GatewayQueue(UnavailableRedis(), UUID(int=4), max_queued=1)

    with pytest.raises(ProtocolError) as push_error:
        await queue.push_action(5, _action("ag_aaaaaaaaaaaaaaaa", "a"))
    with pytest.raises(ProtocolError) as depth_error:
        await queue.action_depth(5)
    with pytest.raises(ProtocolError) as observation_error:
        await queue.read_observation(5, "ag_aaaaaaaaaaaaaaaa")
    with pytest.raises(ProtocolError) as tick_error:
        await queue.read_tick()

    assert push_error.value.code is ErrorCode.GATEWAY_DEGRADED
    assert depth_error.value.code is ErrorCode.GATEWAY_DEGRADED
    assert observation_error.value.code is ErrorCode.GATEWAY_DEGRADED
    assert tick_error.value.code is ErrorCode.GATEWAY_DEGRADED


async def test_gateway_queue_and_observation_publish_bound_slow_redis_operations() -> None:
    redis = FakeRedis()
    redis.delay = 0.05
    run_id = UUID(int=16)
    queue = GatewayQueue(redis, run_id, max_queued=1, redis_timeout_ms=1)
    publisher = ObservationPublisher(redis, run_id, ttl_s=3, redis_timeout_ms=1)

    operations = (
        queue.push_action(5, _action("ag_aaaaaaaaaaaaaaaa", "a")),
        queue.read_observation(5, "ag_aaaaaaaaaaaaaaaa"),
        queue.read_tick(),
        queue.action_depth(5),
    )
    for operation in operations:
        with pytest.raises(ProtocolError) as caught:
            await operation
        assert caught.value.code is ErrorCode.GATEWAY_DEGRADED

    with pytest.raises(OSError, match="observation publish failed"):
        await publisher.publish(5, "ag_aaaaaaaaaaaaaaaa", b"{}")


async def test_all_write_queues_are_capped_and_expire() -> None:
    redis = FakeRedis()
    run_id = UUID(int=5)
    queue = GatewayQueue(redis, run_id, max_queued=1, ttl_s=17)

    await queue.push_memory(8, {"agent_id": "ag_aaaaaaaaaaaaaaaa", "text": "one"})
    await queue.push_touch(8, "ag_aaaaaaaaaaaaaaaa", [1])
    assert await queue.push_registration({"request_type": "register"}) == 1

    for key in (
        f"polis:mem:{run_id}:8",
        f"polis:touch:{run_id}:8",
        f"polis:reg:{run_id}",
    ):
        assert redis.expiry[key] == 17
    with pytest.raises(ProtocolError) as memory:
        await queue.push_memory(8, {"agent_id": "ag_bbbbbbbbbbbbbbbb", "text": "two"})
    with pytest.raises(ProtocolError) as touch:
        await queue.push_touch(8, "ag_bbbbbbbbbbbbbbbb", [2])
    with pytest.raises(ProtocolError) as registration:
        await queue.push_registration({"request_type": "resume"})
    assert memory.value.code is ErrorCode.QUEUE_FULL
    assert touch.value.code is ErrorCode.QUEUE_FULL
    assert registration.value.code is ErrorCode.QUEUE_FULL


async def test_lifecycle_drain_preserves_redis_fifo_order() -> None:
    redis = FakeRedis()
    run_id = UUID(int=6)
    queue = GatewayQueue(redis, run_id, max_queued=2)
    drain = RedisLifecycleDrain(redis, run_id)
    await queue.push_registration({"request_type": "resume", "agent_id": "ag_bbbbbbbbbbbbbbbb"})
    await queue.push_registration({"request_type": "depart", "agent_id": "ag_aaaaaaaaaaaaaaaa"})

    records = await drain.drain(timeout_ms=100)

    assert [record["request_type"] for record in records] == ["resume", "depart"]
    processing_key = f"polis:reg-processing:{run_id}"
    assert f"polis:reg:{run_id}" not in redis.lists
    assert processing_key in redis.lists
    assert await drain.drain(timeout_ms=100) == records
    assert await drain.ack()
    assert processing_key not in redis.lists
    assert await drain.drain(timeout_ms=100) == ()


async def test_lifecycle_drain_logs_and_acknowledges_a_malformed_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = FakeRedis()
    run_id = UUID(int=15)
    queue = GatewayQueue(redis, run_id, max_queued=2)
    drain = RedisLifecycleDrain(redis, run_id, ttl_s=17)
    await queue.push_registration({"request_type": "resume", "agent_id": "ag_valid"})
    redis.lists[f"polis:reg:{run_id}"].append(b"{")

    records = await drain.drain(timeout_ms=100)

    assert [record["request_type"] for record in records] == ["resume"]
    assert "malformed lifecycle queue record ignored" in caplog.text
    assert await drain.ack() is True
    processing_key = f"polis:reg-processing:{run_id}"
    assert processing_key not in redis.lists
    assert await drain.drain(timeout_ms=100) == ()


async def test_lifecycle_drain_redelivers_after_caller_failure() -> None:
    redis = FakeRedis()
    run_id = UUID(int=13)
    queue = GatewayQueue(redis, run_id, max_queued=1)
    await queue.push_registration({"request_type": "resume", "agent_id": "ag_aaaaaaaaaaaaaaaa"})

    first_caller = RedisLifecycleDrain(redis, run_id, ttl_s=17)
    first = await first_caller.drain(timeout_ms=100)

    recovered_caller = RedisLifecycleDrain(redis, run_id, ttl_s=17)
    recovered = await recovered_caller.drain(timeout_ms=100)

    assert recovered == first
    assert redis.expiry[f"polis:reg-processing:{run_id}"] == 17
    assert await recovered_caller.ack()


async def test_lifecycle_ack_refuses_a_changed_processing_batch() -> None:
    redis = FakeRedis()
    run_id = UUID(int=14)
    queue = GatewayQueue(redis, run_id, max_queued=1)
    drain = RedisLifecycleDrain(redis, run_id)
    await queue.push_registration({"request_type": "depart", "agent_id": "ag_aaaaaaaaaaaaaaaa"})
    await drain.drain(timeout_ms=100)
    processing_key = f"polis:reg-processing:{run_id}"
    redis.lists[processing_key].append(b"changed-after-delivery")

    assert await drain.ack() is False
    assert processing_key in redis.lists
