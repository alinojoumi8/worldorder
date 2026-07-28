from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from polis.config.errors import ConfigError
from polis.config.settings import GatewayDeadlineSettings, GatewaySettings, load_settings


def test_baseline_declares_the_complete_gateway_configuration() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))

    assert settings.gateway.enabled is True
    assert settings.llm.request_timeout_ms == settings.gateway.deadline.decision_deadline_ms
    assert settings.llm.providers["reasoning"].timeout_s == 30.0
    assert GatewaySettings().enabled is False
    assert settings.gateway.tools.search_history is False
    assert settings.gateway.registration.require_conformance_token is True
    assert settings.research.gates.external_miss_rate_max == 0.05
    assert "external_miss_rate_max" not in settings.gateway.arena.model_dump()


def test_gateway_seal_must_precede_its_decision_deadline() -> None:
    with pytest.raises(ValidationError, match="seal_margin_ms"):
        GatewayDeadlineSettings(decision_deadline_ms=50, seal_margin_ms=50)


def test_gateway_deadline_must_match_native_request_timeout() -> None:
    with pytest.raises(ConfigError, match=r"llm\.request_timeout_ms"):
        load_settings(
            Path("configs/smoke.yaml"),
            overrides={
                "llm": {"request_timeout_ms": 4_000},
                "gateway": {"enabled": True},
            },
        )


def test_gateway_deadline_is_independent_of_provider_transport_timeout() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "llm": {"providers": {"stub": {"timeout_s": 30.0}}},
            "gateway": {"enabled": True},
        },
    )

    assert settings.llm.providers["stub"].timeout_s == 30.0
    assert settings.llm.request_timeout_ms == settings.gateway.deadline.decision_deadline_ms


def test_pause_mode_marks_the_run_as_not_comparable() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "llm": {"providers": {"stub": {"timeout_s": 3.0}}},
            "gateway": {
                "enabled": True,
                "deadline": {
                    "pause_for_external": True,
                    "pause_max_ms": 50,
                },
            },
        },
    )

    assert "paused_for_external" in settings.run.tags
