from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polis.config.canon import canonical_bytes, sha256_hex
from polis.config.mechanisms import mechanism_manifest
from polis.config.paths import PROMPTS_DIR, repo_git_sha
from polis.config.settings import Settings, reproducibility_tuple
from polis.events.kinds import registry_manifest
from polis.llm.cache import EMPTY_COMPLETION_CACHE_MANIFEST_HASH
from polis.research.metrics import catalogue_manifest


@dataclass(frozen=True, slots=True)
class RunIdentity:
    config_hash: str
    prompt_manifest: Mapping[str, str]
    model_manifest: Mapping[str, Any]
    code_git_sha: str
    master_seed: int
    completion_cache_manifest_hash: str
    mechanism_manifest: Mapping[str, Any]
    metric_manifest: Mapping[str, Any]
    kind_registry_hash: str
    clock_profile: str
    scale: int

    def event_payload(self) -> dict[str, Any]:
        return {
            "config_hash": self.config_hash,
            "prompt_manifest": dict(self.prompt_manifest),
            "model_manifest": dict(self.model_manifest),
            "code_git_sha": self.code_git_sha,
            "master_seed": self.master_seed,
            "completion_cache_manifest_hash": self.completion_cache_manifest_hash,
            "mechanism_manifest": dict(self.mechanism_manifest),
            "metric_manifest": dict(self.metric_manifest),
            "kind_registry_hash": self.kind_registry_hash,
            "clock_profile": self.clock_profile,
            "scale": self.scale,
        }


def run_identity_from_event_payload(payload: Mapping[str, Any]) -> RunIdentity:
    def required_str(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str):
            raise ValueError(f"RUN_STARTED {name} must be a string")
        return value

    def required_int(name: str) -> int:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"RUN_STARTED {name} must be an integer")
        return value

    def required_mapping(name: str) -> dict[str, Any]:
        value = payload.get(name)
        if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
            raise ValueError(f"RUN_STARTED {name} must be a string-keyed object")
        return dict(value)

    prompt_values = required_mapping("prompt_manifest")
    if any(not isinstance(value, str) for value in prompt_values.values()):
        raise ValueError("RUN_STARTED prompt_manifest values must be strings")
    return RunIdentity(
        config_hash=required_str("config_hash"),
        prompt_manifest=prompt_values,
        model_manifest=required_mapping("model_manifest"),
        code_git_sha=required_str("code_git_sha"),
        master_seed=required_int("master_seed"),
        completion_cache_manifest_hash=required_str("completion_cache_manifest_hash"),
        mechanism_manifest=required_mapping("mechanism_manifest"),
        metric_manifest=required_mapping("metric_manifest"),
        kind_registry_hash=required_str("kind_registry_hash"),
        clock_profile=required_str("clock_profile"),
        scale=required_int("scale"),
    )


def _canonical_text_bytes(path: Path) -> bytes:
    """Return text bytes with platform-independent line endings."""
    return path.read_text(encoding="utf-8").encode("utf-8")


def prompt_manifest(settings: Settings) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for purpose, route in sorted(settings.llm.routing.items()):
        path = PROMPTS_DIR / route.template
        material = (
            _canonical_text_bytes(path)
            if path.is_file()
            else route.template.encode(encoding="utf-8")
        )
        manifest[purpose] = sha256_hex(material)
    for path in sorted((PROMPTS_DIR / "news_write").glob("*.jinja")):
        manifest[f"NEWS_WRITE:{path.name}"] = sha256_hex(_canonical_text_bytes(path))
    news_route = settings.llm.routing.get("NEWS_WRITE")
    if news_route is not None and news_route.schema_ is not None:
        schema = PROMPTS_DIR.parent / news_route.schema_
        if schema.is_file():
            manifest["NEWS_WRITE:schema"] = sha256_hex(_canonical_text_bytes(schema))
    return manifest


def model_manifest(settings: Settings) -> dict[str, dict[str, str | None]]:
    return {
        purpose: {
            "lane": route.lane,
            "model": route.model,
            "provider_kind": settings.llm.providers[route.lane].kind,
            "model_version_pin": settings.llm.providers[route.lane].model_version_pin,
        }
        for purpose, route in sorted(settings.llm.routing.items())
    }


def build_run_identity(
    settings: Settings,
    *,
    completion_cache_manifest_hash: str = EMPTY_COMPLETION_CACHE_MANIFEST_HASH,
    code_git_sha: str | None = None,
) -> RunIdentity:
    prompts = prompt_manifest(settings)
    models = model_manifest(settings)
    resolved_code_git_sha = repo_git_sha() if code_git_sha is None else code_git_sha
    reproducibility = reproducibility_tuple(
        settings,
        prompt_manifest=prompts,
        model_manifest=models,
        completion_cache_manifest_hash=completion_cache_manifest_hash,
        code_git_sha=resolved_code_git_sha,
    )
    return RunIdentity(
        config_hash=str(reproducibility["config_hash"]),
        prompt_manifest=prompts,
        model_manifest=models,
        code_git_sha=str(reproducibility["code_git_sha"]),
        master_seed=int(reproducibility["master_seed"]),
        completion_cache_manifest_hash=str(reproducibility["completion_cache_manifest_hash"]),
        mechanism_manifest=mechanism_manifest(settings),
        metric_manifest=catalogue_manifest(),
        kind_registry_hash=sha256_hex(canonical_bytes(registry_manifest())),
        clock_profile=settings.clock.profile,
        scale=settings.population.initial_agents,
    )


def validate_run_identity(
    settings: Settings,
    identity: RunIdentity,
    *,
    completion_cache_manifest_hash: str | None = None,
) -> None:
    expected = build_run_identity(
        settings,
        completion_cache_manifest_hash=(
            identity.completion_cache_manifest_hash
            if completion_cache_manifest_hash is None
            else completion_cache_manifest_hash
        ),
        code_git_sha=identity.code_git_sha,
    )
    if identity != expected:
        raise ValueError("run identity does not match the active settings and manifests")
