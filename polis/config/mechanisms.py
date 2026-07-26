from __future__ import annotations

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
    active_ids = set(settings.mechanisms.values())
    return {key: value for key, value in sorted(MECHANISM_REGISTRY.items()) if key in active_ids}


def mechanism_manifest(
    settings: Settings,
) -> dict[str, dict[str, str | None]]:
    return {key: asdict(value) for key, value in active_mechanisms(settings).items()}
