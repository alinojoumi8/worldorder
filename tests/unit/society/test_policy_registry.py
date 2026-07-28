from pathlib import Path

import pytest

from polis.config.runtime import RuntimeConfig
from polis.config.settings import load_settings
from polis.society.policy import POLICY_REGISTRY, registry_for


def test_policy_registry_is_closed_complete_and_runtime_backed() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    runtime = RuntimeConfig(settings)

    assert len(POLICY_REGISTRY) == 31
    assert set(POLICY_REGISTRY) == set(settings.polity.policy.flat().keys())
    assert "society.feed_algorithm" not in registry_for(settings)
    assert len(registry_for(settings)) == 30
    assert {key: runtime.get(key, 0) for key in registry_for(settings)} == {
        key: value
        for key, value in settings.polity.policy.flat().items()
        if key != "society.feed_algorithm"
    }
    with pytest.raises(TypeError):
        POLICY_REGISTRY["outside.scope"] = POLICY_REGISTRY["tax.vat_bp"]  # type: ignore[index]


def test_feed_policy_row_is_enabled_only_by_explicit_capability() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"polity": {"can_regulate_feed": True}},
    )

    assert "society.feed_algorithm" in registry_for(settings)
    assert len(registry_for(settings)) == 31
