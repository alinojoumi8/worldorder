from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from polis.agents.actions import (
    PARAMS_MODELS,
    REFLEX_ALLOWED,
    Action,
    ActionDispatcher,
    ActionOutcome,
    ActionType,
    ActionValidator,
    DuplicateHandler,
    GateFailure,
    InstitutionSlot,
    LegalAction,
    LegalityVerdict,
    ReflexActionViolation,
    Rejection,
    RejectionLedger,
    ResolutionContext,
    ResolverRegistry,
    SlotLedger,
    ValidatedAction,
    ValidationContext,
    legal_actions,
    make_action,
)
from polis.agents.actions.params.meta import NullActionParams
from polis.agents.actions.params.world import IdleParams, MoveToParams
from polis.config.settings import ActionSettings
from polis.events.kinds import ACTION_REJECTED, ACTION_SUBMITTED
from polis.events.types import Event, NewEvent

RUN_ID = UUID("10000000-0000-0000-0000-000000000001")


def _event(kind: int, seq: int = 1) -> Event:
    return Event(
        seq=seq,
        run_id=RUN_ID,
        tick=1,
        sim_time=datetime(2100, 1, 1, tzinfo=UTC),
        kind=kind,
        actor_id=None,
        subject_ids=(),
        cause_seq=None,
        payload={},
        sig=None,
        prev_hash="0" * 64,
        hash=f"{seq:064x}",
    )


class Collector:
    def __init__(self) -> None:
        self.drafts: list[NewEvent] = []

    def __call__(self, draft: NewEvent) -> Event:
        self.drafts.append(draft)
        return _event(draft.kind, len(self.drafts))


class StubResolver:
    def __init__(
        self,
        slot: InstitutionSlot,
        handles: frozenset[ActionType],
        *,
        failures: Mapping[str, GateFailure] | None = None,
        event_kind: int = 1001,
    ) -> None:
        self.slot = slot
        self.handles = handles
        self.failures = failures or {}
        self.event_kind = event_kind
        self.gates: list[str] = []
        self.batches: list[tuple[ValidatedAction, ...]] = []

    def check_capability(
        self,
        action: Action,
        ctx: ValidationContext,
    ) -> GateFailure | None:
        del action, ctx
        self.gates.append("capability")
        return self.failures.get("capability")

    def check_locality(
        self,
        action: Action,
        ctx: ValidationContext,
    ) -> GateFailure | None:
        del action, ctx
        self.gates.append("locality")
        return self.failures.get("locality")

    def check_resources(
        self,
        action: Action,
        ctx: ValidationContext,
    ) -> GateFailure | None:
        del action, ctx
        self.gates.append("resources")
        return self.failures.get("resources")

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        del tick, ctx
        self.batches.append(tuple(actions))
        return (_event(self.event_kind, len(self.batches)),)

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]:
        del ctx
        return ({"type": action_type.value},)


def _ctx(actor_id: str = "ag_test") -> ValidationContext:
    return ValidationContext(
        observation=SimpleNamespace(place=SimpleNamespace(place_id="pl_test")),
        state=SimpleNamespace(agent_id=actor_id, stage="adult"),
        tick=1,
    )


def _registry(resolver: StubResolver) -> ResolverRegistry:
    registry = ResolverRegistry()
    registry.register(resolver)
    return registry


def test_enum_and_params_models_are_closed_and_frozen() -> None:
    assert len(ActionType) == 71
    assert set(PARAMS_MODELS) == set(ActionType)
    assert len(set(PARAMS_MODELS.values())) == 71
    for model in PARAMS_MODELS.values():
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True
        assert model.model_config["strict"] is True
        for name, field in model.model_fields.items():
            if "cents" in name:
                assert field.annotation is not float

    with pytest.raises(ValidationError):
        IdleParams.model_validate({"unexpected": True})
    idle = IdleParams()
    with pytest.raises(ValidationError):
        idle.extra = "mutated"  # type: ignore[attr-defined]


def test_slot_ledger_uses_the_single_profile_configuration() -> None:
    settings = ActionSettings()
    microscope = SlotLedger.from_settings(settings, "microscope")
    chronicle = SlotLedger.from_settings(settings, "chronicle")

    assert microscope.remaining("ag_test", 1) == 1
    assert chronicle.remaining("ag_test", 1) == 4
    assert microscope.consume("ag_test", 1) == 0
    assert microscope.consume("ag_test", 1) is None
    assert microscope.remaining("ag_test", 2) == 1
    microscope.reset(2)
    assert microscope.remaining("ag_test", 1) == 1


