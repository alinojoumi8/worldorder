"""Cheap-checks-first verification for signed external actions."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import jsonschema

from polis.config.canon import canonical_bytes
from polis.gateway.auth import Session
from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.injection import scan_inbound, sim_aware_score
from polis.gateway.sdk.canonical import SignableAction, canonical_action_bytes, verify


@dataclass(frozen=True, slots=True)
class VerifiedSubmission:
    agent_id: str
    action_id: UUID
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


class NonceStore:
    def __init__(self, initial: Mapping[str, int] | None = None) -> None:
        self._last = dict(initial or {})

    def next_nonce(self, agent_id: str) -> int:
        return self._last.get(agent_id, -1) + 1

    def check(self, agent_id: str, nonce: int) -> None:
        if nonce < self.next_nonce(agent_id):
            raise ProtocolError(ErrorCode.NONCE_REUSED)

    def accept(self, agent_id: str, nonce: int, tick: int) -> None:
        del tick
        self.check(agent_id, nonce)
        self._last[agent_id] = nonce

    def rollback(self, agent_id: str, nonce: int, previous: int | None) -> None:
        if self._last.get(agent_id) != nonce:
            return
        if previous is None:
            self._last.pop(agent_id, None)
        else:
            self._last[agent_id] = previous


class ActionIdLRU:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("action id LRU capacity must be positive")
        self.capacity = capacity
        self._items: OrderedDict[UUID, None] = OrderedDict()

    def check(self, action_id: UUID) -> None:
        if action_id in self._items:
            raise ProtocolError(ErrorCode.DUPLICATE_ACTION_ID)

    def accept(self, action_id: UUID) -> None:
        self.check(action_id)
        self._items[action_id] = None
        if len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def discard(self, action_id: UUID) -> None:
        self._items.pop(action_id, None)

    def __len__(self) -> int:
        return len(self._items)


class Verifier:
    def __init__(
        self,
        run_id: UUID,
        bundle: Mapping[str, Any],
        nonces: NonceStore,
        seen: ActionIdLRU,
        *,
        tick_skew_tolerance: int = 0,
        max_request_bytes: int = 65_536,
        max_params_bytes: int = 8_192,
        charge_request: Callable[[str, int], None] | None = None,
        take_slot: Callable[[str, int], int] | None = None,
        release_slot: Callable[[str, int], None] | None = None,
        strike: Callable[[str, int, str], int] | None = None,
        now_unix_ms: Callable[[], int] | None = None,
        now_received_ms: Callable[[], int] | None = None,
        stage_observer: Callable[[str], None] | None = None,
        injection_policy: str = "flag",
    ) -> None:
        if tick_skew_tolerance < 0:
            raise ValueError("tick_skew_tolerance cannot be negative")
        actions = bundle.get("actions")
        if not isinstance(actions, Mapping):
            raise ValueError("action schema bundle must contain an actions mapping")
        self.run_id = run_id
        self.bundle = actions
        self._validators = {
            action_type: jsonschema.Draft202012Validator(schema)
            for action_type, schema in actions.items()
        }
        self.nonces = nonces
        self.seen = seen
        self.tick_skew_tolerance = tick_skew_tolerance
        self.max_request_bytes = max_request_bytes
        self.max_params_bytes = max_params_bytes
        self.charge_request = charge_request
        self.take_slot = take_slot
        self.release_slot = release_slot
        self.strike = strike
        self.now_unix_ms = now_unix_ms or (lambda: time.time_ns() // 1_000_000)
        self.now_received_ms = now_received_ms or (lambda: time.time_ns() // 1_000_000)
        self.stage_observer = stage_observer
        self.injection_policy = injection_policy
        self._reservations: dict[UUID, tuple[str, int, int | None, int]] = {}

    def _stage(self, name: str) -> None:
        if self.stage_observer is not None:
            self.stage_observer(name)

    def _protocol_error(
        self, code: ErrorCode, *, session: Session, tick: int, trigger: str | None = None
    ) -> ProtocolError:
        strikes = self.strike(session.agent_id, tick, trigger) if trigger and self.strike else 0
        return ProtocolError(code, strikes=strikes)

    def check(
        self,
        body: Mapping[str, Any],
        *,
        session: Session,
        current_tick: int,
        sealed: bool,
    ) -> VerifiedSubmission:
        self._purge_stale_reservations(current_tick)
        self._stage("size")
        try:
            encoded = canonical_bytes(body)
        except Exception as exc:
            raise self._protocol_error(
                ErrorCode.SCHEMA_INVALID,
                session=session,
                tick=current_tick,
                trigger="schema",
            ) from exc
        if len(encoded) > self.max_request_bytes:
            raise self._protocol_error(
                ErrorCode.PAYLOAD_TOO_LARGE,
                session=session,
                tick=current_tick,
                trigger="schema",
            )

        self._stage("session")
        if not session.active_at(unix_ms=self.now_unix_ms(), tick=current_tick):
            code = ErrorCode.REVOKED if session.revoked else ErrorCode.SESSION_INVALID
            if (
                not session.revoked
                and session.suspended_until_tick is not None
                and current_tick < session.suspended_until_tick
            ):
                code = ErrorCode.SUSPENDED
            raise ProtocolError(code)

        self._stage("rate")
        if self.charge_request is not None:
            self.charge_request(session.agent_id, current_tick)

        self._stage("tick")
        try:
            tick = _integer(body, "tick")
        except ProtocolError as exc:
            raise self._protocol_error(
                exc.code,
                session=session,
                tick=current_tick,
                trigger="schema",
            ) from exc
        if abs(tick - current_tick) > self.tick_skew_tolerance:
            raise ProtocolError(ErrorCode.TICK_MISMATCH)
        if sealed:
            raise ProtocolError(ErrorCode.LATE)

        self._stage("nonce")
        try:
            nonce = _integer(body, "nonce")
            action_id = _uuid(body, "action_id")
        except ProtocolError as exc:
            raise self._protocol_error(
                exc.code,
                session=session,
                tick=current_tick,
                trigger="schema",
            ) from exc
        self.nonces.check(session.agent_id, nonce)
        self.seen.check(action_id)

        self._stage("schema")
        action_type = body.get("type")
        if not isinstance(action_type, str) or action_type not in self.bundle:
            raise self._protocol_error(
                ErrorCode.UNKNOWN_ACTION_TYPE,
                session=session,
                tick=current_tick,
                trigger="schema",
            )
        params = body.get("params")
        if not isinstance(params, Mapping):
            raise self._protocol_error(
                ErrorCode.SCHEMA_INVALID,
                session=session,
                tick=current_tick,
                trigger="schema",
            )
        if len(canonical_bytes(params)) > self.max_params_bytes:
            raise self._protocol_error(
                ErrorCode.PAYLOAD_TOO_LARGE,
                session=session,
                tick=current_tick,
                trigger="schema",
            )
        try:
            self._validators[action_type].validate(params)
        except jsonschema.ValidationError as exc:
            raise self._protocol_error(
                ErrorCode.SCHEMA_INVALID,
                session=session,
                tick=current_tick,
                trigger="schema",
            ) from exc
        try:
            reasoning = _optional_text(body, "reasoning", max_length=2_000)
            speech = _optional_text(body, "speech", max_length=1_000)
            extras = _extras(body)
        except ProtocolError as exc:
            raise self._protocol_error(
                exc.code,
                session=session,
                tick=current_tick,
                trigger="schema",
            ) from exc
        sig = body.get("sig")
        if not isinstance(sig, str):
            raise self._protocol_error(
                ErrorCode.SCHEMA_INVALID,
                session=session,
                tick=current_tick,
                trigger="schema",
            )
        action = SignableAction(
            run_id=self.run_id,
            tick=tick,
            action_id=action_id,
            nonce=nonce,
            actor_id=session.agent_id,
            type=action_type,
            params=params,
            reasoning=reasoning,
            speech=speech,
            extras=extras,
        )

        self._stage("signature")
        if not verify(session.pubkey_hex, canonical_action_bytes(action), sig):
            raise self._protocol_error(
                ErrorCode.BAD_SIGNATURE,
                session=session,
                tick=current_tick,
                trigger="signature",
            )

        audit: dict[str, Any] = {}
        scanned_text = "\n".join(text for text in (reasoning, speech) if text is not None)
        if scanned_text:
            hit = scan_inbound(scanned_text, source_ref=str(action_id), channel="action")
            if hit is not None:
                audit["injection"] = {
                    "pattern_id": hit.pattern_id,
                    "sample_hash": hit.sample_hash,
                    "direction": hit.direction,
                    "channel": "action",
                    "action_taken": self.injection_policy,
                }
            awareness = sim_aware_score(scanned_text)
            if awareness > 0:
                audit["sim_aware"] = {
                    "surface": "action",
                    "confidence": awareness,
                    "sample_hash": hashlib.sha256(scanned_text.encode()).hexdigest(),
                }
        submission = VerifiedSubmission(
            agent_id=session.agent_id,
            action_id=action_id,
            tick=tick,
            nonce=nonce,
            type=action_type,
            params=dict(params),
            reasoning=reasoning,
            speech=speech,
            extras=dict(extras),
            sig=sig,
            session_id=session.session_id,
            received_ms=self.now_received_ms(),
            audit=audit,
        )
        previous_nonce = self.nonces.next_nonce(session.agent_id) - 1
        slot_reserved = False
        if self.take_slot is not None:
            self.take_slot(session.agent_id, current_tick)
            slot_reserved = True
        try:
            self.nonces.accept(session.agent_id, nonce, tick)
            self.seen.accept(action_id)
            self._reservations[action_id] = (
                session.agent_id,
                nonce,
                previous_nonce if previous_nonce >= 0 else None,
                current_tick,
            )
        except Exception:
            self.nonces.rollback(
                session.agent_id,
                nonce,
                previous_nonce if previous_nonce >= 0 else None,
            )
            self.seen.discard(action_id)
            if slot_reserved and self.release_slot is not None:
                self.release_slot(session.agent_id, current_tick)
            raise
        return submission

    def _purge_stale_reservations(self, current_tick: int) -> None:
        stale = tuple(
            action_id
            for action_id, reservation in self._reservations.items()
            if reservation[3] < current_tick
        )
        for action_id in stale:
            self._reservations.pop(action_id, None)

    def commit(self, submission: VerifiedSubmission) -> None:
        self._reservations.pop(submission.action_id, None)

    def rollback(self, submission: VerifiedSubmission) -> None:
        reservation = self._reservations.pop(submission.action_id, None)
        if reservation is None:
            return
        agent_id, nonce, previous, tick = reservation
        self.nonces.rollback(agent_id, nonce, previous)
        self.seen.discard(submission.action_id)
        if self.release_slot is not None:
            self.release_slot(agent_id, tick)


def _integer(body: Mapping[str, Any], field: str) -> int:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return value


def _uuid(body: Mapping[str, Any], field: str) -> UUID:
    value = body.get(field)
    if not isinstance(value, str):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    try:
        return UUID(value)
    except ValueError as exc:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID) from exc


def _optional_text(
    body: Mapping[str, Any],
    field: str,
    *,
    max_length: int,
) -> str | None:
    value = body.get(field)
    if value is not None and not isinstance(value, str):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    if isinstance(value, str) and len(value) > max_length:
        raise ProtocolError(ErrorCode.PAYLOAD_TOO_LARGE)
    return value


def _extras(body: Mapping[str, Any]) -> Mapping[str, Any]:
    explicit = body.get("extras")
    if explicit is not None:
        if not isinstance(explicit, Mapping):
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        return dict(explicit)
    result: dict[str, Any] = {}
    for field_name in ("belief_updates", "goal_updates"):
        if field_name in body:
            result[field_name] = body[field_name]
    return result
