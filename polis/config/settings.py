from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from polis.config.canon import canonical_bytes, sha256_hex
from polis.config.errors import ConfigError, ProfileNotFound
from polis.config.paths import PROFILES_DIR


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunSettings(FrozenModel):
    name: str
    seed: int
    ticks: int
    checkpoint_interval: int = 500
    retention: Literal["full", "metrics_only"] = "full"
    scale: int | None = None
    tags: tuple[str, ...] = ()


class ClockSettings(FrozenModel):
    profile: Literal["microscope", "chronicle"] = "microscope"
    ticks_per_sim_day: int = 24
    days_per_sim_year: int = 360
    demographic_acceleration: float = 1.0
    allow_nonstandard: bool = False

    @model_validator(mode="after")
    def validate_profile(self) -> ClockSettings:
        expected = 24 if self.profile == "microscope" else 1
        if not self.allow_nonstandard and self.ticks_per_sim_day != expected:
            raise ValueError(
                f"{self.profile} requires ticks_per_sim_day={expected}; "
                f"got {self.ticks_per_sim_day}"
            )
        return self


class ActionSlotSettings(FrozenModel):
    microscope: int = Field(default=1, ge=1)
    chronicle: int = Field(default=4, ge=1)

    def for_profile(self, profile: Literal["microscope", "chronicle"]) -> int:
        return self.microscope if profile == "microscope" else self.chronicle


class ActionLegalitySettings(FrozenModel):
    oracle: Literal["permissive", "law"] = "permissive"


class ActionSettings(FrozenModel):
    slots_per_tick: ActionSlotSettings = ActionSlotSettings()
    max_params_bytes: int = Field(default=4_096, ge=1)
    max_reasoning_chars: int = Field(default=2_000, ge=0)
    max_speech_chars: int = Field(default=1_000, ge=0)
    legality: ActionLegalitySettings = ActionLegalitySettings()
    reject_on_unregistered: bool = True


class PopulationSettings(FrozenModel):
    initial_agents: int = 1000
    age_distribution: str = "pyramid_ca_2020"
    trait_model: str = "big_five_plus_econ"


class WorldSettings(FrozenModel):
    width: int = 200
    height: int = 200
    districts: int = 6
    places_per_district: int = 60


class LLMBudgetLine(FrozenModel):
    calls_per_tick: int
    tokens_per_tick: int


class LLMBudgetSettings(FrozenModel):
    lines: dict[str, LLMBudgetLine]
    usd_per_run: Decimal = Decimal("60.0")
    usd_halt_multiple: Decimal = Decimal("1.2")
    max_calls_per_run: int | None = Field(default=None, gt=0)
    on_exhaustion: Literal["degrade_to_reflex", "halt"] = "degrade_to_reflex"


class RouteSpec(FrozenModel):
    lane: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 512
    structured: Literal["constrain", "repair", "none"] = "repair"
    schema_: str | None = Field(default=None, alias="schema")
    template: str = ""
    fallback: tuple[Mapping[str, str], ...] = ()
    last_resort: str = "reflex"


class LaneSettings(FrozenModel):
    kind: Literal["codex_cli", "grok_cli", "minimax", "ollama", "openai_compat", "stub"]
    base_url: str | None = None
    api_key_env: str | None = None
    max_concurrency: int = 8
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    calls_per_window: int | None = Field(default=None, gt=0)
    call_window_seconds: int = Field(default=18_000, gt=0)
    quota_scope: str | None = None
    quota_path: str = "file://./.cache/provider-quota.sqlite3"
    timeout_s: float = 45.0
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_base_seconds: float = Field(default=0.25, ge=0)
    retry_max_seconds: float = Field(default=10.0, ge=0)
    structured_output: Literal["schema", "json_mode", "none"] = "schema"
    billing: Literal["token", "gpu_time", "free"] = "token"
    model_version_pin: str | None = None
    price_in_usd_per_mtok: Decimal = Decimal(0)
    price_out_usd_per_mtok: Decimal = Decimal(0)
    price_cached_in_usd_per_mtok: Decimal | None = None
    extra: Mapping[str, Any] = {}


class CacheSettings(FrozenModel):
    mode: Literal["live", "replay", "hybrid"] = "hybrid"
    path: str = "file://./.cache/completions"
    schema_version: int = 1
    verify_render: bool = True
    strict_version: bool = True
    trust: Literal["verify", "trust"] = "verify"
    l0_entries: int = 50_000


class LLMSettings(FrozenModel):
    providers: dict[str, LaneSettings]
    budget: LLMBudgetSettings
    routing: dict[str, RouteSpec]
    fallback_policy: Literal["permissive", "strict"] = "permissive"
    cache: CacheSettings = CacheSettings()
    prompt_variant: str | None = None
    est_tokens_per_call: int = 3300
    request_timeout_ms: int = Field(default=3_000, ge=1)

    @model_validator(mode="after")
    def validate_routes(self) -> LLMSettings:
        missing = sorted({route.lane for route in self.routing.values()} - set(self.providers))
        if missing:
            raise ValueError(f"routing references unknown provider lanes: {missing}")
        cognition = self.budget.lines.get("cognition")
        if (
            cognition is not None
            and cognition.tokens_per_tick < cognition.calls_per_tick * self.est_tokens_per_call
        ):
            required = cognition.calls_per_tick * self.est_tokens_per_call
            raise ValueError(
                "cognition tokens_per_tick is incoherent: "
                f"{cognition.calls_per_tick} calls x {self.est_tokens_per_call} "
                f"requires at least {required}, got {cognition.tokens_per_tick}"
            )
        for name, provider in self.providers.items():
            if provider.kind == "minimax" and provider.structured_output != "none":
                raise ValueError(f"MiniMax lane {name!r} must set structured_output='none'")
        return self


class SalienceSettings(FrozenModel):
    policy: Literal["weighted", "random", "always"] = "weighted"
    exploration_epsilon: float = 0.02
    weights: dict[str, float] = {
        "surprise": 0.30,
        "stakes": 0.35,
        "novelty": 0.10,
        "social": 0.15,
        "scheduled": 0.10,
    }
    deliberate_share: float = 0.07
    cognition_sample_rate: float = 0.02


class MemorySettings(FrozenModel):
    write_threshold: float = 0.25
    max_per_agent: int = 3000
    retrieval_k: int = 12
    retrieval_max_tokens: int = 600
    retrieval_weights: dict[str, float] = {
        "recency": 1.0,
        "importance": 1.0,
        "relevance": 1.0,
    }
    reflection_threshold: float = 4.0
    reflection_min_gap_ticks: int = 24


