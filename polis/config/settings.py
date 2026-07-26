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


def config_yaml(settings: Settings) -> str:
    return cast(str, yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=True))


def config_hash(settings: Settings) -> str:
    return sha256_hex(canonical_bytes(settings.model_dump(mode="json")))


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
