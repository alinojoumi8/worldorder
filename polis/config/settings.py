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
    kind: Literal["minimax", "ollama", "openai_compat", "stub"]
    base_url: str | None = None
    api_key_env: str | None = None
    max_concurrency: int = 8
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    timeout_s: float = 45.0
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


class SocietySettings(FrozenModel):
    feed_algorithm: str = "engagement"
    outlets: int = 4


class EconomySettings(FrozenModel):
    enabled: bool = False
    currency: Literal["POL"] = "POL"
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
    search_window_days: int = 28
    retirement_age: int = 65
    minimum_wage_cents: int = 0
    payroll: LabourPayrollSettings = LabourPayrollSettings()


class FirmMarkupSettings(FrozenModel):
    initial_bp: int = 2_500
    step_bp: int = 200
    max_bp: int = 8_000
    target_low_bp: int = 70_000
    target_high_bp: int = 300_000


class FirmSettings(FrozenModel):
    beta_capital_bp: int = 3_000
    depreciation_bp_per_year: int = 1_000
    learning_bp_per_day: int = 3
    productivity_sigma_bp: int = 40
    productivity_bounds_bp: tuple[int, int] = (2_000, 40_000)
    spoilage_bp_per_day: int = 2_000
    payout_ratio_bp: int = 3_000
    working_capital_months: int = 3
    markup: FirmMarkupSettings = FirmMarkupSettings()


class ConsumptionSettings(FrozenModel):
    subsistence_gamma_bp: int = 4_000
    max_sellers_considered: int = 6
    sales_tax_bp: int = 800


class GoodsSettings(FrozenModel):
    cpi_base_bp: int = 10_000
    initial_inventory_days: int = 30
    consumption: ConsumptionSettings = ConsumptionSettings()


class BankingSettings(FrozenModel):
    reserve_ratio_bp: int = 1_000
    capital_ratio_min_bp: int = 800
    capital_buffer_bp: int = 1_050
    deposit_rate_bp: int = 50
    policy_rate_bp: int = 400
    discount_penalty_bp: int = 200
    insurance_cap_months: int = 6
    fire_sale_bp: int = 7_000
    cb_backstop: bool = False
    resolution: Literal["assume", "liquidate"] = "assume"
    underwriting: Literal["scorecard", "llm"] = "scorecard"
    policy_rate_rule: Literal["taylor", "fixed", "political"] = "fixed"


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
    arrears_penalty_bp: int = 800


class SpendSettings(FrozenModel):
    benefit_replacement_bp: int = 4_000
    benefit_max_days: int = 182


class TreasurySettings(FrozenModel):
    floor_cents: int = 0
    initial_spending_quarters: int = 1
    tax: TaxSettings = TaxSettings()
    spend: SpendSettings = SpendSettings()


class AblationSettings(FrozenModel):
    reflex_only: bool = False
    obfuscate_domain: bool = False
    disclose_simulation: bool = False
    salience_policy_override: str | None = None


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


class Settings(FrozenModel):
    run: RunSettings
    clock: ClockSettings
    population: PopulationSettings
    world: WorldSettings
    llm: LLMSettings
    salience: SalienceSettings = SalienceSettings()
    memory: MemorySettings = MemorySettings()
    mechanisms: dict[str, str] = {}
    society: SocietySettings = SocietySettings()
    economy: EconomySettings = EconomySettings()
    labour: LabourSettings = LabourSettings()
    firms: FirmSettings = FirmSettings()
    goods: GoodsSettings = GoodsSettings()
    banking: BankingSettings = BankingSettings()
    credit: CreditSettings = CreditSettings()
    treasury: TreasurySettings = TreasurySettings()
    ablations: AblationSettings = AblationSettings()
    store: StoreSettings
    telemetry: TelemetrySettings = TelemetrySettings()
    observatory: ObservatorySettings = ObservatorySettings()

    @model_validator(mode="after")
    def default_scale(self) -> Settings:
        if self.run.scale is not None and self.run.scale != self.population.initial_agents:
            raise ValueError(
                "run.scale must equal population.initial_agents in the single-process v1"
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


_M2_CONFIG_FIELDS = frozenset(
    {"economy", "labour", "firms", "goods", "banking", "credit", "treasury"}
)


def _config_payload(settings: Settings, *, by_alias: bool = False) -> dict[str, Any]:
    payload = settings.model_dump(mode="json", by_alias=by_alias)
    if not settings.economy.enabled:
        for field in _M2_CONFIG_FIELDS:
            payload.pop(field, None)
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
) -> dict[str, str]:
    from polis.config.paths import repo_git_sha

    return {
        "config_hash": config_hash(settings),
        "code_git_sha": repo_git_sha(),
        "prompt_manifest_hash": sha256_hex(canonical_bytes(prompt_manifest)),
        "model_manifest_hash": sha256_hex(canonical_bytes(model_manifest)),
    }
