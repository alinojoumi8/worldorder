from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import Any, Final, Protocol

from polis.agents.actions.params.base import ActionParams
from polis.agents.actions.types import (
    Action,
    ActionType,
    GateResult,
    LegalityVerdict,
    ValidatedAction,
)
from polis.events.types import Event, NewEvent

Emit = Callable[[NewEvent], Event]


@dataclass(frozen=True, slots=True)
class ValidationContext:
    observation: object
    state: object
    tick: int
    runtime: object | None = None
    repositories: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    emit: Emit
    runtime: object | None = None
    repositories: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


class InstitutionSlot(IntEnum):
    MOVEMENT = 1
    COMMUNICATION = 2
    LABOUR = 3
    GOODS = 4
    EXCHANGE = 5
    BANKING = 6
    VENTURES = 7
    POLITY = 8
    LAW = 9
    MISC = 10


SLOT_ORDER: Final[tuple[InstitutionSlot, ...]] = (
    InstitutionSlot.MOVEMENT,
    InstitutionSlot.COMMUNICATION,
    InstitutionSlot.LABOUR,
    InstitutionSlot.GOODS,
    InstitutionSlot.EXCHANGE,
    InstitutionSlot.BANKING,
    InstitutionSlot.VENTURES,
    InstitutionSlot.POLITY,
    InstitutionSlot.LAW,
    InstitutionSlot.MISC,
)


class InstitutionResolver(Protocol):
    """Frozen PHASE 5 contract implemented once per institutional slot."""

    slot: InstitutionSlot
    handles: frozenset[ActionType]

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult: ...

    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult: ...

    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult: ...

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]: ...

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]: ...


class LegalityOracle(Protocol):
    def assess(
        self,
        action: Action,
        params: ActionParams,
        ctx: ValidationContext,
    ) -> LegalityVerdict: ...


class PermissiveLegalityOracle:
    """Default until the C19 law oracle replaces it at the M4 law integration tick."""

    def assess(
        self,
        action: Action,
        params: ActionParams,
        ctx: ValidationContext,
    ) -> LegalityVerdict:
        del action, params, ctx
        return LegalityVerdict(is_crime=False)
