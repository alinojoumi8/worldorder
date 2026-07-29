"""Composition root that gives the isolated gateway its five-function read access."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

import redis.asyncio as redis
import uvicorn
from redis.exceptions import RedisError

from polis.config.settings import Settings
from polis.external import (
    ExternalAction,
    ExternalDecisionBatch,
    ExternalLatencyRow,
    ExternalLifecycleRequest,
)
from polis.gateway.app import build_runtime, create_app
from polis.gateway.queue import (
    TICK_KEY,
    ObservationPublisher,
    RedisActionDrain,
    RedisLifecycleDrain,
    RedisLike,
)
from polis.simulation import run_id_for
from polis.store.engine import Database


class RedisExternalDecisionPort:
    """Translate the Redis gateway wire format into the engine-neutral port."""

    def __init__(
        self,
        redis: RedisLike,
        run_id: UUID,
        agent_ids: Sequence[str],
        *,
        observation_ttl_s: int,
        queue_ttl_s: int = 3_600,
        redis_timeout_ms: int = 100,
        pause_for_external: bool = False,
        pause_max_ms: int = 600_000,
    ) -> None:
        self._agent_ids = set(agent_ids)
        self._publisher = ObservationPublisher(
            redis,
            run_id,
            ttl_s=observation_ttl_s,
            redis_timeout_ms=redis_timeout_ms,
        )
        self._drain = RedisActionDrain(redis, run_id, ttl_s=queue_ttl_s)
        self._lifecycle = RedisLifecycleDrain(redis, run_id, ttl_s=queue_ttl_s)
        self._redis = redis
        self._run_id = run_id
        self._tick_ttl_s = observation_ttl_s
        self._pause_for_external = pause_for_external
        self._pause_max_ms = pause_max_ms
        self._tick_windows: dict[int, tuple[float, int, int, int, str]] = {}
        self._observation_pushed_ms: dict[tuple[int, str], int] = {}
        self._latency_rows: list[ExternalLatencyRow] = []

    def controlled_agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._agent_ids))

    def replace_controlled_agents(self, agent_ids: Sequence[str]) -> None:
        self._agent_ids = set(agent_ids)

    def latency_rows(self) -> tuple[ExternalLatencyRow, ...]:
        return tuple(self._latency_rows)

    def clear_latency_rows(self) -> None:
        self._latency_rows.clear()

    async def open_tick(
        self,
        tick: int,
        *,
        sim_time: str,
        decision_deadline_ms: int,
        seal_margin_ms: int,
    ) -> None:
        if tick > 0:
            # Reaching the next tick proves the prior tick's event commit completed.
            await self.acknowledge_committed_tick(tick - 1)
        opened_monotonic = time.monotonic()
        opened_unix_ms = time.time_ns() // 1_000_000
        seal_unix_ms = opened_unix_ms + decision_deadline_ms - seal_margin_ms
        deadline_unix_ms = opened_unix_ms + decision_deadline_ms
        self._tick_windows[tick] = (
            opened_monotonic,
            opened_unix_ms,
            decision_deadline_ms,
            seal_margin_ms,
            sim_time,
        )
        await self._publish_tick(
            tick,
            sim_time=sim_time,
            opened_unix_ms=opened_unix_ms,
            seal_unix_ms=seal_unix_ms,
            deadline_unix_ms=deadline_unix_ms,
            sealed=False,
        )

    async def publish_observation(self, tick: int, agent_id: str, blob: bytes) -> bool:
        if agent_id not in self._agent_ids:
            return False
        published = await self._publisher.publish(tick, agent_id, blob)
        if published:
            self._observation_pushed_ms[(tick, agent_id)] = time.time_ns() // 1_000_000
        return published

    async def drain_actions(
        self,
        tick: int,
        *,
        timeout_ms: int,
    ) -> ExternalDecisionBatch:
        window = self._tick_windows.get(tick)
        if window is not None:
            (
                opened_monotonic,
                opened_unix_ms,
                decision_deadline_ms,
                seal_margin_ms,
                sim_time,
            ) = window
            if self._pause_for_external:
                await self._wait_for_all_actions(
                    tick,
                    timeout_ms=self._pause_max_ms,
                )
            else:
                seal_delay_s = max(
                    0.0,
                    opened_monotonic
                    + (decision_deadline_ms - seal_margin_ms) / 1_000
                    - time.monotonic(),
                )
                if seal_delay_s:
                    await asyncio.sleep(seal_delay_s)
            await self._publish_tick(
                tick,
                sim_time=sim_time,
                opened_unix_ms=opened_unix_ms,
                seal_unix_ms=opened_unix_ms + decision_deadline_ms - seal_margin_ms,
                deadline_unix_ms=opened_unix_ms + decision_deadline_ms,
                sealed=True,
            )
            if not self._pause_for_external:
                drain_delay_s = max(
                    0.0,
                    opened_monotonic + decision_deadline_ms / 1_000 - time.monotonic(),
                )
                if drain_delay_s:
                    await asyncio.sleep(drain_delay_s)
        records = tuple(
            sorted(
                await self._drain.drain(tick, timeout_ms=timeout_ms),
                key=lambda row: (row.agent_id, row.action_id, row.nonce),
            )
        )
        received = {row.agent_id: row.received_ms for row in records}
        opened_unix_ms = window[1] if window is not None else time.time_ns() // 1_000_000
        for agent_id in sorted(self._agent_ids):
            action_received_ms = received.get(agent_id)
            pushed_ms = self._observation_pushed_ms.pop(
                (tick, agent_id),
                opened_unix_ms,
            )
            self._latency_rows.append(
                ExternalLatencyRow(
                    agent_id=agent_id,
                    tick=tick,
                    observation_pushed_ms=pushed_ms,
                    action_received_ms=action_received_ms,
                    decision_ms=(
                        max(0, action_received_ms - pushed_ms)
                        if action_received_ms is not None
                        else None
                    ),
                    missed=action_received_ms is None,
                )
            )
        self._tick_windows.pop(tick, None)
        return ExternalDecisionBatch(
            actions=tuple(
                ExternalAction(
                    agent_id=row.agent_id,
                    action_id=row.action_id,
                    tick=row.tick,
                    nonce=row.nonce,
                    type=row.type,
                    params=row.params,
                    reasoning=row.reasoning,
                    speech=row.speech,
                    extras=row.extras,
                    sig=row.sig,
                    session_id=row.session_id,
                    received_ms=row.received_ms,
                    audit=row.audit,
                )
                for row in records
            )
        )

    async def drain_lifecycle(
        self,
        tick: int,
        *,
        timeout_ms: int,
    ) -> tuple[ExternalLifecycleRequest, ...]:
        rows = await self._lifecycle.drain(timeout_ms=timeout_ms)
        requests: list[ExternalLifecycleRequest] = []
        for row in rows:
            try:
                declaration = row.get("declaration", {})
                if not isinstance(declaration, Mapping):
                    raise ValueError("declaration must be an object")
                queued_tick = row.get("queued_tick", row.get("tick", tick))
                if isinstance(queued_tick, bool):
                    raise ValueError("queued tick must be an integer")
                requests.append(
                    ExternalLifecycleRequest(
                        request_type=str(row.get("request_type", "")),
                        agent_id=str(row.get("agent_id", "")),
                        declaration=dict(declaration),
                        sig=str(row.get("sig", "")),
                        queued_tick=int(queued_tick),
                        reason=(str(row["reason"]) if row.get("reason") is not None else None),
                        revoked_by=(
                            str(row["revoked_by"]) if row.get("revoked_by") is not None else None
                        ),
                    )
                )
            except (TypeError, ValueError):
                requests.append(
                    ExternalLifecycleRequest(
                        request_type="malformed",
                        agent_id=str(row.get("agent_id", "")),
                        declaration={},
                        sig="",
                        queued_tick=tick,
                    )
                )
        return tuple(
            sorted(
                requests,
                key=lambda request: (
                    request.agent_id,
                    request.queued_tick,
                    request.request_type,
                    request.sig,
                ),
            )
        )

    async def acknowledge_committed_tick(self, tick: int) -> bool:
        """Acknowledge Redis batches only after the tick event commit succeeds."""

        acknowledged = True
        try:
            acknowledged = await self._drain.ack(tick, timeout_ms=100) and acknowledged
        except OSError:
            acknowledged = False
        try:
            acknowledged = await self._lifecycle.ack(timeout_ms=100) and acknowledged
        except OSError:
            acknowledged = False
        return acknowledged

    async def publish_admission(
        self,
        agent_id: str,
        status: Mapping[str, Any],
    ) -> None:
        if status.get("status") == "admitted":
            self._agent_ids.add(agent_id)
        elif status.get("status") in {"revoked", "naturalised"}:
            self._agent_ids.discard(agent_id)
        key = f"polis:admission:{self._run_id}:{agent_id}"
        try:
            await self._redis.set(
                key,
                json.dumps(status, sort_keys=True, separators=(",", ":")).encode(),
                ex=86_400,
            )
        except RedisError as exc:
            raise OSError("gateway admission cache is unavailable") from exc

    async def _publish_tick(
        self,
        tick: int,
        *,
        sim_time: str,
        opened_unix_ms: int,
        seal_unix_ms: int,
        deadline_unix_ms: int,
        sealed: bool,
    ) -> None:
        payload = {
            "tick": tick,
            "sim_time": sim_time,
            "phase": 3 if sealed else 1,
            "opened_unix_ms": opened_unix_ms,
            "seal_unix_ms": seal_unix_ms,
            "deadline_unix_ms": deadline_unix_ms,
            "sealed": sealed,
            "run_status": "running",
        }
        try:
            await self._redis.set(
                TICK_KEY.format(run=self._run_id),
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                ex=self._tick_ttl_s,
            )
        except RedisError as exc:
            raise OSError("gateway tick mirror is unavailable") from exc

    async def _wait_for_all_actions(self, tick: int, *, timeout_ms: int) -> None:
        deadline = time.monotonic() + timeout_ms / 1_000
        while time.monotonic() < deadline:
            try:
                queued_agent_ids = await self._drain.queued_agent_ids(tick)
            except OSError:
                return
            if self._agent_ids.issubset(queued_agent_ids):
                return
            await asyncio.sleep(0.025)


async def run_with_gateway(settings: Settings) -> Any:
    """Run the engine with the only authorised Redis gateway adapter."""

    from polis.store.living_city import run_persistent

    client = redis.from_url(settings.store.redis_url, decode_responses=False)
    ttl_s = max(1, (settings.gateway.deadline.decision_deadline_ms * 3) // 1_000 + 1)
    port = RedisExternalDecisionPort(
        cast(RedisLike, client),
        run_id_for(settings),
        (),
        observation_ttl_s=ttl_s,
        queue_ttl_s=settings.gateway.lifecycle.session_ttl_s,
        redis_timeout_ms=settings.gateway.deadline.drain_timeout_ms,
        pause_for_external=settings.gateway.deadline.pause_for_external,
        pause_max_ms=settings.gateway.deadline.pause_max_ms,
    )
    try:
        result = await run_persistent(settings, external_decisions=port)
        await port.acknowledge_committed_tick(result.report.last_tick)
        return result
    finally:
        await client.aclose()


async def serve_gateway(settings: Settings, *, run_id: UUID | None = None) -> None:
    database = await Database.open(
        settings.store,
        role="reader",
        application_name="polis-gateway",
    )
    redis_client = redis.from_url(settings.store.redis_url, decode_responses=False)
    try:
        resolved_run_id = run_id or await _active_run(database)

        async def roster() -> Sequence[Mapping[str, Any]]:
            return await database.fetch(
                """
                SELECT agent_id,pubkey,operator,admitted_tick,revoked_tick,
                       naturalised_tick,resume_grace_until_tick,twin_agent_id
                FROM external_agents
                WHERE run_id=%s
                ORDER BY agent_id
                """,
                (resolved_run_id,),
            )

        async def admission(agent_id: str) -> Mapping[str, Any] | None:
            try:
                cached = await redis_client.get(f"polis:admission:{resolved_run_id}:{agent_id}")
            except RedisError:
                cached = None
            if cached is not None:
                try:
                    decoded = json.loads(cached)
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                    decoded = None
                if isinstance(decoded, dict):
                    return decoded
            rows = await database.fetch(
                """
                SELECT agent_id,pubkey,operator,admitted_tick,revoked_tick,
                       naturalised_tick,resume_grace_until_tick,twin_agent_id
                FROM external_agents
                WHERE run_id=%s AND agent_id=%s
                """,
                (resolved_run_id, agent_id),
            )
            return rows[0] if rows else None

        async def conformance(
            token: str,
            pubkey: str,
            sdk_version: str,
            protocol_version: int,
        ) -> bool:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            rows = await database.fetch(
                """
                SELECT 1 FROM external_conformance_tokens
                WHERE token_hash=%s AND expires_unix_ms>%s
                  AND pubkey=%s AND sdk_version=%s AND protocol_version=%s
                  AND used_run_id IS NULL AND used_agent_id IS NULL
                """,
                (
                    token_hash,
                    time.time_ns() // 1_000_000,
                    pubkey,
                    sdk_version,
                    protocol_version,
                ),
            )
            return bool(rows)

        nonce_rows = await database.fetch(
            """
            SELECT agent_id,last_nonce
            FROM external_nonces
            WHERE run_id=%s
            """,
            (resolved_run_id,),
        )
        runtime = build_runtime(
            settings,
            run_id=resolved_run_id,
            db=database,
            roster=roster,
            admission_reader=admission,
            conformance_validator=conformance,
            nonce_initial={str(row["agent_id"]): int(row["last_nonce"]) for row in nonce_rows},
        )
        application = create_app(
            settings,
            run_id=resolved_run_id,
            runtime=runtime,
        )
        host, raw_port = settings.gateway.bind.rsplit(":", 1)
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host=host,
                port=int(raw_port),
                log_level="info",
                workers=1,
            )
        )
        await server.serve()
    finally:
        await redis_client.aclose()
        await database.close()


async def _active_run(database: Database) -> UUID:
    rows = await database.fetch(
        """
        SELECT run_id FROM runs
        WHERE status='running'
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    if not rows:
        raise RuntimeError("no running POLIS run; pass --run-id explicitly")
    return UUID(str(rows[0]["run_id"]))
