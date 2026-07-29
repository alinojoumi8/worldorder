"""Registration, lifecycle requests, and session custody."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from redis.exceptions import RedisError

from polis.config.settings import GatewaySettings
from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.queue import GatewayQueue
from polis.gateway.sdk.canonical import (
    PROTOCOL_VERSION,
    agent_id_for,
    canonical_depart_bytes,
    canonical_registration_bytes,
    canonical_resume_bytes,
    canonical_revoke_bytes,
    canonical_session_bytes,
    verify,
)

CONFORMANCE_CHECKS = frozenset(
    {
        "protocol_version",
        "vector_count",
        "vector_preimage",
        "vector_signature",
        "mutated_signature",
        "agent_id",
        "local_signing",
        "registration_domain",
    }
)
_SKEW_TOLERANCE_MS = 300_000
# A timestamp accepted at +skew remains valid until the server passes it by the same skew.
_SIGNATURE_RESERVATION_TTL_MS = 2 * _SKEW_TOLERANCE_MS + 1


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    agent_id: str
    custody: Literal["operator", "delegated"]
    delegate_pubkey: str | None
    expires_unix_ms: int
    transport: Literal["mcp_stdio", "mcp_http", "rest", "ws"]
    sdk_version: str
    pubkey_hex: str
    revoked: bool = False
    suspended_until_tick: int | None = None

    def active_at(self, *, unix_ms: int, tick: int) -> bool:
        return (
            not self.revoked
            and unix_ms < self.expires_unix_ms
            and (self.suspended_until_tick is None or tick >= self.suspended_until_tick)
        )


@dataclass(frozen=True, slots=True)
class Challenge:
    value: bytes
    pubkey_hex: str
    client_ip: str
    expires_unix_ms: int


class ChallengeStore:
    def __init__(
        self,
        *,
        ttl_s: int = 300,
        requests_per_minute: int = 10,
        now_unix_ms: Callable[[], int] | None = None,
    ) -> None:
        self.ttl_s = ttl_s
        self.requests_per_minute = requests_per_minute
        self.now_unix_ms = now_unix_ms or (lambda: time.time_ns() // 1_000_000)
        self._items: dict[tuple[str, str], Challenge] = {}
        self._requests: dict[str, deque[int]] = defaultdict(deque)

    def charge(self, client_ip: str) -> int:
        """Charge the shared unauthenticated per-IP request window."""
        now = self.now_unix_ms()
        for key, item in tuple(self._items.items()):
            if item.expires_unix_ms <= now:
                del self._items[key]
        for ip, seen in tuple(self._requests.items()):
            while seen and seen[0] <= now - 60_000:
                seen.popleft()
            if not seen:
                del self._requests[ip]
        requests = self._requests[client_ip]
        if len(requests) >= self.requests_per_minute:
            raise ProtocolError(ErrorCode.RATE_LIMITED, retry_after_ms=60_000)
        requests.append(now)
        return now

    def mint(self, pubkey_hex: str, *, client_ip: str) -> Challenge:
        now = self.charge(client_ip)
        _validated_agent_id(pubkey_hex)
        challenge = Challenge(
            secrets.token_bytes(32),
            pubkey_hex,
            client_ip,
            now + self.ttl_s * 1_000,
        )
        self._items[(pubkey_hex, client_ip)] = challenge
        return challenge

    def consume(self, pubkey_hex: str, challenge_hex: str, *, client_ip: str) -> Challenge:
        challenge = self._items.pop((pubkey_hex, client_ip), None)
        if challenge is None or challenge.expires_unix_ms <= self.now_unix_ms():
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        try:
            supplied = bytes.fromhex(challenge_hex)
        except ValueError as exc:
            raise ProtocolError(ErrorCode.SESSION_INVALID) from exc
        if not secrets.compare_digest(challenge.value, supplied):
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        return challenge


class SessionRegistry:
    _WEBSOCKET_TICKET_TTL_MS = 30_000

    def __init__(self, *, now_unix_ms: Callable[[], int] | None = None) -> None:
        self.now_unix_ms = now_unix_ms or (lambda: time.time_ns() // 1_000_000)
        self._tokens: dict[str, Session] = {}
        self._by_agent: dict[str, set[str]] = defaultdict(set)
        self._websocket_tickets: dict[str, tuple[str, int]] = {}

    def open(self, session: Session) -> str:
        self.reap()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = session
        self._by_agent[session.agent_id].add(token)
        return token

    def reap(self) -> None:
        now = self.now_unix_ms()
        for token, session in tuple(self._tokens.items()):
            if session.revoked or session.expires_unix_ms <= now:
                self.close(token)
        for ticket, (_, expires_unix_ms) in tuple(self._websocket_tickets.items()):
            if expires_unix_ms <= now:
                del self._websocket_tickets[ticket]

    def resolve(self, token: str, *, unix_ms: int, tick: int) -> Session:
        session = self._tokens.get(token)
        if session is None:
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        if not session.active_at(unix_ms=unix_ms, tick=tick):
            if session.revoked or session.expires_unix_ms <= unix_ms:
                self.close(token)
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        return session

    def close(self, token: str) -> Session | None:
        session = self._tokens.pop(token, None)
        for ticket, (source_token, _) in tuple(self._websocket_tickets.items()):
            if source_token == token:
                del self._websocket_tickets[ticket]
        if session is not None:
            tokens = self._by_agent.get(session.agent_id)
            if tokens is not None:
                tokens.discard(token)
                if not tokens:
                    self._by_agent.pop(session.agent_id, None)
        return session

    def issue_websocket_ticket(self, token: str) -> tuple[str, int]:
        """Exchange a live bearer token for a short-lived browser-safe ticket."""
        self.reap()
        session = self._tokens.get(token)
        if session is None:
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        expires_unix_ms = min(
            session.expires_unix_ms,
            self.now_unix_ms() + self._WEBSOCKET_TICKET_TTL_MS,
        )
        ticket = secrets.token_urlsafe(24)
        self._websocket_tickets[ticket] = (token, expires_unix_ms)
        return ticket, expires_unix_ms

    def consume_websocket_ticket(self, ticket: str, *, unix_ms: int, tick: int) -> Session:
        """Resolve and consume a ticket exactly once."""
        source = self._websocket_tickets.pop(ticket, None)
        if source is None or source[1] <= unix_ms:
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        return self.resolve(source[0], unix_ms=unix_ms, tick=tick)

    def restore(self, token: str, session: Session) -> None:
        self.close(token)
        self._tokens[token] = session
        self._by_agent[session.agent_id].add(token)

    def close_agent(self, agent_id: str) -> tuple[Session, ...]:
        closed: list[Session] = []
        for token in sorted(tuple(self._by_agent.get(agent_id, ()))):
            session = self.close(token)
            if session is not None:
                closed.append(session)
        self._by_agent.pop(agent_id, None)
        return tuple(closed)

    def connected_agents(self) -> int:
        self.reap()
        return len(self._by_agent)


class ConformanceAuthority:
    """Single-worker, process-lifetime authority.

    The gateway pins Uvicorn to one worker. A restart rotates the signing secret,
    invalidating every previously minted token as well as the consumed-token set.
    """

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        ttl_s: int = 3_600,
        now_unix_ms: Callable[[], int] | None = None,
    ) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self.ttl_s = ttl_s
        self.now_unix_ms = now_unix_ms or (lambda: time.time_ns() // 1_000_000)
        self._consumed: dict[str, int] = {}

    def mint(
        self,
        checks: Mapping[str, bool],
        *,
        pubkey: str,
        sdk_version: str,
        protocol_version: int,
    ) -> str:
        if set(checks) != CONFORMANCE_CHECKS or not all(checks.values()):
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        _validated_agent_id(pubkey)
        if not sdk_version or protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        payload = {
            "exp": self.now_unix_ms() + self.ttl_s * 1_000,
            "nonce": secrets.token_hex(12),
            "checks": sorted(checks),
            "pubkey": pubkey,
            "sdk_version": sdk_version,
            "protocol_version": protocol_version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        body = _urlsafe(encoded)
        signature = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"cft_{body}.{signature}"

    async def validate(
        self,
        token: str,
        pubkey: str,
        sdk_version: str,
        protocol_version: int,
    ) -> bool:
        try:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            now = self.now_unix_ms()
            self._consumed = {
                item_hash: expires for item_hash, expires in self._consumed.items() if expires > now
            }
            if token_hash in self._consumed:
                return False
            prefix, signature = token.rsplit(".", 1)
            if not prefix.startswith("cft_"):
                return False
            body = prefix[4:]
            expected = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return False
            payload = json.loads(_urlsafe_decode(body))
            valid = (
                isinstance(payload, dict)
                and int(payload["exp"]) > now
                and set(payload["checks"]) == CONFORMANCE_CHECKS
                and payload.get("pubkey") == pubkey
                and payload.get("sdk_version") == sdk_version
                and payload.get("protocol_version") == protocol_version
            )
            if valid:
                self._consumed[token_hash] = int(payload["exp"])
            return valid
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False


RosterReader = Callable[[], Awaitable[Sequence[Mapping[str, Any]]]]
AdmissionReader = Callable[[str], Awaitable[Mapping[str, Any] | None]]
TokenValidator = Callable[[str, str, str, int], Awaitable[bool]]
TickReader = Callable[[], int]
SignatureOperation = Literal["depart", "revoke", "session", "resume"]


class Registrar:
    def __init__(
        self,
        run_id: UUID,
        settings: GatewaySettings,
        queue: GatewayQueue,
        *,
        roster: RosterReader,
        admission_reader: AdmissionReader,
        conformance_validator: TokenValidator | None = None,
        tick_reader: TickReader,
        challenges: ChallengeStore | None = None,
        sessions: SessionRegistry | None = None,
        now_unix_ms: Callable[[], int] | None = None,
    ) -> None:
        self.run_id = run_id
        self.settings = settings
        self.queue = queue
        self.roster = roster
        self.admission_reader = admission_reader
        self.conformance_validator = conformance_validator
        self.tick_reader = tick_reader
        self.now_unix_ms = now_unix_ms or (lambda: time.time_ns() // 1_000_000)
        self.challenges = challenges or ChallengeStore(now_unix_ms=self.now_unix_ms)
        self.sessions = sessions or SessionRegistry(now_unix_ms=self.now_unix_ms)
        self._pending: dict[str, Mapping[str, Any]] = {}
        self._admitted: dict[str, Mapping[str, Any]] = {}
        self._lifecycle_signatures: dict[str, int] = {}
        self._registration_lock = asyncio.Lock()

    def limit_unauthenticated(self, client_ip: str) -> None:
        self.challenges.charge(client_ip)

    async def challenge(self, pubkey_hex: str, *, client_ip: str) -> Mapping[str, Any]:
        value = self.challenges.mint(pubkey_hex, client_ip=client_ip)
        return {
            "challenge": value.value.hex(),
            "expires_unix_ms": value.expires_unix_ms,
        }

    async def register(
        self,
        declaration: Mapping[str, Any],
        sig_hex: str,
        *,
        client_ip: str,
    ) -> Mapping[str, Any]:
        clean = _validate_declaration(declaration)
        pubkey = str(clean["pubkey"])
        challenge = self.challenges.consume(
            pubkey,
            str(clean["challenge"]),
            client_ip=client_ip,
        )
        if not verify(
            pubkey,
            canonical_registration_bytes(challenge.value, clean),
            sig_hex,
        ):
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        token = clean.get("conformance_token")
        if self.settings.registration.require_conformance_token and (
            not isinstance(token, str) or not token
        ):
            raise ProtocolError(ErrorCode.NOT_ADMITTED)
        async with self._registration_lock:
            tick = self.tick_reader()
            closes = self.settings.registration.open_until_tick
            if closes == 0 or (closes > 0 and tick > closes):
                raise ProtocolError(ErrorCode.NOT_ADMITTED)
            self._expire_pending(tick)
            raw_records = (
                tuple(await self.roster())
                + tuple(self._pending.values())
                + tuple(self._admitted.values())
            )
            unique_records: list[Mapping[str, Any]] = []
            seen_agent_ids: set[str] = set()
            seen_pubkeys: set[str] = set()
            for record in raw_records:
                record_agent_id = str(record.get("agent_id") or "")
                record_pubkey = str(record.get("pubkey") or "")
                if (record_agent_id and record_agent_id in seen_agent_ids) or (
                    record_pubkey and record_pubkey in seen_pubkeys
                ):
                    continue
                unique_records.append(record)
                if record_agent_id:
                    seen_agent_ids.add(record_agent_id)
                if record_pubkey:
                    seen_pubkeys.add(record_pubkey)
            records = tuple(unique_records)
            if len(records) >= self.settings.registration.max_external_agents:
                raise ProtocolError(ErrorCode.NOT_ADMITTED)
            agent_id = _validated_agent_id(pubkey)
            if any(
                str(record.get("pubkey", "")) == pubkey
                or str(record.get("agent_id", "")) == agent_id
                for record in records
            ):
                raise ProtocolError(ErrorCode.NOT_ADMITTED)
            operator = str(clean["operator"])
            if (
                sum(str(record.get("operator")) == operator for record in records)
                >= self.settings.registration.registrations_per_operator
            ):
                raise ProtocolError(ErrorCode.NOT_ADMITTED)
            if self.settings.registration.require_conformance_token and (
                self.conformance_validator is None
                or not await self.conformance_validator(
                    str(token),
                    pubkey,
                    str(clean["sdk_version"]),
                    int(clean["protocol_version"]),
                )
            ):
                raise ProtocolError(ErrorCode.NOT_ADMITTED)
            safe_declaration = dict(clean)
            safe_declaration["conformance_token"] = (
                "verified" if isinstance(token, str) and token else None
            )
            queued = {
                "request_type": "register",
                "agent_id": agent_id,
                "queued_tick": tick,
                "declaration": safe_declaration,
                "sig": sig_hex,
            }
            await self.queue.push_registration(queued)
            self._pending[agent_id] = {
                "agent_id": agent_id,
                "pubkey": pubkey,
                "operator": operator,
                "queued_tick": tick,
            }
            return {"agent_id": agent_id, "status": "pending", "queued_tick": tick}

    async def admission(self, agent_id: str) -> Mapping[str, Any]:
        self._expire_pending(self.tick_reader())
        admitted = await self.admission_reader(agent_id)
        if admitted is None:
            if agent_id in self._pending:
                return {"status": "pending", "agent_id": agent_id}
            raise ProtocolError(ErrorCode.NOT_ADMITTED)
        self._pending.pop(agent_id, None)
        if admitted.get("revoked_tick") is not None:
            self._admitted.pop(agent_id, None)
            raise ProtocolError(ErrorCode.REVOKED)
        self._admitted[agent_id] = admitted
        return {
            "status": "admitted",
            "agent_id": agent_id,
        }

    def _expire_pending(self, tick: int) -> None:
        ttl = self.settings.registration.pending_ttl_ticks
        for agent_id, record in tuple(self._pending.items()):
            queued_tick = record.get("queued_tick")
            if (
                isinstance(queued_tick, bool)
                or not isinstance(queued_tick, int)
                or tick >= queued_tick + ttl
            ):
                self._pending.pop(agent_id, None)

    async def open_session(
        self,
        agent_id: str,
        ttl_s: int,
        sig_hex: str,
        delegate_pubkey: str | None,
        transport: str,
        *,
        unix_ms: int,
        sdk_version: str,
    ) -> Mapping[str, Any]:
        admitted = await self.admission_reader(agent_id)
        if admitted is None or admitted.get("revoked_tick") is not None:
            raise ProtocolError(ErrorCode.NOT_ADMITTED)
        pubkey = str(admitted["pubkey"])
        if ttl_s < 1 or ttl_s > self.settings.lifecycle.session_ttl_s:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        server_now = self.now_unix_ms()
        if abs(unix_ms - server_now) > _SKEW_TOLERANCE_MS:
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        try:
            delegate = bytes.fromhex(delegate_pubkey) if delegate_pubkey is not None else None
        except ValueError as exc:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID) from exc
        if delegate is not None and len(delegate) != 32:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        if not verify(
            pubkey,
            canonical_session_bytes(self.run_id, agent_id, unix_ms, ttl_s, delegate),
            sig_hex,
        ):
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        if transport not in {"mcp_stdio", "mcp_http", "rest", "ws"}:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        self._reserve_lifecycle_signature("session", sig_hex)
        custody: Literal["operator", "delegated"] = (
            "delegated" if delegate_pubkey is not None else "operator"
        )
        session = Session(
            session_id=f"ses_{secrets.token_hex(12)}",
            agent_id=agent_id,
            custody=custody,
            delegate_pubkey=delegate_pubkey,
            expires_unix_ms=server_now + ttl_s * 1_000,
            transport=transport,  # type: ignore[arg-type]
            sdk_version=sdk_version,
            pubkey_hex=delegate_pubkey or pubkey,
        )
        token = self.sessions.open(session)
        try:
            await self.queue.push_registration(
                {
                    "request_type": "session_open",
                    "agent_id": agent_id,
                    "queued_tick": self.tick_reader(),
                    "declaration": {
                        "session_id": session.session_id,
                        "custody": custody,
                        "delegate_pubkey": delegate_pubkey,
                        "ttl_s": ttl_s,
                        "transport": transport,
                        "sdk_version": sdk_version,
                        "protocol_version": 1,
                        "expires_unix_ms": session.expires_unix_ms,
                        "client": {
                            "transport": transport,
                            "sdk_version": sdk_version,
                        },
                    },
                    "sig": sig_hex,
                }
            )
        except ProtocolError:
            self.sessions.close(token)
            self._release_lifecycle_signature("session", sig_hex)
            raise
        except (OSError, RedisError) as exc:
            self.sessions.close(token)
            self._release_lifecycle_signature("session", sig_hex)
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED) from exc
        return {
            "session_id": session.session_id,
            "token": token,
            "expires_unix_ms": session.expires_unix_ms,
            "custody": custody,
        }

    async def close_session(self, token: str, *, reason: str = "client_closed") -> None:
        session = self.sessions.close(token)
        if session is None:
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        try:
            await self.queue.push_registration(
                {
                    "request_type": "session_close",
                    "agent_id": session.agent_id,
                    "queued_tick": self.tick_reader(),
                    "declaration": {
                        "session_id": session.session_id,
                        "reason": reason,
                    },
                    "sig": "",
                }
            )
        except ProtocolError:
            self.sessions.restore(token, session)
            raise
        except (OSError, RedisError) as exc:
            self.sessions.restore(token, session)
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED) from exc

    async def revoke(
        self,
        agent_id: str,
        reason: str,
        sig_hex: str,
        *,
        unix_ms: int,
    ) -> int:
        await self._verify_lifecycle("revoke", agent_id, reason, sig_hex, unix_ms)
        tick = self.tick_reader()
        try:
            await self.queue.push_registration(
                {
                    "request_type": "revoke",
                    "agent_id": agent_id,
                    "reason": reason,
                    "revoked_by": "operator",
                    "tick": tick,
                    "sig": sig_hex,
                }
            )
        except (OSError, RedisError, ProtocolError):
            self._release_lifecycle_signature("revoke", sig_hex)
            raise
        self.sessions.close_agent(agent_id)
        return tick

    async def depart(self, agent_id: str, reason: str, sig_hex: str, *, unix_ms: int) -> int:
        await self._verify_lifecycle("depart", agent_id, reason, sig_hex, unix_ms)
        tick = self.tick_reader()
        try:
            await self.queue.push_registration(
                {
                    "request_type": "depart",
                    "agent_id": agent_id,
                    "reason": reason,
                    "tick": tick,
                    "sig": sig_hex,
                }
            )
        except (OSError, RedisError, ProtocolError):
            self._release_lifecycle_signature("depart", sig_hex)
            raise
        self.sessions.close_agent(agent_id)
        return tick

    async def resume(
        self,
        agent_id: str,
        sig_hex: str,
        *,
        unix_ms: int,
    ) -> Mapping[str, Any]:
        admitted = await self.admission_reader(agent_id)
        if admitted is None or admitted.get("revoked_tick") is not None:
            raise ProtocolError(ErrorCode.REVOKED)
        if abs(unix_ms - self.now_unix_ms()) > _SKEW_TOLERANCE_MS:
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        pubkey = str(admitted["pubkey"])
        if not verify(
            pubkey,
            canonical_resume_bytes(self.run_id, agent_id, unix_ms),
            sig_hex,
        ):
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        tick = self.tick_reader()
        naturalised = admitted.get("naturalised_tick")
        grace_until = admitted.get("resume_grace_until_tick")
        if naturalised is None or grace_until is None or tick > int(grace_until):
            raise ProtocolError(ErrorCode.NOT_ADMITTED)
        self._reserve_lifecycle_signature("resume", sig_hex)
        try:
            await self.queue.push_registration(
                {
                    "request_type": "resume",
                    "agent_id": agent_id,
                    "tick": tick,
                    "sig": sig_hex,
                }
            )
        except (OSError, RedisError, ProtocolError):
            self._release_lifecycle_signature("resume", sig_hex)
            raise
        return {"resumed_tick": tick, "gap_ticks": tick - int(naturalised)}

    async def _verify_lifecycle(
        self,
        operation: Literal["depart", "revoke"],
        agent_id: str,
        reason: str,
        sig_hex: str,
        unix_ms: int,
    ) -> Mapping[str, Any]:
        admitted = await self.admission_reader(agent_id)
        if admitted is None or admitted.get("revoked_tick") is not None:
            raise ProtocolError(ErrorCode.REVOKED)
        if abs(unix_ms - self.now_unix_ms()) > _SKEW_TOLERANCE_MS:
            raise ProtocolError(ErrorCode.SESSION_INVALID)
        pubkey = str(admitted["pubkey"])
        preimage = (
            canonical_depart_bytes(self.run_id, agent_id, unix_ms, reason)
            if operation == "depart"
            else canonical_revoke_bytes(self.run_id, agent_id, unix_ms, reason)
        )
        if not verify(pubkey, preimage, sig_hex):
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        self._reserve_lifecycle_signature(operation, sig_hex)
        return admitted

    def _reserve_lifecycle_signature(
        self,
        operation: SignatureOperation,
        sig_hex: str,
    ) -> None:
        now = self.now_unix_ms()
        self._lifecycle_signatures = {
            digest: expires
            for digest, expires in self._lifecycle_signatures.items()
            if expires > now
        }
        digest = self._lifecycle_signature_digest(operation, sig_hex)
        if digest in self._lifecycle_signatures:
            raise ProtocolError(ErrorCode.NONCE_REUSED)
        self._lifecycle_signatures[digest] = now + _SIGNATURE_RESERVATION_TTL_MS

    @staticmethod
    def _lifecycle_signature_digest(operation: SignatureOperation, sig_hex: str) -> str:
        return hashlib.sha256(f"{operation}:{sig_hex}".encode()).hexdigest()

    def _release_lifecycle_signature(
        self,
        operation: SignatureOperation,
        sig_hex: str,
    ) -> None:
        self._lifecycle_signatures.pop(
            self._lifecycle_signature_digest(operation, sig_hex),
            None,
        )


_DECLARATION_FIELDS = {
    "protocol_version",
    "pubkey",
    "display_name",
    "operator",
    "contact",
    "declared_model",
    "declared_model_version",
    "declared_scaffold",
    "scaffold_notes",
    "memory",
    "sdk_version",
    "requested_embodiment",
    "conformance_token",
    "challenge",
}
_REQUIRED_DECLARATION_FIELDS = _DECLARATION_FIELDS - {
    "requested_embodiment",
    "conformance_token",
}
_DECLARATION_TEXT_MAX = {
    "display_name": 100,
    "operator": 128,
    "contact": 256,
    "declared_model": 128,
    "declared_model_version": 128,
    "declared_scaffold": 128,
    "scaffold_notes": 2_000,
    "memory": 32,
    "sdk_version": 64,
    "challenge": 64,
}


def _validate_declaration(declaration: Mapping[str, Any]) -> Mapping[str, Any]:
    if set(declaration) - _DECLARATION_FIELDS:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    if not _REQUIRED_DECLARATION_FIELDS.issubset(declaration):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    if declaration.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    pubkey = declaration.get("pubkey")
    if not isinstance(pubkey, str):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    _validated_agent_id(pubkey)
    for field in _REQUIRED_DECLARATION_FIELDS - {
        "protocol_version",
        "pubkey",
    }:
        value = declaration.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        if len(value) > _DECLARATION_TEXT_MAX[field]:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    if declaration.get("memory") not in {"ours", "ours+private"}:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return dict(declaration)


def _validated_agent_id(pubkey: str) -> str:
    try:
        return agent_id_for(pubkey)
    except ValueError as exc:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID) from exc


def _urlsafe(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _urlsafe_decode(value: str) -> bytes:
    import base64

    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
