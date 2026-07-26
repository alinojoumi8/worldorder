from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from polis.config.metric_catalogue import METRICS, UNAVAILABLE_M1_METRICS
from polis.config.runtime_time import utc_now_naive
from polis.config.settings import Settings, load_settings
from polis.events.kinds import KIND_REGISTRY
from polis.observatory.live import LiveHub, apply_client_message
from polis.store.engine import Database

API_PREFIX = "/api/v1"


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for key, value in row.items()
    }


async def _freshness(db: Database, run_id: UUID) -> dict[str, Any]:
    rows = await db.fetch(
        """
        SELECT r.last_tick,
               COALESCE((SELECT MAX(seq) FROM events e WHERE e.run_id=r.run_id),0) as as_of_seq,
               COALESCE((SELECT MIN(as_of_tick) FROM agents a WHERE a.run_id=r.run_id),0)
                   as projection_tick,
               h.updated_at
        FROM runs r LEFT JOIN engine_heartbeats h ON h.run_id=r.run_id
        WHERE r.run_id=%s
        """,
        (run_id,),
    )
    if not rows:
        raise HTTPException(404, "run not found")
    row = rows[0]
    engine_tick = int(row["last_tick"])
    projection_tick = int(row["projection_tick"])
    updated_at = row["updated_at"]
    heartbeat_age = (
        (utc_now_naive() - updated_at).total_seconds() if isinstance(updated_at, datetime) else None
    )
    return {
        "as_of_tick": projection_tick,
        "as_of_seq": int(row["as_of_seq"]),
        "engine": {
            "tick": engine_tick,
            "projection_lag_ticks": max(0, engine_tick - projection_tick),
            "heartbeat_age_s": round(heartbeat_age, 3) if heartbeat_age is not None else None,
            "fresh": heartbeat_age is not None and heartbeat_age <= 30,
        },
    }