FEED_FEATURE_COUNT = 11


class FeedEngagementSettings(FrozenModel):
    eta: float = Field(default=0.05, gt=0)
    passes: int = Field(default=20, ge=1)
    n0: int = Field(default=5_000, ge=0)
    beta_prior: tuple[float, ...] = Field(
        default=(0.0,) * FEED_FEATURE_COUNT,
        min_length=FEED_FEATURE_COUNT,
        max_length=FEED_FEATURE_COUNT,
    )


class SocietyFeedSettings(FrozenModel):
    recency_halflife_sim_hours: float = Field(default=12.0, gt=0)
    pop_norm: int = Field(default=200, ge=1)
    follower_norm: int = Field(default=200, ge=1)
    adversarial_gamma: float = Field(default=0.5, ge=0)
    engagement: FeedEngagementSettings = FeedEngagementSettings()


class BeliefGenesisSettings(FrozenModel):
    mixture_separation: float = Field(default=0.0, ge=0)
    sd: float = Field(default=0.25, gt=0)


class BeliefSettings(FrozenModel):
    alpha: dict[str, float] = {
        "experience": 0.35,
        "social": 0.10,
        "media": 0.08,
    }
    theta_backfire: float = Field(default=0.60, ge=0, le=1)
    theta_entrench: float = Field(default=0.60, ge=0, le=1)
    theta_trust: float = Field(default=0.40, ge=0, le=1)
    beta_backfire: float = Field(default=0.05, ge=0)
    delta_entrench: float = Field(default=0.03, ge=0)
    eta_trust: float = Field(default=0.04, ge=0)
    gamma_c: float = Field(default=0.02, ge=0)
    max_belief_updates_per_call: int = Field(default=5, ge=0)
    max_step: float = Field(default=0.35, ge=0)
    genesis: BeliefGenesisSettings = BeliefGenesisSettings()
    heritability_beliefs: float = Field(default=0.40, ge=0, le=1)
    confidence_dilution: float = Field(default=0.5, ge=0, le=1)
    sigma_belief: float = Field(default=0.08, ge=0)
    consensus_floor: float = Field(default=0.02, ge=0)
    social_influence_off: bool = False
    backfire_off: bool = False

    @model_validator(mode="after")
    def validate_alpha(self) -> BeliefSettings:
        expected = {"experience", "social", "media"}
        if set(self.alpha) != expected:
            raise ValueError("beliefs.alpha must define experience, social and media")
        if any(value < 0 for value in self.alpha.values()):
            raise ValueError("beliefs.alpha values must be non-negative")
        return self


class SocietySettings(FrozenModel):
    feed_algorithm: Literal["chronological", "engagement", "random", "adversarial"] = "engagement"
    outlets: int = Field(default=4, ge=0)
    feed_slice: int = Field(default=15, ge=1, le=15)
    feed_candidate_cap: int = Field(default=300, ge=15)
    feed_window_sim_hours: int = Field(default=72, ge=1)
    feed_out_of_network_quota: float = Field(default=0.30, ge=0, le=1)
    repeat_penalty: float = Field(default=0.4, ge=0, le=1)
    hearing_threshold: float = Field(default=0.35, ge=0, le=1)
    max_dms_per_tick: int = Field(default=2, ge=0)
    conversation_idle_ticks: int = Field(default=2, ge=1)
    cascade_idle_ticks: int = Field(default=24, ge=1)
    colocation_threshold: int = Field(default=6, ge=1)
    befriend_window_sim_days: int = Field(default=14, ge=1)
    tie_event_threshold: float = Field(default=0.02, ge=0, le=1)
    tie_halflife_sim_days: dict[str, float | None] = {
        "acquaintance": 30.0,
        "friend": 90.0,
        "colleague": 120.0,
        "rival": 180.0,
        "kin": None,
        "partner": None,
        "creditor": None,
    }
    homophily_bias: float = Field(default=0.0, ge=0)
    comms_attention: Literal["tie_weighted", "uniform"] = "tie_weighted"
    outlet_slant_dispersion: float = Field(default=0.55, ge=0)
    cpm_cents: int = Field(default=40, ge=0)
    news_cycle: Literal["daily"] = "daily"
    stories_per_reporter_per_cycle: int = Field(default=1, ge=0)
    claim_tolerance: float = Field(default=0.10, ge=0)
    misinfo_audit_rate: float = Field(default=0.05, ge=0, le=1)
    source_window_sim_days: int = Field(default=14, ge=1)
    line_threshold: float = Field(default=0.25, ge=0, le=1)
    correction_reach_multiplier: float = Field(default=0.6, ge=0, le=1)
    subscription_price_cents: int = Field(default=0, ge=0)
    reach_norm: int = Field(default=500, ge=1)
    newsworthiness_weights: dict[str, float] = {
        "mag": 0.25,
        "prom": 0.20,
        "nov": 0.15,
        "conf": 0.20,
        "prox": 0.10,
        "slant": 0.10,
    }
    distribution_weights: dict[str, float] = {
        "trust": 0.35,
        "topic": 0.25,
        "prox": 0.15,
        "sub": 0.15,
        "reach": 0.10,
    }
    feed: SocietyFeedSettings = SocietyFeedSettings()

    @model_validator(mode="after")
    def validate_weight_maps(self) -> SocietySettings:
        expected_maps = (
            (
                "newsworthiness_weights",
                self.newsworthiness_weights,
                {"mag", "prom", "nov", "conf", "prox", "slant"},
            ),
            (
                "distribution_weights",
                self.distribution_weights,
                {"trust", "topic", "prox", "sub", "reach"},
            ),
        )
        for name, weights, expected in expected_maps:
            if set(weights) != expected:
                raise ValueError(f"society.{name} must define exactly {sorted(expected)}")
            if abs(sum(weights.values()) - 1.0) > 1e-6:
                raise ValueError(f"society.{name} must sum to 1.0")
        return self

    @model_validator(mode="after")
    def validate_tie_halflives(self) -> SocietySettings:
        expected = {
            "acquaintance",
            "friend",
            "colleague",
            "rival",
            "kin",
            "partner",
            "creditor",
        }
        actual = set(self.tie_halflife_sim_days)
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ValueError(
                "society.tie_halflife_sim_days must define every tie type exactly once; "
                f"missing={missing}, unknown={unknown}"
            )
        return self


