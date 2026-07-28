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
    KindRange(9000, 9999, "ventures", "polis.economy", Persistence.PERSISTED),
    KindRange(10000, 10999, "communication", "polis.society", Persistence.PERSISTED),
    KindRange(11000, 11020, "social_media", "polis.society", Persistence.PERSISTED),
    KindRange(11021, 11021, "feed_sample", "polis.society", Persistence.SAMPLED),
    KindRange(11022, 11999, "social_media", "polis.society", Persistence.PERSISTED),
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
    if (
        declared.owner != "*"
        and owner != declared.owner
        and not owner.startswith(f"{declared.owner}.")
    ):
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
ACTION_SUBMITTED = register_kind(
    2060,
    "ACTION_SUBMITTED",
    owner="polis.agents",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "action_id",
        "actor_id",
        "type",
        "params",
        "origin",
        "salience",
        "reasoning",
        "llm_call_id",
        "slot_index",
    ),
)
ACTION_REJECTED = register_kind(
    2061,
    "ACTION_REJECTED",
    owner="polis.agents",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "action_id",
        "actor_id",
        "type",
        "gate",
        "reason",
        "detail",
        "origin",
        "slot_consumed",
        "substituted_with",
    ),
)
ACTION_FLAGGED_ILLEGAL = register_kind(
    2062,
    "ACTION_FLAGGED_ILLEGAL",
    owner="polis.agents",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "action_id",
        "actor_id",
        "type",
        "crime_type",
        "victim_id",
        "amount_cents",
        "crime_id",
        "proceeded",
    ),
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

