from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any
from uuid import UUID

from polis.cli.wiring.external import RedisExternalDecisionPort
from polis.gateway.queue import TICK_KEY, DrainedAction, GatewayQueue


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[bytes]] = defaultdict(list)
        self.values: dict[str, bytes] = {}
        self.expiry: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, *args: object) -> Any:
        del numkeys
        key = str(args[0])
        if "POLIS_PUSH_CAPPED" in script:
            cap = int(args[1])
            if len(self.lists[key]) >= cap:
                return -1
            value = args[2]
            assert isinstance(value, bytes)
            self.lists[key].append(value)
            self.expiry[key] = int(args[3])
            return len(self.lists[key])
        if "POLIS_CLAIM_BATCH" in script:
            processing_key = str(args[1])
            if processing_key not in self.lists and key in self.lists:
                self.lists[processing_key] = self.lists.pop(key)
                if key in self.expiry:
                    self.expiry[processing_key] = self.expiry.pop(key)
            if processing_key in self.lists:
                self.expiry[processing_key] = int(args[2])
            return list(self.lists.get(processing_key, ()))
        if "POLIS_PEEK_BATCH" in script:
            return list(self.lists.get(key, ()))
        if "POLIS_ACK_BATCH" in script:
            existed = key in self.lists
            self.lists.pop(key, None)
            self.expiry.pop(key, None)
            return int(existed)
        raise AssertionError("unexpected Redis script")

    async def rpush(self, key: str, *values: object) -> int:
        self.lists[key].extend(value for value in values if isinstance(value, bytes))
        return len(self.lists[key])

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: bytes, *, ex: int) -> bool:
        assert ex > 0
        self.values[key] = value
        return True

    async def llen(self, key: str) -> int:
        return len(self.lists[key])


async def test_redis_port_opens_seals_then_drains_at_the_deadline() -> None:
    redis = FakeRedis()
    run_id = UUID(int=22)
    agent_id = "ag_1111111111111111"
    port = RedisExternalDecisionPort(
        redis,
        run_id,
        (agent_id,),
        observation_ttl_s=3,
    )
    queue = GatewayQueue(redis, run_id, max_queued=2)
    await queue.push_action(
        5,
        DrainedAction(
            agent_id,
            str(UUID(int=5)),
            5,
            0,
            "NULL_ACTION",
            {},
            None,
            None,
            {},
            "0" * 128,
            "ses_1",
            1,
        ),
    )

    started = time.monotonic()
    await port.open_tick(
        5,
        sim_time="2100-01-06T00:00:00",
        decision_deadline_ms=40,
        seal_margin_ms=10,
    )
    batch = await port.drain_actions(5, timeout_ms=20)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.035
    assert [action.agent_id for action in batch.actions] == [agent_id]
    latency = port.latency_rows()
    assert len(latency) == 1
    assert latency[0].agent_id == agent_id
    assert latency[0].missed is False
    port.clear_latency_rows()
    assert port.latency_rows() == ()
    mirrored = json.loads(redis.values[TICK_KEY.format(run=run_id)])
    assert mirrored["tick"] == 5
    assert mirrored["sealed"] is True


async def test_redis_port_acknowledges_lifecycle_only_after_next_tick() -> None:
    redis = FakeRedis()
    run_id = UUID(int=23)
    port = RedisExternalDecisionPort(
        redis,
        run_id,
        (),
        observation_ttl_s=3,
    )
    queue = GatewayQueue(redis, run_id, max_queued=1)
    await queue.push_registration({"request_type": "resume", "agent_id": "ag_1111111111111111"})

    requests = await port.drain_lifecycle(5, timeout_ms=100)
    processing_key = f"polis:reg-processing:{run_id}"

    assert [request.request_type for request in requests] == ["resume"]
    assert processing_key in redis.lists
    await port.open_tick(
        6,
        sim_time="2100-01-07T00:00:00",
        decision_deadline_ms=40,
        seal_margin_ms=10,
    )
    assert processing_key not in redis.lists