class EconomySettings(FrozenModel):
    enabled: bool = False
    currency: Literal["POL"] = "POL"
    occupations_path: str = "configs/occupations.yaml"
    initial_firms: int = 0
    initial_banks: int = 3
    m0_cents_per_capita: int = 1_800_000
    household_share_bp: int = 7_000
    firm_share_bp: int = 2_000
    bank_share_bp: int = 1_000
    median_wage_cents: int = 3_600_000

    @model_validator(mode="after")
    def validate_genesis_shares(self) -> EconomySettings:
        if self.household_share_bp + self.firm_share_bp + self.bank_share_bp != 10_000:
            raise ValueError("economy genesis shares must sum to 10,000 bp")
        if self.initial_banks < 1:
            raise ValueError("economy.initial_banks must be positive")
        return self


class LabourPayrollSettings(FrozenModel):
    days: tuple[int, ...] = (1, 15)
    hour: int = 17


class LabourSettings(FrozenModel):
    vacancy_ttl_days: int = 30
    vacancy_visibility_k: int = 8
    max_open_vacancies_per_firm: int = 5
    max_open_applications: int = 6
    min_match_score_bp: int = 5_500
    shortlist_multiple: int = 3
    max_bargaining_rounds: int = 2
    offer_stale_days: int = 3
    offer_ttl_days: int = 5
    autopost_window_days: int = 5
    search_window_days: int = 28
    severance_periods_bp: int = 0
    notice_ticks: int = 0
    retirement_age: int = 65
    minimum_wage_cents: int = 0
    skill_decay_bp_per_month: int = 40
    payroll: LabourPayrollSettings = LabourPayrollSettings()


class FirmMarkupSettings(FrozenModel):
    initial_bp: int = 2_500
    step_bp: int = 200
    max_bp: int = 8_000
    target_low_bp: int = 70_000
    target_high_bp: int = 300_000


class FirmSettings(FrozenModel):
    beta_capital_bp: int = 3_000
    capital_ref_cents: int = 1_000_000
    depreciation_bp_per_year: int = 1_000
    learning_bp_per_day: int = 3
    productivity_sigma_bp: int = 40
    productivity_bounds_bp: tuple[int, int] = (2_000, 40_000)
    spoilage_bp_per_day: int = 2_000
    price_override_ttl_days: int = 30
    payout_ratio_bp: int = 3_000
    retained_floor_months: int = 1
    min_founding_capital_cents: int = 0
    max_firms_per_founder: int = 3
    working_capital_months: int = 3
    seed_effective_labour_bp_per_worker: int = 4_000
    markup: FirmMarkupSettings = FirmMarkupSettings()


class ConsumptionSettings(FrozenModel):
    subsistence_gamma_bp: int = 4_000
    max_sellers_considered: int = 6
    sales_tax_bp: int = 800
    savings_share_bp: int = 2_400
    buffer_bp: int = 2_000


class GoodsSettings(FrozenModel):
    catalogue_path: str = "configs/skus.yaml"
    search_k: int = 5
    search_radius_districts: int = 2
    food_stock_cap_units: int = 14
    reflex_value_cap_cents: int = 0
    purchase_max_qty: int = 1_000
    cpi_base_bp: int = 10_000
    cpi_window_days: int = 30
    cpi_basket_min_skus: int = 12
    cpi_carry_warn_frac_bp: int = 2_500
    fisher_enabled: bool = True
    initial_inventory_days: int = 30
    max_purchases_per_agent_per_day: int = 3
    consumption: ConsumptionSettings = ConsumptionSettings()


class TaylorRuleSettings(FrozenModel):
    neutral_bp: int = 250
    target_bp: int = 200
    phi_pi_bp: int = 15_000
    phi_y_bp: int = 5_000
    bounds_bp: tuple[int, int] = (0, 4_000)


class BankingSettings(FrozenModel):
    reserve_ratio_bp: int = 1_000
    capital_ratio_min_bp: int = 800
    capital_buffer_bp: int = 1_050
    stress_score_bump_bp: int = 500
    interbank_min_ratio_bp: int = 900
    interbank_spread_bp: int = 50
    interbank_concentration_bp: int = 2_500
    deposit_rate_bp: int = 50
    policy_rate_bp: int = 400
    discount_penalty_bp: int = 200
    insurance_premium_bp: int = 5
    insurance_cap_months: int = 6
    fire_sale_bp: int = 7_000
    policy_review_days: int = 42
    cb_backstop: bool = False
    resolution: Literal["assume", "liquidate"] = "assume"
    underwriting: Literal["scorecard", "llm"] = "scorecard"
    policy_rate_rule: Literal["taylor", "fixed", "political"] = "fixed"
    taylor: TaylorRuleSettings = TaylorRuleSettings()


class CreditSettings(FrozenModel):
    min_score_bp: int = 4_500
    risk_spread_k: int = 6_000
    base_spread_bp: int = 150
    term_premium_bp_per_year: int = 25
    concentration_bp: int = 2_500
    max_loan_income_multiple_bp: int = 40_000
    grace_days: int = 14
    delinquency_days: int = 30
    default_days: int = 90
    writeoff_after_days: int = 180
    delinquency_penalty_bp: int = 300
    payment_interval_days: int = 30
    max_term_days: dict[str, int] = {
        "consumer": 1_080,
        "mortgage": 9_000,
        "corporate": 2_520,
        "interbank": 1,
        "sovereign": 3_600,
        "tax_arrears": 1_080,
    }
    risk_weight_bp: dict[str, int] = {
        "sovereign": 0,
        "mortgage": 5_000,
        "corporate": 10_000,
        "consumer": 10_000,
        "interbank": 2_000,
        "tax_arrears": 10_000,
    }


class TaxSettings(FrozenModel):
    income_brackets: tuple[tuple[int, int], ...] = (
        (0, 0),
        (2_000_000, 1_500),
        (6_000_000, 2_500),
        (15_000_000, 3_500),
    )
    payroll_employer_bp: int = 500
    corporate_bp: int = 2_000
    sales_bp: int = 800
    exempt_necessities: bool = False
    arrears_penalty_bp: int = 800


class SpendSettings(FrozenModel):
    benefit_replacement_bp: int = 4_000
    benefit_max_days: int = 182
    health_subsidy_bp: int = 0


