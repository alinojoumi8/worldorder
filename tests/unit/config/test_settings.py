from pathlib import Path

import pytest
import yaml

from polis.config.errors import ConfigError
from polis.config.settings import (
    FeedEngagementSettings,
    Settings,
    SocietySettings,
    config_hash,
    config_yaml,
    load_settings,
)


def test_smoke_and_baseline_load() -> None:
    baseline = load_settings(Path("configs/baseline.yaml"))
    smoke = load_settings(Path("configs/smoke.yaml"))
    assert baseline.population.initial_agents == 1000
    assert smoke.population.initial_agents == 50
    assert config_hash(baseline) == config_hash(load_settings(Path("configs/baseline.yaml")))


@pytest.mark.parametrize(
    "path,kind,max_calls",
    [
        ("configs/live-minimax-m3-smoke.yaml", "minimax", 1),
        ("configs/live-minimax-m3-pilot.yaml", "minimax", 8_000),
        ("configs/live-codex-cli-smoke.yaml", "codex_cli", 1),
        ("configs/live-grok-cli-smoke.yaml", "grok_cli", 1),
    ],
)
def test_bounded_live_provider_configs_load(
    path: str,
    kind: str,
    max_calls: int,
) -> None:
    settings = load_settings(Path(path))
    route = settings.llm.routing["DELIBERATE"]
    assert settings.llm.providers[route.lane].kind == kind
    assert settings.llm.budget.max_calls_per_run == max_calls


def test_semantic_override_changes_hash() -> None:
    base = load_settings(Path("configs/smoke.yaml"))
    changed = load_settings(Path("configs/smoke.yaml"), overrides={"run": {"seed": 99}})
    assert config_hash(base) != config_hash(changed)


def test_config_yaml_round_trips_aliased_fields() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    serialized = config_yaml(settings)
    assert "max_calls_per_run" not in serialized
    assert "calls_per_window" not in serialized
    restored = Settings.model_validate(yaml.safe_load(serialized))
    assert restored == settings


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown"):
        load_settings(Path("configs/smoke.yaml"), overrides={"run": {"unknown": True}})


def test_society_tie_halflives_reject_partial_and_unknown_maps() -> None:
    with pytest.raises(ValueError, match="missing"):
        SocietySettings(tie_halflife_sim_days={"friend": 60.0})

    values = dict(SocietySettings().tie_halflife_sim_days)
    values["stranger"] = 10.0
    with pytest.raises(ValueError, match="unknown"):
        SocietySettings(tie_halflife_sim_days=values)


def test_society_weight_maps_require_exact_normalized_keys() -> None:
    with pytest.raises(ValueError, match="define exactly"):
        SocietySettings(newsworthiness_weights={"mag": 1.0})

    weights = dict(SocietySettings().distribution_weights)
    weights["trust"] = 0.5
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        SocietySettings(distribution_weights=weights)


@pytest.mark.parametrize("length", [10, 12])
def test_feed_beta_prior_has_exact_feature_arity(length: int) -> None:
    with pytest.raises(ValueError):
        FeedEngagementSettings(beta_prior=(0.0,) * length)
