"""Bounded Redis handoff between the isolated gateway and the tick engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Final, Protocol
from uuid import UUID

from redis.exceptions import RedisError

from polis.gateway.errors import ErrorCode, ProtocolError

logger = logging.getLogger(__name__)

OBS_KEY: Final[str] = "polis:obs:{run}:{tick}:{agent}"
ACT_KEY: Final[str] = "polis:act:{run}:{tick}"
ACT_PROCESSING_KEY: Final[str] = "polis:act-processing:{run}:{tick}"
ACT_QUARANTINE_KEY: Final[str] = "polis:act-quarantine:{run}:{tick}"
MEM_KEY: Final[str] = "polis:mem:{run}:{tick}"
TOUCH_KEY: Final[str] = "polis:touch:{run}:{tick}"
REG_KEY: Final[str] = "polis:reg:{run}"
REG_PROCESSING_KEY: Final[str] = "polis:reg-processing:{run}"
TICK_KEY: Final[str] = "polis:tick:{run}"

_PUSH_CAPPED = """
-- POLIS_PUSH_CAPPED
local current = redis.call('LLEN', KEYS[1])
if current >= tonumber(ARGV[1]) then return -1 end
redis.call('RPUSH', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return current + 1
"""
_CLAIM_BATCH = """
-- POLIS_CLAIM_BATCH
if redis.call('EXISTS', KEYS[2]) == 0 and redis.call('EXISTS', KEYS[1]) == 1 then
    redis.call('RENAME', KEYS[1], KEYS[2])
    redis.call('EXPIRE', KEYS[2], tonumber(ARGV[1]))
end
if redis.call('EXISTS', KEYS[2]) == 1 then
    return redis.call('LRANGE', KEYS[2], 0, -1)
end
return {}
"""
_ACK_BATCH = """
-- POLIS_ACK_BATCH
return redis.call('DEL', KEYS[1])
"""
_ACK_BATCH_WITH_QUARANTINE = """
-- POLIS_ACK_BATCH_WITH_QUARANTINE
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
for index = 2, #ARGV do
    redis.call('RPUSH', KEYS[2], ARGV[index])
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[1]))
return redis.call('DEL', KEYS[1])
"""
_PEEK_BATCH = """
-- POLIS_PEEK_BATCH
return redis.call('LRANGE', KEYS[1], 0, -1)
"""


class RedisLike(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Awaitable[Any]: ...

    def rpush(self, key: str, *values: object) -> Awaitable[int]: ...

    def get(self, key: str) -> Awaitable[bytes | str | None]: ...

    def set(self, key: str, value: bytes, *, ex: int) -> Awaitable[object]: ...

    def llen(self, key: str) -> Awaitable[int]: ...


@dataclass(frozen=True, slots=True)
class DrainedAction:
    agent_id: str
    action_id: str
    tick: int
    nonce: int
    type: str
    params: Mapping[str, Any]
    reasoning: str | None
    speech: str | None
    extras: Mapping[str, Any]
    sig: str
    session_id: str
    received_ms: int
    audit: Mapping[str, Any] = field(default_factory=dict)


def _encode(record: object) -> bytes:
    payload = asdict(record) if is_dataclass(record) and not isinstance(record, type) else record
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _decode_action(blob: bytes | str) -> DrainedAction:
    raw = blob.decode() if isinstance(blob, bytes) else blob
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("queued action must be a JSON object")
    return DrainedAction(
        agent_id=str(payload["agent_id"]),
        action_id=str(payload["action_id"]),
        tick=int(payload["tick"]),
        nonce=int(payload["nonce"]),
        type=str(payload["type"]),
        params=dict(payload["params"]),
        reasoning=payload.get("reasoning"),
        speech=payload.get("speech"),
        extras=dict(payload.get("extras", {})),
        sig=str(payload["sig"]),
        session_id=str(payload["session_id"]),
        received_ms=int(payload["received_ms"]),
        audit=dict(payload.get("audit", {})),
    )


def _stable(records: Sequence[DrainedAction]) -> tuple[DrainedAction, ...]:
    return tuple(sorted(records, key=lambda item: (item.agent_id, item.action_id, item.nonce)))


def _queue_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    return repr(value).encode()


def _batch_marker(values: Sequence[object]) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = _queue_bytes(value)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


class GatewayQueue:
    def __init__(
        self,
        redis: RedisLike,
        run_id: UUID,
        *,
        max_queued: int,
        ttl_s: int = 3_600,
        redis_timeout_ms: int = 100,
    ) -> None:
        if max_queued < 1:
            raise ValueError("max_queued must be positive")
        if ttl_s < 1:
            raise ValueError("queue TTL must be positive")
        if redis_timeout_ms < 1:
            raise ValueError("Redis timeout must be positive")
        self.redis = redis
        self.run_id = run_id
        self.max_queued = max_queued
        self.ttl_s = ttl_s
        self.redis_timeout_ms = redis_timeout_ms

    def _key(self, template: str, tick: int, agent: str = "") -> str:
        return template.format(run=self.run_id, tick=tick, agent=agent)

    async def push_action(self, tick: int, record: DrainedAction) -> int:
        if tick != record.tick:
            raise ValueError("queue tick must match record tick")
        return await self._push_capped(self._key(ACT_KEY, tick), _encode(record))

    async def _push_capped(self, key: str, payload: bytes) -> int:
        try:
            value = await asyncio.wait_for(
                self.redis.eval(
                    _PUSH_CAPPED,
                    1,
                    key,
                    self.max_queued,
                    payload,
                    self.ttl_s,
                ),
                timeout=self.redis_timeout_ms / 1_000,
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED) from exc
        depth = int(value)
        if depth < 0:
            raise ProtocolError(ErrorCode.QUEUE_FULL)
        return depth

    async def push_memory(self, tick: int, record: Mapping[str, Any]) -> None:
        await self._push_capped(self._key(MEM_KEY, tick), _encode(record))

    async def push_touch(self, tick: int, agent_id: str, memory_ids: Sequence[int | str]) -> None:
        await self._push_capped(
            self._key(TOUCH_KEY, tick),
            _encode({"agent_id": agent_id, "memory_ids": list(memory_ids)}),
        )

    async def push_registration(self, record: Mapping[str, Any]) -> int:
        return await self._push_capped(REG_KEY.format(run=self.run_id), _encode(record))

    async def read_observation(self, tick: int, agent_id: str) -> bytes | None:
        try:
            value = await asyncio.wait_for(
                self.redis.get(self._key(OBS_KEY, tick, agent_id)),
                timeout=self.redis_timeout_ms / 1_000,
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED) from exc
        if value is None:
            return None
        return value.encode() if isinstance(value, str) else value

    async def read_tick(self) -> Mapping[str, Any] | None:
        try:
            value = await asyncio.wait_for(
                self.redis.get(TICK_KEY.format(run=self.run_id)),
                timeout=self.redis_timeout_ms / 1_000,
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED) from exc
        if value is None:
            return None
        try:
            raw = value.decode() if isinstance(value, bytes) else value
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    async def action_depth(self, tick: int) -> int:
        try:
            return int(
                await asyncio.wait_for(
                    self.redis.llen(self._key(ACT_KEY, tick)),
                    timeout=self.redis_timeout_ms / 1_000,
                )
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED) from exc


class ObservationPublisher:
    def __init__(
        self,
        redis: RedisLike,
        run_id: UUID,
        *,
        ttl_s: int,
        redis_timeout_ms: int = 100,
    ) -> None:
        if ttl_s < 1:
            raise ValueError("observation TTL must be positive")
        if redis_timeout_ms < 1:
            raise ValueError("Redis timeout must be positive")
        self.redis = redis
        self.run_id = run_id
        self.ttl_s = ttl_s
        self.redis_timeout_ms = redis_timeout_ms

    async def publish(self, tick: int, agent_id: str, blob: bytes) -> bool:
        key = OBS_KEY.format(run=self.run_id, tick=tick, agent=agent_id)
        try:
            return bool(
                await asyncio.wait_for(
                    self.redis.set(key, blob, ex=self.ttl_s),
                    timeout=self.redis_timeout_ms / 1_000,
                )
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise OSError("observation publish failed") from exc


class ActionDrain(Protocol):
    async def drain(self, tick: int, *, timeout_ms: int) -> tuple[DrainedAction, ...]: ...


class RedisActionDrain:
    def __init__(self, redis: RedisLike, run_id: UUID, *, ttl_s: int = 3_600) -> None:
        if ttl_s < 1:
            raise ValueError("processing TTL must be positive")
        self.redis = redis
        self.run_id = run_id
        self.ttl_s = ttl_s
        self._delivered: dict[int, str] = {}
        self._malformed: dict[int, tuple[bytes, ...]] = {}

    async def drain(self, tick: int, *, timeout_ms: int) -> tuple[DrainedAction, ...]:
        if timeout_ms < 1:
            raise ValueError("drain timeout must be positive")
        key = ACT_KEY.format(run=self.run_id, tick=tick)
        processing_key = ACT_PROCESSING_KEY.format(run=self.run_id, tick=tick)
        try:
            values = await asyncio.wait_for(
                self.redis.eval(
                    _CLAIM_BATCH,
                    2,
                    key,
                    processing_key,
                    self.ttl_s,
                ),
                timeout=timeout_ms / 1_000,
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise OSError("action queue drain failed") from exc
        try:
            raw_values = tuple(values)
        except TypeError as exc:
            raise OSError("action queue returned an invalid batch") from exc
        decoded: list[DrainedAction] = []
        malformed: list[bytes] = []
        try:
            for index, value in enumerate(raw_values):
                try:
                    decoded.append(_decode_action(value))
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ) as exc:
                    malformed.append(_queue_bytes(value))
                    logger.warning(
                        "malformed action queue record ignored",
                        extra={
                            "run_id": str(self.run_id),
                            "tick": tick,
                            "record_index": index,
                            "error_type": type(exc).__name__,
                        },
                    )
                    continue
        except TypeError as exc:
            raise OSError("action queue returned an invalid batch") from exc
        self._delivered = {
            delivered_tick: marker
            for delivered_tick, marker in self._delivered.items()
            if delivered_tick >= tick - 1
        }
        self._malformed = {
            delivered_tick: values
            for delivered_tick, values in self._malformed.items()
            if delivered_tick >= tick - 1
        }
        self._delivered[tick] = _batch_marker(raw_values)
        if malformed:
            self._malformed[tick] = tuple(malformed)
            logger.warning(
                "action queue batch contained malformed records",
                extra={
                    "run_id": str(self.run_id),
                    "tick": tick,
                    "dropped": len(malformed),
                },
            )
        else:
            self._malformed.pop(tick, None)
        return _stable(tuple(decoded))

    async def queued_agent_ids(self, tick: int, *, timeout_ms: int = 100) -> frozenset[str]:
        if timeout_ms < 1:
            raise ValueError("peek timeout must be positive")
        key = ACT_KEY.format(run=self.run_id, tick=tick)
        try:
            values = await asyncio.wait_for(
                self.redis.eval(_PEEK_BATCH, 1, key),
                timeout=timeout_ms / 1_000,
            )
            raw_values = tuple(values)
        except (TimeoutError, OSError, RedisError, TypeError) as exc:
            raise OSError("action queue peek failed") from exc
        agent_ids: set[str] = set()
        for index, value in enumerate(raw_values):
            try:
                agent_ids.add(_decode_action(value).agent_id)
            except (
                KeyError,
                TypeError,
                ValueError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                logger.warning(
                    "malformed action queue record ignored while peeking",
                    extra={
                        "run_id": str(self.run_id),
                        "tick": tick,
                        "record_index": index,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
        return frozenset(agent_ids)

    async def ack(self, tick: int, *, timeout_ms: int = 100) -> bool:
        if timeout_ms < 1:
            raise ValueError("acknowledgement timeout must be positive")
        delivered_marker = self._delivered.get(tick)
        if delivered_marker is None:
            return False
        processing_key = ACT_PROCESSING_KEY.format(run=self.run_id, tick=tick)
        quarantine_key = ACT_QUARANTINE_KEY.format(run=self.run_id, tick=tick)
        try:
            values = await asyncio.wait_for(
                self.redis.eval(_PEEK_BATCH, 1, processing_key),
                timeout=timeout_ms / 1_000,
            )
            current_marker = _batch_marker(tuple(values))
            if current_marker != delivered_marker:
                return False
            malformed = self._malformed.get(tick, ())
            if malformed:
                deleted = await asyncio.wait_for(
                    self.redis.eval(
                        _ACK_BATCH_WITH_QUARANTINE,
                        2,
                        processing_key,
                        quarantine_key,
                        self.ttl_s,
                        *malformed,
                    ),
                    timeout=timeout_ms / 1_000,
                )
            else:
                deleted = await asyncio.wait_for(
                    self.redis.eval(_ACK_BATCH, 1, processing_key),
                    timeout=timeout_ms / 1_000,
                )
        except (TimeoutError, OSError, RedisError) as exc:
            raise OSError("action queue acknowledgement failed") from exc
        except TypeError as exc:
            raise OSError("action queue acknowledgement returned an invalid batch") from exc
        self._delivered.pop(tick, None)
        self._malformed.pop(tick, None)
        return bool(deleted)


class ReplayActionDrain:
    def __init__(self, records_by_tick: Mapping[int, Sequence[DrainedAction]]) -> None:
        self.records_by_tick = {
            tick: _stable(tuple(records)) for tick, records in records_by_tick.items()
        }

    async def drain(self, tick: int, *, timeout_ms: int) -> tuple[DrainedAction, ...]:
        del timeout_ms
        return self.records_by_tick.get(tick, ())


class RedisLifecycleDrain:
    def __init__(self, redis: RedisLike, run_id: UUID, *, ttl_s: int = 3_600) -> None:
        if ttl_s < 1:
            raise ValueError("processing TTL must be positive")
        self.redis = redis
        self.run_id = run_id
        self.ttl_s = ttl_s
        self._delivered: str | None = None

    async def drain(
        self,
        *,
        timeout_ms: int,
    ) -> tuple[Mapping[str, Any], ...]:
        if timeout_ms < 1:
            raise ValueError("lifecycle drain timeout must be positive")
        key = REG_KEY.format(run=self.run_id)
        processing_key = REG_PROCESSING_KEY.format(run=self.run_id)
        try:
            values = await asyncio.wait_for(
                self.redis.eval(
                    _CLAIM_BATCH,
                    2,
                    key,
                    processing_key,
                    self.ttl_s,
                ),
                timeout=timeout_ms / 1_000,
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise OSError("lifecycle queue drain failed") from exc
        try:
            raw_values = tuple(values)
        except TypeError as exc:
            raise OSError("lifecycle queue returned an invalid batch") from exc
        decoded: list[Mapping[str, Any]] = []
        for index, value in enumerate(raw_values):
            try:
                raw = value.decode() if isinstance(value, bytes) else value
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "malformed lifecycle queue record ignored",
                    extra={
                        "run_id": str(self.run_id),
                        "record_index": index,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            if isinstance(payload, dict):
                decoded.append(payload)
            else:
                logger.warning(
                    "non-object lifecycle queue record ignored",
                    extra={
                        "run_id": str(self.run_id),
                        "record_index": index,
                        "error_type": type(payload).__name__,
                    },
                )
        self._delivered = _batch_marker(raw_values)
        return tuple(decoded)

    async def ack(self, *, timeout_ms: int = 100) -> bool:
        if timeout_ms < 1:
            raise ValueError("acknowledgement timeout must be positive")
        if self._delivered is None:
            return False
        processing_key = REG_PROCESSING_KEY.format(run=self.run_id)
        try:
            values = await asyncio.wait_for(
                self.redis.eval(_PEEK_BATCH, 1, processing_key),
                timeout=timeout_ms / 1_000,
            )
            current_marker = _batch_marker(tuple(values))
            if current_marker != self._delivered:
                return False
            deleted = await asyncio.wait_for(
                self.redis.eval(_ACK_BATCH, 1, processing_key),
                timeout=timeout_ms / 1_000,
            )
        except (TimeoutError, OSError, RedisError) as exc:
            raise OSError("lifecycle queue acknowledgement failed") from exc
        except TypeError as exc:
            raise OSError("lifecycle queue acknowledgement returned an invalid batch") from exc
        self._delivered = None
        return bool(deleted)