class TreasurySettings(FrozenModel):
    floor_cents: int = 0
    initial_spending_quarters: int = 1
    bond_denomination_cents: int = 100_000
    bond_terms_days: tuple[int, ...] = (360, 1_800, 3_600)
    sovereign_spread_bp: int = 100
    tax: TaxSettings = TaxSettings()
    spend: SpendSettings = SpendSettings()


class ExchangeSettings(FrozenModel):
    enabled: bool = False
    tick_size_cents: int = 1
    commission_bp: int = 20
    commission_floor_cents: int = 1
    max_order_qty_bp: int = 1_000
    band_bp: int = 2_000
    halt_bp: int = 3_000
    halt_ticks: int = 2
    max_halts_per_session: int = 2
    max_short_bp: int = 1_000
    initial_margin_bp: int = 15_000
    maintenance_margin_bp: int = 3_000
    borrow_fee_bp: int = 200
    ipo_min_age_days: int = 720
    ipo_min_revenue_cents: int = 0
    ipo_book_days: int = 3
    underwriter_discount_bp: int = 500
    underwriting_fee_bp: int = 500
    listing_fee_cents: int = 10_000
    lockup_days: int = 180
    equity_risk_premium_bp: int = 500
    bootstrap_listing_day: int | None = None
    bootstrap_shares: int = 100_000
    bootstrap_price_cents: int = 1_000
    zero_intelligence_participation_bp: int = 500
    zero_intelligence_spread_bp: int = 500
    zero_intelligence_max_order_qty: int = 10

    @model_validator(mode="after")
    def validate_exchange(self) -> ExchangeSettings:
        if self.tick_size_cents <= 0:
            raise ValueError("exchange.tick_size_cents must be positive")
        if not 0 <= self.commission_bp <= 10_000:
            raise ValueError("exchange.commission_bp must be between 0 and 10,000")
        if not 0 < self.max_order_qty_bp <= 10_000:
            raise ValueError("exchange.max_order_qty_bp must be between 1 and 10,000")
        if self.bootstrap_listing_day is not None and self.bootstrap_listing_day < 1:
            raise ValueError("exchange.bootstrap_listing_day must be at least 1")
        if self.bootstrap_shares <= 0 or self.bootstrap_price_cents <= 0:
            raise ValueError("exchange bootstrap shares and price must be positive")
        if not 0 <= self.zero_intelligence_participation_bp <= 10_000:
            raise ValueError(
                "exchange.zero_intelligence_participation_bp must be between 0 and 10,000"
            )
        if not 0 <= self.zero_intelligence_spread_bp <= self.band_bp:
            raise ValueError(
                "exchange.zero_intelligence_spread_bp must be between 0 and exchange.band_bp"
            )
        if self.zero_intelligence_max_order_qty <= 0:
            raise ValueError("exchange.zero_intelligence_max_order_qty must be positive")
        return self


class VentureSettings(FrozenModel):
    enabled: bool = False
    acceptance_fixture: bool = False
    founder_shares: int = 1_000_000
    option_pool_bp: int = 1_000
    liq_pref_bp: int = 10_000
    term_sheet_days: int = 14
    fundraise_trigger_days: int = 180
    max_open_pitches: int = 5
    comparable_window: int = 8
    seed_default_pre_money_cents: int = 100_000_000
    sector_multiple_bp: dict[str, int] = Field(default_factory=dict)
    growth_cap_bp: int = 20_000
    valuation_llm_weight_bp: int = 5_000
    management_fee_bp: int = 200
    carry_bp: int = 2_000
    hurdle_bp: int = 800
    lp_unit_cents: int = 10_000
    call_grace_days: int = 14
    acquisition_premium_bp: int = 2_500
    acquisition_threshold_bp: int = 5_001
    drag_along_bp: int = 7_500
    squeeze_out_bp: int = 9_000
    redundancy_bp: int = 3_000
    integration_synergy_bp: int = 0


class BankruptcySettings(FrozenModel):
    enabled: bool = False
    grace_days: int = 14
    insolvency_persist_days: int = 30
    petition_min_cents: int = 100_000
    stay_max_days: int = 60
    liquidation_days: int = 5
    unlisted_haircut_bp: int = 5_000
    inventory_haircut_bp: int = 5_000
    capital_haircut_bp: int = 4_000
    admin_fee_bp: int = 300
    wage_priority_days: int = 90
    exempt_months: int = 1
    credit_flag_years: int = 7


class PolityOfficeSettings(FrozenModel):
    seats: int = 1
    term_sim_years: int | None = None
    term_limit: int | None = None
    method: Literal["plurality", "approval", "irv", "proportional"] | None = None
    salary_cents: int = 0
    min_skill_law: float | None = None


class PolityAbstainSettings(FrozenModel):
    theta_0: float = 0.35
    theta_conscientiousness: float = 0.15
    theta_civic: float = 0.10


class IncomePolicySettings(FrozenModel):
    brackets: tuple[tuple[int, int], ...] = (
        (0, 0),
        (3_000_000, 2_000),
        (9_000_000, 3_500),
    )


class TaxPolicySettings(FrozenModel):
    income: IncomePolicySettings = IncomePolicySettings()
    corporate_bp: int = 2_000
    capital_gains_bp: int = 1_500
    inheritance_bp: int = 1_000
    vat_bp: int = 1_000


class MoneyPolicySettings(FrozenModel):
    policy_rate_bp: int = 200


class WelfarePolicySettings(FrozenModel):
    unemployment_benefit_cents: int = 120_000
    benefit_duration_ticks: int = 8_640
    pension_cents: int = 150_000
    child_benefit_cents: int = 40_000


class EducationPolicySettings(FrozenModel):
    spend_cents_per_student: int = 0
    compulsory_until_age: int = 18


class PolicePolicySettings(FrozenModel):
    budget_cents: int = 5_000_000


class CourtsPolicySettings(FrozenModel):
    budget_cents: int = 3_000_000
    loser_pays: bool = False


class PrisonPolicySettings(FrozenModel):
    capacity: int = 40


class SentencingPolicySettings(FrozenModel):
    multiplier_bp: int = 10_000


class LabourPolicySettings(FrozenModel):
    minimum_wage_cents: int = 1_200
    max_hours_per_sim_week: int = 48


class FinanceRegulationPolicySettings(FrozenModel):
    margin_allowed: bool = True
    short_selling_allowed: bool = True
    insider_trading_enforced: bool = True


class LabourRegulationPolicySettings(FrozenModel):
    at_will_dismissal: bool = True


