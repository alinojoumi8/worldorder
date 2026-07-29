"""Async REST client for an external citizen."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID, uuid4

import httpx

from polis.gateway.sdk.canonical import (
    PROTOCOL_VERSION,
    SignableAction,
    canonical_action_bytes,
    canonical_registration_bytes,
    canonical_resume_bytes,
    canonical_session_bytes,
)
from polis.gateway.sdk.keys import Keypair
from polis.gateway.sdk.text import decode_untrusted


@dataclass(frozen=True, slots=True)
class ProtocolResponseError(Exception):
    status_code: int
    code: str
    payload: Mapping[str, Any]

    def __str__(self) -> str:
        return f"{self.code} ({self.status_code})"


def _invalid_response(field: str, payload: Mapping[str, Any]) -> NoReturn:
    raise ProtocolResponseError(
        200,
        "TRANSPORT_INVALID_RESPONSE",
        {"error": {"code": "TRANSPORT_INVALID_RESPONSE", "field": field}, "body": payload},
    )


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        _invalid_response(field, payload)
    return value


def _required_mapping(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        _invalid_response(field, payload)
    return value


def _required_int(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid_response(field, payload)
    return value


class AgentClient:
    def __init__(
        self,
        base_url: str,
        keypair: Keypair,
        *,
        token: str | None = None,
        timeout_s: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.keypair = keypair
        self.token = token
        self.http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_s,
            transport=transport,
        )

    async def __aenter__(self) -> AgentClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.http.aclose()

    async def run_info(self) -> Mapping[str, Any]:
        return await self._request("GET", "/v1/run", authenticated=False)

    async def register(self, declaration: Mapping[str, Any]) -> Mapping[str, Any]:
        challenge = await self._request(
            "POST",
            "/v1/register/challenge",
            json={"pubkey": self.keypair.pubkey_hex},
            authenticated=False,
        )
        challenge_hex = _required_text(challenge, "challenge")
        try:
            challenge_bytes = bytes.fromhex(challenge_hex)
        except ValueError:
            _invalid_response("challenge", challenge)
        if len(challenge_bytes) != 32:
            _invalid_response("challenge", challenge)
        complete = {
            **declaration,
            "protocol_version": PROTOCOL_VERSION,
            "pubkey": self.keypair.pubkey_hex,
            "challenge": challenge_hex,
        }
        signature = self.keypair.sign(canonical_registration_bytes(challenge_bytes, complete))
        return await self._request(
            "POST",
            "/v1/register",
            json=complete,
            headers={"X-Polis-Signature": signature},
            authenticated=False,
        )

    async def admission(self) -> Mapping[str, Any]:
        return await self._request(
            "GET",
            f"/v1/admission/{self.keypair.agent_id}",
            authenticated=False,
        )

    async def open_session(
        self,
        *,
        run_id: UUID,
        ttl_s: int = 3_600,
        delegate_pubkey: str | None = None,
        sdk_version: str = "polis-agent-sdk/1.0.0",
    ) -> Mapping[str, Any]:
        unix_ms = time.time_ns() // 1_000_000
        delegate = bytes.fromhex(delegate_pubkey) if delegate_pubkey is not None else None
        signature = self.keypair.sign(
            canonical_session_bytes(
                run_id,
                self.keypair.agent_id,
                unix_ms,
                ttl_s,
                delegate,
            )
        )
        result = await self._request(
            "POST",
            "/v1/session",
            json={
                "agent_id": self.keypair.agent_id,
                "ttl_s": ttl_s,
                "unix_ms": unix_ms,
                "delegate_pubkey": delegate_pubkey,
                "sdk_version": sdk_version,
            },
            headers={"X-Polis-Signature": signature},
            authenticated=False,
        )
        self.token = _required_text(result, "token")
        return result

    async def resume(self, *, run_id: UUID) -> Mapping[str, Any]:
        unix_ms = time.time_ns() // 1_000_000
        signature = self.keypair.sign(
            canonical_resume_bytes(run_id, self.keypair.agent_id, unix_ms)
        )
        return await self._request(
            "POST",
            "/v1/resume",
            json={"agent_id": self.keypair.agent_id, "unix_ms": unix_ms},
            headers={"X-Polis-Signature": signature},
            authenticated=False,
        )

    async def whoami(self) -> Mapping[str, Any]:
        return await self._request("GET", "/v1/whoami")

    async def observe(self) -> Mapping[str, Any]:
        return await self._request("GET", "/v1/observe")

    async def act(
        self,
        action_type: str,
        params: Mapping[str, Any],
        *,
        reasoning: str | None = None,
        speech: str | None = None,
        extras: Mapping[str, Any] | None = None,
        tick: int | None = None,
        nonce: int | None = None,
        action_id: UUID | None = None,
    ) -> Mapping[str, Any]:
        run = await self.run_info()
        if tick is None or nonce is None:
            identity = await self.whoami()
            protocol = _required_mapping(identity, "protocol")
            tick = _required_int(protocol, "tick") if tick is None else tick
            nonce = _required_int(protocol, "next_nonce") if nonce is None else nonce
        raw_run_id = _required_text(run, "run_id")
        try:
            resolved_run_id = UUID(raw_run_id)
        except ValueError:
            _invalid_response("run_id", run)
        identifier = action_id or uuid4()
        signable = SignableAction(
            resolved_run_id,
            tick,
            identifier,
            nonce,
            self.keypair.agent_id,
            action_type,
            params,
            reasoning,
            speech,
            extras or {},
        )
        signature = self.keypair.sign(canonical_action_bytes(signable))
        body = {
            "action_id": str(identifier),
            "tick": tick,
            "nonce": nonce,
            "type": action_type,
            "params": dict(params),
            "reasoning": reasoning,
            "speech": speech,
            "extras": dict(extras or {}),
        }
        return await self._request(
            "POST",
            "/v1/act",
            json=body,
            headers={"X-Polis-Signature": signature},
        )

    async def recall(
        self, query: str, *, k: int = 12, since_tick: int | None = None
    ) -> Mapping[str, Any]:
        params: dict[str, Any] = {"query": query, "k": k}
        if since_tick is not None:
            params["since_tick"] = since_tick
        return await self._request("GET", "/v1/recall", params=params)

    async def remember(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._request("POST", "/v1/remember", json=dict(body))

    async def wait_for_tick(
        self,
        *,
        after_tick: int = 0,
        timeout_ms: int = 30_000,
    ) -> Mapping[str, Any]:
        return await self._request(
            "GET",
            "/v1/tick",
            params={"after_tick": after_tick, "timeout_ms": timeout_ms},
        )

    async def selftest(self) -> Mapping[str, Any]:
        from polis.gateway.sdk.selftest import run_selftest

        return await run_selftest(self)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        if authenticated:
            if self.token is None:
                raise ProtocolResponseError(
                    401,
                    "SESSION_INVALID",
                    {"error": {"code": "SESSION_INVALID"}},
                )
            headers["Authorization"] = f"Bearer {self.token}"
        response = await self.http.request(method, path, headers=headers, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProtocolResponseError(
                response.status_code,
                "TRANSPORT_INVALID_RESPONSE",
                {"body": response.text[:500]},
            ) from exc
        if response.is_error:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = str(error.get("code", "TRANSPORT_ERROR"))
            raise ProtocolResponseError(response.status_code, code, payload)
        if not isinstance(payload, dict):
            raise ProtocolResponseError(
                response.status_code,
                "TRANSPORT_INVALID_RESPONSE",
                {"body": payload},
            )
        decoded = decode_untrusted(payload)
        if not isinstance(decoded, Mapping):
            raise ProtocolResponseError(
                response.status_code,
                "TRANSPORT_INVALID_RESPONSE",
                {"body": decoded},
            )
        return decoded
