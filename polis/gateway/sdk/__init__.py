"""Standalone client-side primitives for the POLIS external-agent protocol."""

from polis.gateway.sdk.canonical import (
    PROTOCOL_VERSION,
    SignableAction,
    agent_id_for,
    canonical_action_bytes,
    canonical_depart_bytes,
    canonical_memory_bytes,
    canonical_registration_bytes,
    canonical_resume_bytes,
    canonical_revoke_bytes,
    canonical_session_bytes,
    sign,
    test_vectors,
    verify,
)
from polis.gateway.sdk.keys import Keypair
from polis.gateway.sdk.text import InWorldText

__all__ = [
    "PROTOCOL_VERSION",
    "InWorldText",
    "Keypair",
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
