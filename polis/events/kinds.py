from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Final

from polis.config.canon import canonical_bytes, sha256_hex
from polis.config.errors import PolisError


class Persistence(StrEnum):
    PERSISTED = "persisted"
    SAMPLED = "sampled"
    EPHEMERAL = "ephemeral"


@dataclass(frozen=True, slots=True)
class KindSpec:
    kind: int
    name: str
    owner: str
    persistence: Persistence
    schema: dict[str, Any]
    description: str = ""
    since: str = "1.0"


@dataclass(frozen=True, slots=True)
class KindRange:
    lo: int
    hi: int
    domain: str
    owner: str
    persistence: Persistence


class KindError(PolisError):
    """Invalid or unknown event kind."""


KIND_RANGES: Final = (
    KindRange(1000, 1999, "kernel", "polis.kernel", Persistence.PERSISTED),
    KindRange(2000, 2999, "agents", "polis.agents", Persistence.PERSISTED),
    KindRange(3000, 3999, "world", "polis.world", Persistence.PERSISTED),
    KindRange(4000, 4099, "cognition", "polis.agents", Persistence.SAMPLED),
    KindRange(4100, 4199, "llm", "polis.llm", Persistence.PERSISTED),
    KindRange(4200, 4999, "agent_aux", "polis.agents", Persistence.PERSISTED),
    KindRange(5000, 5999, "labour", "polis.economy", Persistence.PERSISTED),
    KindRange(6000, 6999, "firms_goods", "polis.economy", Persistence.PERSISTED),
    KindRange(7000, 7999, "exchange", "polis.economy", Persistence.PERSISTED),
    KindRange(8000, 8999, "banking", "polis.economy", Persistence.PERSISTED),
    KindRange(14000, 14999, "education", "polis.agents", Persistence.PERSISTED),
    KindRange(90000, 90999, "ephemeral", "*", Persistence.EPHEMERAL),
    KindRange(99000, 99999, "research", "polis.research", Persistence.PERSISTED),
)
KIND_REGISTRY: Final[dict[int, KindSpec]] = {}
KIND_BY_NAME: Final[dict[str, int]] = {}


def range_for(kind: int) -> KindRange:
    for item in KIND_RANGES:
        if item.lo <= kind <= item.hi:
            return item
    raise KindError(f"event kind {kind} is outside every declared range")


def register_kind(
    kind: int,
    name: str,
    *,
    owner: str,
    persistence: Persistence,
    schema: dict[str, Any],
    description: str = "",
) -> int:
    declared = range_for(kind)
    if kind in KIND_REGISTRY:
        raise KindError(f"duplicate event kind: {kind}")
    if name in KIND_BY_NAME:
        raise KindError(f"duplicate event name: {name}")
    if declared.owner != "*" and owner != declared.owner:
        raise KindError(f"kind {kind} belongs to {declared.owner}, not requested owner {owner}")
    if declared.persistence != persistence:
        raise KindError(
            f"kind {kind} requires persistence={declared.persistence}, got {persistence}"
        )
    KIND_REGISTRY[kind] = KindSpec(kind, name, owner, persistence, schema, description)
    KIND_BY_NAME[name] = kind
    return kind


def spec(kind: int) -> KindSpec:
    try:
        return KIND_REGISTRY[kind]
    except KeyError as exc:
        raise KindError(f"unknown event kind: {kind}") from exc


def is_ephemeral(kind: int) -> bool:
    return range_for(kind).persistence == Persistence.EPHEMERAL


def is_known(kind: int) -> bool:
    return kind in KIND_REGISTRY


def registry_manifest() -> dict[str, Any]:
    return {
        str(kind): {
            **asdict(item),
            "persistence": item.persistence.value,
            "schema_hash": sha256_hex(canonical_bytes(item.schema)),
        }
        for kind, item in sorted(KIND_REGISTRY.items())
    }


def _schema(*required: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": list(required),
        "additionalProperties": True,
    }


