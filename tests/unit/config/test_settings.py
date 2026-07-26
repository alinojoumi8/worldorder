from pathlib import Path

import pytest

from polis.config.errors import ConfigError
from polis.config.settings import config_hash, load_settings


def test_smoke_and_baseline_load() -> None:
    baseline = load_settings(Path("configs/baseline.yaml"))
    smoke = load_settings(Path("configs/smoke.yaml"))
    assert baseline.population.initial_agents == 1000
    assert smoke.population.initial_agents == 50
    assert config_hash(baseline) == config_hash(load_settings(Path("configs/baseline.yaml")))


def test_semantic_override_changes_hash() -> None:
    base = load_settings(Path("configs/smoke.yaml"))
    changed = load_settings(Path("configs/smoke.yaml"), overrides={"run": {"seed": 99}})
    assert config_hash(base) != config_hash(changed)


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown"):
        load_settings(Path("configs/smoke.yaml"), overrides={"run": {"unknown": True}})
