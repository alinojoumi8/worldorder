"""Twelve bounded compatibility checks used before registration."""

from __future__ import annotations

from typing import Any

from polis.gateway.sdk.canonical import (
    DOMAIN_REG,
    SignableAction,
    agent_id_for,
    canonical_action_bytes,
    canonical_registration_bytes,
    test_vectors,
    verify,
)


async def run_selftest(client: Any) -> dict[str, Any]:
    errors: dict[str, str] = {}
    try:
        run = await client.run_info()
    except Exception as exc:
        run = {}
        errors["run_info"] = type(exc).__name__
    try:
        action_schema = bool(
            (
                await client._request(
                    "GET",
                    "/v1/schemas/actions.v1.json",
                    authenticated=False,
                )
            ).get("actions")
        )
    except Exception as exc:
        action_schema = False
        errors["action_schema"] = type(exc).__name__
    vectors = test_vectors()
    first = vectors[0]
    preimage = bytes.fromhex(first["preimage_hex"])
    signature = str(first["signature_hex"])
    pubkey = str(first["pubkey_hex"])
    action = first["action"]
    reconstructed = SignableAction(
        run_id=__import__("uuid").UUID(action["run_id"]),
        tick=action["tick"],
        action_id=__import__("uuid").UUID(action["action_id"]),
        nonce=action["nonce"],
        actor_id=action["actor_id"],
        type=action["type"],
        params=action["params"],
        reasoning=action["reasoning"],
        speech=action["speech"],
        extras=action["extras"],
    )
    challenge = bytes(32)
    declaration = {"protocol_version": 1, "pubkey": client.keypair.pubkey_hex}
    registration_preimage = canonical_registration_bytes(challenge, declaration)
    local_signature = client.keypair.sign(preimage)
    mutated_local_signature = client.keypair.sign(preimage + b"\x00")
    checks = {
        "protocol_version": run.get("protocol_version") == 1,
        "run_id": _is_uuid(run.get("run_id")),
        "action_schema": action_schema,
        "vector_count": len(vectors) == 24,
        "vector_preimage": canonical_action_bytes(reconstructed) == preimage,
        "vector_signature": verify(pubkey, preimage, signature),
        "mutated_signature": not verify(pubkey, preimage + b"\x00", signature),
        "agent_id": client.keypair.agent_id == agent_id_for(client.keypair.pubkey_hex),
        "local_signing": verify(
            client.keypair.pubkey_hex,
            preimage,
            local_signature,
        ),
        "registration_domain": registration_preimage.startswith(DOMAIN_REG),
        "tools_declared": _has_minimum_items(run.get("tools_enabled"), 7),
        "deadline_declared": _is_positive_int(run.get("decision_deadline_ms")),
    }
    try:
        result = await client._request(
            "POST",
            "/v1/conformance",
            json={
                "pubkey": client.keypair.pubkey_hex,
                "agent_id": client.keypair.agent_id,
                "sdk_version": "polis-agent-sdk/1.0.0",
                "protocol_version": 1,
                "vector_index": int(first["index"]),
                "preimage_hex": preimage.hex(),
                "vector_signature": signature,
                "local_signature": local_signature,
                "mutated_local_signature": mutated_local_signature,
                "registration_preimage_hex": registration_preimage.hex(),
            },
            authenticated=False,
        )
    except Exception as exc:
        result = {}
        errors["conformance"] = type(exc).__name__
    token = result.get("conformance_token")
    return {
        "ok": all(checks.values()) and isinstance(token, str) and bool(token),
        "checks": checks,
        "conformance_token": token if isinstance(token, str) else None,
        "server": {**dict(result), "transport_errors": errors},
    }


def _is_uuid(value: object) -> bool:
    from uuid import UUID

    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


def _has_minimum_items(value: object, minimum: int) -> bool:
    try:
        return len(value) >= minimum  # type: ignore[arg-type]
    except TypeError:
        return False


def _is_positive_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0
