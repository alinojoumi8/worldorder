from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from polis.config.settings import GatewaySettings
from polis.gateway.auth import (
    CONFORMANCE_CHECKS,
    ChallengeStore,
    ConformanceAuthority,
    Registrar,
    Session,
    SessionRegistry,
)
from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.queue import GatewayQueue
from polis.gateway.sdk.canonical import (
    canonical_registration_bytes,
    canonical_resume_bytes,
    canonical_session_bytes,
)
from polis.gateway.sdk.keys import Keypair

RUN_ID = UUID(int=1)


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[bytes]] = {}

    async def rpush(self, key: str, *values: object) -> int:
        target = self.lists.setdefault(key, [])
        target.extend(value for value in values if isinstance(value, bytes))
        return len(target)

    async def eval(self, script: str, numkeys: int, *args: object) -> int:
        del script, numkeys
        key = str(args[0])
        target = self.lists.setdefault(key, [])
        cap = int(args[1])
        if len(target) >= cap:
            return -1
        value = args[2]
        assert isinstance(value, bytes)
        target.append(value)
        return len(target)

    async def get(self, key: str) -> bytes | None:
        del key
        return None

    async def set(self, key: str, value: bytes, *, ex: int) -> bool:
        del key, value, ex
        return True

    async def llen(self, key: str) -> int:
        del key
        return 0


def test_challenge_store_sweeps_expired_challenges_and_client_windows() -> None:
    now = 1_000
    store = ChallengeStore(ttl_s=1, now_unix_ms=lambda: now)
    first = Keypair.from_private_bytes(b"\x01" * 32)
    second = Keypair.from_private_bytes(b"\x02" * 32)
    store.mint(first.pubkey_hex, client_ip="old")
    now = 62_000

    store.mint(second.pubkey_hex, client_ip="new")

    assert all(pubkey != first.pubkey_hex for pubkey, _client_ip in store._items)
    assert "old" not in store._requests


def test_invalid_public_key_is_reported_as_a_protocol_schema_error() -> None:
    store = ChallengeStore()

    with pytest.raises(ProtocolError) as caught:
        store.mint("not-a-public-key", client_ip="127.0.0.1")

    assert caught.value.code is ErrorCode.SCHEMA_INVALID


def _declaration(key: Keypair, challenge: str) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "pubkey": key.pubkey_hex,
        "display_name": "Nikos",
        "operator": "alice@example.org",
        "contact": "https://example.org/contact",
        "declared_model": "example",
        "declared_model_version": "1",
        "declared_scaffold": "custom/1",
        "scaffold_notes": "single turn",
        "memory": "ours",
        "sdk_version": "polis-agent-sdk/1",
        "requested_embodiment": "cohort_matched",
        "conformance_token": "cft_valid",
        "challenge": challenge,
    }


async def test_registration_stays_pending_until_engine_admission() -> None:
    now = 1_000_000
    key = Keypair.from_private_bytes(b"\x31" * 32)
    redis = FakeRedis()
    admitted: dict[str, MappingLike] = {}
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(enabled=True),
        GatewayQueue(redis, RUN_ID, max_queued=4),
        roster=lambda: _async_value([]),
        admission_reader=lambda agent_id: _async_value(admitted.get(agent_id)),
        conformance_validator=lambda token, pubkey, sdk_version, protocol_version: _async_value(
            token == "cft_valid" and bool(pubkey) and bool(sdk_version) and protocol_version == 1
        ),
        tick_reader=lambda: 7,
        challenges=ChallengeStore(now_unix_ms=lambda: now),
        now_unix_ms=lambda: now,
    )
    challenge = await registrar.challenge(key.pubkey_hex, client_ip="127.0.0.1")
    declaration = _declaration(key, challenge["challenge"])
    signature = key.sign(
        canonical_registration_bytes(
            bytes.fromhex(challenge["challenge"]),
            declaration,
        )
    )

    result = await registrar.register(
        declaration,
        signature,
        client_ip="127.0.0.1",
    )

    assert result == {"agent_id": key.agent_id, "status": "pending", "queued_tick": 7}
    assert await registrar.admission(key.agent_id) == {
        "status": "pending",
        "agent_id": key.agent_id,
    }
    assert len(redis.lists[f"polis:reg:{RUN_ID}"]) == 1
    queued = json.loads(redis.lists[f"polis:reg:{RUN_ID}"][0])
    assert queued["declaration"]["conformance_token"] == "verified"

    admitted[key.agent_id] = {
        "agent_id": key.agent_id,
        "pubkey": key.pubkey_hex,
        "admitted_tick": 8,
        "twin_agent_id": None,
        "revoked_tick": None,
    }
    assert await registrar.admission(key.agent_id) == {
        "status": "admitted",
        "agent_id": key.agent_id,
    }