class MediaRegulationPolicySettings(FrozenModel):
    disclosure_required: bool = False


class HousingRegulationPolicySettings(FrozenModel):
    rent_cap_bp: int | None = None


class RegulationPolicySettings(FrozenModel):
    finance: FinanceRegulationPolicySettings = FinanceRegulationPolicySettings()
    labour: LabourRegulationPolicySettings = LabourRegulationPolicySettings()
    media: MediaRegulationPolicySettings = MediaRegulationPolicySettings()
    housing: HousingRegulationPolicySettings = HousingRegulationPolicySettings()


class MigrationPolicySettings(FrozenModel):
    quota_per_sim_year: int = 60


class PolityControlPolicySettings(FrozenModel):
    campaign_cap_cents: int | None = None
    felon_franchise: bool = False


class GovernmentPolicySettings(FrozenModel):
    debt_ceiling_cents: int = 500_000_000
    public_notices_budget_cents: int = 0


class SocietyPolicySettings(FrozenModel):
    feed_algorithm: Literal["chronological", "engagement", "social", "diversity"] = "engagement"


class PolicyInitialSettings(FrozenModel):
    tax: TaxPolicySettings = TaxPolicySettings()
    money: MoneyPolicySettings = MoneyPolicySettings()
    welfare: WelfarePolicySettings = WelfarePolicySettings()
    education: EducationPolicySettings = EducationPolicySettings()
    police: PolicePolicySettings = PolicePolicySettings()
    courts: CourtsPolicySettings = CourtsPolicySettings()
    prison: PrisonPolicySettings = PrisonPolicySettings()
    sentencing: SentencingPolicySettings = SentencingPolicySettings()
    labour: LabourPolicySettings = LabourPolicySettings()
    regulation: RegulationPolicySettings = RegulationPolicySettings()
    migration: MigrationPolicySettings = MigrationPolicySettings()
    polity: PolityControlPolicySettings = PolityControlPolicySettings()
    government: GovernmentPolicySettings = GovernmentPolicySettings()
    society: SocietyPolicySettings = SocietyPolicySettings()

    def flat(self) -> Mapping[str, Any]:
        result: dict[str, Any] = {}

        def visit(prefix: str, value: Any) -> None:
            if isinstance(value, BaseModel):
                for field_name in value.__class__.model_fields:
                    visit(
                        f"{prefix}.{field_name}" if prefix else field_name,
                        getattr(value, field_name),
                    )
                return
            result[prefix] = value

        visit("", self)
        return result


def _default_polity_offices() -> dict[str, PolityOfficeSettings]:
    return {
        "president": PolityOfficeSettings(
            term_sim_years=4,
            term_limit=2,
            salary_cents=900_000,
        ),
        "council": PolityOfficeSettings(
            seats=7,
            term_sim_years=2,
            method="proportional",
            salary_cents=450_000,
        ),
        "judge": PolityOfficeSettings(
            seats=2,
            term_sim_years=6,
            salary_cents=700_000,
            min_skill_law=0.6,
        ),
        "police_chief": PolityOfficeSettings(salary_cents=600_000),
        "cb_governor": PolityOfficeSettings(term_sim_years=6, salary_cents=800_000),
    }


class PolitySettings(FrozenModel):
    election_method: Literal["plurality", "approval", "irv", "proportional"] = "plurality"
    offices: dict[str, PolityOfficeSettings] = Field(default_factory=_default_polity_offices)
    council_session: str = "weekly"
    policy_review: str = "weekly"
    court_session: str = "twice_weekly"
    campaign_length_sim_days: int = 30
    candidacy_close_sim_days: int = 7
    candidacy_deposit_cents: int = 250_000
    deposit_refund_share: float = 0.05
    candidacy_record_bar: tuple[str, ...] = ("fraud", "embezzlement", "perjury")
    party_founding_fee_cents: int = 100_000
    max_platform_planks: int = 8
    initiative_signatures: int = 50
    exposure_halflife_sim_days: int = 14
    outlet_efficiency: float = 0.6
    abstain: PolityAbstainSettings = PolityAbstainSettings()
    vote_model: Literal["fitted_from_deliberate"] = "fitted_from_deliberate"
    vote_holdout_share: float = 0.20
    vote_min_holdout_lift: float = 0.5
    omega_prior: dict[str, float] = {
        "congruence": 1.0,
        "self_interest": 0.6,
        "social": 0.4,
        "media": 0.3,
        "party_id": 0.8,
        "incumbency": 0.2,
    }
    llm_election_multiplier: float = 6.0
    can_regulate_feed: bool = False
    policy: PolicyInitialSettings = PolicyInitialSettings()

    @model_validator(mode="after")
    def validate_polity(self) -> PolitySettings:
        expected_offices = {"president", "council", "judge", "police_chief", "cb_governor"}
        if set(self.offices) != expected_offices:
            raise ValueError("polity.offices must define the five constitutional offices")
        expected_omega = {
            "congruence",
            "self_interest",
            "social",
            "media",
            "party_id",
            "incumbency",
        }
        if set(self.omega_prior) != expected_omega:
            raise ValueError("polity.omega_prior must define all six vote features")
        if not 0 <= self.deposit_refund_share <= 1:
            raise ValueError("polity.deposit_refund_share must be between zero and one")
        if not 0 < self.vote_holdout_share < 1:
            raise ValueError("polity.vote_holdout_share must be between zero and one")
        return self


