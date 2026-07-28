from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from polis.agents.actions.budget import SlotLedger
from polis.agents.actions.compat import LEGACY_PARAM_MODELS
from polis.agents.actions.legal import CHILD_ALLOWED, REFLEX_ALLOWED
from polis.agents.actions.params import PARAMS_MODELS
from polis.agents.actions.params.base import ActionParams
from polis.agents.actions.protocol import (
    InstitutionResolver,
    LegalityOracle,
    PermissiveLegalityOracle,
    ValidationContext,
)
from polis.agents.actions.registry import ResolverRegistry
from polis.agents.actions.types import (
    Action,
    ActionType,
    Gate,
    GateFailure,
    Rejection,
    RejectReason,
    ValidatedAction,
    null_action,
)
from polis.agents.types import AgentState
from polis.config.canon import canonical_bytes
from polis.config.errors import ConfigError, PolisError
from polis.config.settings import ActionSettings
from polis.events.kinds import (
    ACTION_FLAGGED_ILLEGAL,
    ACTION_REJECTED,
    ACTION_SUBMITTED,
)
from polis.events.types import Event, NewEvent
from polis.kernel.clock import ClockProfile
from polis.world.api import World

LegacyGateResult = Literal["pass", "fail", "clean", "flagged", "not_checked"]
Emit = Callable[[NewEvent], Event]


class ReflexActionViolation(PolisError):
    """A reflex policy attempted an action reserved for deliberate cognition."""


class UnregisteredActionType(PolisError):
    """A run configured fail-fast encountered an action without a resolver."""


def action_response_schema(legal_actions: Sequence[str]) -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    seen: set[ActionType] = set()
    for value in legal_actions:
        try:
            action_type = ActionType(value)
        except ValueError:
            continue
        if action_type in seen:
            continue
        seen.add(action_type)
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"const": action_type.value},
                    "params": (
                        LEGACY_PARAM_MODELS.get(
                            action_type, PARAMS_MODELS[action_type]
                        ).model_json_schema()
                    ),
                },
                "required": ["type", "params"],
            }
        )
    if not branches:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"const": ActionType.NULL_ACTION.value},
                    "params": LEGACY_PARAM_MODELS[ActionType.NULL_ACTION].model_json_schema(),
                },
                "required": ["type", "params"],
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reasoning": {"type": "string", "maxLength": 300},
            "action": {"oneOf": branches},
        },
        "required": ["reasoning", "action"],
    }


@dataclass(slots=True)
class ActionBudget:
    """Compatibility adapter for the M1-M3 living-city validator."""

    slots_per_agent: int
    used: dict[str, int] = field(default_factory=dict)

    @classmethod
    def for_profile(
        cls,
        profile: ClockProfile,
        settings: ActionSettings | None = None,
    ) -> ActionBudget:
        action_settings = settings or ActionSettings()
        return cls(action_settings.slots_per_tick.for_profile(profile.name))

    def available(self, agent_id: str) -> bool:
        return self.used.get(agent_id, 0) < self.slots_per_agent

    def consume(self, agent_id: str) -> bool:
        if not self.available(agent_id):
            return False
        self.used[agent_id] = self.used.get(agent_id, 0) + 1
        return True


@dataclass(frozen=True, slots=True)
class Validation:
    """Compatibility result used by the existing M1-M3 phase wiring."""

    accepted: bool
    action: Action
    reason: str | None
    gates: dict[str, LegacyGateResult]
    detail: dict[str, Any]


def _reject_legacy(
    action: Action,
    reason: str,
    gates: dict[str, LegacyGateResult],
    detail: dict[str, Any] | None = None,
) -> Validation:
    return Validation(
        False,
        null_action(action, reasoning=f"rejected: {reason}"),
        reason,
        gates,
        detail or {},
    )


_LEGACY_DOMAIN_ACTIONS = frozenset(ActionType).difference(
    {
        ActionType.MOVE_TO,
        ActionType.IDLE,
        ActionType.SLEEP,
        ActionType.EAT,
        ActionType.STUDY,
        ActionType.NULL_ACTION,
    }
)