async def test_registration_challenge_is_single_use() -> None:
    now = 2_000_000
    key = Keypair.from_private_bytes(b"\x32" * 32)
    redis = FakeRedis()
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(enabled=True),
        GatewayQueue(redis, RUN_ID, max_queued=4),
        roster=lambda: _async_value([]),
        admission_reader=lambda agent_id: _async_value(None),
        conformance_validator=lambda token, pubkey, sdk_version, protocol_version: _async_value(
            all((token, pubkey, sdk_version)) and protocol_version == 1
        ),
        tick_reader=lambda: 1,
        challenges=ChallengeStore(now_unix_ms=lambda: now),
        now_unix_ms=lambda: now,
    )
    challenge = await registrar.challenge(key.pubkey_hex, client_ip="127.0.0.1")
    declaration = _declaration(key, challenge["challenge"])
    signature = key.sign(
        canonical_registration_bytes(bytes.fromhex(challenge["challenge"]), declaration)
    )

    await registrar.register(declaration, signature, client_ip="127.0.0.1")
    with pytest.raises(ProtocolError) as caught:
        await registrar.register(declaration, signature, client_ip="127.0.0.1")
    assert caught.value.code is ErrorCode.SESSION_INVALID


async def test_registration_challenge_is_bound_to_client_ip_without_overwrite() -> None:
    now = 2_500_000
    key = Keypair.from_private_bytes(b"\x34" * 32)
    redis = FakeRedis()
    challenges = ChallengeStore(now_unix_ms=lambda: now)
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(enabled=True),
        GatewayQueue(redis, RUN_ID, max_queued=4),
        roster=lambda: _async_value([]),
        admission_reader=lambda agent_id: _async_value(None),
        conformance_validator=lambda token, pubkey, sdk_version, protocol_version: _async_value(
            all((token, pubkey, sdk_version)) and protocol_version == 1
        ),
        tick_reader=lambda: 1,
        challenges=challenges,
        now_unix_ms=lambda: now,
    )
    first = await registrar.challenge(key.pubkey_hex, client_ip="192.0.2.1")
    second = await registrar.challenge(key.pubkey_hex, client_ip="192.0.2.2")
    assert first["challenge"] != second["challenge"]

    declaration = _declaration(key, first["challenge"])
    signature = key.sign(
        canonical_registration_bytes(bytes.fromhex(first["challenge"]), declaration)
    )
    with pytest.raises(ProtocolError) as caught:
        await registrar.register(declaration, signature, client_ip="192.0.2.3")
    assert caught.value.code is ErrorCode.SESSION_INVALID

    result = await registrar.register(declaration, signature, client_ip="192.0.2.1")
    assert result["status"] == "pending"


