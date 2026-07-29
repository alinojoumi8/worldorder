from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from polis.gateway.sdk.canonical import (
    SignableAction,
    _canonical_bytes,
    agent_id_for,
    canonical_action_bytes,
    canonical_depart_bytes,
    canonical_memory_bytes,
    canonical_registration_bytes,
    canonical_resume_bytes,
    canonical_revoke_bytes,
    canonical_session_bytes,
    sign,
    verify,
)
from polis.gateway.sdk.canonical import (
    test_vectors as canonical_test_vectors,
)
from polis.gateway.sdk.keys import Keypair


def test_all_published_vectors_are_byte_exact_and_verify() -> None:
    vectors = canonical_test_vectors()

    assert len(vectors) == 24
    for vector in vectors:
        action = vector["action"]
        signable = SignableAction(
            run_id=UUID(action["run_id"]),
            tick=action["tick"],
            action_id=UUID(action["action_id"]),
            nonce=action["nonce"],
            actor_id=action["actor_id"],
            type=action["type"],
            params=action["params"],
            reasoning=action["reasoning"],
            speech=action["speech"],
            extras=action["extras"],
        )
        preimage = canonical_action_bytes(signable)
        assert preimage.hex() == vector["preimage_hex"]
        assert sign(bytes.fromhex(vector["private_key_hex"]), preimage) == vector["signature_hex"]
        assert verify(vector["pubkey_hex"], preimage, vector["signature_hex"])


def test_mutating_every_action_field_invalidates_the_signature() -> None:
    key = Keypair.from_private_bytes(bytes(range(32)))
    base = SignableAction(
        UUID("11111111-1111-4111-8111-111111111111"),
        42,
        UUID("22222222-2222-4222-8222-222222222222"),
        7,
        key.agent_id,
        "BUY_GOODS",
        {"sku": "bread", "qty": 1},
        "reason",
        "speech",
        {"belief_updates": []},
    )
    signature = key.sign(canonical_action_bytes(base))
    mutations = (
        replace(base, run_id=UUID("33333333-3333-4333-8333-333333333333")),
        replace(base, tick=43),
        replace(base, action_id=UUID("44444444-4444-4444-8444-444444444444")),
        replace(base, nonce=8),
        replace(base, actor_id=f"ag_{'0' * 64}"),
        replace(base, type="SELL_GOODS"),
        replace(base, params={"sku": "bread", "qty": 2}),
        replace(base, reasoning="changed"),
        replace(base, speech="changed"),
        replace(base, extras={"goal_updates": []}),
    )

    assert all(
        not verify(key.pubkey_hex, canonical_action_bytes(mutation), signature)
        for mutation in mutations
    )


def test_domain_separators_are_not_interchangeable() -> None:
    key = Keypair.from_private_bytes(b"\x07" * 32)
    run_id = UUID(int=9)
    declaration = {"pubkey": key.pubkey_hex}
    registration = canonical_registration_bytes(b"\x11" * 32, declaration)
    session = canonical_session_bytes(run_id, key.agent_id, 1000, 60, None)
    resume = canonical_resume_bytes(run_id, key.agent_id, 1000)
    revoke = canonical_revoke_bytes(run_id, key.agent_id, 1000, "depart")
    depart = canonical_depart_bytes(run_id, key.agent_id, 1000, "depart")
    memory = canonical_memory_bytes(run_id, key.agent_id, 3, 4, {"text": "remember"})
    signature = key.sign(registration)

    assert verify(key.pubkey_hex, registration, signature)
    assert depart != revoke
    assert all(
        not verify(key.pubkey_hex, other, signature)
        for other in (session, resume, revoke, depart, memory)
    )


def test_session_delegate_presence_is_unambiguous() -> None:
    run_id = UUID(int=9)
    agent_id = agent_id_for("12" * 32)

    absent = canonical_session_bytes(run_id, agent_id, 1_000, 60, None)
    zero_key = canonical_session_bytes(run_id, agent_id, 1_000, 60, bytes(32))

    assert absent != zero_key
    assert absent.endswith(b"\x00")
    assert zero_key.endswith(b"\x01" + bytes(32))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"number": 1e30}, b'{"number":1e+30}'),
        ({"number": 4.5}, b'{"number":4.5}'),
        ({"number": 2e-3}, b'{"number":0.002}'),
        ({"number": 1e-27}, b'{"number":1e-27}'),
        ({"number": -0.0}, b'{"number":0}'),
    ],
)
def test_rfc8785_number_serialization_vectors(value: object, expected: bytes) -> None:
    assert _canonical_bytes(value) == expected


def test_rfc8785_rejects_integers_outside_the_safe_cross_language_domain() -> None:
    with pytest.raises(ValueError, match="canonical JSON"):
        _canonical_bytes({"number": 2**53})


def test_null_and_empty_signed_text_have_distinct_preimages() -> None:
    key = Keypair.from_private_bytes(b"\x08" * 32)
    base = SignableAction(
        UUID(int=1),
        1,
        UUID(int=2),
        0,
        key.agent_id,
        "NULL_ACTION",
        {},
        reasoning=None,
        speech=None,
    )

    assert canonical_action_bytes(base) != canonical_action_bytes(replace(base, reasoning=""))
    assert canonical_action_bytes(base) != canonical_action_bytes(replace(base, speech=""))


@pytest.mark.parametrize(
    ("pubkey", "valid"),
    [
        ("a" * 64, True),
        ("A" * 64, False),
        ("a" * 63, False),
        ("g" * 64, False),
    ],
)
def test_agent_id_derivation_is_strict(pubkey: str, valid: bool) -> None:
    if valid:
        assert agent_id_for(pubkey) == f"ag_{pubkey}"
    else:
        with pytest.raises(ValueError):
            agent_id_for(pubkey)