class LawSettings(FrozenModel):
    detection_window_sim_days: int = Field(default=180, ge=1)
    mnpi_window_sim_days: int = Field(default=14, ge=1)
    base_detect: dict[str, float] = {
        "theft": 0.35,
        "assault": 0.45,
        "fraud": 0.12,
        "insider_trading": 0.06,
        "embezzlement": 0.10,
        "contract_breach": 0.30,
        "perjury": 0.20,
    }
    victim_awareness: dict[str, float] = {
        "theft": 0.95,
        "assault": 0.95,
        "fraud": 0.30,
        "insider_trading": 0.05,
        "embezzlement": 0.15,
        "contract_breach": 0.85,
        "perjury": 0.40,
    }
    capacity_exponent: float = Field(default=0.6, gt=0)
    witness_bonus_per_witness: float = Field(default=0.4, ge=0)
    witness_bonus_cap: float = Field(default=1.2, ge=0)
    cost_per_patrol_cents: int = Field(default=20_000, ge=1)
    cost_per_investigation_cents: int = Field(default=150_000, ge=1)
    cost_per_case_cents: int = Field(default=400_000, ge=1)
    charge_threshold: float = Field(default=0.45, ge=0, le=1)
    conviction_threshold: float = Field(default=0.60, ge=0, le=1)
    evidence_window_sim_days: int = Field(default=30, ge=1)
    strength_norm: float = Field(default=6.0, gt=0)
    counsel_base_evidence: int = Field(default=3, ge=0)
    counsel_skill_factor: int = Field(default=8, ge=0)
    legal_aid_wealth_pct: float = Field(default=0.25, ge=0, le=1)
    min_counsel_skill_law: float = Field(default=0.5, ge=0, le=1)
    filing_fee_cents: int = Field(default=50_000, ge=0)
    filing_fee_waiver_pct: float = Field(default=0.25, ge=0, le=1)
    garnishment_rate: float = Field(default=0.20, ge=0, le=1)
    fine_per_tick_cents: int = Field(default=8_000, ge=0)
    ex_offender_wage_penalty: float = Field(default=0.08, ge=0, le=1)
    ex_offender_penalty_floor: float = Field(default=0.6, ge=0, le=1)
    incarceration_decay_multiplier: float = Field(default=2.0, ge=1)
    civil_causes: tuple[str, ...] = (
        "contract_breach",
        "negligence",
        "fraud",
        "defamation",
        "wrongful_dismissal",
    )

    @model_validator(mode="after")
    def validate_crime_maps(self) -> LawSettings:
        expected = {
            "theft",
            "fraud",
            "insider_trading",
            "assault",
            "contract_breach",
            "embezzlement",
            "perjury",
        }
        if set(self.base_detect) != expected or set(self.victim_awareness) != expected:
            raise ValueError("law detection maps must define all seven crime types")
        if any(not 0 <= value <= 1 for value in self.base_detect.values()):
            raise ValueError("law.base_detect values must be probabilities")
        if any(not 0 <= value <= 1 for value in self.victim_awareness.values()):
            raise ValueError("law.victim_awareness values must be probabilities")
        return self


class AblationSettings(FrozenModel):
    reflex_only: bool = False
    obfuscate_domain: bool = False
    disclose_simulation: bool = False
    salience_policy_override: str | None = None
    feed_off: bool = False
    social_influence_off: bool = False
    backfire_off: bool = False
    no_record_penalty: bool = False


class StoreSettings(FrozenModel):
    dsn: str
    reader_dsn: str | None = None
    pool_min: int = 2
    pool_max: int = 16
    redis_url: str = "redis://127.0.0.1:6379/0"
    blob_url: str = "file://./.blobs"


class TelemetrySettings(FrozenModel):
    timing_sample_every: int = 25
    phase_budget_warn: bool = True
    redis_publish: bool = True


class ObservatoryLiveSettings(FrozenModel):
    ring_frames: int = 256
    rate_hz: int = 10
    max_channels: int = 16
    max_pins: int = 32
    max_frame_bytes: int = 262_144


class ObservatorySettings(FrozenModel):
    enabled: bool = True
    bind: str = "127.0.0.1:8080"
    statement_timeout_ms: int = 5000
    max_pool: int = 8
    static_dir: str = "web/dist"
    lag_banner_ticks: int = 5
    live: ObservatoryLiveSettings = ObservatoryLiveSettings()


class GatewayRegistrationSettings(FrozenModel):
    open_until_tick: int = Field(default=2_400, ge=-1)
    max_external_agents: int = Field(default=32, ge=1)
    registrations_per_operator: int = Field(default=8, ge=1)
    pending_ttl_ticks: int = Field(default=240, ge=1)
    require_conformance_token: bool = True
    embodiment: Literal["cohort_matched", "paired_control", "adopt_existing"] = "cohort_matched"


class GatewayDeadlineSettings(FrozenModel):
    decision_deadline_ms: int = Field(default=3_000, ge=1)
    seal_margin_ms: int = Field(default=50, ge=0)
    drain_timeout_ms: int = Field(default=100, ge=1)
    tick_lookahead: int = Field(default=0, ge=0)
    tick_skew_tolerance: int = Field(default=0, ge=0)
    pause_for_external: bool = False
    pause_max_ms: int = Field(default=600_000, ge=1)

    @model_validator(mode="after")
    def seal_precedes_deadline(self) -> GatewayDeadlineSettings:
        if self.seal_margin_ms >= self.decision_deadline_ms:
            raise ValueError("gateway.deadline.seal_margin_ms must be less than the deadline")
        return self


class GatewayLifecycleSettings(FrozenModel):
    naturalise_after_consecutive_misses: int = Field(default=240, ge=1)
    resume_grace_ticks: int = Field(default=720, ge=0)
    suspension_ticks: int = Field(default=240, ge=1)
    session_ttl_s: int = Field(default=3_600, ge=1)


class GatewayLimitSettings(FrozenModel):
    requests_per_tick: int = Field(default=40, ge=1)
    requests_per_second: int = Field(default=20, ge=1)
    recall_queries_per_tick: int = Field(default=6, ge=0)
    history_queries_per_tick: int = Field(default=3, ge=0)
    memory_writes_per_tick: int = Field(default=2, ge=0)
    ws_connections_per_agent: int = Field(default=2, ge=1)
    max_request_bytes: int = Field(default=65_536, ge=1)
    max_frame_bytes: int = Field(default=262_144, ge=1)
    long_poll_max_ms: int = Field(default=60_000, ge=1)
    market_depth_visible: int = Field(default=5, ge=1)


class GatewayToolSettings(FrozenModel):
    observe: bool = True
    act: bool = True
    recall: bool = True
    remember: bool = True
    who_am_i: bool = True
    market_quote: bool = True
    wait_for_tick: bool = True
    search_history: bool = False


class GatewaySecuritySettings(FrozenModel):
    injection_policy: Literal["flag", "redact", "block"] = "flag"
    external_speech_filter: Literal["flag", "redact", "block"] = "flag"
    error_codes_uniform: bool = True
    external_obs_sample_rate: float = Field(default=0.05, ge=0, le=1)


class GatewayArenaSettings(FrozenModel):
    min_driven_fraction: float = Field(default=0.90, ge=0, le=1)
    live_scorecard: bool = False
    scoring_interval_ticks: int = Field(default=8_640, ge=1)
    seeds_per_cell_min: int = Field(default=5, ge=1)


