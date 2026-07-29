"""Canonical bytes and ed25519 primitives for protocol version 1.

This module is deliberately shared by the gateway verifier and the standalone SDK.
There must not be a second implementation of these byte layouts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PROTOCOL_VERSION: Final[int] = 1
DOMAIN_ACT: Final[bytes] = b"POLIS/ACT/1\x00"
DOMAIN_REG: Final[bytes] = b"POLIS/REG/1\x00"
DOMAIN_SES: Final[bytes] = b"POLIS/SES/1\x00"
DOMAIN_RES: Final[bytes] = b"POLIS/RES/1\x00"
DOMAIN_REV: Final[bytes] = b"POLIS/REV/1\x00"
DOMAIN_DEP: Final[bytes] = b"POLIS/DEP/1\x00"
DOMAIN_MEM: Final[bytes] = b"POLIS/MEM/1\x00"

_AGENT_ID = re.compile(r"ag_[0-9a-f]{64}\Z")
_PUBKEY = re.compile(r"[0-9a-f]{64}\Z")
_SIG = re.compile(r"[0-9a-f]{128}\Z")
_MAX_U64 = (1 << 64) - 1
_MAX_U32 = (1 << 32) - 1


def _canonical_bytes(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except rfc8785.CanonicalizationError as exc:
        raise ValueError(f"value is not canonical JSON: {exc}") from exc


@dataclass(frozen=True, slots=True)
class SignableAction:
    run_id: UUID
    tick: int
    action_id: UUID
    nonce: int
    actor_id: str
    type: str
    params: Mapping[str, Any]
    reasoning: str | None = None
    speech: str | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)


def _uint(value: int, width: int, label: str) -> bytes:
    maximum = (1 << (width * 8)) - 1
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{label} must be an unsigned {width * 8}-bit integer")
    return value.to_bytes(width, "big")


def _length_prefixed(value: bytes, width: int, label: str) -> bytes:
    maximum = (1 << (width * 8)) - 1
    if len(value) > maximum:
        raise ValueError(f"{label} is too large for its {width}-byte length prefix")
    return len(value).to_bytes(width, "big") + value


def _agent_bytes(agent_id: str) -> bytes:
    if not _AGENT_ID.fullmatch(agent_id):
        raise ValueError("agent_id must be 'ag_' followed by 64 lowercase hex characters")
    return agent_id.encode("ascii")


def _text_hash(value: str | None, label: str) -> bytes:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{label} must be a string or null")
    payload = b"\x00" if value is None else b"\x01" + value.encode("utf-8")
    return hashlib.sha256(payload).digest()


def canonical_action_bytes(action: SignableAction) -> bytes:
    type_bytes = action.type.encode("ascii")
    if not type_bytes:
        raise ValueError("action type must not be empty")
    params = _canonical_bytes(action.params)
    extras = _canonical_bytes(action.extras)
    return b"".join(
        (
            DOMAIN_ACT,
            action.run_id.bytes,
            _uint(action.tick, 8, "tick"),
            action.action_id.bytes,
            _uint(action.nonce, 8, "nonce"),
            _agent_bytes(action.actor_id),
            _length_prefixed(type_bytes, 2, "action type"),
            _length_prefixed(params, 4, "params"),
            _text_hash(action.reasoning, "reasoning"),
            _text_hash(action.speech, "speech"),
            hashlib.sha256(extras).digest(),
        )
    )


def canonical_registration_bytes(challenge: bytes, declaration: Mapping[str, Any]) -> bytes:
    if len(challenge) != 32:
        raise ValueError("registration challenge must be exactly 32 bytes")
    body = _canonical_bytes(declaration)
    return DOMAIN_REG + challenge + _length_prefixed(body, 4, "declaration")


def canonical_session_bytes(
    run_id: UUID,
    agent_id: str,
    unix_ms: int,
    ttl_s: int,
    delegate_pubkey: bytes | None,
) -> bytes:
    if delegate_pubkey is not None and len(delegate_pubkey) != 32:
        raise ValueError("delegate_pubkey must be exactly 32 bytes")
    return b"".join(
        (
            DOMAIN_SES,
            run_id.bytes,
            _agent_bytes(agent_id),
            _uint(unix_ms, 8, "unix_ms"),
            _uint(ttl_s, 4, "ttl_s"),
            b"\x00" if delegate_pubkey is None else b"\x01" + delegate_pubkey,
        )
    )


def canonical_resume_bytes(run_id: UUID, agent_id: str, unix_ms: int) -> bytes:
    return b"".join(
        (
            DOMAIN_RES,
            run_id.bytes,
            _agent_bytes(agent_id),
            _uint(unix_ms, 8, "unix_ms"),
        )
    )


def canonical_revoke_bytes(run_id: UUID, agent_id: str, unix_ms: int, reason: str) -> bytes:
    return _canonical_lifecycle_bytes(DOMAIN_REV, run_id, agent_id, unix_ms, reason)


def canonical_depart_bytes(run_id: UUID, agent_id: str, unix_ms: int, reason: str) -> bytes:
    return _canonical_lifecycle_bytes(DOMAIN_DEP, run_id, agent_id, unix_ms, reason)


def _canonical_lifecycle_bytes(
    domain: bytes,
    run_id: UUID,
    agent_id: str,
    unix_ms: int,
    reason: str,
) -> bytes:
    reason_bytes = reason.encode("utf-8")
    return b"".join(
        (
            domain,
            run_id.bytes,
            _agent_bytes(agent_id),
            _uint(unix_ms, 8, "unix_ms"),
            _length_prefixed(reason_bytes, 4, "reason"),
        )
    )


def canonical_memory_bytes(
    run_id: UUID,
    agent_id: str,
    tick: int,
    nonce: int,
    body: Mapping[str, Any],
) -> bytes:
    encoded = _canonical_bytes(body)
    return b"".join(
        (
            DOMAIN_MEM,
            run_id.bytes,
            _agent_bytes(agent_id),
            _uint(tick, 8, "tick"),
            _uint(nonce, 8, "nonce"),
            _length_prefixed(encoded, 4, "memory body"),
        )
    )


def sign(sk_bytes: bytes, preimage: bytes) -> str:
    if len(sk_bytes) != 32:
        raise ValueError("ed25519 private key must be exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(sk_bytes).sign(preimage).hex()


def verify(pubkey_hex: str, preimage: bytes, sig_hex: str) -> bool:
    if not _PUBKEY.fullmatch(pubkey_hex) or not _SIG.fullmatch(sig_hex):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
            bytes.fromhex(sig_hex), preimage
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def agent_id_for(pubkey_hex: str) -> str:
    if not _PUBKEY.fullmatch(pubkey_hex):
        raise ValueError("public key must be 64 lowercase hexadecimal characters")
    return f"ag_{pubkey_hex}"


def test_vectors() -> list[Mapping[str, Any]]:
    """Return 24 deterministic, self-contained cross-language action vectors."""
    vectors: list[Mapping[str, Any]] = []
    for index in range(24):
        private = hashlib.sha256(f"polis-vector-{index}".encode()).digest()
        public = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes_raw().hex()
        action = SignableAction(
            run_id=UUID(int=index + 1),
            tick=index * 17,
            action_id=UUID(int=(index + 1) << 64 | index),
            nonce=index,
            actor_id=agent_id_for(public),
            type=("NULL_ACTION" if index == 0 else f"VECTOR_{index:02d}"),
            params={
                "index": index,
                "empty": "" if index % 2 == 0 else None,
                "unicode": "München 東京" if index % 3 == 0 else "plain",
                "nested": {"z": index, "a": [True, False]},
            },
            reasoning=None if index % 2 == 0 else f"reason {index}",
            speech="" if index % 5 == 0 else f"speech {index}",
            extras={"belief_updates": []} if index % 4 == 0 else {},
        )
        preimage = canonical_action_bytes(action)
        vectors.append(
            {
                "index": index,
                "private_key_hex": private.hex(),
                "pubkey_hex": public,
                "agent_id": action.actor_id,
                "action": {
                    "run_id": str(action.run_id),
                    "tick": action.tick,
                    "action_id": str(action.action_id),
                    "nonce": action.nonce,
                    "actor_id": action.actor_id,
                    "type": action.type,
                    "params": action.params,
                    "reasoning": action.reasoning,
                    "speech": action.speech,
                    "extras": action.extras,
                },
                "preimage_hex": preimage.hex(),
                "signature_hex": sign(private, preimage),
            }
        )
    return vectors


__all__ = [
    "PROTOCOL_VERSION",
    "SignableAction",
    "agent_id_for",
    "canonical_action_bytes",
    "canonical_depart_bytes",
    "canonical_memory_bytes",
    "canonical_registration_bytes",
    "canonical_resume_bytes",
    "canonical_revoke_bytes",
    "canonical_session_bytes",
    "sign",
    "test_vectors",
    "verify",
]