# Society: communication, relationships, and the social platform.
SPEECH_UTTERED = register_kind(
    10010,
    "SPEECH_UTTERED",
    owner="polis.society.comms",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "speaker_id",
        "place_id",
        "text",
        "addressed_to",
        "heard_by",
        "topic",
        "stance_proposition",
        "stance_value",
        "conversation_id",
        "turn_index",
        "closing",
        "claims",
    ),
)
CONVERSATION_OPENED = register_kind(
    10011,
    "CONVERSATION_OPENED",
    owner="polis.society.comms",
    persistence=Persistence.PERSISTED,
    schema=_schema("conversation_id", "place_id", "participants", "opener_id", "topic"),
)
CONVERSATION_CLOSED = register_kind(
    10012,
    "CONVERSATION_CLOSED",
    owner="polis.society.comms",
    persistence=Persistence.PERSISTED,
    schema=_schema("conversation_id", "turns", "reason", "duration_ticks", "participants"),
)
MESSAGE_SENT = register_kind(
    10020,
    "MESSAGE_SENT",
    owner="polis.society.comms",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "message_id",
        "sender_id",
        "recipient_id",
        "text",
        "in_reply_to",
        "topic",
        "stance_proposition",
        "stance_value",
        "claims",
    ),
)
MESSAGE_READ = register_kind(
    10021,
    "MESSAGE_READ",
    owner="polis.society.comms",
    persistence=Persistence.PERSISTED,
    schema=_schema("message_id", "reader_id", "latency_ticks", "entered_memory"),
)
BROADCAST_MADE = register_kind(
    10030,
    "BROADCAST_MADE",
    owner="polis.society.comms",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "broadcaster_id",
        "place_id",
        "text",
        "topic",
        "audience_ids",
        "audience_size",
        "venue_fee_cents",
        "txn_id",
        "stance_proposition",
        "stance_value",
    ),
)
TIE_FORMED = register_kind(
    10040,
    "TIE_FORMED",
    owner="polis.society.graph",
    persistence=Persistence.PERSISTED,
    schema=_schema("a_id", "b_id", "type", "context", "strength", "valence", "trust"),
)
TIE_UPDATED = register_kind(
    10041,
    "TIE_UPDATED",
    owner="polis.society.graph",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "a_id",
        "b_id",
        "type",
        "d_strength",
        "d_valence",
        "d_trust",
        "drivers",
    ),
)
TIE_ENDED = register_kind(
    10042,
    "TIE_ENDED",
    owner="polis.society.graph",
    persistence=Persistence.PERSISTED,
    schema=_schema("a_id", "b_id", "type", "reason", "final_strength"),
)
TIE_TYPE_CHANGED = register_kind(
    10043,
    "TIE_TYPE_CHANGED",
    owner="polis.society.graph",
    persistence=Persistence.PERSISTED,
    schema=_schema("a_id", "b_id", "from_type", "to_type", "trigger"),
)
NETWORK_SNAPSHOT = register_kind(
    10050,
    "NETWORK_SNAPSHOT",
    owner="polis.society.graph",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "n_nodes",
        "n_edges",
        "mean_degree",
        "degree_gini",
        "powerlaw_alpha",
        "powerlaw_ks",
        "clustering_global",
        "clustering_avg_local",
        "assortativity_degree",
        "assortativity_wealth",
        "assortativity_belief",
        "assortativity_district",
        "modularity",
        "n_communities",
        "largest_component_share",
        "n_components",
    ),
)
POST_PUBLISHED = register_kind(
    11010,
    "POST_PUBLISHED",
    owner="polis.society.media",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "post_id",
        "author_id",
        "text",
        "topic",
        "stance_proposition",
        "stance_value",
        "in_reply_to",
        "repost_of",
        "root_post_id",
        "claims",
        "follower_count_at_post",
    ),
)
POST_DELETED = register_kind(
    11011,
    "POST_DELETED",
    owner="polis.society.media",
    persistence=Persistence.PERSISTED,
    schema=_schema("post_id", "author_id", "reason"),
)
REPOST_MADE = register_kind(
    11012,
    "REPOST_MADE",
    owner="polis.society.media",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "post_id",
        "repost_of",
        "root_post_id",
        "author_id",
        "original_author_id",
        "cascade_depth",
        "comment",
    ),
)
POST_ENGAGED = register_kind(
    11020,
    "POST_ENGAGED",
    owner="polis.society.media",
    persistence=Persistence.PERSISTED,
    schema=_schema("post_id", "agent_id", "type", "author_id"),
)
FEED_SERVED = register_kind(
    11021,
    "FEED_SERVED",
    owner="polis.society.media",
    persistence=Persistence.SAMPLED,
    schema=_schema(
        "agent_id",
        "algorithm",
        "post_ids",
        "scores",
        "candidate_pool_size",
        "out_of_network_count",
        "cross_cutting_count",
        "mean_extremity",
    ),
)
CASCADE_CLOSED = register_kind(
    11022,
    "CASCADE_CLOSED",
    owner="polis.society.media",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "root_post_id",
        "size",
        "depth",
        "breadth",
        "structural_virality",
        "reach",
        "impressions",
        "unique_reposters",
        "lifetime_ticks",
    ),
)
FOLLOW_CREATED = register_kind(
    11040,
    "FOLLOW_CREATED",
    owner="polis.society.media",
    persistence=Persistence.PERSISTED,
    schema=_schema("follower_id", "followee_id", "context"),
)
FOLLOW_ENDED = register_kind(
    11041,
    "FOLLOW_ENDED",
    owner="polis.society.media",
    persistence=Persistence.PERSISTED,
    schema=_schema("follower_id", "followee_id", "reason"),
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
LEGACY_ACTION_REJECTED = register_kind(
    4202,
    "ACTION_REJECTED_LEGACY",
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
GOODS_PURCHASED = register_kind(
    6120,
    "GOODS_PURCHASED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "txn_id",
        "buyer_id",
        "seller_firm_id",
        "sku",
        "qty",
        "unit_price_cents",
        "gross_cents",
        "sales_tax_cents",
        "subsidy_cents",
        "ledger_txn_id",
    ),
)
PURCHASE_FAILED = register_kind(
    6121,
    "PURCHASE_FAILED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("buyer_id", "sku", "qty", "reason"),
)
NEED_SATISFIED = register_kind(
    6124,
    "NEED_SATISFIED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "need", "sku", "from_bp", "to_bp"),
)
DURABLE_EXPIRED = register_kind(
    6125,
    "DURABLE_EXPIRED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("agent_id", "sku", "acquired_tick", "life_ticks"),
)
CPI_COMPUTED = register_kind(
    6141,
    "CPI_COMPUTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "basket_version",
        "index_bp",
        "category_index_bp",
        "carried_forward_skus",
        "window_ticks",
    ),
)
INFLATION_COMPUTED = register_kind(
    6142,
    "INFLATION_COMPUTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("yoy_bp", "mom_annualised_bp", "core_bp"),
)
SECTOR_OUTPUT = register_kind(
    6143,
    "SECTOR_OUTPUT",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("sector", "units", "value_cents", "firms_n"),
)
BASKET_FIXED = register_kind(
    6144,
    "BASKET_FIXED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("basket_version", "quantities", "base_prices_cents", "tick"),
)
RENT_PAID = register_kind(
    6150,
    "RENT_PAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("place_id", "tenant_id", "landlord_id", "cents", "period_ticks", "txn_id"),
)
RENT_ARREARS = register_kind(
    6151,
    "RENT_ARREARS",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("place_id", "tenant_id", "owed_cents", "periods_missed"),
)
SECURITY_LISTED = register_kind(
    7001,
    "SECURITY_LISTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "symbol",
        "issuer_firm_id",
        "class",
        "shares_outstanding",
        "listing_price_cents",
        "ipo_round_id",
        "lockup_until_tick",
    ),
)
SECURITY_DELISTED = register_kind(
    7002,
    "SECURITY_DELISTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("symbol", "reason", "final_price_cents", "holders_n"),
)
SESSION_OPENED = register_kind(
    7003,
    "SESSION_OPENED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("session_id", "tick", "symbols", "opening_auction", "reference_prices"),
)
SESSION_CLOSED = register_kind(
    7004,
    "SESSION_CLOSED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("session_id", "tick", "closing_auction", "trades_n", "volume", "notional_cents"),
)
ORDER_SUBMITTED = register_kind(
    7010,
    "ORDER_SUBMITTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "order_id",
        "symbol",
        "trader_id",
        "side",
        "order_type",
        "limit_price_cents",
        "qty",
        "tif",
        "reserved_cents",
        "reserved_qty",
        "arrival_ordinal",
    ),
)
ORDER_REJECTED = register_kind(
    7011,
    "ORDER_REJECTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("trader_id", "symbol", "reason", "detail"),
)
ORDER_CANCELLED = register_kind(
    7012,
    "ORDER_CANCELLED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("order_id", "remaining_qty", "released_cents", "released_qty", "initiator"),
)
ORDER_EXPIRED = register_kind(
    7013,
    "ORDER_EXPIRED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("order_id", "remaining_qty", "released_cents", "released_qty"),
)
TRADE_EXECUTED = register_kind(
    7020,
    "TRADE_EXECUTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "trade_id",
        "symbol",
        "price_cents",
        "qty",
        "buy_order_id",
        "sell_order_id",
        "buyer_id",
        "seller_id",
        "aggressor",
        "commission_buy_cents",
        "commission_sell_cents",
        "ledger_txn_id",
    ),
)
ORDER_FILLED = register_kind(
    7021,
    "ORDER_FILLED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("order_id", "total_qty", "avg_price_cents", "commission_cents"),
)
ORDER_PARTIALLY_FILLED = register_kind(
    7022,
    "ORDER_PARTIALLY_FILLED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("order_id", "filled_qty", "remaining_qty", "avg_price_cents"),
)
BOOK_SNAPSHOT = register_kind(
    7030,
    "BOOK_SNAPSHOT",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "symbol", "best_bid_cents", "best_ask_cents", "bid_depth", "ask_depth", "levels"
    ),
)
OHLCV_COMPUTED = register_kind(
    7040,
    "OHLCV_COMPUTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "symbol",
        "session_tick",
        "open_cents",
        "high_cents",
        "low_cents",
        "close_cents",
        "volume",
        "vwap_cents",
        "trades_n",
    ),
)
INDEX_COMPUTED = register_kind(
    7041,
    "INDEX_COMPUTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("index_name", "value_bp", "divisor", "constituents", "mcap_cents"),
)
CIRCUIT_BREAKER_TRIGGERED = register_kind(
    7050,
    "CIRCUIT_BREAKER_TRIGGERED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "symbol",
        "reference_cents",
        "last_cents",
        "move_bp",
        "band_bp",
        "halt_until_tick",
        "breaker_count",
    ),
)
TRADING_RESUMED = register_kind(
    7051,
    "TRADING_RESUMED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("symbol", "reopen_auction_price_cents", "new_band_bp"),
)
SHORT_OPENED = register_kind(
    7060,
    "SHORT_OPENED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "trader_id",
        "symbol",
        "qty",
        "price_cents",
        "borrow_fee_bp",
        "collateral_cents",
        "margin_ratio_bp",
    ),
)
SHORT_COVERED = register_kind(
    7061,
    "SHORT_COVERED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "trader_id", "symbol", "qty", "price_cents", "realised_pnl_cents", "fees_paid_cents"
    ),
)
BORROW_FEE_CHARGED = register_kind(
    7062,
    "BORROW_FEE_CHARGED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("trader_id", "symbol", "cents", "distributed_to", "txn_id"),
)
MARGIN_CALL = register_kind(
    7063,
    "MARGIN_CALL",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("trader_id", "symbol", "equity_cents", "required_cents", "deadline_tick"),
)
FORCED_LIQUIDATION = register_kind(
    7064,
    "FORCED_LIQUIDATION",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("trader_id", "symbol", "qty", "avg_price_cents", "shortfall_cents"),
)
IPO_ANNOUNCED = register_kind(
    7070,
    "IPO_ANNOUNCED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "symbol",
        "shares_offered",
        "primary_shares",
        "secondary_shares",
        "price_low_cents",
        "price_high_cents",
        "underwriter_bank_id",
        "book_close_tick",
    ),
)
IPO_INDICATION = register_kind(
    7071,
    "IPO_INDICATION",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "investor_id", "qty", "limit_price_cents"),
)
IPO_PRICED = register_kind(
    7072,
    "IPO_PRICED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "symbol",
        "clearing_price_cents",
        "offer_price_cents",
        "discount_bp",
        "oversubscription_bp",
    ),
)
IPO_COMPLETED = register_kind(
    7073,
    "IPO_COMPLETED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "symbol",
        "allocations",
        "gross_proceeds_cents",
        "primary_cents",
        "secondary_cents",
        "underwriting_fee_cents",
        "listing_fee_cents",
        "txn_id",
    ),
)
BOND_LISTED = register_kind(
    7080,
    "BOND_LISTED",
    owner="polis.economy.exchange",
    persistence=Persistence.PERSISTED,
    schema=_schema("symbol", "issuer", "face_cents", "coupon_bp", "matures_tick"),
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
ACCOUNT_CLOSED = register_kind(
    8003,
    "ACCOUNT_CLOSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("account_id", "final_balance_cents", "reason"),
)
DEPOSIT_MADE = register_kind(
    8004,
    "DEPOSIT_MADE",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("owner_id", "bank_id", "cents", "source", "txn_id"),
)
WITHDRAWAL_MADE = register_kind(
    8005,
    "WITHDRAWAL_MADE",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("owner_id", "bank_id", "cents", "txn_id"),
)
WITHDRAWAL_REFUSED = register_kind(
    8006,
    "WITHDRAWAL_REFUSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "owner_id",
        "bank_id",
        "requested_cents",
        "available_cents",
        "queue_position",
    ),
)
LOAN_ORIGINATED = register_kind(
    8010,
    "LOAN_ORIGINATED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "loan_id",
        "lender_id",
        "borrower_id",
        "principal_cents",
        "annual_rate_bp",
        "term_ticks",
        "payment_cents",
        "payments_n",
        "collateral",
        "credit_score_bp",
        "txn_id",
    ),
)
LOAN_APPLICATION_SUBMITTED = register_kind(
    8011,
    "LOAN_APPLICATION_SUBMITTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "application_id",
        "borrower_id",
        "lender_id",
        "requested_cents",
        "purpose",
        "term_ticks",
        "collateral",
    ),
)
LOAN_APPLICATION_DECIDED = register_kind(
    8012,
    "LOAN_APPLICATION_DECIDED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "application_id",
        "approved",
        "credit_score_bp",
        "score_components",
        "offered_rate_bp",
        "offered_cents",
        "reason_codes",
    ),
)
LOAN_PAYMENT_MADE = register_kind(
    8013,
    "LOAN_PAYMENT_MADE",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "loan_id",
        "payment_no",
        "principal_cents",
        "interest_cents",
        "outstanding_after_cents",
        "txn_id",
    ),
)
LOAN_PAYMENT_MISSED = register_kind(
    8014,
    "LOAN_PAYMENT_MISSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("loan_id", "due_cents", "available_cents", "days_past_due"),
)
LOAN_DELINQUENT = register_kind(
    8015,
    "LOAN_DELINQUENT",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("loan_id", "days_past_due", "capitalised_interest_cents", "txn_id"),
)
LOAN_DEFAULTED = register_kind(
    8016,
    "LOAN_DEFAULTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("loan_id", "outstanding_cents", "trigger"),
)
LOAN_WRITTEN_OFF = register_kind(
    8017,
    "LOAN_WRITTEN_OFF",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "loan_id",
        "written_off_cents",
        "recovery_cents",
        "loss_given_default_bp",
        "txn_id",
    ),
)
LOAN_REPAID = register_kind(
    8018,
    "LOAN_REPAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("loan_id", "total_interest_cents", "ticks_to_repay", "early"),
)
COLLATERAL_SEIZED = register_kind(
    8019,
    "COLLATERAL_SEIZED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "loan_id",
        "asset_ref",
        "appraised_cents",
        "realised_cents",
        "txn_id",
    ),
)
INTEREST_ACCRUED = register_kind(
    8020,
    "INTEREST_ACCRUED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "loan_id",
        "cents",
        "annual_rate_bp",
        "period_ticks",
        "accrued_total_cents",
    ),
)
DEPOSIT_INTEREST_PAID = register_kind(
    8021,
    "DEPOSIT_INTEREST_PAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("bank_id", "total_cents", "accounts_n", "rate_bp", "txn_id"),
)
POLICY_RATE_SET = register_kind(
    8030,
    "POLICY_RATE_SET",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "rate_bp",
        "prev_rate_bp",
        "setter",
        "inflation_bp",
        "output_gap_bp",
    ),
)
RESERVE_REQUIREMENT_SET = register_kind(
    8031,
    "RESERVE_REQUIREMENT_SET",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("ratio_bp", "prev_bp", "setter"),
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
MONEY_WITHDRAWN = register_kind(
    8033,
    "MONEY_WITHDRAWN",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "amount_cents",
        "source_account_id",
        "instrument",
        "purpose",
        "txn_id",
    ),
)
OPEN_MARKET_OPERATION = register_kind(
    8034,
    "OPEN_MARKET_OPERATION",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "direction",
        "amount_cents",
        "counterparty_bank_id",
        "symbol",
        "qty",
        "price_cents",
        "txn_id",
    ),
)
INTERBANK_LOAN = register_kind(
    8040,
    "INTERBANK_LOAN",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "loan_id",
        "lender_bank_id",
        "borrower_bank_id",
        "cents",
        "rate_bp",
        "term_ticks",
    ),
)
DISCOUNT_WINDOW_BORROWED = register_kind(
    8041,
    "DISCOUNT_WINDOW_BORROWED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("bank_id", "cents", "penalty_rate_bp", "reserve_shortfall_cents"),
)
INTERBANK_REFUSED = register_kind(
    8042,
    "INTERBANK_REFUSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "borrower_bank_id",
        "lender_bank_id",
        "cents",
        "reason",
    ),
)
BANK_RATIOS_COMPUTED = register_kind(
    8050,
    "BANK_RATIOS_COMPUTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "bank_id",
        "capital_cents",
        "rwa_cents",
        "capital_ratio_bp",
        "reserve_ratio_bp",
        "ldr_bp",
        "npl_bp",
    ),
)
BANK_UNDERCAPITALISED = register_kind(
    8051,
    "BANK_UNDERCAPITALISED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "bank_id",
        "capital_ratio_bp",
        "threshold_bp",
        "new_lending_frozen",
    ),
)
BANK_RUN_DETECTED = register_kind(
    8052,
    "BANK_RUN_DETECTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "bank_id",
        "requested_cents",
        "served_cents",
        "refused_n",
        "deposits_before_cents",
        "deposits_after_cents",
    ),
)
BANK_FAILED = register_kind(
    8053,
    "BANK_FAILED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "bank_id",
        "capital_cents",
        "deposits_cents",
        "shortfall_cents",
        "resolution",
    ),
)
DEPOSIT_INSURANCE_PAID = register_kind(
    8054,
    "DEPOSIT_INSURANCE_PAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("bank_id", "covered_cents", "depositors_n", "txn_id"),
)
DEPOSIT_HAIRCUT = register_kind(
    8055,
    "DEPOSIT_HAIRCUT",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "bank_id",
        "depositor_id",
        "haircut_cents",
        "recovery_bp",
        "txn_id",
    ),
)
BOND_ISSUED = register_kind(
    8060,
    "BOND_ISSUED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("symbol", "face_cents", "coupon_bp", "matures_tick", "auction_id"),
)
BOND_AUCTION_CLEARED = register_kind(
    8061,
    "BOND_AUCTION_CLEARED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "auction_id",
        "offered_cents",
        "bid_cents",
        "clearing_yield_bp",
        "allocations",
        "txn_id",
    ),
)
BOND_AUCTION_FAILED = register_kind(
    8062,
    "BOND_AUCTION_FAILED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("auction_id", "offered_cents", "bid_cents", "shortfall_cents"),
)
COUPON_PAID = register_kind(
    8063,
    "COUPON_PAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("symbol", "holders_n", "total_cents", "txn_id"),
)
BOND_MATURED = register_kind(
    8064,
    "BOND_MATURED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("symbol", "face_cents", "holders_n", "txn_id"),
)
TAX_ASSESSED = register_kind(
    8070,
    "TAX_ASSESSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "taxpayer_id",
        "tax_type",
        "base_cents",
        "rate_bp",
        "assessed_cents",
        "period",
        "due_tick",
    ),
)
TAX_COLLECTED = register_kind(
    8071,
    "TAX_COLLECTED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("taxpayer_id", "tax_type", "cents", "txn_id"),
)
TAX_ARREARS = register_kind(
    8072,
    "TAX_ARREARS",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("taxpayer_id", "cents", "loan_id", "penalty_rate_bp"),
)
TRANSFER_PAID = register_kind(
    8073,
    "TRANSFER_PAID",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema("recipient_id", "programme", "cents", "txn_id"),
)
GOV_BUDGET_CLOSED = register_kind(
    8074,
    "GOV_BUDGET_CLOSED",
    owner="polis.economy",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "period",
        "receipts_cents",
        "spending_cents",
        "debt_service_cents",
        "balance_cents",
        "debt_cents",
        "debt_to_gdp_bp",
    ),
)
STARTUP_FOUNDED = register_kind(
    9001,
    "STARTUP_FOUNDED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "startup_id",
        "firm_id",
        "founder_id",
        "thesis",
        "sector",
        "initial_capital_cents",
        "burn_rate_cents",
    ),
)
THESIS_REVISED = register_kind(
    9002,
    "THESIS_REVISED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("startup_id", "from_thesis", "to_thesis", "trigger"),
)
RUNWAY_UPDATED = register_kind(
    9003,
    "RUNWAY_UPDATED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "startup_id",
        "liquid_cents",
        "burn_rate_cents",
        "runway_ticks",
        "stage",
        "revenue_ttm_cents",
    ),
)
STARTUP_DIED = register_kind(
    9004,
    "STARTUP_DIED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "startup_id", "cause", "age_ticks", "total_raised_cents", "investors_loss_cents"
    ),
)
VC_FUND_FORMED = register_kind(
    9005,
    "VC_FUND_FORMED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "fund_id",
        "firm_id",
        "gp_agent_id",
        "committed_cents",
        "lps",
        "vintage_tick",
        "thesis",
        "mgmt_fee_bp",
        "carry_bp",
        "hurdle_bp",
    ),
)
CAPITAL_CALLED = register_kind(
    9006,
    "CAPITAL_CALLED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("fund_id", "lp_id", "called_cents", "cumulative_called_cents", "txn_id"),
)
LP_DEFAULTED = register_kind(
    9007,
    "LP_DEFAULTED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("fund_id", "lp_id", "called_cents", "forfeited_units", "reallocated_to"),
)
FUND_DISTRIBUTION = register_kind(
    9008,
    "FUND_DISTRIBUTION",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "fund_id",
        "source_exit_id",
        "gross_cents",
        "lp_cents",
        "carry_cents",
        "hurdle_met",
        "txn_id",
    ),
)
MANAGEMENT_FEE_CHARGED = register_kind(
    9009,
    "MANAGEMENT_FEE_CHARGED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("fund_id", "cents", "period", "txn_id"),
)
ROUND_CLOSED = register_kind(
    9010,
    "ROUND_CLOSED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "round_id",
        "startup_id",
        "stage",
        "pre_money_cents",
        "amount_cents",
        "post_money_cents",
        "price_per_share_cents",
        "new_shares",
        "lead_investor_id",
        "participants",
        "option_pool_shares",
        "txn_id",
    ),
)
PITCH_MADE = register_kind(
    9011,
    "PITCH_MADE",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "pitch_id",
        "startup_id",
        "founder_id",
        "investor_id",
        "ask_cents",
        "pre_money_ask_cents",
        "deck_text",
        "traction",
    ),
)
PITCH_EVALUATED = register_kind(
    9012,
    "PITCH_EVALUATED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "pitch_id",
        "investor_id",
        "conviction_bp",
        "thesis_fit_bp",
        "valuation_view_cents",
        "check_size_cents",
        "verdict",
        "concerns",
        "llm_call_id",
    ),
)
TERM_SHEET_ISSUED = register_kind(
    9013,
    "TERM_SHEET_ISSUED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "term_sheet_id",
        "startup_id",
        "investor_id",
        "pre_money_cents",
        "amount_cents",
        "security",
        "liq_pref_bp",
        "participating",
        "pro_rata",
        "board_seat",
        "option_pool_bp",
        "anti_dilution",
        "expires_tick",
    ),
)
TERM_SHEET_ACCEPTED = register_kind(
    9014,
    "TERM_SHEET_ACCEPTED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("term_sheet_id", "round_id"),
)
TERM_SHEET_DECLINED = register_kind(
    9015,
    "TERM_SHEET_DECLINED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("term_sheet_id", "reason_code", "counter_pre_money_cents"),
)
TERM_SHEET_EXPIRED = register_kind(
    9016,
    "TERM_SHEET_EXPIRED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("term_sheet_id"),
)
DOWN_ROUND = register_kind(
    9017,
    "DOWN_ROUND",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "round_id",
        "prior_price_per_share_cents",
        "new_price_per_share_cents",
        "decline_bp",
        "anti_dilution_applied",
        "extra_shares_issued",
    ),
)
CAP_TABLE_UPDATED = register_kind(
    9018,
    "CAP_TABLE_UPDATED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "firm_id",
        "holder_id",
        "share_class",
        "shares_before",
        "shares_after",
        "cause",
        "fully_diluted_after",
    ),
)
OPTION_POOL_SET = register_kind(
    9019,
    "OPTION_POOL_SET",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "pool_shares", "pool_bp", "pre_money_pool", "granted_to"),
)
ACQUISITION_PROPOSED = register_kind(
    9020,
    "ACQUISITION_PROPOSED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "deal_id",
        "acquirer_id",
        "target_id",
        "offer_cents",
        "per_share_cents",
        "consideration",
        "stock_ratio_bp",
        "premium_bp",
        "integration_mode",
        "expires_tick",
        "financing",
    ),
)
ACQUISITION_APPROVED = register_kind(
    9021,
    "ACQUISITION_APPROVED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "deal_id", "accepting_holders", "accepting_bp", "threshold_bp", "drag_along_applied"
    ),
)
ACQUISITION_REJECTED = register_kind(
    9022,
    "ACQUISITION_REJECTED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("deal_id", "accepting_bp", "reason"),
)
ACQUISITION_COMPLETED = register_kind(
    9023,
    "ACQUISITION_COMPLETED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "deal_id", "price_cents", "per_share_cents", "integration_mode", "txn_id", "waterfall_ref"
    ),
)
ASSET_SALE = register_kind(
    9024,
    "ASSET_SALE",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("deal_id", "seller_id", "buyer_id", "assets", "cents", "txn_id"),
)
INTEGRATION_COMPLETED = register_kind(
    9025,
    "INTEGRATION_COMPLETED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "deal_id",
        "headcount_retained",
        "redundancies",
        "sku_transfers",
        "productivity_delta_bp",
        "loans_transferred",
    ),
)
ACQUISITION_BLOCKED = register_kind(
    9026,
    "ACQUISITION_BLOCKED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("deal_id", "blocker", "hhi_before", "hhi_after", "sector", "policy_ref"),
)
BANKRUPTCY_FILED = register_kind(
    9030,
    "BANKRUPTCY_FILED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "case_id",
        "entity_id",
        "entity_type",
        "trigger",
        "assets_cents",
        "liabilities_cents",
        "filed_by",
        "petitioning_creditor_id",
    ),
)
AUTOMATIC_STAY_IMPOSED = register_kind(
    9031,
    "AUTOMATIC_STAY_IMPOSED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "case_id",
        "entity_id",
        "cancelled_order_ids",
        "released_cents",
        "released_shares",
        "blocked_action_types",
        "stay_until_tick",
    ),
)
CLAIM_REGISTERED = register_kind(
    9032,
    "CLAIM_REGISTERED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "case_id", "creditor_id", "claim_cents", "priority_class", "collateral_ref", "loan_id"
    ),
)
ASSETS_LIQUIDATED = register_kind(
    9033,
    "ASSETS_LIQUIDATED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "case_id", "item", "book_cents", "realised_cents", "haircut_bp", "buyer_id", "txn_id"
    ),
)
DISTRIBUTION_MADE = register_kind(
    9034,
    "DISTRIBUTION_MADE",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "case_id",
        "priority_class",
        "creditor_id",
        "claim_cents",
        "paid_cents",
        "class_recovery_bp",
        "txn_id",
    ),
)
BANKRUPTCY_DISCHARGED = register_kind(
    9035,
    "BANKRUPTCY_DISCHARGED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "case_id", "outcome", "written_off_cents", "blended_recovery_bp", "resolved_tick"
    ),
)
CREDIT_FLAG_SET = register_kind(
    9036,
    "CREDIT_FLAG_SET",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("entity_id", "flag", "set_tick", "expires_tick"),
)
EXEMPTION_APPLIED = register_kind(
    9037,
    "EXEMPTION_APPLIED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("case_id", "entity_id", "exempt_cents", "basis"),
)
ESTATE_DEFERRED_TO_CASE = register_kind(
    9038,
    "ESTATE_DEFERRED_TO_CASE",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("case_id", "deceased_agent_id", "estate_cents", "heirs"),
)
EXIT_COMPLETED = register_kind(
    9040,
    "EXIT_COMPLETED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema(
        "startup_id",
        "type",
        "gross_proceeds_cents",
        "distribution",
        "multiple_bp",
        "holding_period_ticks",
    ),
)
WATERFALL_APPLIED = register_kind(
    9041,
    "WATERFALL_APPLIED",
    owner="polis.economy.ventures",
    persistence=Persistence.PERSISTED,
    schema=_schema("firm_id", "proceeds_cents", "tranches"),
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
