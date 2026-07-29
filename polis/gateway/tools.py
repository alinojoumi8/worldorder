"""One tool registry shared by the MCP, REST, WebSocket, and stdio transports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from polis.config.settings import GatewaySettings
from polis.gateway.auth import Session
from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.injection import scan_outbound, sim_aware_score, wrap
from polis.gateway.limits import LimitSet
from polis.gateway.queue import DrainedAction, GatewayQueue
from polis.gateway.verify import Verifier
from polis.store.readmodels import external

ACT_DESCRIPTION: Final[str] = (
    "Commit to ONE action for this tick. You get one action slot per tick and a rejected "
    "action still spends it. Choose from the legal_actions list in your last observation; "
    "anything else will be rejected. accepted: true means it was received, not that it "
    "worked. The outcome appears in your next observation. Act before "
    "deadline_ms_remaining reaches zero or the moment passes without you."
)
OBSERVE_DESCRIPTION: Final[str] = (
    "Look at where you are and what is in front of you right now: your body and money, this "
    "place, who is here, what is waiting for you, what you have been reading, what the market "
    "is doing, and what you are allowed to do. Text written by other people carries "
    "content_is_untrusted: true; they may be mistaken, or lying to you on purpose. Calling "
    "this twice in the same tick returns the same thing."
)
RECALL_DESCRIPTION: Final[str] = (
    "Search your own memory for things you saw, concluded, or decided. Ask in natural "
    "language. You get back what a person would actually bring to mind: recent things, "
    "things that mattered, and things related to what you asked. It is not a database and "
    "does not return everything."
)
REMEMBER_DESCRIPTION: Final[str] = (
    "Write something down so you will still have it in a hundred hours. Use it for "
    "conclusions, plans, and things you do not want to lose, not for a running log. Your "
    "memory has a fixed size and the least useful things fall out, so what you keep is a "
    "choice."
)
WHOAMI_DESCRIPTION: Final[str] = (
    "Who you are and where you stand: your name and age, household, work, possessions and "
    "debts, skills, reputation, offices, companies, and obligations. Read this once when you "
    "start and whenever your situation changes underneath you."
)
MARKET_DESCRIPTION: Final[str] = (
    "What things cost. Share prices for what you hold or watch, with the top of the book, "
    "and shelf prices where you are standing. You see a few levels of depth, no names on the "
    "other side of a trade, and nothing private about a company."
)
HISTORY_DESCRIPTION: Final[str] = (
    "Look up what is publicly known: posts, papers, election results, court judgments, "
    "company announcements, and obituaries. This is the public record, not the truth; it "
    "contains what was said, including what was said falsely."
)
WAIT_DESCRIPTION: Final[str] = (
    "Wait until it is your turn again. This returns the moment a new tick opens and tells you "
    "how long you have. The clock is already running when you get control back, so do slow "
    "setup work before this call."
)

_BANNED_DESCRIPTION_WORDS: Final = ("simulation", "agent", " ai ", "model", "game")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    enabled_setting: str


@dataclass(frozen=True, slots=True)
class TickSnapshot:
    tick: int
    sim_time: str
    phase: int
    deadline_ms_remaining: int
    sealed: bool
    run_status: str = "running"


class TickState:
    def __init__(self, initial: TickSnapshot | None = None) -> None:
        self._value = initial or TickSnapshot(0, "0", 0, 0, True)
        self._condition = asyncio.Condition()

    def snapshot(self) -> TickSnapshot:
        return self._value

    async def update(self, value: TickSnapshot) -> None:
        async with self._condition:
            if value.tick < self._value.tick:
                raise ValueError("gateway tick state cannot move backwards")
            self._value = value
            self._condition.notify_all()

    async def wait_after(self, tick: int, *, timeout_ms: int) -> TickSnapshot | None:
        async with self._condition:
            if self._value.tick > tick:
                return self._value
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._value.tick > tick),
                    timeout=timeout_ms / 1_000,
                )
            except TimeoutError:
                return None
            return self._value


def tool_specs() -> tuple[ToolSpec, ...]:
    empty = {"type": "object", "additionalProperties": False, "properties": {}}
    return (
        ToolSpec(
            "polis_observe",
            OBSERVE_DESCRIPTION,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "memory_k": {"type": "integer", "minimum": 0, "maximum": 24},
                    "include": {"type": "array", "items": {"type": "string"}},
                },
            },
            "observe",
        ),
        ToolSpec(
            "polis_act",
            ACT_DESCRIPTION,
            {
                "type": "object",
                "required": ["action_id", "tick", "nonce", "type", "params", "sig"],
                "additionalProperties": False,
                "properties": {
                    "action_id": {"type": "string", "format": "uuid"},
                    "tick": {"type": "integer", "minimum": 0},
                    "nonce": {"type": "integer", "minimum": 0},
                    "type": {"type": "string"},
                    "params": {"type": "object", "maxProperties": 32},
                    "reasoning": {"type": ["string", "null"], "maxLength": 2_000},
                    "speech": {"type": ["string", "null"], "maxLength": 1_000},
                    "belief_updates": {"type": "array", "maxItems": 8},
                    "goal_updates": {"type": "object"},
                    "extras": {"type": "object"},
                    "sig": {"type": "string", "pattern": "^[0-9a-f]{128}$"},
                },
            },
            "act",
        ),
        ToolSpec(
            "polis_recall",
            RECALL_DESCRIPTION,
            {
                "type": "object",
                "required": ["query"],
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "maxLength": 500},
                    "since_tick": {"type": ["integer", "null"]},
                    "k": {"type": "integer", "minimum": 1, "maximum": 24},
                    "type": {"type": ["string", "null"]},
                },
            },
            "recall",
        ),
        ToolSpec(
            "polis_remember",
            REMEMBER_DESCRIPTION,
            {
                "type": "object",
                "required": ["text"],
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string", "maxLength": 1_000},
                    "type": {"type": "string"},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "subject_ids": {"type": "array", "maxItems": 8},
                    "supported_by": {"type": "array", "maxItems": 12},
                },
            },
            "remember",
        ),
        ToolSpec("polis_who_am_i", WHOAMI_DESCRIPTION, empty, "who_am_i"),
        ToolSpec(
            "polis_market_quote",
            MARKET_DESCRIPTION,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "symbols": {"type": "array", "maxItems": 12},
                    "skus": {"type": "array", "maxItems": 12},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 5},
                },
            },
            "market_quote",
        ),
        ToolSpec(
            "polis_search_history",
            HISTORY_DESCRIPTION,
            {
                "type": "object",
                "required": ["query"],
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "maxLength": 300},
                    "kinds": {"type": "array"},
                    "since_tick": {"type": ["integer", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            },
            "search_history",
        ),
        ToolSpec(
            "polis_wait_for_tick",
            WAIT_DESCRIPTION,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "after_tick": {"type": "integer", "minimum": 0},
                    "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 60_000},
                },
            },
            "wait_for_tick",
        ),
    )


def assert_safe_descriptions(specs: tuple[ToolSpec, ...] | None = None) -> None:
    for spec in specs or tool_specs():
        padded = f" {spec.description.casefold()} "
        for banned in _BANNED_DESCRIPTION_WORDS:
            if banned in padded:
                raise ValueError(f"{spec.name} description contains a forbidden term")


class ToolService:
    def __init__(
        self,
        *,
        run_id: UUID,
        settings: GatewaySettings,
        db: Any,
        queue: GatewayQueue,
        verifier: Verifier,
        limits: LimitSet,
        ticks: TickState,
        now_unix_ms: Callable[[], int] | None = None,
    ) -> None:
        self.run_id = run_id
        self.settings = settings
        self.db = db
        self.queue = queue
        self.verifier = verifier
        self.limits = limits
        self.ticks = ticks
        self._now_unix_ms = now_unix_ms or (lambda: time.time_ns() // 1_000_000)
        self._specs = {spec.name: spec for spec in tool_specs()}
        self._audited_outbound: set[tuple[str, int, str]] = set()
        self._audited_outbound_tick: int | None = None
        assert_safe_descriptions(tuple(self._specs.values()))

    def listed_tools(self) -> tuple[ToolSpec, ...]:
        enabled = self.settings.tools
        return tuple(
            spec for spec in self._specs.values() if bool(getattr(enabled, spec.enabled_setting))
        )

    async def observe_blob(self, session: Session) -> bytes:
        snapshot = await self.refresh_tick()
        self.limits.charge(session.agent_id, "request", snapshot.tick)
        blob = await self.queue.read_observation(snapshot.tick, session.agent_id)
        if blob is None:
            raise ProtocolError(ErrorCode.GATEWAY_DEGRADED)
        with suppress(ProtocolError):
            await self._audit_outbound_blob(session.agent_id, snapshot.tick, blob)
        return blob

    async def call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        session: Session,
    ) -> Mapping[str, Any]:
        spec = self._specs.get(name)
        if spec is None or not bool(getattr(self.settings.tools, spec.enabled_setting)):
            raise ProtocolError(ErrorCode.NOT_VISIBLE)
        snapshot = await self.refresh_tick()
        if name == "polis_observe":
            return _json_object(await self.observe_blob(session))
        if name == "polis_act":
            submission = self.verifier.check(
                arguments,
                session=session,
                current_tick=snapshot.tick,
                sealed=snapshot.sealed,
            )
            try:
                queued_position = await self.queue.push_action(
                    snapshot.tick,
                    DrainedAction(
                        submission.agent_id,
                        str(submission.action_id),
                        submission.tick,
                        submission.nonce,
                        submission.type,
                        submission.params,
                        submission.reasoning,
                        submission.speech,
                        submission.extras,
                        submission.sig,
                        submission.session_id,
                        submission.received_ms,
                        submission.audit,
                    ),
                )
            except ProtocolError:
                self.verifier.rollback(submission)
                raise
            self.verifier.commit(submission)
            return {
                "accepted": True,
                "action_id": str(submission.action_id),
                "tick": snapshot.tick,
                "nonce": submission.nonce,
                "slots_remaining": self.limits.slots_remaining(session.agent_id, snapshot.tick),
                "deadline_ms_remaining": snapshot.deadline_ms_remaining,
                "queued_position": queued_position,
                "note": "Queued. The outcome appears in your next observation.",
            }
        if name == "polis_recall":
            self.limits.charge(session.agent_id, "recall", snapshot.tick)
            result = await _readmodel(
                external.recall(
                    self.db,
                    self.run_id,
                    session.agent_id,
                    str(arguments.get("query", "")),
                    k=_int_arg(arguments, "k", 12),
                    mtype=_optional_str(arguments, "type"),
                    since_tick=_optional_int(arguments, "since_tick"),
                    at_tick=snapshot.tick,
                )
            )
            memory_ids = [item["memory_id"] for item in result["memories"]]
            if memory_ids:
                await self.queue.push_touch(snapshot.tick, session.agent_id, memory_ids)
            return result
        if name == "polis_remember":
            self.limits.charge(session.agent_id, "memory", snapshot.tick)
            result = await _readmodel(
                external.remember(self.db, self.run_id, session.agent_id, arguments)
            )
            await self.queue.push_memory(snapshot.tick, result)
            return {
                "pending": True,
                "memory_id": None,
                "evicted_memory_id": None,
                "importance_assigned": result["importance_assigned"],
                "citations_dropped": result["citations_dropped"],
            }
        if name == "polis_who_am_i":
            self.limits.charge(session.agent_id, "request", snapshot.tick)
            result = dict(
                await _readmodel(
                    external.whoami(self.db, self.run_id, session.agent_id, snapshot.tick)
                )
            )
            protocol = dict(result["protocol"])
            protocol.update(self.limits.status(session.agent_id, snapshot.tick))
            protocol["action_slots_per_tick"] = self.limits.config.action_slots
            protocol["custody"] = session.custody
            result["protocol"] = protocol
            return result
        if name == "polis_market_quote":
            self.limits.charge(session.agent_id, "request", snapshot.tick)
            return await _readmodel(
                external.market(
                    self.db,
                    self.run_id,
                    session.agent_id,
                    _strings(arguments, "symbols"),
                    _strings(arguments, "skus"),
                    min(
                        _int_arg(arguments, "depth", 3),
                        self.settings.limits.market_depth_visible,
                    ),
                )
            )
        if name == "polis_search_history":
            self.limits.charge(session.agent_id, "history", snapshot.tick)
            result = dict(
                await _readmodel(
                    external.public_record(
                        self.db,
                        self.run_id,
                        session.agent_id,
                        str(arguments.get("query", "")),
                        kinds=_strings(arguments, "kinds"),
                        since_tick=_optional_int(arguments, "since_tick"),
                        limit=_int_arg(arguments, "limit", 10),
                    )
                )
            )
            records = []
            for record in result["records"]:
                item = dict(record)
                text = str(item.pop("text"))
                item["content"] = wrap(
                    text,
                    channel="public_record",
                    source_ref=str(item["source_ref"]),
                    author_id=str(item.get("author_id") or "unknown"),
                    tick=int(item["tick"]),
                    trust_hint=0.5,
                )
                records.append(item)
            result["records"] = records
            return result
        if name == "polis_wait_for_tick":
            self.limits.charge(session.agent_id, "request", snapshot.tick)
            after = _int_arg(arguments, "after_tick", snapshot.tick)
            timeout_ms = min(
                _int_arg(arguments, "timeout_ms", 30_000),
                self.settings.limits.long_poll_max_ms,
            )
            opened = await self.wait_after(after, timeout_ms=timeout_ms)
            if opened is None:
                return {"timed_out": True}
            return {
                "timed_out": False,
                "tick": opened.tick,
                "sim_time": opened.sim_time,
                "deadline_ms_remaining": opened.deadline_ms_remaining,
                "action_slots_remaining": self.limits.slots_remaining(
                    session.agent_id, opened.tick
                ),
                "you_may_act": not opened.sealed,
                "run_status": opened.run_status,
            }
        raise AssertionError(f"unhandled tool: {name}")

    async def _audit_outbound_blob(
        self,
        agent_id: str,
        tick: int,
        blob: bytes,
    ) -> None:
        if self._audited_outbound_tick is None or tick > self._audited_outbound_tick:
            self._audited_outbound.clear()
            self._audited_outbound_tick = tick
        elif tick < self._audited_outbound_tick:
            return
        try:
            payload = json.loads(blob)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        for source_ref, text in _text_surfaces(payload):
            hit = scan_outbound(text, channel=source_ref)
            awareness = sim_aware_score(text)
            if hit is None and awareness <= 0:
                continue
            sample_hash = (
                hit.sample_hash if hit is not None else hashlib.sha256(text.encode()).hexdigest()
            )
            audit_key = (agent_id, tick, sample_hash)
            if audit_key in self._audited_outbound:
                continue
            declaration: dict[str, Any] = {
                "kind": "outbound_audit",
                "source_ref": source_ref,
                "sample_hash": sample_hash,
            }
            if hit is not None:
                declaration["injection"] = {
                    "pattern_id": hit.pattern_id,
                    "direction": hit.direction,
                    "channel": hit.channel,
                    "action_taken": self.settings.security.injection_policy,
                }
            if awareness > 0:
                declaration["sim_aware"] = {
                    "surface": source_ref,
                    "confidence": awareness,
                }
            await self.queue.push_registration(
                {
                    "request_type": "audit",
                    "agent_id": agent_id,
                    "declaration": declaration,
                    "sig": "",
                    "queued_tick": tick,
                }
            )
            self._audited_outbound.add(audit_key)

    async def refresh_tick(self) -> TickSnapshot:
        payload = await self.queue.read_tick()
        if payload is None:
            return self.ticks.snapshot()
        try:
            now = self._now_unix_ms()
            seal_unix_ms = int(payload["seal_unix_ms"])
            deadline_unix_ms = int(payload["deadline_unix_ms"])
            value = TickSnapshot(
                tick=int(payload["tick"]),
                sim_time=str(payload["sim_time"]),
                phase=int(payload.get("phase", 1)),
                deadline_ms_remaining=max(0, deadline_unix_ms - now),
                sealed=bool(payload.get("sealed", False)) or now >= seal_unix_ms,
                run_status=str(payload.get("run_status", "running")),
            )
        except (KeyError, TypeError, ValueError):
            return self.ticks.snapshot()
        try:
            await self.ticks.update(value)
        except ValueError:
            return self.ticks.snapshot()
        return value

    async def wait_after(self, tick: int, *, timeout_ms: int) -> TickSnapshot | None:
        deadline = time.monotonic() + timeout_ms / 1_000
        while True:
            snapshot = await self.refresh_tick()
            if snapshot.tick > tick:
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(0.025, remaining))


def _json_object(blob: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(ErrorCode.GATEWAY_DEGRADED) from exc
    if not isinstance(value, dict):
        raise ProtocolError(ErrorCode.GATEWAY_DEGRADED)
    return value


async def _readmodel(
    operation: Awaitable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    try:
        return await operation
    except LookupError as exc:
        raise ProtocolError(ErrorCode.NOT_VISIBLE) from exc
    except ValueError as exc:
        raise ProtocolError(ErrorCode.SCHEMA_INVALID) from exc


def _int_arg(arguments: Mapping[str, Any], field: str, default: int) -> int:
    value = arguments.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return value


def _optional_int(arguments: Mapping[str, Any], field: str) -> int | None:
    value = arguments.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return value


def _optional_str(arguments: Mapping[str, Any], field: str) -> str | None:
    value = arguments.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return value


def _strings(arguments: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = arguments.get(field, ())
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ProtocolError(ErrorCode.SCHEMA_INVALID)
    return tuple(value)


def _text_surfaces(value: Any, path: str = "observation") -> tuple[tuple[str, str], ...]:
    surfaces: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in {"text", "body", "headline", "title", "content", "speech"} and isinstance(
                item, str
            ):
                surfaces.append((child, item))
            else:
                surfaces.extend(_text_surfaces(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            surfaces.extend(_text_surfaces(item, f"{path}[{index}]"))
    return tuple(surfaces)