class GatewaySettings(FrozenModel):
    enabled: bool = False
    bind: str = "127.0.0.1:8081"
    protocol_version: Literal[1] = 1
    registration: GatewayRegistrationSettings = GatewayRegistrationSettings()
    deadline: GatewayDeadlineSettings = GatewayDeadlineSettings()
    lifecycle: GatewayLifecycleSettings = GatewayLifecycleSettings()
    limits: GatewayLimitSettings = GatewayLimitSettings()
    tools: GatewayToolSettings = GatewayToolSettings()
    security: GatewaySecuritySettings = GatewaySecuritySettings()
    arena: GatewayArenaSettings = GatewayArenaSettings()


class ResearchGateSettings(FrozenModel):
    external_miss_rate_max: float = Field(default=0.05, ge=0, le=1)


class ResearchSettings(FrozenModel):
    gates: ResearchGateSettings = ResearchGateSettings()


class FertilitySettings(FrozenModel):
    peak_age: int = Field(default=28, ge=0, le=120)
    band: tuple[int, int] = (16, 45)
    kappa_income: dict[str, float] = {"a": 0.6, "b": 0.8}
    kappa_parity: tuple[float, ...] = (1.0, 0.85, 0.6, 0.35, 0.15, 0.05)
    phi_single: float = Field(default=0.15, ge=0)
    iota_intent: float = Field(default=2.0, ge=0)
    intent_window_sim_days: int = Field(default=90, ge=1)
    psi_child_benefit: float = Field(default=0.4, ge=0)
    kappa_housing_penalty: float = Field(default=0.4, ge=0, le=1)
    gestation_sim_days: int = Field(default=270, ge=1)
    loss_base: float = Field(default=0.03, ge=0, le=1)

    @model_validator(mode="after")
    def validate_fertility(self) -> FertilitySettings:
        if self.band[0] >= self.band[1]:
            raise ValueError("demography.fertility.band must be increasing")
        if not self.band[0] <= self.peak_age <= self.band[1]:
            raise ValueError("demography.fertility.peak_age must fall inside the fertility band")
        if set(self.kappa_income) != {"a", "b"}:
            raise ValueError("demography.fertility.kappa_income must define a and b")
        if len(self.kappa_parity) != 6 or any(value < 0 for value in self.kappa_parity):
            raise ValueError(
                "demography.fertility.kappa_parity must contain six non-negative values"
            )
        return self


class ChildSettings(FrozenModel):
    base_cost_cents_per_sim_day: int = Field(default=3_500, ge=0)
    age_multiplier: dict[str, float] = {
        "infant": 1.0,
        "child": 1.2,
        "adolescent": 1.6,
    }
    arrears_tolerance_sim_days: int = Field(default=30, ge=0)
    welfare_threshold_health: float = Field(default=0.35, ge=0, le=1)

    @model_validator(mode="after")
    def validate_age_multiplier(self) -> ChildSettings:
        if set(self.age_multiplier) != {"infant", "child", "adolescent"}:
            raise ValueError(
                "demography.child.age_multiplier must define infant, child and adolescent"
            )
        if any(value < 0 for value in self.age_multiplier.values()):
            raise ValueError("demography.child.age_multiplier values must be non-negative")
        return self


class MigrationOriginProfileSettings(FrozenModel):
    skill_premium: float = 0.0
    wealth_offset_cents: int = 0
    belief_offsets: dict[str, float] = {}


class MigrationSettings(FrozenModel):
    cadence: Literal["monthly"] = "monthly"
    origin_profile: MigrationOriginProfileSettings = MigrationOriginProfileSettings()
    base_emig_per_sim_day: float = Field(default=0.00015, ge=0, le=1)


class EstateSettings(FrozenModel):
    liquidate_on_intestacy: bool = True
    creditor_priority: tuple[Literal["secured", "tax", "unsecured"], ...] = (
        "secured",
        "tax",
        "unsecured",
    )

    @model_validator(mode="after")
    def validate_priority(self) -> EstateSettings:
        if self.creditor_priority != ("secured", "tax", "unsecured"):
            raise ValueError("demography.estate.creditor_priority must be secured, tax, unsecured")
        return self


class BereavementSettings(FrozenModel):
    strong_tie_threshold: float = Field(default=0.45, ge=0, le=1)
    health_delta: float = Field(default=-0.04, le=0)
    social_need_delta: float = Field(default=-0.25, le=0)
    salience_boost_ticks: int = Field(default=72, ge=0)


class DemographySettings(FrozenModel):
    courtship_window_sim_days: int = Field(default=60, ge=1)
    courtship_salience_boost: float = Field(default=0.3, ge=0)
    leave_home_age: int = Field(default=18, ge=0)
    independence_threshold_cents: int = Field(default=180_000, ge=0)
    housing_burden: float = Field(default=0.35, ge=0, le=1)
    compatibility_weights: dict[str, float] = {
        "age": 0.20,
        "traits": 0.25,
        "beliefs": 0.20,
        "tie": 0.20,
        "econ": 0.15,
    }
    age_norm_years: int = Field(default=20, ge=1)
    fertility: FertilitySettings = FertilitySettings()
    child: ChildSettings = ChildSettings()
    migration: MigrationSettings = MigrationSettings()
    estate: EstateSettings = EstateSettings()
    bereavement: BereavementSettings = BereavementSettings()

    @model_validator(mode="after")
    def validate_compatibility_weights(self) -> DemographySettings:
        expected = {"age", "traits", "beliefs", "tie", "econ"}
        if set(self.compatibility_weights) != expected:
            raise ValueError(
                "demography.compatibility_weights must define age, traits, beliefs, tie and econ"
            )
        if any(value < 0 for value in self.compatibility_weights.values()):
            raise ValueError("demography.compatibility_weights values must be non-negative")
        if abs(sum(self.compatibility_weights.values()) - 1.0) > 1e-9:
            raise ValueError("demography.compatibility_weights must sum to 1")
        return self


