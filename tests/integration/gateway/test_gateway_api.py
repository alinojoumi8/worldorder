from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from polis.config.settings import load_settings
from polis.gateway.app import GatewayMetrics, GatewayRuntime, create_app
from polis.gateway.auth import Registrar, Session, SessionRegistry
from polis.gateway.limits import LimitConfig, LimitSet
from polis.gateway.queue import GatewayQueue
from polis.gateway.sdk.keys import Keypair
from polis.gateway.tools import TickSnapshot, TickState, ToolService
from polis.gateway.verify import ActionIdLRU, NonceStore, Verifier

RUN_ID = UUID(int=7)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.lists: dict[str, list[bytes]] = {}

    async def eval(self, script: str, numkeys: int, *args: object) -> int:
        del script, numkeys
        key = str(args[0])
        target = self.lists.setdefault(key, [])
        if len(args) >= 3:
            cap = int(args[1])
            if len(target) >= cap:
                return -1
            value = args[2]
            assert isinstance(value, bytes)
            target.append(value)
        return len(target)

    async def rpush(self, key: str, *values: object) -> int:
        target = self.lists.setdefault(key, [])
        target.extend(value for value in values if isinstance(value, bytes))
        return len(target)

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: bytes, *, ex: int) -> bool:
        del ex
        self.values[key] = value
        return True

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, ()))


class FakeReader:
    async def fetch(
        self, query: str, params: tuple[Any, ...] | list[Any] | None = None
    ) -> list[dict[str, Any]]:
        del query, params
        return []


class FakeScorecardReader(FakeReader):
    async def fetch(
        self, query: str, params: tuple[Any, ...] | list[Any] | None = None
    ) -> list[dict[str, Any]]:
        del params
        if "SELECT status,last_tick,tags FROM runs" in query:
            return [{"status": "completed", "last_tick": 5, "tags": []}]
        return []


def _runtime() -> tuple[GatewayRuntime, Session, FakeRedis]:
    settings = load_settings(Path("configs/baseline.yaml"))

    def clock() -> int:
        return 1_000

    key = Keypair.from_private_bytes(b"\x41" * 32)
    session = Session(
        "ses_1",
        key.agent_id,
        "operator",
        None,
        2_000,
        "rest",
        "test",
        key.pubkey_hex,
    )
    sessions = SessionRegistry(now_unix_ms=clock)
    token = sessions.open(session)
    session = Session(
        session.session_id,
        session.agent_id,
        session.custody,
        session.delegate_pubkey,
        session.expires_unix_ms,
        session.transport,
        f"{session.sdk_version}:{token}",
        session.pubkey_hex,
    )
    redis = FakeRedis()
    queue = GatewayQueue(redis, RUN_ID, max_queued=4)
    limits = LimitSet(LimitConfig(action_slots=1))
    ticks = TickState(TickSnapshot(5, "day 1", 1, 2_000, False))
    bundle = {"version": 1, "actions": {"NULL_ACTION": {"type": "object"}}}
    tools = ToolService(
        run_id=RUN_ID,
        settings=settings.gateway,
        db=FakeReader(),
        queue=queue,
        verifier=Verifier(
            RUN_ID,
            bundle,
            NonceStore(),
            ActionIdLRU(8),
            charge_request=lambda agent_id, tick: limits.charge(agent_id, "request", tick),
            take_slot=limits.slot_take,
            release_slot=limits.slot_release,
        ),
        limits=limits,
        ticks=ticks,
    )
    registrar = Registrar(
        RUN_ID,
        settings.gateway,
        queue,
        roster=lambda: _empty([]),
        admission_reader=lambda agent_id: _empty(None),
        tick_reader=lambda: ticks.snapshot().tick,
        sessions=sessions,
        now_unix_ms=clock,
    )
    runtime = GatewayRuntime(
        tools=tools,
        registrar=registrar,
        sessions=sessions,
        action_bundle=bundle,
        metrics=GatewayMetrics(),
        clock=clock,
    )
    return runtime, session, redis


async def _empty(value: Any) -> Any:
    return value