RUN_STARTED = register_kind(
    1001,
    "RUN_STARTED",
    owner="polis.kernel",
    persistence=Persistence.PERSISTED,
    schema=_schema("config_hash", "seed"),
)
TICK_STARTED = register_kind(
    1002,
    "TICK_STARTED",
    owner="polis.kernel",
    persistence=Persistence.PERSISTED,
    schema=_schema("tick"),
)
TICK_COMPLETED = register_kind(
    1003,
    "TICK_COMPLETED",
    owner="polis.kernel",
    persistence=Persistence.PERSISTED,
    schema=_schema("tick", "event_count"),
)
CHECKPOINT_CREATED = register_kind(
    1004,
    "CHECKPOINT_CREATED",
    owner="polis.kernel",
    persistence=Persistence.PERSISTED,
    schema=_schema("tick", "state_hash"),
)
INVARIANT_VIOLATED = register_kind(
    1010,
    "INVARIANT_VIOLATED",
    owner="polis.kernel",
    persistence=Persistence.PERSISTED,
    schema=_schema("invariant_id", "halting"),
)
AGENT_BORN = register_kind(
    2001,
    "AGENT_BORN",
    owner="polis.agents",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id"),
)
AGENT_MOVED = register_kind(
    3001,
    "AGENT_MOVED",
    owner="polis.world",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "from_place", "to_place"),
)
JOURNEY_STARTED = register_kind(
    3002,
    "JOURNEY_STARTED",
    owner="polis.world",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "from_place", "to_place", "travel_ticks"),
)
MOVE_BLOCKED = register_kind(
    3005,
    "MOVE_BLOCKED",
    owner="polis.world",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "place_id", "reason"),
)
WORLD_GENERATED = register_kind(
    3100,
    "WORLD_GENERATED",
    owner="polis.world",
    persistence=Persistence.PERSISTED,
    schema=_schema("world_hash", "width", "height", "districts", "places"),
)
PATHS_PRECOMPUTED = register_kind(
    3101,
    "PATHS_PRECOMPUTED",
    owner="polis.world",
    persistence=Persistence.PERSISTED,
    schema=_schema("world_hash", "pairs"),
)
PERCEPTION_BUILT = register_kind(
    4001,
    "PERCEPTION_BUILT",
    owner="polis.agents",
    persistence=Persistence.SAMPLED,
    schema=_schema("agent_id", "digest_hash"),
)
SALIENCE_SCORED = register_kind(
    4002,
    "SALIENCE_SCORED",
    owner="polis.agents",
    persistence=Persistence.SAMPLED,
    schema=_schema("agent_id", "score", "routed_mode"),
)
COGNITION_ROUTED = register_kind(
    4003,
    "COGNITION_ROUTED",
    owner="polis.agents",
    persistence=Persistence.SAMPLED,
    schema=_schema("tick", "n_deliberate", "n_reflex"),
)
MEMORY_RETRIEVED = register_kind(
    4004,
    "MEMORY_RETRIEVED",
    owner="polis.agents",
    persistence=Persistence.SAMPLED,
    schema=_schema("agent_id", "memory_ids"),
)
MEMORY_WRITTEN = register_kind(
    4010,
    "MEMORY_WRITTEN",
    owner="polis.agents",
    persistence=Persistence.SAMPLED,
    schema=_schema("memory_id", "agent_id", "type", "importance"),
)
REFLECTION_PRODUCED = register_kind(
    4020,
    "REFLECTION_PRODUCED",
    owner="polis.agents",
    persistence=Persistence.SAMPLED,
    schema=_schema("memory_id", "agent_id", "parent_memory_ids"),
)
LANE_HEALTH_CHECKED = register_kind(
    4100,
    "LANE_HEALTH_CHECKED",
    owner="polis.llm",
    persistence=Persistence.PERSISTED,
    schema=_schema("lane", "model", "ok", "latency_ms"),
)
LLM_CALL_FAILED = register_kind(
    4101,
    "LLM_CALL_FAILED",
    owner="polis.llm",
    persistence=Persistence.PERSISTED,
    schema=_schema("purpose", "lane", "model", "error_class"),
)
BUDGET_EXHAUSTED = register_kind(
    4102,
    "BUDGET_EXHAUSTED",
    owner="polis.llm",
    persistence=Persistence.PERSISTED,
    schema=_schema("line", "cap", "tick"),
)
ACTION_VALIDATED = register_kind(
    4201,
    "ACTION_VALIDATED",
    owner="polis.agents",
    persistence=Persistence.PERSISTED,
    schema=_schema("action_id", "agent_id", "type"),
)
ACTION_REJECTED = register_kind(
    4202,
    "ACTION_REJECTED",
    owner="polis.agents",
    persistence=Persistence.PERSISTED,
    schema=_schema("action_id", "agent_id", "reason"),
)
FIRM_FOUNDED = register_kind(
    6001,
    "FIRM_FOUNDED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "founder_id",
        "name",
        "sector",
        "place_id",
        "initial_capital_cents",
        "ledger_account_id",
    ),
)
BANK_FOUNDED = register_kind(
    8001,
    "BANK_FOUNDED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "bank_id",
        "name",
        "place_id",
        "capital_cents",
        "reserve_ratio_bp",
        "is_central",
    ),
)
ACCOUNT_OPENED = register_kind(
    8002,
    "ACCOUNT_OPENED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "account_id",
        "owner_id",
        "owner_type",
        "account_type",
        "code",
    ),
)
MONEY_ISSUED = register_kind(
    8032,
    "MONEY_ISSUED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "amount_cents",
        "recipient_account_id",
        "instrument",
        "purpose",
        "txn_id",
    ),
)
SKILL_ACCRUED = register_kind(
    14001,
    "SKILL_ACCRUED",
    owner="polis.agents",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "skill", "delta"),
)
METRIC_RECORDED = register_kind(
    99071,
    "METRIC_RECORDED",
    owner="polis.research",
    persistence=Persistence.PERSISTED,
    schema=_schema("metric", "value", "definition_hash"),
)
LIVE_TICK = register_kind(
    90050,
    "LIVE_TICK",
    owner="polis.observatory",
    persistence=Persistence.EPHEMERAL,
    schema=_schema("tick"),
)
LIVE_AGENTS = register_kind(
    90051,
    "LIVE_AGENTS",
    owner="polis.observatory",
    persistence=Persistence.EPHEMERAL,
    schema=_schema("tick", "agents"),
)
