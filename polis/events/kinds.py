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
VACANCY_POSTED = register_kind(
    5001,
    "VACANCY_POSTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "vacancy_id",
        "firm_id",
        "occupation",
        "skill_reqs",
        "wage_offer_cents",
        "headcount",
        "posted_tick",
        "expires_tick",
        "district_id",
    ),
)
VACANCY_CLOSED = register_kind(
    5002,
    "VACANCY_CLOSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("vacancy_id", "reason", "applicants_n", "days_open"),
)
JOB_APPLICATION_SUBMITTED = register_kind(
    5003,
    "JOB_APPLICATION_SUBMITTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "application_id",
        "vacancy_id",
        "agent_id",
        "asked_wage_cents",
        "referral_id",
    ),
)
APPLICATION_SCREENED = register_kind(
    5004,
    "APPLICATION_SCREENED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "application_id",
        "match_score_bp",
        "rank",
        "shortlisted",
        "reject_reason",
    ),
)
OFFER_MADE = register_kind(
    5005,
    "OFFER_MADE",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "offer_id",
        "vacancy_id",
        "firm_id",
        "agent_id",
        "wage_cents",
        "occupation",
        "expires_tick",
    ),
)
OFFER_ACCEPTED = register_kind(
    5006,
    "OFFER_ACCEPTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("offer_id", "employment_id", "wage_cents"),
)
OFFER_DECLINED = register_kind(
    5007,
    "OFFER_DECLINED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("offer_id", "agent_id", "reason_code", "counter_wage_cents"),
)
OFFER_EXPIRED = register_kind(
    5008,
    "OFFER_EXPIRED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("offer_id", "agent_id"),
)
WAGE_NEGOTIATED = register_kind(
    5009,
    "WAGE_NEGOTIATED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("offer_id", "from_cents", "to_cents", "round", "initiator", "outcome"),
)
HIRED = register_kind(
    5010,
    "HIRED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "agent_id",
        "firm_id",
        "employment_id",
        "occupation",
        "wage_cents",
        "match_score_bp",
        "search_duration_ticks",
    ),
)
FIRED = register_kind(
    5011,
    "FIRED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("employment_id", "agent_id", "firm_id", "reason"),
)
QUIT = register_kind(
    5012,
    "QUIT",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("employment_id", "agent_id", "firm_id", "destination"),
)
LAYOFF_BATCH = register_kind(
    5013,
    "LAYOFF_BATCH",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "employment_ids", "headcount_before", "headcount_after", "trigger"),
)
WORK_PERFORMED = register_kind(
    5020,
    "WORK_PERFORMED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "employment_id",
        "agent_id",
        "firm_id",
        "hours_bp",
        "effort_bp",
        "effective_labour_bp",
        "skill_deltas",
    ),
)
ABSENCE = register_kind(
    5021,
    "ABSENCE",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("employment_id", "cause", "hours_lost_bp"),
)
PAYROLL_RUN = register_kind(
    5030,
    "PAYROLL_RUN",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "period_start_tick",
        "period_end_tick",
        "n_employees",
        "gross_cents",
        "income_tax_cents",
        "employer_tax_cents",
        "net_cents",
        "txn_ids",
    ),
)
WAGE_PAID = register_kind(
    5031,
    "WAGE_PAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "employment_id",
        "agent_id",
        "firm_id",
        "gross_cents",
        "income_tax_cents",
        "net_cents",
        "hours_bp",
        "txn_id",
    ),
)
PAYROLL_SHORTFALL = register_kind(
    5032,
    "PAYROLL_SHORTFALL",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "required_cents",
        "available_cents",
        "unpaid_employment_ids",
        "accrued_claim_cents",
    ),
)
SKILL_DECAYED = register_kind(
    5040,
    "SKILL_DECAYED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "skill", "from_level_bp", "to_level_bp", "ticks_unused"),
)
UNEMPLOYMENT_SPELL_STARTED = register_kind(
    5041,
    "UNEMPLOYMENT_SPELL_STARTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "prior_employment_id", "prior_wage_cents", "cause"),
)
UNEMPLOYMENT_SPELL_ENDED = register_kind(
    5042,
    "UNEMPLOYMENT_SPELL_ENDED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "duration_ticks", "exit", "new_wage_cents", "wage_change_bp"),
)
LABOUR_SESSION_SUMMARY = register_kind(
    5050,
    "LABOUR_SESSION_SUMMARY",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "tick",
        "vacancies_open",
        "searchers",
        "applications",
        "offers",
        "hires",
        "mean_match_score_bp",
        "mean_offer_wage_cents",
        "median_hire_wage_cents",
    ),
)
SELF_EMPLOYMENT_STARTED = register_kind(
    5060,
    "SELF_EMPLOYMENT_STARTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "firm_id", "sector"),
)
SELF_EMPLOYMENT_ENDED = register_kind(
    5061,
    "SELF_EMPLOYMENT_ENDED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "firm_id", "reason"),
)
RETIRED = register_kind(
    5070,
    "RETIRED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "age_years", "final_wage_cents", "pension_entitlement_cents"),
)
BENEFIT_CLAIM_OPENED = register_kind(
    5080,
    "BENEFIT_CLAIM_OPENED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "weekly_benefit_cents", "entitlement_ticks", "base_wage_cents"),
)
BENEFIT_EXHAUSTED = register_kind(
    5081,
    "BENEFIT_EXHAUSTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "ticks_claimed"),
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
FIRM_DISSOLVED = register_kind(
    6002,
    "FIRM_DISSOLVED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "reason",
        "residual_cents",
        "headcount_at_exit",
        "age_ticks",
    ),
)
FIRM_STATUS_CHANGED = register_kind(
    6003,
    "FIRM_STATUS_CHANGED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "from", "to", "trigger", "net_worth_cents", "liquid_cents"),
)
OWNERSHIP_TRANSFERRED = register_kind(
    6004,
    "OWNERSHIP_TRANSFERRED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "from_holder", "to_holder", "shares", "share_class", "cause"),
)
PRODUCTION_RUN = register_kind(
    6010,
    "PRODUCTION_RUN",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "sku",
        "labour_bp",
        "capital_cents_used",
        "productivity_bp",
        "output_micro",
        "units_produced",
        "unit_cost_cents",
        "carry_micro_after",
    ),
)
CAPITAL_PURCHASED = register_kind(
    6011,
    "CAPITAL_PURCHASED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "seller_firm_id",
        "sku",
        "units",
        "cents",
        "capital_cents_after",
        "txn_id",
    ),
)
CAPITAL_DEPRECIATED = register_kind(
    6012,
    "CAPITAL_DEPRECIATED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "from_cents", "to_cents", "rate_bp"),
)
INVENTORY_WRITTEN_OFF = register_kind(
    6013,
    "INVENTORY_WRITTEN_OFF",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "sku", "units", "unit_cost_cents", "value_cents", "reason"),
)
PRODUCTIVITY_UPDATED = register_kind(
    6014,
    "PRODUCTIVITY_UPDATED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "from_bp", "to_bp", "cause"),
)
PRICE_SET = register_kind(
    6022,
    "PRICE_SET",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "sku",
        "from_cents",
        "to_cents",
        "rule",
        "markup_bp",
        "inventory_days",
    ),
)
RESTOCK_ORDERED = register_kind(
    6023,
    "RESTOCK_ORDERED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "sku", "from_firm_id", "units", "cents", "txn_id"),
)
DIVIDEND_DECLARED = register_kind(
    6030,
    "DIVIDEND_DECLARED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "per_share_cents",
        "total_cents",
        "record_tick",
        "payable_tick",
        "decided_by",
    ),
)
DIVIDEND_PAID = register_kind(
    6031,
    "DIVIDEND_PAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "holder_id", "shares", "cents", "txn_id"),
)
FIRM_PERIOD_CLOSED = register_kind(
    6040,
    "FIRM_PERIOD_CLOSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "period",
        "revenue_cents",
        "wage_cents",
        "input_cents",
        "depreciation_cents",
        "interest_cents",
        "tax_cents",
        "profit_cents",
        "cumulative_losses_cents",
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
