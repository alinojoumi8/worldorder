"""Isolated FastAPI application for REST, WebSocket, and MCP transports."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID

import redis.asyncio as redis
from fastapi import (
    Depends,
    FastAPI,
    Header,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from polis.config.settings import Settings
from polis.gateway.auth import (
    ConformanceAuthority,
    Registrar,
    Session,
    SessionRegistry,
)
from polis.gateway.errors import HTTP_STATUS, ErrorCode, ProtocolError, envelope
from polis.gateway.limits import LimitConfig, LimitSet
from polis.gateway.queue import GatewayQueue
from polis.gateway.scorecard import compute as compute_scorecard
from polis.gateway.sdk.canonical import (
    DOMAIN_REG,
    PROTOCOL_VERSION,
    agent_id_for,
    canonical_registration_bytes,
    test_vectors,
    verify,
)
from polis.gateway.stream import AgentConnectionLimiter, BoundedFrameBuffer
from polis.gateway.tools import TickState, ToolService
from polis.gateway.verify import ActionIdLRU, NonceStore, Verifier

Clock = Callable[[], int]
_WEBSOCKET_TICKET_PREFIX = "polis.v1.ticket."


class _UnavailableReader:
    async def fetch(self, query: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        del query, params
        return []


@dataclass(slots=True)
class GatewayMetrics:
    requests: int = 0
    errors: int = 0
    actions_queued: int = 0

    def prometheus(self) -> str:
        return (
            "# TYPE polis_gateway_requests_total counter\n"
            f"polis_gateway_requests_total {self.requests}\n"
            "# TYPE polis_gateway_errors_total counter\n"
            f"polis_gateway_errors_total {self.errors}\n"
            "# TYPE polis_gateway_actions_queued_total counter\n"
            f"polis_gateway_actions_queued_total {self.actions_queued}\n"
        )


@dataclass(slots=True)
class GatewayRuntime:
    tools: ToolService
    registrar: Registrar
    sessions: SessionRegistry
    action_bundle: Mapping[str, Any]
    metrics: GatewayMetrics
    clock: Clock
    conformance: ConformanceAuthority | None = None
    close: Callable[[], Awaitable[None]] | None = None


async def _empty_roster() -> Sequence[Mapping[str, Any]]:
    return ()


async def _no_admission(agent_id: str) -> Mapping[str, Any] | None:
    del agent_id
    return None


async def _reject_token(
    token: str,
    pubkey: str,
    sdk_version: str,
    protocol_version: int,
) -> bool:
    del token, pubkey, sdk_version, protocol_version
    return False


def _action_bundle() -> Mapping[str, Any]:
    path = Path(__file__).resolve().parents[1] / "events" / "schemas" / "actions.v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("action schema bundle must be a JSON object")
    return value


def build_runtime(
    settings: Settings,
    *,
    run_id: UUID,
    db: Any | None = None,
    roster: Callable[[], Awaitable[Sequence[Mapping[str, Any]]]] = _empty_roster,
    admission_reader: Callable[[str], Awaitable[Mapping[str, Any] | None]] = _no_admission,
    conformance_validator: Callable[[str, str, str, int], Awaitable[bool]] = _reject_token,
    ticks: TickState | None = None,
    now_unix_ms: Clock | None = None,
    nonce_initial: Mapping[str, int] | None = None,
) -> GatewayRuntime:
    clock = now_unix_ms or (lambda: time.time_ns() // 1_000_000)
    client = redis.from_url(settings.store.redis_url, decode_responses=False)
    action_slots = settings.actions.slots_per_tick.for_profile(settings.clock.profile)
    limits = LimitSet(
        LimitConfig(
            requests_per_tick=settings.gateway.limits.requests_per_tick,
            requests_per_second=settings.gateway.limits.requests_per_second,
            recall_queries_per_tick=settings.gateway.limits.recall_queries_per_tick,
            history_queries_per_tick=settings.gateway.limits.history_queries_per_tick,
            memory_writes_per_tick=settings.gateway.limits.memory_writes_per_tick,
            action_slots=action_slots,
            suspension_ticks=settings.gateway.lifecycle.suspension_ticks,
        )
    )
    queue = GatewayQueue(
        cast(Any, client),
        run_id,
        max_queued=settings.gateway.registration.max_external_agents * action_slots * 2,
        ttl_s=settings.gateway.lifecycle.session_ttl_s,
        redis_timeout_ms=settings.gateway.deadline.drain_timeout_ms,
    )
    bundle = _action_bundle()
    state = ticks or TickState()
    sessions = SessionRegistry(now_unix_ms=clock)
    authority = ConformanceAuthority(now_unix_ms=clock)

    async def validate_conformance(
        token: str,
        pubkey: str,
        sdk_version: str,
        protocol_version: int,
    ) -> bool:
        return await authority.validate(
            token,
            pubkey,
            sdk_version,
            protocol_version,
        ) or await conformance_validator(
            token,
            pubkey,
            sdk_version,
            protocol_version,
        )

    verifier = Verifier(
        run_id,
        bundle,
        NonceStore(nonce_initial),
        ActionIdLRU(
            max(
                1,
                4 * action_slots * settings.gateway.registration.max_external_agents,
            )
        ),
        tick_skew_tolerance=settings.gateway.deadline.tick_skew_tolerance,
        max_request_bytes=settings.gateway.limits.max_request_bytes,
        max_params_bytes=8_192,
        charge_request=lambda agent_id, tick: limits.charge(agent_id, "request", tick),
        take_slot=limits.slot_take,
        release_slot=limits.slot_release,
        strike=lambda agent_id, tick, trigger: limits.strike(agent_id, tick, _trigger(trigger)),
        now_unix_ms=clock,
        injection_policy=settings.gateway.security.injection_policy,
    )
    tools = ToolService(
        run_id=run_id,
        settings=settings.gateway,
        db=db or _UnavailableReader(),
        queue=queue,
        verifier=verifier,
        limits=limits,
        ticks=state,
        now_unix_ms=clock,
    )
    registrar = Registrar(
        run_id,
        settings.gateway,
        queue,
        roster=roster,
        admission_reader=admission_reader,
        conformance_validator=validate_conformance,
        tick_reader=lambda: state.snapshot().tick,
        sessions=sessions,
        now_unix_ms=clock,
    )
    return GatewayRuntime(
        tools=tools,
        registrar=registrar,
        sessions=sessions,
        action_bundle=bundle,
        metrics=GatewayMetrics(),
        clock=clock,
        conformance=authority,
        close=client.aclose,
    )


def _trigger(value: str) -> Any:
    if value not in {"schema", "signature", "rate"}:
        raise ValueError(f"unknown strike trigger: {value}")
    return value


def create_app(
    settings: Settings,
    *,
    run_id: UUID,
    runtime: GatewayRuntime | None = None,
) -> FastAPI:
    run_id_for_app = run_id
    resolved = runtime or build_runtime(settings, run_id=run_id)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.gateway = resolved
        yield
        if resolved.close is not None:
            await resolved.close()

    app = FastAPI(title="POLIS Gateway", version="1", lifespan=lifespan)
    app.state.gateway = resolved
    websocket_connections = AgentConnectionLimiter(settings.gateway.limits.ws_connections_per_agent)

    @app.middleware("http")
    async def request_limits(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        resolved.metrics.requests += 1
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                parsed_length = int(content_length)
                too_large = (
                    parsed_length < 0 or parsed_length > settings.gateway.limits.max_request_bytes
                )
            except ValueError:
                too_large = True
            if too_large:
                error = ProtocolError(ErrorCode.PAYLOAD_TOO_LARGE)
                return _error_response(error, resolved.tools.ticks.snapshot().tick)
        if request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > settings.gateway.limits.max_request_bytes:
                    error = ProtocolError(ErrorCode.PAYLOAD_TOO_LARGE)
                    return _error_response(error, resolved.tools.ticks.snapshot().tick)
            request._body = bytes(body)
        return await call_next(request)

    @app.exception_handler(ProtocolError)
    async def protocol_error_handler(request: Request, error: ProtocolError) -> JSONResponse:
        del request
        resolved.metrics.errors += 1
        return _error_response(error, resolved.tools.ticks.snapshot().tick)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request, error
        resolved.metrics.errors += 1
        protocol_error = ProtocolError(ErrorCode.SCHEMA_INVALID)
        return _error_response(protocol_error, resolved.tools.ticks.snapshot().tick)

    def session_dependency(
        authorization: str | None = Header(default=None),
    ) -> Session:
        token = _bearer(authorization)
        snapshot = resolved.tools.ticks.snapshot()
        return resolved.sessions.resolve(
            token,
            unix_ms=resolved.clock(),
            tick=snapshot.tick,
        )

    @app.get("/healthz")
    async def healthz() -> Mapping[str, Any]:
        snapshot = await resolved.tools.refresh_tick()
        try:
            queue_depth = await resolved.tools.queue.action_depth(snapshot.tick)
            ok = True
        except (OSError, TimeoutError, ProtocolError):
            queue_depth = -1
            ok = False
        return {
            "ok": ok,
            "tick": snapshot.tick,
            "queue_depth": queue_depth,
            "connected_agents": resolved.sessions.connected_agents(),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        return resolved.metrics.prometheus()

    @app.get("/v1/run")
    async def run_info() -> Mapping[str, Any]:
        snapshot = await resolved.tools.refresh_tick()
        registration_until = settings.gateway.registration.open_until_tick
        registration_open = registration_until == -1 or (
            registration_until > 0 and snapshot.tick <= registration_until
        )
        return {
            "run_id": str(run_id),
            "protocol_version": PROTOCOL_VERSION,
            "tick": snapshot.tick,
            "clock_profile": settings.clock.profile,
            "action_slots": settings.actions.slots_per_tick.for_profile(settings.clock.profile),
            "decision_deadline_ms": settings.gateway.deadline.decision_deadline_ms,
            "registration_open": registration_open,
            "tools_enabled": [spec.name for spec in resolved.tools.listed_tools()],
        }

    @app.get("/v1/schemas/actions.v1.json")
    async def action_schemas() -> Mapping[str, Any]:
        return resolved.action_bundle

    @app.get("/v1/schemas/testvectors.json")
    async def signing_vectors() -> Sequence[Mapping[str, Any]]:
        return test_vectors()

    @app.post("/v1/conformance")
    async def conformance(request: Request) -> Mapping[str, Any]:
        client_ip = request.client.host if request.client is not None else "unknown"
        resolved.registrar.limit_unauthenticated(client_ip)
        body = await _json_body(request)
        if resolved.conformance is None:
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED)
        checks, subject = _verify_conformance_evidence(
            body,
        )
        return {
            "conformance_token": resolved.conformance.mint(
                checks,
                pubkey=subject["pubkey"],
                sdk_version=subject["sdk_version"],
                protocol_version=subject["protocol_version"],
            ),
            "checks": checks,
        }

    @app.post("/v1/register/challenge")
    async def registration_challenge(request: Request) -> Mapping[str, Any]:
        body = await _json_body(request)
        pubkey = body.get("pubkey")
        if not isinstance(pubkey, str):
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        client_ip = request.client.host if request.client is not None else "unknown"
        return await resolved.registrar.challenge(pubkey, client_ip=client_ip)

    @app.post("/v1/register")
    async def register(
        request: Request,
        x_polis_signature: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        if x_polis_signature is None:
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        client_ip = request.client.host if request.client is not None else "unknown"
        return await resolved.registrar.register(
            await _json_body(request),
            x_polis_signature,
            client_ip=client_ip,
        )

    @app.get("/v1/admission/{agent_id}")
    async def admission(agent_id: str) -> Mapping[str, Any]:
        return await resolved.registrar.admission(agent_id)

    @app.post("/v1/session")
    async def open_session(
        request: Request,
        x_polis_signature: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        if x_polis_signature is None:
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        body = await _json_body(request)
        return await resolved.registrar.open_session(
            str(body.get("agent_id", "")),
            _int(body, "ttl_s"),
            x_polis_signature,
            _optional_string(body, "delegate_pubkey"),
            "rest",
            unix_ms=_int(body, "unix_ms"),
            sdk_version=str(body.get("sdk_version", "unknown")),
        )

    @app.delete("/v1/session", status_code=204)
    async def close_session(
        authorization: str | None = Header(default=None),
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Response:
        del session
        await resolved.registrar.close_session(_bearer(authorization))
        return Response(status_code=204)

    @app.post("/v1/ws-ticket")
    async def websocket_ticket(
        response: Response,
        authorization: str | None = Header(default=None),
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        del session
        ticket, expires_unix_ms = resolved.sessions.issue_websocket_ticket(_bearer(authorization))
        response.headers["Cache-Control"] = "no-store"
        return {
            "ticket": ticket,
            "expires_unix_ms": expires_unix_ms,
            "subprotocol": f"{_WEBSOCKET_TICKET_PREFIX}{ticket}",
        }

    @app.get("/v1/whoami")
    async def whoami(
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        return await resolved.tools.call("polis_who_am_i", {}, session=session)

    @app.get("/v1/observe")
    async def observe(
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Response:
        blob = await resolved.tools.observe_blob(session)
        return Response(content=blob, media_type="application/json")

    @app.post("/v1/act")
    async def act(
        request: Request,
        session: Session = Depends(session_dependency),  # noqa: B008
        x_polis_signature: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        body = dict(await _json_body(request))
        if x_polis_signature is not None:
            body["sig"] = x_polis_signature
        result = await resolved.tools.call("polis_act", body, session=session)
        resolved.metrics.actions_queued += 1
        return result

    @app.get("/v1/recall")
    async def recall(
        query: str,
        k: int = 12,
        type_: Annotated[str | None, Query(alias="type")] = None,
        since_tick: int | None = None,
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        return await resolved.tools.call(
            "polis_recall",
            {"query": query, "k": k, "type": type_, "since_tick": since_tick},
            session=session,
        )

    @app.post("/v1/remember")
    async def remember(
        request: Request,
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        return await resolved.tools.call(
            "polis_remember", await _json_body(request), session=session
        )

    @app.get("/v1/market")
    async def market(
        symbols: Annotated[list[str] | None, Query()] = None,
        skus: Annotated[list[str] | None, Query()] = None,
        depth: int = 3,
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        return await resolved.tools.call(
            "polis_market_quote",
            {"symbols": symbols or [], "skus": skus or [], "depth": depth},
            session=session,
        )

    @app.get("/v1/history")
    async def history(
        query: str,
        kinds: Annotated[list[str] | None, Query()] = None,
        since_tick: int | None = None,
        limit: int = 10,
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        return await resolved.tools.call(
            "polis_search_history",
            {
                "query": query,
                "kinds": kinds or [],
                "since_tick": since_tick,
                "limit": limit,
            },
            session=session,
        )

    @app.get("/v1/tick")
    async def wait_for_tick(
        after_tick: int,
        timeout_ms: int = 30_000,
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        return await resolved.tools.call(
            "polis_wait_for_tick",
            {"after_tick": after_tick, "timeout_ms": timeout_ms},
            session=session,
        )

    @app.get("/v1/scorecard")
    async def scorecard(
        requested_run_id: Annotated[UUID | None, Query(alias="run_id")] = None,
        at_tick: int | None = None,
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Mapping[str, Any]:
        if requested_run_id is not None and requested_run_id != run_id_for_app:
            raise ProtocolError(ErrorCode.NOT_VISIBLE)
        if at_tick is not None and at_tick < 0:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        snapshot = resolved.tools.ticks.snapshot()
        resolved.tools.limits.charge(session.agent_id, "request", snapshot.tick)
        requested_run = run_id_for_app
        rows = await resolved.tools.db.fetch(
            "SELECT status,last_tick,tags FROM runs WHERE run_id=%s",
            (requested_run,),
        )
        if not rows:
            raise ProtocolError(ErrorCode.NOT_VISIBLE)
        run = rows[0]
        status = str(run["status"])
        if status == "running" and not settings.gateway.arena.live_scorecard:
            raise ProtocolError(ErrorCode.NOT_VISIBLE)
        last_tick = int(run.get("last_tick") or 0)
        if at_tick is not None and at_tick > last_tick:
            raise ProtocolError(ErrorCode.SCHEMA_INVALID)
        score_tick = at_tick if at_tick is not None else last_tick
        gate_status = {
            f"V{number}": f"V{number}:pass" in tuple(run.get("tags") or ())
            for number in range(1, 6)
        }
        score_rows = await compute_scorecard(
            resolved.tools.db,
            requested_run,
            at_tick=score_tick,
            interval_ticks=settings.gateway.arena.scoring_interval_ticks,
            run_tags=tuple(run.get("tags") or ()),
            gates=gate_status,
            external_miss_rate_max=settings.research.gates.external_miss_rate_max,
            min_driven_fraction=settings.gateway.arena.min_driven_fraction,
        )
        return {
            "run_id": str(requested_run),
            "at_tick": score_tick,
            "dimensions": ["W", "W_growth", "R", "C", "P", "I", "S", "L", "liveness"],
            "agents": [asdict(row) for row in score_rows if row.agent_id == session.agent_id],
            "composite": None,
        }

    @app.post("/v1/depart")
    async def depart(
        request: Request,
        x_polis_signature: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        if x_polis_signature is None:
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        body = await _json_body(request)
        tick = await resolved.registrar.depart(
            str(body.get("agent_id", "")),
            str(body.get("reason", "")),
            x_polis_signature,
            unix_ms=_int(body, "unix_ms"),
        )
        return {"naturalised_at_tick": tick}

    @app.post("/v1/revoke")
    async def revoke(
        request: Request,
        x_polis_signature: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        if x_polis_signature is None:
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        body = await _json_body(request)
        tick = await resolved.registrar.revoke(
            str(body.get("agent_id", "")),
            str(body.get("reason", "")),
            x_polis_signature,
            unix_ms=_int(body, "unix_ms"),
        )
        return {"revoked_tick": tick}

    @app.post("/v1/resume")
    async def resume(
        request: Request,
        x_polis_signature: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        if x_polis_signature is None:
            raise ProtocolError(ErrorCode.BAD_SIGNATURE)
        body = await _json_body(request)
        return await resolved.registrar.resume(
            str(body.get("agent_id", "")),
            x_polis_signature,
            unix_ms=_int(body, "unix_ms"),
        )

    @app.post("/mcp")
    async def mcp(
        request: Request,
        session: Session = Depends(session_dependency),  # noqa: B008
    ) -> Response:
        identifier: Any = None
        try:
            body = await _json_body(request)
            identifier = body.get("id")
            method = body.get("method")
            if method == "notifications/initialized":
                return Response(status_code=202)
            if method == "initialize":
                result: Mapping[str, Any] = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "polis", "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "inputSchema": spec.input_schema,
                        }
                        for spec in resolved.tools.listed_tools()
                    ]
                }
            elif method == "tools/call":
                params = body.get("params")
                if not isinstance(params, Mapping):
                    raise ProtocolError(ErrorCode.SCHEMA_INVALID)
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, Mapping):
                    raise ProtocolError(ErrorCode.SCHEMA_INVALID)
                structured = await resolved.tools.call(name, arguments, session=session)
                result = {
                    "content": [{"type": "text", "text": json.dumps(structured)}],
                    "structuredContent": structured,
                    "isError": False,
                }
            elif method == "ping":
                result = {}
            else:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
            return JSONResponse({"jsonrpc": "2.0", "id": identifier, "result": result})
        except ProtocolError as exc:
            tick = resolved.tools.ticks.snapshot().tick
            protocol_error = envelope(exc, tick=tick)["error"]
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {
                        "code": -32000,
                        "message": str(exc),
                        "data": protocol_error,
                    },
                }
            )

    @app.websocket("/v1/stream")
    async def stream(websocket: WebSocket) -> None:
        snapshot = await resolved.tools.refresh_tick()
        try:
            credential, is_ticket = _websocket_token(websocket)
            if is_ticket:
                session = resolved.sessions.consume_websocket_ticket(
                    credential,
                    unix_ms=resolved.clock(),
                    tick=snapshot.tick,
                )
            else:
                session = resolved.sessions.resolve(
                    credential,
                    unix_ms=resolved.clock(),
                    tick=snapshot.tick,
                )
        except ProtocolError:
            await websocket.close(code=1008, reason="authentication required")
            return
        if not websocket_connections.acquire(session.agent_id):
            await websocket.close(code=1013, reason="connection limit")
            return
        try:
            await websocket.accept(
                subprotocol=_accepted_subprotocol(websocket) if is_ticket else None
            )
            frames = BoundedFrameBuffer()
            tasks = (
                asyncio.create_task(
                    _stream_producer(
                        resolved.tools,
                        session,
                        frames,
                        initial=snapshot,
                    )
                ),
                asyncio.create_task(_stream_writer(websocket, frames)),
                asyncio.create_task(
                    _stream_receiver(
                        websocket,
                        resolved.tools,
                        session,
                        frames,
                        max_frame_bytes=settings.gateway.limits.max_frame_bytes,
                    )
                ),
            )
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                await frames.close()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            websocket_connections.release(session.agent_id)

    return app


async def _stream_producer(
    tools: ToolService,
    session: Session,
    frames: BoundedFrameBuffer,
    *,
    initial: Any,
) -> None:
    current_tick = -1
    observation_tick = -1
    sealed_tick = -1
    snapshot = initial
    while True:
        if snapshot.tick != current_tick:
            current_tick = snapshot.tick
            observation_tick = -1
            sealed_tick = -1
            await frames.put(
                {
                    "type": "tick.open",
                    "tick": snapshot.tick,
                    "sim_time": snapshot.sim_time,
                    "deadline_ms_remaining": snapshot.deadline_ms_remaining,
                    "action_slots": tools.limits.slots_remaining(session.agent_id, snapshot.tick),
                }
            )
        if observation_tick != snapshot.tick:
            blob = await tools.queue.read_observation(snapshot.tick, session.agent_id)
            if blob is not None:
                try:
                    observation = json.loads(blob)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    observation = None
                if isinstance(observation, dict):
                    await frames.put(
                        {
                            "type": "observation",
                            "tick": snapshot.tick,
                            "observation": observation,
                        }
                    )
                    observation_tick = snapshot.tick
        if snapshot.sealed and sealed_tick != snapshot.tick:
            remaining = tools.limits.slots_remaining(session.agent_id, snapshot.tick)
            await frames.put(
                {
                    "type": "tick.sealed",
                    "tick": snapshot.tick,
                    "accepted": remaining == 0,
                    "missed": remaining > 0,
                }
            )
            sealed_tick = snapshot.tick
        if snapshot.run_status != "running":
            await frames.put(
                {
                    "type": "run.ended",
                    "tick": snapshot.tick,
                    "status": snapshot.run_status,
                    "last_tick": snapshot.tick,
                    "halt_reason": None,
                }
            )
            return
        await asyncio.sleep(0.025)
        snapshot = await tools.refresh_tick()


async def _stream_writer(websocket: WebSocket, frames: BoundedFrameBuffer) -> None:
    sequence = 0
    while True:
        frame = await frames.get()
        if frame is None:
            return
        frame["seq"] = sequence
        sequence += 1
        await websocket.send_json(frame)


async def _stream_receiver(
    websocket: WebSocket,
    tools: ToolService,
    session: Session,
    frames: BoundedFrameBuffer,
    *,
    max_frame_bytes: int,
) -> None:
    try:
        while True:
            text = await websocket.receive_text()
            if len(text.encode()) > max_frame_bytes:
                await websocket.close(code=1009, reason="frame too large")
                return
            try:
                frame = json.loads(text, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ProtocolError):
                continue
            if not isinstance(frame, dict):
                continue
            frame_type = frame.get("type")
            if frame_type == "ping":
                await frames.put(
                    {
                        "type": "pong",
                        "tick": tools.ticks.snapshot().tick,
                    }
                )
            elif frame_type == "act" and isinstance(frame.get("action"), Mapping):
                try:
                    result = await tools.call(
                        "polis_act",
                        frame["action"],
                        session=session,
                    )
                    tick = tools.ticks.snapshot().tick
                    receipt: dict[str, Any] = {
                        **result,
                        "type": "action.receipt",
                        "tick": tick,
                    }
                except ProtocolError as error:
                    receipt = {
                        "type": "action.receipt",
                        "tick": tools.ticks.snapshot().tick,
                        "accepted": False,
                        "error": envelope(
                            error,
                            tick=tools.ticks.snapshot().tick,
                        )["error"],
                    }
                except Exception:
                    tick = tools.ticks.snapshot().tick
                    receipt = {
                        "type": "action.receipt",
                        "tick": tick,
                        "accepted": False,
                        "error": envelope(
                            ProtocolError(ErrorCode.GATEWAY_DEGRADED),
                            tick=tick,
                        )["error"],
                    }
                await frames.put(receipt)
    except WebSocketDisconnect:
        return


def _verify_conformance_evidence(
    body: Mapping[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    pubkey = body.get("pubkey")
    agent_id = body.get("agent_id")
    sdk_version = body.get("sdk_version")
    protocol_version = body.get("protocol_version")
    vector_index = body.get("vector_index")
    preimage_hex = body.get("preimage_hex")
    vector_signature = body.get("vector_signature")
    local_signature = body.get("local_signature")
    mutated_local_signature = body.get("mutated_local_signature")
    registration_preimage_hex = body.get("registration_preimage_hex")
    if (
        not isinstance(pubkey, str)
        or not isinstance(agent_id, str)
        or not isinstance(sdk_version, str)
        or not sdk_version
        or isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or isinstance(vector_index, bool)
        or not isinstance(vector_index, int)
        or not isinstance(preimage_hex, str)
        or not isinstance(vector_signature, str)
        or not isinstance(local_signature, str)
        or not isinstance(mutated_local_signature, str)
        or not isinstance(registration_preimage_hex, str)
    ):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    vectors = test_vectors()
    if not 0 <= vector_index < len(vectors):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    try:
        derived_agent_id = agent_id_for(pubkey)
        supplied_preimage = bytes.fromhex(preimage_hex)
        supplied_registration = bytes.fromhex(registration_preimage_hex)
    except ValueError as exc:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID) from exc
    vector = vectors[vector_index]
    expected_preimage = bytes.fromhex(str(vector["preimage_hex"]))
    expected_vector_signature = str(vector["signature_hex"])
    vector_pubkey = str(vector["pubkey_hex"])
    expected_registration = canonical_registration_bytes(
        bytes(32),
        {"protocol_version": PROTOCOL_VERSION, "pubkey": pubkey},
    )
    checks = {
        "protocol_version": protocol_version == PROTOCOL_VERSION,
        "vector_count": len(vectors) == 24,
        "vector_preimage": supplied_preimage == expected_preimage,
        "vector_signature": vector_signature == expected_vector_signature
        and verify(vector_pubkey, expected_preimage, vector_signature),
        "mutated_signature": verify(
            pubkey,
            expected_preimage + b"\x00",
            mutated_local_signature,
        )
        and not verify(pubkey, expected_preimage, mutated_local_signature),
        "agent_id": agent_id == derived_agent_id,
        "local_signing": verify(pubkey, expected_preimage, local_signature),
        "registration_domain": supplied_registration == expected_registration
        and supplied_registration.startswith(DOMAIN_REG),
    }
    if not all(checks.values()):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return checks, {
        "pubkey": pubkey,
        "sdk_version": sdk_version,
        "protocol_version": protocol_version,
    }


def _error_response(error: ProtocolError, tick: int) -> JSONResponse:
    return JSONResponse(envelope(error, tick=tick), status_code=HTTP_STATUS[error.code])


def _bearer(value: str | None) -> str:
    if value is None or not value.startswith("Bearer ") or len(value) <= 7:
        raise ProtocolError(ErrorCode.SESSION_INVALID)
    return value[7:]


def _websocket_token(websocket: WebSocket) -> tuple[str, bool]:
    """Return a bearer token or a short-lived one-time browser ticket."""
    authorization = websocket.headers.get("authorization")
    if authorization is not None:
        return _bearer(authorization), False
    for protocol in websocket.scope.get("subprotocols", ()):
        if protocol.startswith(_WEBSOCKET_TICKET_PREFIX) and len(protocol) > len(
            _WEBSOCKET_TICKET_PREFIX
        ):
            return cast(str, protocol[len(_WEBSOCKET_TICKET_PREFIX) :]), True
    raise ProtocolError(ErrorCode.SESSION_INVALID)


def _accepted_subprotocol(websocket: WebSocket) -> str | None:
    for protocol in websocket.scope.get("subprotocols", ()):
        if protocol.startswith(_WEBSOCKET_TICKET_PREFIX):
            return cast(str, protocol)
    return None


async def _json_body(request: Request) -> Mapping[str, Any]:
    try:
        raw = (await request.body()).decode("utf-8")
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID) from exc
    if not isinstance(value, dict):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return value


def _reject_json_constant(value: str) -> Any:
    del value
    raise ProtocolError(ErrorCode.SCHEMA_INVALID)


def _int(body: Mapping[str, Any], field: str) -> int:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return value


def _optional_string(body: Mapping[str, Any], field: str) -> str | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return value
