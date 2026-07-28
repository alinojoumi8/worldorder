from __future__ import annotations

import pytest

from polis.gateway.app import _verify_conformance_evidence
from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.sdk.canonical import (
    canonical_registration_bytes,
)
from polis.gateway.sdk.canonical import (
    test_vectors as signing_vectors,
)
from polis.gateway.sdk.keys import Keypair


def _evidence() -> tuple[dict[str, object], Keypair]:
    key = Keypair.from_private_bytes(b"\x65" * 32)
    vector = signing_vectors()[0]
    preimage = bytes.fromhex(str(vector["preimage_hex"]))
    registration = canonical_registration_bytes(
        bytes(32),
        {"protocol_version": 1, "pubkey": key.pubkey_hex},
    )
    return (
        {
            "pubkey": key.pubkey_hex,
            "agent_id": key.agent_id,
            "sdk_version": "polis-agent-sdk/1.0.0",
            "protocol_version": 1,
            "vector_index": 0,
            "preimage_hex": preimage.hex(),
            "vector_signature": vector["signature_hex"],
            "local_signature": key.sign(preimage),
            "mutated_local_signature": key.sign(preimage + b"\x00"),
            "registration_preimage_hex": registration.hex(),
        },
        key,
    )


def test_conformance_checks_are_computed_from_verifiable_evidence() -> None:
    evidence, key = _evidence()

    checks, subject = _verify_conformance_evidence(evidence)

    assert all(checks.values())
    assert subject["pubkey"] == key.pubkey_hex


def test_conformance_rejects_a_client_claim_without_valid_local_signature() -> None:
    evidence, _ = _evidence()
    evidence["local_signature"] = "0" * 128

    with pytest.raises(ProtocolError) as caught:
        _verify_conformance_evidence(evidence)

    assert caught.value.code is ErrorCode.SCHEMA_INVALID


def test_conformance_rejects_a_mutation_signature_not_supplied_by_the_client() -> None:
    evidence, _ = _evidence()
    evidence["mutated_local_signature"] = evidence["local_signature"]

    with pytest.raises(ProtocolError) as caught:
        _verify_conformance_evidence(evidence)

    assert caught.value.code is ErrorCode.SCHEMA_INVALID