def test_native_and_external_actions_share_one_validator_and_gate_order() -> None:
    resolver = StubResolver(InstitutionSlot.MISC, frozenset({ActionType.IDLE}))
    collector = Collector()
    validator = ActionValidator.from_settings(
        _registry(resolver),
        ActionSettings(),
        "microscope",
        emit=collector,
    )
    native = make_action(
        actor_id="ag_native",
        tick=1,
        action_type=ActionType.IDLE,
        origin="deliberate",
    )
    external = make_action(
        actor_id="ag_external",
        tick=1,
        action_type=ActionType.IDLE,
        origin="external",
        sig="signed",
    )

    native_result = validator.validate(native, _ctx("ag_native"))
    native_gates = tuple(resolver.gates)
    resolver.gates.clear()
    external_result = validator.validate(external, _ctx("ag_external"))

    assert isinstance(native_result, ValidatedAction)
    assert isinstance(external_result, ValidatedAction)
    assert (
        native_gates
        == tuple(resolver.gates)
        == (
            "capability",
            "locality",
            "resources",
        )
    )
    assert type(native_result.validated_params) is type(external_result.validated_params)
    assert [draft.kind for draft in collector.drafts] == [
        ACTION_SUBMITTED,
        ACTION_SUBMITTED,
    ]


def test_schema_rejection_spends_a_slot_and_substitutes_null_action() -> None:
    resolver = StubResolver(InstitutionSlot.MISC, frozenset({ActionType.IDLE}))
    collector = Collector()
    validator = ActionValidator(
        _registry(resolver),
        SlotLedger(1),
        emit=collector,
    )
    invalid = make_action(
        actor_id="ag_test",
        tick=1,
        action_type=ActionType.IDLE,
        params={"unexpected": True},
        origin="deliberate",
        salience=0.8,
        reasoning="preserve this verbatim",
    )

    rejected = validator.validate(invalid, _ctx())
    assert isinstance(rejected, Rejection)
    assert rejected.reason == "schema"
    assert rejected.substitute.type == ActionType.NULL_ACTION
    assert rejected.substitute.origin == invalid.origin
    assert rejected.substitute.salience == invalid.salience
    assert rejected.substitute.reasoning == invalid.reasoning
    assert rejected.substitute.params == {
        "replaced_type": ActionType.IDLE.value,
        "reason": "schema",
    }
    assert [draft.kind for draft in collector.drafts] == [
        ACTION_SUBMITTED,
        ACTION_REJECTED,
    ]

    exhausted = validator.validate(
        make_action(
            actor_id="ag_test",
            tick=1,
            action_type=ActionType.IDLE,
            origin="deliberate",
            ordinal=1,
        ),
        _ctx(),
    )
    assert isinstance(exhausted, Rejection)
    assert exhausted.reason == "no_slots"


def test_stale_action_is_rejected_against_the_context_tick_budget() -> None:
    resolver = StubResolver(InstitutionSlot.MISC, frozenset({ActionType.IDLE}))
    slots = SlotLedger(1)
    validator = ActionValidator(_registry(resolver), slots)
    stale = make_action(
        actor_id="ag_test",
        tick=0,
        action_type=ActionType.IDLE,
        origin="deliberate",
    )

    result = validator.validate(stale, _ctx())
    assert isinstance(result, Rejection)
    assert result.reason == "schema"
    assert slots.remaining("ag_test", 1) == 0
    assert slots.remaining("ag_test", 0) == 1


def test_gate_order_stops_at_the_first_failure() -> None:
    resolver = StubResolver(
        InstitutionSlot.MISC,
        frozenset({ActionType.IDLE}),
        failures={
            "capability": GateFailure("capability", "not permitted"),
            "locality": GateFailure("locality", "not here"),
            "resources": GateFailure("resources", "not enough"),
        },
    )
    validator = ActionValidator(_registry(resolver), SlotLedger(1))
    action = make_action(
        actor_id="ag_test",
        tick=1,
        action_type=ActionType.IDLE,
        origin="deliberate",
    )

    result = validator.validate(action, _ctx())
    assert isinstance(result, Rejection)
    assert result.reason == "capability"
    assert resolver.gates == ["capability"]