def validate_action(
    action: Action,
    *,
    agent: AgentState,
    world: World,
    profile: ClockProfile,
    budget: ActionBudget,
) -> Validation:
    """Preserve the established M1-M3 validator until resolvers migrate slot by slot."""

    gates: dict[str, LegacyGateResult] = {
        "schema": "not_checked",
        "capability": "not_checked",
        "locality": "not_checked",
        "resources": "not_checked",
        "legality": "not_checked",
    }
    try:
        params = LEGACY_PARAM_MODELS[action.type].model_validate_json(
            canonical_bytes(action.params)
        )
    except (KeyError, ValidationError) as exc:
        gates["schema"] = "fail"
        return _reject_legacy(action, "schema", gates, {"error": str(exc)})
    gates["schema"] = "pass"

    if agent.employment_status == "child" and action.type not in CHILD_ALLOWED:
        gates["capability"] = "fail"
        return _reject_legacy(action, "capability", gates)
    gates["capability"] = "pass"

    location = world.locations[action.actor_id]
    if action.type == ActionType.MOVE_TO:
        target_id = str(params.model_dump()["place_id"])
        if not world.has_place(target_id):
            gates["locality"] = "fail"
            return _reject_legacy(
                action,
                "locality",
                gates,
                {"reason": "unknown_place"},
            )
        if target_id != agent.home_place_id and not world.is_open(
            target_id,
            action.tick,
            profile,
        ):
            gates["locality"] = "fail"
            return _reject_legacy(action, "locality", gates, {"reason": "closed"})
    elif action.type not in _LEGACY_DOMAIN_ACTIONS and not world.affords(
        location.place_id,
        action.type.value,
    ):
        gates["locality"] = "fail"
        return _reject_legacy(action, "locality", gates)
    gates["locality"] = "pass"

    if not budget.consume(action.actor_id):
        gates["resources"] = "fail"
        return _reject_legacy(
            action,
            "resources",
            gates,
            {"reason": "action_slots"},
        )
    gates["resources"] = "pass"
    gates["legality"] = "clean"
    return Validation(True, action, None, gates, {})