async def test_public_metadata_health_and_schema_routes() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, _, _ = _runtime()
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        run = await client.get("/v1/run")
        health = await client.get("/healthz")
        schemas = await client.get("/v1/schemas/actions.v1.json")
        vectors = await client.get("/v1/schemas/testvectors.json")

    assert run.status_code == 200
    assert run.json()["run_id"] == str(RUN_ID)
    assert len(run.json()["tools_enabled"]) == 7
    assert health.json()["ok"] is True
    assert schemas.json()["actions"]["NULL_ACTION"]["type"] == "object"
    assert len(vectors.json()) == 24


async def test_observe_response_preserves_engine_bytes_exactly() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, session, redis = _runtime()
    token = session.sdk_version.split(":", 1)[1]
    blob = b'{"tick":5,"self":{"name":"Nikos"},"legal_actions":[]}'
    redis.values[f"polis:obs:{RUN_ID}:5:{session.agent_id}"] = blob
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/observe", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.content == blob


async def test_mcp_lists_the_same_enabled_tools() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, session, _ = _runtime()
    token = session.sdk_version.split(":", 1)[1]
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {tool.name for tool in runtime.tools.listed_tools()}


async def test_mcp_protocol_failures_use_json_rpc_error_envelopes() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, session, _ = _runtime()
    token = session.sdk_version.split(":", 1)[1]
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": []},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 9
    assert payload["error"]["code"] == -32000
    assert payload["error"]["data"]["code"] == "SCHEMA_INVALID"


async def test_websocket_ticket_exchange_never_returns_the_bearer_token() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, session, _ = _runtime()
    token = session.sdk_version.split(":", 1)[1]
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/ws-ticket",
            headers={"Authorization": f"Bearer {token}"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert payload["subprotocol"].startswith("polis.v1.ticket.")
    assert token not in payload["subprotocol"]
    assert payload["expires_unix_ms"] == 2_000


async def test_scorecard_requires_a_session_and_rejects_cross_run_queries() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, session, _ = _runtime()
    token = session.sdk_version.split(":", 1)[1]
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        anonymous = await client.get("/v1/scorecard")
        cross_run = await client.get(
            "/v1/scorecard",
            params={"run_id": str(UUID(int=99))},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "SESSION_INVALID"
    assert cross_run.status_code == 404
    assert cross_run.json()["error"]["code"] == "NOT_VISIBLE"


async def test_scorecard_rejects_negative_and_future_ticks() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, session, _ = _runtime()
    runtime.tools.db = FakeScorecardReader()
    token = session.sdk_version.split(":", 1)[1]
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        negative = await client.get(
            "/v1/scorecard",
            params={"at_tick": -1},
            headers=headers,
        )
        future = await client.get(
            "/v1/scorecard",
            params={"at_tick": 6},
            headers=headers,
        )

    assert negative.status_code == 422
    assert negative.json()["error"]["code"] == "SCHEMA_INVALID"
    assert future.status_code == 422
    assert future.json()["error"]["code"] == "SCHEMA_INVALID"


async def test_conformance_shares_the_unauthenticated_per_ip_rate_limit() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, _, _ = _runtime()
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = [await client.post("/v1/conformance", json={}) for _ in range(11)]

    assert all(response.status_code == 503 for response in responses[:10])
    assert responses[-1].status_code == 429
    assert responses[-1].json()["error"]["code"] == "RATE_LIMITED"


async def test_oversized_and_malformed_requests_use_uniform_errors() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))
    runtime, _, _ = _runtime()
    app = create_app(settings, run_id=RUN_ID, runtime=runtime)
    streamed_chunks: list[int] = []

    async def oversized_stream() -> AsyncIterator[bytes]:
        streamed_chunks.append(1)
        yield b"x" * (settings.gateway.limits.max_request_bytes + 1)
        streamed_chunks.append(2)
        yield b"must-not-be-consumed"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        oversized = await client.post(
            "/v1/register/challenge",
            content=b"x" * (settings.gateway.limits.max_request_bytes + 1),
        )
        malformed = await client.post(
            "/v1/register/challenge",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        streamed = await client.post(
            "/v1/register/challenge",
            content=oversized_stream(),
            headers={"content-type": "application/json"},
        )
        non_finite = await client.post(
            "/v1/register/challenge",
            content=b'{"pubkey":NaN}',
            headers={"content-type": "application/json"},
        )

    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert streamed.status_code == 413
    assert streamed.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert streamed_chunks == [1]
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "SCHEMA_INVALID"
    assert non_finite.status_code == 422
    assert non_finite.json()["error"]["code"] == "SCHEMA_INVALID"