class Settings(FrozenModel):
    run: RunSettings
    clock: ClockSettings
    population: PopulationSettings
    world: WorldSettings
    llm: LLMSettings
    actions: ActionSettings = ActionSettings()
    salience: SalienceSettings = SalienceSettings()
    memory: MemorySettings = MemorySettings()
    mechanisms: dict[str, str] = {}
    society: SocietySettings = SocietySettings()
    beliefs: BeliefSettings = BeliefSettings()
    economy: EconomySettings = EconomySettings()
    labour: LabourSettings = LabourSettings()
    firms: FirmSettings = FirmSettings()
    goods: GoodsSettings = GoodsSettings()
    banking: BankingSettings = BankingSettings()
    credit: CreditSettings = CreditSettings()
    treasury: TreasurySettings = TreasurySettings()
    exchange: ExchangeSettings = ExchangeSettings()
    ventures: VentureSettings = VentureSettings()
    bankruptcy: BankruptcySettings = BankruptcySettings()
    polity: PolitySettings = PolitySettings()
    law: LawSettings = LawSettings()
    demography: DemographySettings = DemographySettings()
    ablations: AblationSettings = AblationSettings()
    store: StoreSettings
    telemetry: TelemetrySettings = TelemetrySettings()
    observatory: ObservatorySettings = ObservatorySettings()
    gateway: GatewaySettings = GatewaySettings()
    research: ResearchSettings = ResearchSettings()

    @model_validator(mode="after")
    def default_scale(self) -> Settings:
        if self.run.scale is not None and self.run.scale != self.population.initial_agents:
            raise ValueError(
                "run.scale must equal population.initial_agents in the single-process v1"
            )
        object.__setattr__(
            self,
            "beliefs",
            self.beliefs.model_copy(
                update={
                    "social_influence_off": (
                        self.beliefs.social_influence_off or self.ablations.social_influence_off
                    ),
                    "backfire_off": self.beliefs.backfire_off or self.ablations.backfire_off,
                }
            ),
        )
        if self.gateway.enabled:
            if self.llm.routing.get("DELIBERATE") is None:
                raise ValueError("gateway-enabled runs require an LLM DELIBERATE route")
            if self.llm.request_timeout_ms != self.gateway.deadline.decision_deadline_ms:
                raise ValueError(
                    "gateway decision_deadline_ms must equal llm.request_timeout_ms "
                    f"({self.llm.request_timeout_ms} ms)"
                )
        if self.gateway.enabled and self.gateway.deadline.pause_for_external:
            tags = tuple(dict.fromkeys((*self.run.tags, "paused_for_external")))
            object.__setattr__(
                self,
                "run",
                self.run.model_copy(update={"tags": tags}),
            )
        return self


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), Mapping):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"configuration root must be a mapping: {path}")
    return value


def _set_dotted(root: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current = root
    for part in parts[:-1]:
        node = current.setdefault(part, {})
        if not isinstance(node, dict):
            raise ConfigError(f"cannot override non-mapping key {part!r}")
        current = node
    current[parts[-1]] = value


def _env_overrides() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in sorted(os.environ.items()):
        if not key.startswith("POLIS_"):
            continue
        dotted = key.removeprefix("POLIS_").lower().replace("__", ".")
        _set_dotted(result, dotted, yaml.safe_load(value))
    return result


def load_settings(
    path: Path,
    *,
    profiles: Sequence[str] = (),
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    merged: dict[str, Any] = {}
    for profile in profiles:
        profile_path = PROFILES_DIR / f"{profile}.yaml"
        if not profile_path.is_file():
            raise ProfileNotFound(f"profile not found: {profile}")
        _deep_merge(merged, _read_yaml(profile_path))
    _deep_merge(merged, _read_yaml(path))
    _deep_merge(merged, _env_overrides())
    if overrides:
        _deep_merge(merged, overrides)
    try:
        return Settings.model_validate(merged)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


_ECONOMY_CONFIG_FIELDS = frozenset(
    {
        "economy",
        "labour",
        "firms",
        "goods",
        "banking",
        "credit",
        "treasury",
        "exchange",
        "ventures",
        "bankruptcy",
    }
)


def _config_payload(settings: Settings, *, by_alias: bool = False) -> dict[str, Any]:
    payload = settings.model_dump(mode="json", by_alias=by_alias)
    if not settings.gateway.enabled:
        payload.pop("gateway")
        if settings.research == ResearchSettings():
            payload.pop("research")
    if settings.actions == ActionSettings():
        payload.pop("actions")
    society_defaults = SocietySettings().model_dump(mode="json", by_alias=by_alias)
    society_payload = payload["society"]
    for field, value in society_defaults.items():
        if field not in {"feed_algorithm", "outlets"} and society_payload.get(field) == value:
            society_payload.pop(field, None)
    if not settings.ablations.feed_off:
        payload["ablations"].pop("feed_off", None)
    budget = payload["llm"]["budget"]
    if budget["max_calls_per_run"] is None:
        budget.pop("max_calls_per_run")
    for provider in payload["llm"]["providers"].values():
        if provider["calls_per_window"] is None:
            provider.pop("calls_per_window")
            provider.pop("call_window_seconds")
            provider.pop("quota_scope")
            provider.pop("quota_path")
        if (
            provider["max_retries"] == 2
            and provider["retry_base_seconds"] == 0.25
            and provider["retry_max_seconds"] == 10.0
        ):
            provider.pop("max_retries")
            provider.pop("retry_base_seconds")
            provider.pop("retry_max_seconds")
    if not settings.economy.enabled:
        for field in _ECONOMY_CONFIG_FIELDS:
            payload.pop(field, None)
    else:
        if not settings.exchange.enabled:
            payload.pop("exchange", None)
        if not settings.ventures.enabled:
            payload.pop("ventures", None)
        if not settings.bankruptcy.enabled:
            payload.pop("bankruptcy", None)
    return payload


def config_yaml(settings: Settings) -> str:
    return cast(
        str,
        yaml.safe_dump(_config_payload(settings, by_alias=True), sort_keys=True),
    )


def config_hash(settings: Settings) -> str:
    return sha256_hex(canonical_bytes(_config_payload(settings)))


def reproducibility_tuple(
    settings: Settings,
    *,
    prompt_manifest: Mapping[str, str],
    model_manifest: Mapping[str, Any],
    completion_cache_manifest_hash: str,
    code_git_sha: str,
) -> dict[str, Any]:
    if not completion_cache_manifest_hash:
        raise ValueError(
            "completion_cache_manifest_hash is required for a reproducible run identity"
        )
    if not code_git_sha:
        raise ValueError("code_git_sha is required for a reproducible run identity")

    return {
        "config_hash": config_hash(settings),
        "prompt_manifest": dict(prompt_manifest),
        "model_manifest": dict(model_manifest),
        "code_git_sha": code_git_sha,
        "master_seed": settings.run.seed,
        "completion_cache_manifest_hash": completion_cache_manifest_hash,
    }