class ActionValidator:
    """C10 PHASE 4 validator shared by native and external action origins."""

    def __init__(
        self,
        registry: ResolverRegistry,
        slots: SlotLedger,
        *,
        oracle: LegalityOracle | None = None,
        emit: Emit | None = None,
        max_params_bytes: int = 4_096,
        reject_on_unregistered: bool = True,
    ) -> None:
        self.registry = registry
        self.slots = slots
        self.oracle = oracle or PermissiveLegalityOracle()
        self.emit = emit
        self.max_params_bytes = max_params_bytes
        self.reject_on_unregistered = reject_on_unregistered

    @classmethod
    def from_settings(
        cls,
        registry: ResolverRegistry,
        settings: ActionSettings,
        profile: Literal["microscope", "chronicle"],
        *,
        oracle: LegalityOracle | None = None,
        emit: Emit | None = None,
    ) -> ActionValidator:
        if settings.legality.oracle == "law" and oracle is None:
            raise ValueError("actions.legality.oracle=law requires the C19 legality oracle")
        return cls(
            registry,
            SlotLedger.from_settings(settings, profile),
            oracle=oracle,
            emit=emit,
            max_params_bytes=settings.max_params_bytes,
            reject_on_unregistered=settings.reject_on_unregistered,
        )

    def _emit(self, draft: NewEvent) -> Event | None:
        return self.emit(draft) if self.emit is not None else None

    def _reject(
        self,
        action: Action,
        *,
        gate: Gate | None,
        reason: RejectReason,
        detail: str,
        cause_seq: int | None = None,
        slot_consumed: bool = True,
    ) -> Rejection:
        substitute = null_action(action, reasoning=reason)
        rejection = Rejection(
            action_id=action.action_id,
            actor_id=action.actor_id,
            type=action.type,
            gate=gate,
            reason=reason,
            detail=detail,
            substitute=substitute,
        )
        self._emit(
            NewEvent(
                ACTION_REJECTED,
                {
                    "action_id": str(action.action_id),
                    "actor_id": action.actor_id,
                    "type": action.type.value,
                    "gate": gate,
                    "reason": reason,
                    "detail": detail,
                    "origin": action.origin,
                    "slot_consumed": slot_consumed,
                    "substituted_with": ActionType.NULL_ACTION.value,
                },
                actor_id=action.actor_id,
                cause_seq=cause_seq,
            )
        )
        return rejection

    def _run_gate(
        self,
        action: Action,
        resolver: InstitutionResolver,
        ctx: ValidationContext,
        gate: Gate,
    ) -> GateFailure | None:
        if gate == "capability":
            return resolver.check_capability(action, ctx)
        if gate == "locality":
            return resolver.check_locality(action, ctx)
        if gate == "resources":
            return resolver.check_resources(action, ctx)
        raise AssertionError(f"unsupported resolver gate: {gate}")

    def validate(
        self,
        action: Action,
        ctx: ValidationContext,
    ) -> ValidatedAction | Rejection:
        if action.origin == "reflex" and action.type not in REFLEX_ALLOWED:
            raise ReflexActionViolation(
                f"reflex action {action.type.value} is outside REFLEX_ALLOWED"
            )

        self.slots.reset(ctx.tick)
        slot_index = self.slots.consume(action.actor_id, ctx.tick)
        if slot_index is None:
            return self._reject(
                action,
                gate=None,
                reason="no_slots",
                detail="the actor has exhausted this tick's action slots",
                slot_consumed=False,
            )

        submitted = self._emit(
            NewEvent(
                ACTION_SUBMITTED,
                {
                    "action_id": str(action.action_id),
                    "actor_id": action.actor_id,
                    "type": action.type.value,
                    "params": dict(action.params),
                    "origin": action.origin,
                    "salience": action.salience,
                    "reasoning": action.reasoning,
                    "llm_call_id": None,
                    "slot_index": slot_index,
                },
                actor_id=action.actor_id,
            )
        )
        cause_seq = submitted.seq if submitted is not None else None

        resolver = self.registry.for_type(action.type)
        if resolver is None:
            if not self.reject_on_unregistered:
                raise UnregisteredActionType(f"no resolver is registered for {action.type.value}")
            return self._reject(
                action,
                gate=None,
                reason="unavailable",
                detail=f"no resolver is registered for {action.type.value}",
                cause_seq=cause_seq,
            )

        try:
            if action.tick != ctx.tick:
                raise ValueError(
                    f"action tick {action.tick} does not match validation tick {ctx.tick}"
                )
            params_payload = canonical_bytes(action.params)
            if len(params_payload) > self.max_params_bytes:
                raise ValueError(f"params exceed max_params_bytes={self.max_params_bytes}")
            if action.origin == "external" and action.sig is None:
                raise ValueError("external actions require sig")
            params: ActionParams = PARAMS_MODELS[action.type].model_validate_json(params_payload)
        except (ConfigError, KeyError, ValidationError, ValueError) as exc:
            return self._reject(
                action,
                gate="schema",
                reason="schema",
                detail=str(exc),
                cause_seq=cause_seq,
            )

        for gate in ("capability", "locality", "resources"):
            failure = self._run_gate(action, resolver, ctx, gate)
            if failure is not None:
                return self._reject(
                    action,
                    gate=gate,
                    reason=failure.reason,
                    detail=failure.detail,
                    cause_seq=cause_seq,
                )

        verdict = self.oracle.assess(action, params, ctx)
        if verdict.is_crime:
            self._emit(
                NewEvent(
                    ACTION_FLAGGED_ILLEGAL,
                    {
                        "action_id": str(action.action_id),
                        "actor_id": action.actor_id,
                        "type": action.type.value,
                        "crime_type": verdict.crime_type,
                        "victim_id": verdict.victim_id,
                        "amount_cents": verdict.amount_cents,
                        "crime_id": verdict.crime_id,
                        "proceeded": True,
                    },
                    actor_id=action.actor_id,
                    subject_ids=(verdict.victim_id,) if verdict.victim_id is not None else (),
                    cause_seq=cause_seq,
                )
            )
        return ValidatedAction(action, params, verdict, slot_index)

    def validate_batch(
        self,
        actions: Sequence[Action],
        tick: int,
        ctxs: Mapping[str, ValidationContext],
    ) -> tuple[tuple[ValidatedAction, ...], tuple[Rejection, ...]]:
        validated: list[ValidatedAction] = []
        rejected: list[Rejection] = []
        for action in sorted(
            actions,
            key=lambda item: (item.actor_id, str(item.action_id)),
        ):
            ctx = ctxs[action.actor_id]
            if ctx.tick != tick:
                raise ValueError(f"context tick {ctx.tick} does not match batch tick {tick}")
            result = self.validate(action, ctx)
            if isinstance(result, Rejection):
                rejected.append(result)
            else:
                validated.append(result)
        return tuple(validated), tuple(rejected)