async def test_stale_pending_entries_expire_before_capacity_and_token_checks() -> None:
    now = 2_750_000
    tick = 1
    redis = FakeRedis()
    validator_calls: list[str] = []

    async def validate(token: str, pubkey: str, sdk_version: str, protocol_version: int) -> bool:
        del pubkey, sdk_version, protocol_version
        validator_calls.append(token)
        return True

    registrar = Registrar(
        RUN_ID,
        GatewaySettings(
            enabled=True,
            registration={
                "max_external_agents": 1,
                "registrations_per_operator": 1,
                "pending_ttl_ticks": 2,
                "require_conformance_token": True,
            },
        ),
        GatewayQueue(redis, RUN_ID, max_queued=4),
        roster=lambda: _async_value([]),
        admission_reader=lambda agent_id: _async_value(None),
        conformance_validator=validate,
        tick_reader=lambda: tick,
        challenges=ChallengeStore(now_unix_ms=lambda: now),
        now_unix_ms=lambda: now,
    )

    async def register_candidate(key: Keypair, client_ip: str) -> MappingLike:
        challenge = await registrar.challenge(key.pubkey_hex, client_ip=client_ip)
        declaration = _declaration(key, challenge["challenge"])
        signature = key.sign(
            canonical_registration_bytes(bytes.fromhex(challenge["challenge"]), declaration)
        )
        return dict(
            await registrar.register(
                declaration,
                signature,
                client_ip=client_ip,
            )
        )

    first = Keypair.from_private_bytes(b"\x37" * 32)
    second = Keypair.from_private_bytes(b"\x38" * 32)
    assert (await register_candidate(first, "192.0.2.10"))["status"] == "pending"
    with pytest.raises(ProtocolError) as capacity:
        await register_candidate(second, "192.0.2.11")
    assert capacity.value.code is ErrorCode.NOT_ADMITTED
    assert validator_calls == ["cft_valid"]

    tick = 3
    with pytest.raises(ProtocolError) as expired:
        await registrar.admission(first.agent_id)
    assert expired.value.code is ErrorCode.NOT_ADMITTED
    assert (await register_candidate(second, "192.0.2.11"))["status"] == "pending"
    assert validator_calls == ["cft_valid", "cft_valid"]


async def test_capacity_deduplicates_the_same_agent_across_roster_and_cache() -> None:
    now = 2_900_000
    existing = Keypair.from_private_bytes(b"\x39" * 32)
    candidate = Keypair.from_private_bytes(b"\x3a" * 32)
    existing_record = {
        "agent_id": existing.agent_id,
        "pubkey": existing.pubkey_hex,
        "operator": "other@example.org",
    }
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(
            enabled=True,
            registration={
                "max_external_agents": 2,
                "registrations_per_operator": 2,
                "require_conformance_token": True,
            },
        ),
        GatewayQueue(FakeRedis(), RUN_ID, max_queued=4),
        roster=lambda: _async_value([existing_record]),
        admission_reader=lambda agent_id: _async_value(None),
        conformance_validator=lambda token, pubkey, sdk_version, protocol_version: _async_value(
            all((token, pubkey, sdk_version)) and protocol_version == 1
        ),
        tick_reader=lambda: 1,
        challenges=ChallengeStore(now_unix_ms=lambda: now),
        now_unix_ms=lambda: now,
    )
    registrar._admitted[existing.agent_id] = existing_record
    challenge = await registrar.challenge(candidate.pubkey_hex, client_ip="192.0.2.20")
    declaration = _declaration(candidate, challenge["challenge"])
    signature = candidate.sign(
        canonical_registration_bytes(bytes.fromhex(challenge["challenge"]), declaration)
    )

    result = await registrar.register(declaration, signature, client_ip="192.0.2.20")

    assert result["status"] == "pending"