async def test_redis_port_returns_canonical_control_action_and_lifecycle_order() -> None:
    redis = FakeRedis()
    run_id = UUID(int=26)
    first = f"ag_{'11' * 32}"
    second = f"ag_{'22' * 32}"
    port = RedisExternalDecisionPort(
        redis,
        run_id,
        (second, first),
        observation_ttl_s=3,
    )
    queue = GatewayQueue(redis, run_id, max_queued=6)

    assert port.controlled_agent_ids() == (first, second)
    for agent_id, action_id, nonce in (
        (second, str(UUID(int=9)), 2),
        (first, str(UUID(int=8)), 2),
        (first, str(UUID(int=7)), 1),
    ):
        await queue.push_action(
            5,
            DrainedAction(
                agent_id,
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
                1,
            ),
        )

    batch = await port.drain_actions(5, timeout_ms=100)
    action_keys = [(action.agent_id, action.action_id, action.nonce) for action in batch.actions]
    assert action_keys == sorted(action_keys)

    for request in (
        {
            "request_type": "resume",
            "agent_id": second,
            "queued_tick": 2,
            "sig": "c",
        },
        {
            "request_type": "resume",
            "agent_id": first,
            "queued_tick": 2,
            "sig": "b",
        },
        {
            "request_type": "depart",
            "agent_id": first,
            "queued_tick": 1,
            "sig": "a",
        },
    ):
        await queue.push_registration(request)

    lifecycle = await port.drain_lifecycle(5, timeout_ms=100)
    lifecycle_keys = [
        (request.agent_id, request.queued_tick, request.request_type, request.sig)
        for request in lifecycle
    ]
    assert lifecycle_keys == sorted(lifecycle_keys)


async def test_pause_barrier_waits_for_distinct_controlled_agents() -> None:
    redis = FakeRedis()
    run_id = UUID(int=24)
    first = "ag_1111111111111111"
    second = "ag_2222222222222222"
    port = RedisExternalDecisionPort(
        redis,
        run_id,
        (first, second),
        observation_ttl_s=3,
        pause_for_external=True,
        pause_max_ms=30,
    )
    queue = GatewayQueue(redis, run_id, max_queued=3)
    for nonce in (0, 1):
        await queue.push_action(
            5,
            DrainedAction(
                first,
                str(UUID(int=nonce + 1)),
                5,
                nonce,
                "NULL_ACTION",
                {},
                None,
                None,
                {},
                "0" * 128,
                "ses_1",
                1,
            ),
        )
    await port.open_tick(
        5,
        sim_time="2100-01-06T00:00:00",
        decision_deadline_ms=40,
        seal_margin_ms=10,
    )

    started = time.monotonic()
    await port.drain_actions(5, timeout_ms=20)

    assert time.monotonic() - started >= 0.025


async def test_ack_failure_does_not_abort_the_next_tick() -> None:
    class AckFailureRedis(FakeRedis):
        async def eval(self, script: str, numkeys: int, *args: object) -> Any:
            if "POLIS_ACK_BATCH" in script:
                raise OSError("ack unavailable")
            return await super().eval(script, numkeys, *args)

    redis = AckFailureRedis()
    run_id = UUID(int=25)
    port = RedisExternalDecisionPort(redis, run_id, (), observation_ttl_s=3)
    queue = GatewayQueue(redis, run_id, max_queued=1)
    await queue.push_registration({"request_type": "resume", "agent_id": "ag_1"})
    await port.drain_lifecycle(1, timeout_ms=100)

    await port.open_tick(
        2,
        sim_time="2100-01-03T00:00:00",
        decision_deadline_ms=40,
        seal_margin_ms=10,
    )

    assert json.loads(redis.values[TICK_KEY.format(run=run_id)])["tick"] == 2
