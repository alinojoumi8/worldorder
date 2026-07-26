from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Final, TypeVar

from polis.config.errors import MechanismError
from polis.config.settings import Settings


@dataclass(frozen=True, slots=True)
class MechanismSpec:
    id: str
    entails: str
    config_key: str | None
    module: str
    qualname: str


MECHANISM_REGISTRY: Final[dict[str, MechanismSpec]] = {}
F = TypeVar("F", bound=Callable[..., Any])


def mechanism(id: str, *, entails: str, config_key: str | None = None) -> Callable[[F], F]:
    def register(function: F) -> F:
        if id in MECHANISM_REGISTRY:
            raise MechanismError(f"duplicate mechanism id: {id}")
        MECHANISM_REGISTRY[id] = MechanismSpec(
            id=id,
            entails=entails,
            config_key=config_key,
            module=function.__module__,
            qualname=function.__qualname__,
        )
        return function

    return register


def active_mechanisms(settings: Settings) -> dict[str, MechanismSpec]:
    if settings.economy.enabled:
        for module in (
            "polis.economy.firms",
            "polis.economy.labour",
            "polis.economy.policy",
            "polis.economy.ventures",
        ):
            importlib.import_module(module)
    disabled = {"off", "false", "disabled", "none"}
    result: dict[str, MechanismSpec] = {}
    for mechanism_id, spec in sorted(MECHANISM_REGISTRY.items()):
        if spec.module.startswith("polis.economy") and not settings.economy.enabled:
            continue
        if spec.module == "polis.economy.ventures" and not settings.ventures.enabled:
            continue
        candidates: tuple[str, ...] = (mechanism_id, mechanism_id.replace(".", "_"))
        configured: str | None = None
        if spec.config_key is not None and spec.config_key.startswith("mechanisms."):
            candidates = (spec.config_key.removeprefix("mechanisms."), *candidates)
        for key in candidates:
            if key in settings.mechanisms:
                configured = settings.mechanisms[key]
                break
        if configured is None or configured.lower() not in disabled:
            result[mechanism_id] = spec
    return result


def mechanism_manifest(
    settings: Settings,
) -> dict[str, dict[str, str | None]]:
    return {key: asdict(value) for key, value in active_mechanisms(settings).items()}