async def test_session_requires_admission_and_valid_signature() -> None:
    now = 3_000_000
    client_now = now + 299_000
    key = Keypair.from_private_bytes(b"\x33" * 32)
    redis = FakeRedis()
    admitted = {
        "agent_id": key.agent_id,
        "pubkey": key.pubkey_hex,
        "admitted_tick": 1,
        "revoked_tick": None,
    }
    sessions = SessionRegistry()
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(enabled=True),
        GatewayQueue(redis, RUN_ID, max_queued=4),
        roster=lambda: _async_value([admitted]),
        admission_reader=lambda agent_id: _async_value(
            admitted if agent_id == key.agent_id else None
        ),
        tick_reader=lambda: 2,
        sessions=sessions,
        now_unix_ms=lambda: now,
    )
    signature = key.sign(canonical_session_bytes(RUN_ID, key.agent_id, client_now, 60, None))

    result = await registrar.open_session(
        key.agent_id,
        60,
        signature,
        None,
        "rest",
        unix_ms=client_now,
        sdk_version="test",
    )

    session = sessions.resolve(result["token"], unix_ms=now + 1, tick=2)
    assert session.agent_id == key.agent_id
    assert session.custody == "operator"
    assert result["expires_unix_ms"] == now + 60_000
    opened = json.loads(redis.lists[f"polis:reg:{RUN_ID}"][0])
    assert opened["request_type"] == "session_open"
    assert opened["declaration"]["session_id"] == result["session_id"]

    with pytest.raises(ProtocolError) as replay:
        await registrar.open_session(
            key.agent_id,
            60,
            signature,
            None,
            "rest",
            unix_ms=client_now,
            sdk_version="test",
        )
    assert replay.value.code is ErrorCode.NONCE_REUSED

    await registrar.close_session(result["token"])

    closed = json.loads(redis.lists[f"polis:reg:{RUN_ID}"][1])
    assert closed["request_type"] == "session_close"
    with pytest.raises(ProtocolError):
        sessions.resolve(result["token"], unix_ms=now + 2, tick=2)


async def test_resume_signature_is_fresh_and_single_use() -> None:
    now = [3_500_000]
    key = Keypair.from_private_bytes(b"\x35" * 32)
    redis = FakeRedis()
    admitted = {
        "agent_id": key.agent_id,
        "pubkey": key.pubkey_hex,
        "admitted_tick": 1,
        "revoked_tick": None,
        "naturalised_tick": 4,
        "resume_grace_until_tick": 20,
    }
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(enabled=True),
        GatewayQueue(redis, RUN_ID, max_queued=4),
        roster=lambda: _async_value([admitted]),
        admission_reader=lambda agent_id: _async_value(
            admitted if agent_id == key.agent_id else None
        ),
        tick_reader=lambda: 10,
        now_unix_ms=lambda: now[0],
    )
    signed_unix_ms = now[0] + 300_000
    signature = key.sign(canonical_resume_bytes(RUN_ID, key.agent_id, signed_unix_ms))

    assert await registrar.resume(key.agent_id, signature, unix_ms=signed_unix_ms) == {
        "resumed_tick": 10,
        "gap_ticks": 6,
    }
    now[0] += 300_002
    with pytest.raises(ProtocolError) as replay:
        await registrar.resume(key.agent_id, signature, unix_ms=signed_unix_ms)
    assert replay.value.code is ErrorCode.NONCE_REUSED


def test_session_registry_reaps_expired_sessions_and_keeps_index_consistent() -> None:
    now = [1_000]
    registry = SessionRegistry(now_unix_ms=lambda: now[0])
    active = Session(
        "ses_active",
        "ag_active",
        "operator",
        None,
        2_000,
        "rest",
        "test",
        "a" * 64,
    )
    expired = Session(
        "ses_expired",
        "ag_expired",
        "operator",
        None,
        1_500,
        "rest",
        "test",
        "b" * 64,
    )
    active_token = registry.open(active)
    expired_token = registry.open(expired)

    assert registry.connected_agents() == 2
    now[0] = 1_500
    assert registry.connected_agents() == 1
    with pytest.raises(ProtocolError) as expired_error:
        registry.resolve(expired_token, unix_ms=now[0], tick=1)
    assert expired_error.value.code is ErrorCode.SESSION_INVALID

    assert registry.close_agent(active.agent_id) == (active,)
    assert registry.connected_agents() == 0
    with pytest.raises(ProtocolError):
        registry.resolve(active_token, unix_ms=now[0], tick=1)

    replacement = Session(
        "ses_replacement",
        "ag_replacement",
        "operator",
        None,
        2_500,
        "rest",
        "test",
        "c" * 64,
    )
    registry.restore(expired_token, replacement)
    assert registry.resolve(expired_token, unix_ms=now[0], tick=1) == replacement
    assert registry.connected_agents() == 1

    ticket, ticket_expiry = registry.issue_websocket_ticket(expired_token)
    assert ticket_expiry == replacement.expires_unix_ms
    assert registry.consume_websocket_ticket(ticket, unix_ms=now[0], tick=1) == replacement
    with pytest.raises(ProtocolError) as reused_ticket:
        registry.consume_websocket_ticket(ticket, unix_ms=now[0], tick=1)
    assert reused_ticket.value.code is ErrorCode.SESSION_INVALID

    revoked_ticket, _ = registry.issue_websocket_ticket(expired_token)
    registry.close(expired_token)
    with pytest.raises(ProtocolError) as revoked:
        registry.consume_websocket_ticket(revoked_ticket, unix_ms=now[0], tick=1)
    assert revoked.value.code is ErrorCode.SESSION_INVALID


