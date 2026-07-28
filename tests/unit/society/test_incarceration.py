from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType, SimpleNamespace
from typing import Any

from polis.agents.actions import (
    Action,
    ActionType,
    ActionValidator,
    InstitutionSlot,
    Rejection,
    ResolutionContext,
    ResolverRegistry,
    SlotLedger,
    ValidatedAction,
    ValidationContext,
    legal_actions,
    make_action,
)
from polis.events.kinds import INCARCERATION_ENDED, INCARCERATION_STARTED
from polis.events.types import Event
from polis.society.law import Incarceration, Obligation, ObligationIndex
from polis.world.api import Location
from tests.law_support import clock, law_cfg, log, runtime, world


class AllResolver:
    slot = InstitutionSlot.MISC
    handles = frozenset(ActionType)

    def check_capability(self, action: Action, ctx: ValidationContext) -> None:
        del action, ctx

    def check_locality(self, action: Action, ctx: ValidationContext) -> None:
        del action, ctx

    def check_resources(self, action: Action, ctx: ValidationContext) -> None:
        del action, ctx

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        del actions, tick, ctx
        return ()

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]:
        del action_type, ctx
        return ()


def _incarceration(*, sentence_capacity: int = 40):
    configured_world = world()
    home = configured_world.places_of_type("home")[0]
    configured_world.locations["ag_actor"] = Location(
        home.place_id,
        home.district_id,
        home.x,
        home.y,
    )
    configured_world.freeze_occupancy()
    configured_runtime = runtime()
    if sentence_capacity != 40:
        configured_runtime.enact(
            "prison.capacity",
            sentence_capacity,
            1,
            "py_capacity",
            1,
            enacted_tick=0,
        )
    terminated: list[tuple[str, int]] = []
    converted: list[tuple[str, str, int, int]] = []
    service = Incarceration(
        log=log(),
        clock=clock(),
        world=configured_world,
        runtime=configured_runtime,
        cfg=law_cfg(),
        terminate_employment=lambda agent_id, tick: terminated.append((agent_id, tick)) or (),
        household_return=lambda _agent_id: ("hh_one", home.place_id),
        conversion_fine=lambda agent_id, case_id, cents, tick: (
            converted.append((agent_id, case_id, cents, tick)) or ()
        ),
    )
    return service, configured_world, home, terminated, converted


def test_incarceration_restricts_actions_terminates_work_and_doubles_decay() -> None:
    service, configured_world, _home, terminated, _converted = _incarceration()
    events = service.commit("ag_actor", "ca_one", 2, 1)
    registry = ResolverRegistry()
    registry.register(AllResolver())
    ctx = ValidationContext(
        observation=object(),
        state=object(),
        tick=1,
        repositories=MappingProxyType({"incarceration": service}),
    )
    advertised = legal_actions(
        object(),
        SimpleNamespace(agent_id="ag_actor", stage="adult"),
        registry,
        ctx,
    )

    assert [item.kind for item in events] == [INCARCERATION_STARTED]
    assert {item.type for item in advertised} == Incarceration.ALLOWED_ACTIONS
    assert terminated == [("ag_actor", 1)]
    assert service.decay_multiplier("ag_actor", 1) == 2.0
    assert (
        configured_world.place(configured_world.locations["ag_actor"].place_id or "").type
        == "prison"
    )
    rejected = ActionValidator(registry, SlotLedger(1)).validate(
        make_action(
            actor_id="ag_actor",
            tick=1,
            action_type=ActionType.WORK,
            params={},
        ),
        ctx,
    )
    assert isinstance(rejected, Rejection)
    assert rejected.gate == "capability"


def test_obligations_continue_and_release_returns_to_the_household() -> None:
    service, configured_world, home, _terminated, _converted = _incarceration()
    obligations = ObligationIndex(lambda _agent_id: 500)
    obligations.add(Obligation("ob_one", "ag_actor", "ba_one", 500, 2))
    service.commit("ag_actor", "ca_one", 2, 1)

    assert obligations.due("ag_actor", 3)
    released = service.release_due(3)
    assert [item.kind for item in released] == [INCARCERATION_ENDED]
    assert configured_world.locations["ag_actor"].place_id == home.place_id
    assert not service.is_incarcerated("ag_actor", 3)


def test_capacity_overflow_converts_the_sentence_to_a_logged_fine() -> None:
    service, configured_world, home, terminated, converted = _incarceration(sentence_capacity=0)
    service.record_conviction("ag_actor")
    events = service.commit("ag_actor", "ca_one", 4, 1)

    assert events[0].payload["converted_to_fine"] is True
    assert converted == [("ag_actor", "ca_one", 4 * law_cfg().fine_per_tick_cents, 1)]
    assert configured_world.locations["ag_actor"].place_id == home.place_id
    assert terminated == []
    assert service.criminal_record("ag_actor") == 1