def _with_freshness(
    payload: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    return {**payload, **freshness}


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings(
        Path(os.environ.get("POLIS_CONFIG", "configs/baseline.yaml"))
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = await Database.open(
            resolved.store,
            role="reader",
            application_name="polis-observatory",
        )
        redis_client = redis.from_url(
            resolved.store.redis_url,
            decode_responses=True,
        )
        hub = LiveHub(
            redis_client,
            ring_frames=resolved.observatory.live.ring_frames,
        )
        app.state.db = db
        app.state.redis = redis_client
        app.state.live_hub = hub
        try:
            yield
        finally:
            await hub.close()
            await redis_client.aclose()
            await db.close()

    app = FastAPI(
        title="POLIS Observatory",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )

    def db(request: Request) -> Database:
        return cast(Database, request.app.state.db)

    @app.get(f"{API_PREFIX}/health")
    async def health(request: Request) -> dict[str, Any]:
        database = db(request)
        report = await database.health()
        redis_ok = bool(await request.app.state.redis.ping())
        heartbeat = await database.fetch(
            """
            SELECT run_id,tick,as_of_seq,updated_at
            FROM engine_heartbeats ORDER BY updated_at DESC LIMIT 1
            """
        )
        latest = _json_row(heartbeat[0]) if heartbeat else None
        return {
            "ok": report.ok and redis_ok,
            "database": {
                "ok": report.ok,
                "server_version": report.server_version,
                "extensions": sorted(report.extensions),
                "alembic_head": report.alembic_head,
                "role": "reader",
            },
            "redis": {"ok": redis_ok},
            "engine": latest,
            "as_of_tick": int(latest["tick"]) if latest else 0,
            "as_of_seq": int(latest["as_of_seq"]) if latest else 0,
        }

    @app.get(f"{API_PREFIX}/runs")
    async def runs(request: Request) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT run_id,name,status,last_tick,scale,started_at,ended_at,
                   terminal_hash,tags,sweep_id
            FROM runs ORDER BY started_at DESC
            """
        )
        as_of_tick = max((int(row["last_tick"]) for row in rows), default=0)
        return {
            "items": [_json_row(row) for row in rows],
            "as_of_tick": as_of_tick,
            "as_of_seq": 0,
            "engine": {"fresh": bool(rows), "projection_lag_ticks": 0},
        }

    @app.get(f"{API_PREFIX}/runs/{{run_id}}")
    async def run_detail(run_id: UUID, request: Request) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch("SELECT * FROM runs WHERE run_id=%s", (run_id,))
        if not rows:
            raise HTTPException(404, "run not found")
        return _with_freshness(
            {"run": _json_row(rows[0])},
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/manifest")
    async def manifest(run_id: UUID, request: Request) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT config_hash,code_git_sha,prompt_manifest,model_manifest,
                   metric_manifest,mechanism_manifest,ablations
            FROM runs WHERE run_id=%s
            """,
            (run_id,),
        )
        if not rows:
            raise HTTPException(404, "run not found")
        return _with_freshness(
            {"manifest": _json_row(rows[0])},
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/metrics/catalogue")
    async def metric_catalogue(run_id: UUID, request: Request) -> dict[str, Any]:
        database = db(request)
        return _with_freshness(
            {
                "items": [
                    {
                        "id": item.metric_id,
                        "unit": item.unit,
                        "cadence": item.cadence,
                        "definition": item.definition,
                        "research_questions": item.research_questions,
                        "definition_hash": item.definition_hash,
                    }
                    for item in METRICS.values()
                ],
                "unavailable_in_m1": sorted(UNAVAILABLE_M1_METRICS),
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/metrics")
    async def metric_series(
        run_id: UUID,
        request: Request,
        metric: str,
        from_tick: int = 0,
        to_tick: int | None = None,
        points: int = Query(2_000, ge=2, le=10_000),
    ) -> dict[str, Any]:
        if metric in UNAVAILABLE_M1_METRICS:
            raise HTTPException(501, f"{metric} is unavailable until its owning milestone")
        if metric not in METRICS:
            raise HTTPException(404, "metric not registered")
        database = db(request)
        rows = await database.fetch(
            """
            SELECT tick,value,as_of_seq FROM metrics
            WHERE run_id=%s AND metric=%s AND tick>=%s
              AND (%s::bigint IS NULL OR tick<=%s)
            ORDER BY tick
            """,
            (run_id, metric, from_tick, to_tick, to_tick),
        )
        if len(rows) > points:
            step = max(1, len(rows) // points)
            rows = rows[::step][:points]
        return _with_freshness(
            {
                "metric": metric,
                "definition": METRICS[metric].definition,
                "unit": METRICS[metric].unit,
                "cadence": METRICS[metric].cadence,
                "points": [_json_row(row) for row in rows],
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/ticks/{{tick}}")
    async def tick_summary(run_id: UUID, tick: int, request: Request) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT kind,count(*) AS count FROM events
            WHERE run_id=%s AND tick=%s GROUP BY kind ORDER BY kind
            """,
            (run_id, tick),
        )
        llm = await database.fetch(
            """
            SELECT count(*) AS calls,COALESCE(sum(cost_usd),0) AS cost_usd
            FROM llm_calls WHERE run_id=%s AND tick=%s
            """,
            (run_id, tick),
        )
        return _with_freshness(
            {
                "tick": tick,
                "events": [
                    {
                        "kind": int(row["kind"]),
                        "name": KIND_REGISTRY[int(row["kind"])].name,
                        "count": int(row["count"]),
                    }
                    for row in rows
                ],
                "llm_calls": int(llm[0]["calls"]),
                "cost_usd": str(llm[0]["cost_usd"]),
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/map/static")
    async def map_static(run_id: UUID, request: Request) -> dict[str, Any]:
        database = db(request)
        districts = await database.fetch(
            """
            SELECT district_id,name,polygon,properties FROM districts
            WHERE run_id=%s ORDER BY district_id
            """,
            (run_id,),
        )
        places = await database.fetch(
            """
            SELECT place_id,district_id,type,name,x,y,capacity,rent_cents,open_hours
            FROM places WHERE run_id=%s ORDER BY place_id
            """,
            (run_id,),
        )
        tiles = await database.fetch(
            """
            SELECT x,y,terrain FROM tiles WHERE run_id=%s ORDER BY y,x
            """,
            (run_id,),
        )
        raster: list[list[int]] = []
        for row in tiles:
            value = int(row["terrain"])
            if raster and raster[-1][0] == value:
                raster[-1][1] += 1
            else:
                raster.append([value, 1])
        return _with_freshness(
            {
                "districts": [_json_row(row) for row in districts],
                "places": [_json_row(row) for row in places],
                "tile_raster_rle": raster,
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/map")
    async def map_state(
        run_id: UUID,
        request: Request,
        tick: int | None = None,
    ) -> dict[str, Any]:
        database = db(request)
        freshness = await _freshness(database, run_id)
        requested = tick if tick is not None else int(freshness["as_of_tick"])
        if requested != int(freshness["as_of_tick"]):
            raise HTTPException(
                501,
                "historical map reconstruction is unavailable in M1; use the event timeline",
            )
        rows = await database.fetch(
            """
            SELECT agent_id,current_place_id,pos_x AS x,pos_y AS y,cognition_mode AS mode,
                   district_id
            FROM agents WHERE run_id=%s ORDER BY agent_id
            """,
            (run_id,),
        )
        return _with_freshness(
            {"tick": requested, "agents": [_json_row(row) for row in rows]},
            freshness,
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/agents")
    async def agents(
        run_id: UUID,
        request: Request,
        district: str | None = None,
        mode: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT agent_id,display_name,age_years,district_id,current_place_id,
                   education_level,employment_status,wealth_cents,health,
                   cognition_mode,state,as_of_tick,as_of_seq
            FROM agents
            WHERE run_id=%s
              AND (%s::text IS NULL OR district_id=%s)
              AND (%s::text IS NULL OR cognition_mode=%s)
              AND (%s::text IS NULL OR agent_id>%s)
            ORDER BY agent_id LIMIT %s
            """,
            (run_id, district, district, mode, mode, cursor, cursor, limit + 1),
        )
        next_cursor = str(rows[limit - 1]["agent_id"]) if len(rows) > limit else None
        return _with_freshness(
            {
                "items": [_json_row(row) for row in rows[:limit]],
                "next_cursor": next_cursor,
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/agents/{{agent_id}}")
    async def agent_detail(
        run_id: UUID,
        agent_id: str,
        request: Request,
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            "SELECT * FROM agents WHERE run_id=%s AND agent_id=%s",
            (run_id, agent_id),
        )
        if not rows:
            raise HTTPException(404, "agent not found")
        return _with_freshness(
            {"agent": _json_row(rows[0])},
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/agents/{{agent_id}}/tick/{{tick}}")
    async def inspector(
        run_id: UUID,
        agent_id: str,
        tick: int,
        request: Request,
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT trace,as_of_seq FROM cognition_traces
            WHERE run_id=%s AND agent_id=%s AND tick=%s
            """,
            (run_id, agent_id, tick),
        )
        if rows:
            trace = dict(rows[0]["trace"])
            payload = {
                key: trace.get(key) if trace.get(key) is not None else "not recorded"
                for key in (
                    "perception",
                    "salience",
                    "retrieval",
                    "prompt",
                    "response",
                    "action",
                    "validation",
                    "outcome",
                )
            }
            payload["recording"] = "sampled"
        else:
            payload = {
                key: "not recorded"
                for key in (
                    "perception",
                    "salience",
                    "retrieval",
                    "prompt",
                    "response",
                    "action",
                    "validation",
                    "outcome",
                )
            }
            payload["recording"] = "not recorded"
        return _with_freshness(
            payload,
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/agents/{{agent_id}}/timeline")
    async def timeline(
        run_id: UUID,
        agent_id: str,
        request: Request,
        from_tick: int = 0,
        to_tick: int | None = None,
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT seq,tick,sim_time,kind,actor_id,subject_ids,cause_seq,payload,hash
            FROM events
            WHERE run_id=%s AND (actor_id=%s OR %s=ANY(subject_ids))
              AND tick>=%s AND (%s::bigint IS NULL OR tick<=%s)
            ORDER BY seq LIMIT 2000
            """,
            (run_id, agent_id, agent_id, from_tick, to_tick, to_tick),
        )
        return _with_freshness(
            {
                "items": [
                    {
                        **_json_row(row),
                        "name": KIND_REGISTRY[int(row["kind"])].name,
                    }
                    for row in rows
                ]
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/agents/{{agent_id}}/memories")
    async def memories(
        run_id: UUID,
        agent_id: str,
        request: Request,
        type: str | None = None,
        from_tick: int = Query(0, alias="from"),
        to_tick: int | None = Query(None, alias="to"),
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT memory_id,tick,type,text,importance,source_event_seq,
                   parent_memory_ids,subject_ids,last_accessed_tick,access_count,archived
            FROM memories
            WHERE run_id=%s AND agent_id=%s
              AND (%s::text IS NULL OR type=%s)
              AND tick>=%s AND (%s::bigint IS NULL OR tick<=%s)
            ORDER BY tick,memory_id
            """,
            (run_id, agent_id, type, type, from_tick, to_tick, to_tick),
        )
        return _with_freshness(
            {"items": [_json_row(row) for row in rows]},
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/agents/{{agent_id}}/beliefs")
    async def beliefs(
        run_id: UUID,
        agent_id: str,
        request: Request,
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT proposition,value,confidence,updated_tick,source,source_ref
            FROM beliefs WHERE run_id=%s AND agent_id=%s ORDER BY proposition
            """,
            (run_id, agent_id),
        )
        return _with_freshness(
            {"items": [_json_row(row) for row in rows]},
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/agents/{{agent_id}}/salience")
    async def salience(
        run_id: UUID,
        agent_id: str,
        request: Request,
        from_tick: int = Query(0, alias="from"),
        to_tick: int | None = Query(None, alias="to"),
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            """
            SELECT tick,trace->'salience' AS salience
            FROM cognition_traces
            WHERE run_id=%s AND agent_id=%s AND tick>=%s
              AND (%s::bigint IS NULL OR tick<=%s)
            ORDER BY tick
            """,
            (run_id, agent_id, from_tick, to_tick, to_tick),
        )
        return _with_freshness(
            {"items": [_json_row(row) for row in rows]},
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/events")
    async def events(
        run_id: UUID,
        request: Request,
        kind: int | None = None,
        actor: str | None = None,
        subject: str | None = None,
        from_tick: int | None = None,
        to_tick: int | None = None,
        limit: int = Query(200, ge=1, le=2_000),
    ) -> dict[str, Any]:
        if kind is None and from_tick is None and to_tick is None:
            raise HTTPException(400, "event query requires a kind or tick bound")
        database = db(request)
        rows = await database.fetch(
            """
            SELECT seq,tick,sim_time,kind,actor_id,subject_ids,cause_seq,payload,hash
            FROM events WHERE run_id=%s
              AND (%s::integer IS NULL OR kind=%s)
              AND (%s::text IS NULL OR actor_id=%s)
              AND (%s::text IS NULL OR %s=ANY(subject_ids))
              AND (%s::bigint IS NULL OR tick>=%s)
              AND (%s::bigint IS NULL OR tick<=%s)
            ORDER BY seq LIMIT %s
            """,
            (
                run_id,
                kind,
                kind,
                actor,
                actor,
                subject,
                subject,
                from_tick,
                from_tick,
                to_tick,
                to_tick,
                limit,
            ),
        )
        return _with_freshness(
            {
                "items": [
                    {
                        **_json_row(row),
                        "name": KIND_REGISTRY[int(row["kind"])].name,
                    }
                    for row in rows
                ]
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/events/{{seq}}")
    async def event_detail(run_id: UUID, seq: int, request: Request) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            "SELECT * FROM events WHERE run_id=%s AND seq=%s",
            (run_id, seq),
        )
        if not rows:
            raise HTTPException(404, "event not found")
        row = rows[0]
        return _with_freshness(
            {
                "event": {
                    **_json_row(row),
                    "name": KIND_REGISTRY[int(row["kind"])].name,
                    "schema": KIND_REGISTRY[int(row["kind"])].schema,
                }
            },
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/llm_calls/{{call_id}}")
    async def llm_call(
        run_id: UUID,
        call_id: UUID,
        request: Request,
    ) -> dict[str, Any]:
        database = db(request)
        rows = await database.fetch(
            "SELECT * FROM llm_calls WHERE run_id=%s AND call_id=%s",
            (run_id, call_id),
        )
        if not rows:
            raise HTTPException(404, "LLM call not recorded")
        return _with_freshness(
            {"call": _json_row(rows[0])},
            await _freshness(database, run_id),
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/why")
    @app.get(f"{API_PREFIX}/runs/{{run_id}}/events/{{seq}}/causes")
    @app.get(f"{API_PREFIX}/runs/{{run_id}}/events/{{seq}}/effects")
    async def unavailable_causal(
        run_id: UUID,
        request: Request,
        seq: int = 0,
    ) -> JSONResponse:
        del run_id, request, seq
        return JSONResponse(
            status_code=501,
            content={
                "available": False,
                "milestone": "M6",
                "detail": "Causal exploration is visibly unavailable in M1.",
                "as_of_tick": 0,
                "as_of_seq": 0,
                "engine": {"fresh": False, "projection_lag_ticks": 0},
            },
        )

    @app.get(f"{API_PREFIX}/compare")
    async def unavailable_compare() -> JSONResponse:
        return JSONResponse(
            status_code=501,
            content={
                "available": False,
                "milestone": "M6",
                "detail": "Run comparison is visibly unavailable in M1.",
                "as_of_tick": 0,
                "as_of_seq": 0,
                "engine": {"fresh": False, "projection_lag_ticks": 0},
            },
        )

    @app.websocket(f"{API_PREFIX}/ws/live")
    async def websocket_live(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        hub: LiveHub = websocket.app.state.live_hub
        database: Database = websocket.app.state.db
        try:
            parsed_run_id = UUID(run_id)
            freshness = await _freshness(database, parsed_run_id)
        except (ValueError, HTTPException):
            await websocket.close(code=1008, reason="unknown run")
            return
        client = await hub.connect(run_id)
        await websocket.send_json(
            {
                "op": "hello",
                "run_id": run_id,
                "tick": freshness["as_of_tick"],
                "as_of_seq": freshness["as_of_seq"],
                "profile": resolved.clock.profile,
                "limits": {
                    "max_channels": resolved.observatory.live.max_channels,
                    "max_pins": resolved.observatory.live.max_pins,
                    "max_frame_bytes": resolved.observatory.live.max_frame_bytes,
                    "rate_hz": resolved.observatory.live.rate_hz,
                },
            }
        )
        try:
            while True:
                receive = asyncio.create_task(websocket.receive_json())
                send = asyncio.create_task(client.queue.get())
                done, pending = await asyncio.wait(
                    (receive, send),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if receive in done:
                    response = apply_client_message(
                        client,
                        receive.result(),
                        max_channels=resolved.observatory.live.max_channels,
                        max_pins=resolved.observatory.live.max_pins,
                    )
                    if response is not None:
                        await websocket.send_json(response)
                if send in done:
                    await websocket.send_json(send.result())
                    client.queue.task_done()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(run_id, client)

    static_dir = Path(resolved.observatory.static_dir)
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="observatory")
    return app


app = create_app()
