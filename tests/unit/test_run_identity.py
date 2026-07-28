from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from polis.config.settings import config_hash, load_settings, reproducibility_tuple
from polis.run_identity import (
    build_run_identity,
    model_manifest,
    prompt_manifest,
    run_identity_from_event_payload,
    validate_run_identity,
)


def test_run_identity_carries_the_complete_reproducibility_tuple() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    cache_hash = "b" * 64
    identity = build_run_identity(
        settings,
        completion_cache_manifest_hash=cache_hash,
        code_git_sha="a" * 40,
    )

    payload = identity.event_payload()
    assert payload["config_hash"] == config_hash(settings)
    assert payload["prompt_manifest"]
    assert payload["model_manifest"]
    assert payload["code_git_sha"] == "a" * 40
    assert payload["master_seed"] == settings.run.seed
    assert payload["completion_cache_manifest_hash"] == cache_hash
    assert payload["mechanism_manifest"]
    assert {
        "bench_rule",
        "crime_detection",
        "mortality_hazard",
        "party_platform_drift",
        "vote_model",
    } <= set(identity.mechanism_manifest)
    assert payload["metric_manifest"]
    assert len(str(payload["kind_registry_hash"])) == 64
    assert payload["clock_profile"] == settings.clock.profile
    assert payload["scale"] == settings.population.initial_agents
    validate_run_identity(settings, identity)
    assert run_identity_from_event_payload(payload) == identity


def test_run_identity_rejects_manifest_drift() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    identity = build_run_identity(settings, code_git_sha="a" * 40)

    with pytest.raises(ValueError, match="does not match"):
        validate_run_identity(settings, replace(identity, prompt_manifest={}))

    with pytest.raises(ValueError, match="does not match"):
        validate_run_identity(
            settings,
            identity,
            completion_cache_manifest_hash="d" * 64,
        )


def test_prompt_manifest_normalizes_checkout_line_endings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    deliberate = settings.llm.routing["DELIBERATE"].model_copy(update={"template": "sample.jinja"})
    llm = settings.llm.model_copy(update={"routing": {"DELIBERATE": deliberate}})
    settings = settings.model_copy(update={"llm": llm})
    prompt = tmp_path / "sample.jinja"
    monkeypatch.setattr("polis.run_identity.PROMPTS_DIR", tmp_path)

    prompt.write_bytes(b"first\r\nsecond\r\n")
    crlf_hash = prompt_manifest(settings)["DELIBERATE"]
    prompt.write_bytes(b"first\nsecond\n")

    assert prompt_manifest(settings)["DELIBERATE"] == crlf_hash


def test_reproducibility_tuple_rejects_an_empty_code_sha() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))

    with pytest.raises(ValueError, match="code_git_sha is required"):
        reproducibility_tuple(
            settings,
            prompt_manifest=prompt_manifest(settings),
            model_manifest=model_manifest(settings),
            completion_cache_manifest_hash="b" * 64,
            code_git_sha="",
        )


def test_reproducibility_tuple_rejects_an_empty_cache_manifest_hash() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))

    with pytest.raises(ValueError, match="completion_cache_manifest_hash is required"):
        reproducibility_tuple(
            settings,
            prompt_manifest=prompt_manifest(settings),
            model_manifest=model_manifest(settings),
            completion_cache_manifest_hash="",
            code_git_sha="a" * 40,
        )