def test_suspended_session_and_ticket_survive_until_suspension_ends() -> None:
    now = [1_000]
    registry = SessionRegistry(now_unix_ms=lambda: now[0])
    suspended = Session(
        "ses_suspended",
        "ag_suspended",
        "operator",
        None,
        40_000,
        "ws",
        "test",
        "d" * 64,
        suspended_until_tick=5,
    )
    token = registry.open(suspended)
    ticket, _ = registry.issue_websocket_ticket(token)

    with pytest.raises(ProtocolError) as blocked:
        registry.resolve(token, unix_ms=now[0], tick=4)
    assert blocked.value.code is ErrorCode.SESSION_INVALID
    assert registry.connected_agents() == 1
    assert registry.resolve(token, unix_ms=now[0], tick=5) == suspended
    assert registry.consume_websocket_ticket(ticket, unix_ms=now[0], tick=5) == suspended


async def test_admission_rejects_and_uncaches_a_revoked_projection() -> None:
    key = Keypair.from_private_bytes(b"\x37" * 32)
    admitted: MappingLike = {
        "agent_id": key.agent_id,
        "pubkey": key.pubkey_hex,
        "admitted_tick": 1,
        "revoked_tick": None,
    }
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(enabled=True),
        GatewayQueue(FakeRedis(), RUN_ID, max_queued=4),
        roster=lambda: _async_value([admitted]),
        admission_reader=lambda agent_id: _async_value(
            admitted if agent_id == key.agent_id else None
        ),
        tick_reader=lambda: 2,
    )

    assert (await registrar.admission(key.agent_id))["status"] == "admitted"
    admitted["revoked_tick"] = 2
    with pytest.raises(ProtocolError) as revoked:
        await registrar.admission(key.agent_id)
    assert revoked.value.code is ErrorCode.REVOKED
    assert key.agent_id not in registrar._admitted


async def test_registration_rejects_oversized_free_text() -> None:
    now = 3_750_000
    key = Keypair.from_private_bytes(b"\x36" * 32)
    registrar = Registrar(
        RUN_ID,
        GatewaySettings(enabled=True),
        GatewayQueue(FakeRedis(), RUN_ID, max_queued=4),
        roster=lambda: _async_value([]),
        admission_reader=lambda agent_id: _async_value(None),
        tick_reader=lambda: 1,
        now_unix_ms=lambda: now,
    )
    declaration = _declaration(key, "00" * 32)
    declaration["scaffold_notes"] = "x" * 2_001

    with pytest.raises(ProtocolError) as caught:
        await registrar.register(declaration, "00", client_ip="127.0.0.1")
    assert caught.value.code is ErrorCode.SCHEMA_INVALID


async def test_conformance_tokens_are_single_use() -> None:
    authority = ConformanceAuthority(secret=b"\x55" * 32, now_unix_ms=lambda: 1_000)
    key = Keypair.from_private_bytes(b"\x56" * 32)
    token = authority.mint(
        {name: True for name in CONFORMANCE_CHECKS},
        pubkey=key.pubkey_hex,
        sdk_version="test-sdk",
        protocol_version=1,
    )

    assert await authority.validate(token, key.pubkey_hex, "test-sdk", 1) is True
    assert await authority.validate(token, key.pubkey_hex, "test-sdk", 1) is False


MappingLike = dict[str, Any]


async def _async_value(value: Any) -> Any:
    return value