def test_reflex_guard_halts_instead_of_rejecting() -> None:
    assert (
        frozenset(
            {
                ActionType.MOVE_TO,
                ActionType.IDLE,
                ActionType.SLEEP,
                ActionType.EAT,
                ActionType.WORK,
                ActionType.STUDY,
                ActionType.BUY_GOOD,
                ActionType.SAY,
                ActionType.REPAY_LOAN,
                ActionType.NULL_ACTION,
            }
        )
        == REFLEX_ALLOWED
    )
    resolver = StubResolver(
        InstitutionSlot.LABOUR,
        frozenset({ActionType.APPLY_FOR_JOB}),
    )
    validator = ActionValidator(_registry(resolver), SlotLedger(1))
    action = make_action(
        actor_id="ag_test",
        tick=1,
        action_type=ActionType.APPLY_FOR_JOB,
        params={"vacancy_id": "va_test"},
    )

    with pytest.raises(ReflexActionViolation):
        validator.validate(action, _ctx())


def test_registry_rejects_duplicate_action_handlers() -> None:
    registry = ResolverRegistry()
    registry.register(StubResolver(InstitutionSlot.MOVEMENT, frozenset({ActionType.MOVE_TO})))
    with pytest.raises(DuplicateHandler):
        registry.register(StubResolver(InstitutionSlot.MISC, frozenset({ActionType.MOVE_TO})))


def test_dispatcher_uses_literal_slot_order_and_calls_empty_batches() -> None:
    misc = StubResolver(
        InstitutionSlot.MISC,
        frozenset({ActionType.NULL_ACTION}),
        event_kind=1003,
    )
    movement = StubResolver(
        InstitutionSlot.MOVEMENT,
        frozenset({ActionType.MOVE_TO}),
        event_kind=1001,
    )
    registry = ResolverRegistry()
    registry.register(misc)
    registry.register(movement)
    dispatcher = ActionDispatcher(registry)
    context = ResolutionContext(emit=Collector())

    events = dispatcher.dispatch((), 1, context)
    assert [event.kind for event in events] == [1001, 1003]
    assert movement.batches == [()]
    assert misc.batches == [()]

    second = make_action(
        actor_id="ag_z",
        tick=1,
        action_type=ActionType.MOVE_TO,
        params={"place_id": "pl_test"},
        origin="deliberate",
    )
    first = make_action(
        actor_id="ag_a",
        tick=1,
        action_type=ActionType.MOVE_TO,
        params={"place_id": "pl_test"},
        origin="deliberate",
    )
    validated = (
        ValidatedAction(second, MoveToParams(place_id="pl_test"), LegalityVerdict(False), 0),
        ValidatedAction(first, MoveToParams(place_id="pl_test"), LegalityVerdict(False), 0),
    )
    dispatcher.dispatch(validated, 1, context)
    assert [item.action.actor_id for item in movement.batches[-1]] == ["ag_a", "ag_z"]


def test_legal_actions_keep_enum_order_and_exclude_unregistered_types() -> None:
    resolver = StubResolver(
        InstitutionSlot.MISC,
        frozenset({ActionType.IDLE, ActionType.NULL_ACTION}),
    )
    available = legal_actions(
        _ctx().observation,
        _ctx().state,
        _registry(resolver),
        _ctx(),
    )
    assert all(isinstance(item, LegalAction) for item in available)
    assert [item.type for item in available] == [
        ActionType.IDLE,
        ActionType.NULL_ACTION,
    ]


def test_outcome_ledger_only_exposes_the_previous_tick() -> None:
    ledger = RejectionLedger()
    action = make_action(
        actor_id="ag_test",
        tick=1,
        action_type=ActionType.IDLE,
        origin="deliberate",
    )
    validated = ValidatedAction(action, IdleParams(), LegalityVerdict(False), 0)
    ledger.record_applied(validated, 1, ("TICK_STARTED",))

    assert ledger.last_action_outcome("ag_test", 1) is None
    outcome = ledger.last_action_outcome("ag_test", 2)
    assert outcome == ActionOutcome(
        action_id=action.action_id,
        tick=1,
        type=ActionType.IDLE,
        status="applied",
        reason=None,
        detail=None,
        effects=("TICK_STARTED",),
    )
    ledger.prune(4)
    assert ledger.last_action_outcome("ag_test", 2) is None


def test_null_params_accept_the_protocol_substitute_shape() -> None:
    parsed = NullActionParams.model_validate(
        {"replaced_type": ActionType.IDLE.value, "reason": "schema"}
    )
    assert parsed.replaced_type == "IDLE"
