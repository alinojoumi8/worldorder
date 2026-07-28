from __future__ import annotations

import copy
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

from polis.agents.actions import (
    Action,
    ActionType,
    GateFailure,
    GateResult,
    InstitutionSlot,
    ResolutionContext,
    ValidatedAction,
    ValidationContext,
)
from polis.agents.genesis import (
    advance_age,
    assign_genesis_household_ids,
    derive_reflex_profile,
    inherit_traits,
    population_mean_traits,
    stage_for_age,
)
from polis.agents.ports import (
    BeliefPriorPort,
    EmploymentPort,
    EstatePort,
    HousingPort,
    IncarcerationPort,
    LedgerReadPort,
    MemoryArchivePort,
    SocialGraphPort,
)
from polis.agents.state import AgentPopulation
from polis.agents.types import SKILLS, AgentState, Needs, Traits
from polis.config.mechanisms import mechanism
from polis.config.runtime import RuntimeOverlay
from polis.config.settings import DemographySettings
from polis.events.kinds import (
    AGENT_BORN,
    AGENT_DIED,
    BELIEF_PRIORS_INHERITED,
    BEREAVEMENT_APPLIED,
    CHILD_COST_CHARGED,
    CONCEPTION,
    COURTSHIP_ENDED,
    COURTSHIP_STARTED,
    ESTATE_CLOSED,
    ESTATE_DEBTS_SETTLED,
    ESTATE_DISTRIBUTED,
    ESTATE_OPENED,
    HOUSEHOLD_DISSOLVED,
    HOUSEHOLD_FORMED,
    HOUSEHOLD_JOINED,
    HOUSEHOLD_LEFT,
    MIGRATION_IN,
    MIGRATION_OUT,
    MORTALITY_HAZARD_DRAWN,
    PREGNANCY_ENDED,
    STATE_CARE_STARTED,
    TIE_ENDED,
    UNION_DISSOLVED,
    UNION_FORMED,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.det import det_id, stable
from polis.kernel.rng import RngRegistry
from polis.world.api import Location, World

FERTILITY_ENTAILS: Final = (
    "the birth rate is increasing in household income and in the child benefit, and "
    "decreasing in parity. Therefore redistribution and wealth-family-size associations "
    "are partly entailed; ablate with fertility_hazard: uniform."
)
MORTALITY_ENTAILS: Final = (
    "mortality rises with age, ill health, low wealth percentile, and district crime; "
    "the socioeconomic gradient is imposed by the hazard and must be ablated."
)
EMIGRATION_ENTAILS: Final = (
    "selective out-migration of poor and weakly tied residents mechanically improves "
    "resident wealth and tie-density statistics; ablate with base_emig_per_sim_day: 0."
)
CUSTODY_ENTAILS: Final = (
    "dependants follow the higher-income parent on dissolution, so child outcomes "
    "correlate with parental income through assignment; ablate with custody_default: coin_flip."
)


def _stage(
    log: EventLog,
    clock: Clock,
    tick: int,
    kind: int,
    payload: Mapping[str, object],
    *,
    actor_id: str | None = None,
    subjects: Sequence[str] = (),
    cause_seq: int | None = None,
) -> Event:
    return log.stage(
        NewEvent(
            kind,
            dict(payload),
            actor_id=actor_id,
            subject_ids=tuple(subjects),
            cause_seq=cause_seq,
        ),
        tick=tick,
        sim_time=clock.sim_time_at(tick),
    )


def _param(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _target(action: Action, params: object | None = None) -> str:
    value = _param(params if params is not None else action.params, "target_id", "")
    return str(value)


def _numeric_agent_id(namespace: str, *parts: object) -> str:
    value = det_id("ag", namespace, *parts).removeprefix("ag_")
    return f"ag_{int(value, 16)}"


def _restore_object(target: object, snapshot: object) -> None:
    if not isinstance(snapshot, Mapping):
        raise TypeError("rollback snapshot must be a mapping")
    loader = getattr(target, "load", None)
    if callable(loader):
        loader(snapshot)
        return
    target_dict = getattr(target, "__dict__", None)
    if isinstance(target_dict, dict):
        target_dict.clear()
        target_dict.update(copy.deepcopy(snapshot))
        return
    raise TypeError(f"rollback target cannot be restored: {type(target).__name__}")


def _snapshot_object(target: object) -> object:
    dumper = getattr(target, "dump", None)
    if callable(dumper):
        return copy.deepcopy(dumper())
    target_dict = getattr(target, "__dict__", None)
    return copy.deepcopy(target_dict if isinstance(target_dict, dict) else target)


@dataclass(frozen=True, slots=True)
class Household:
    household_id: str
    formed_at_tick: int
    dissolved_at_tick: int | None
    home_place_id: str
    member_ids: tuple[str, ...]
    head_agent_id: str | None
    tenure: Literal["own", "rent", "shelter"]
    rent_cents: int
    joint_baseline_cents: Mapping[str, int] = field(default_factory=dict)
    arrears_cents: int = 0


class HouseholdRegistry:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        world: World,
        agents: AgentPopulation,
        housing: HousingPort | None = None,
        ledger: LedgerReadPort | None = None,
        employment: EmploymentPort | None = None,
        cfg: DemographySettings,
        rng: RngRegistry,
        custody_mode: str,
    ) -> None:
        self.log = log
        self.clock = clock
        self.world = world
        self.agents = agents
        self.housing = housing
        self.ledger = ledger
        self.employment = employment
        self.cfg = cfg
        self.rng = rng
        self.custody_mode = custody_mode
        self.households: dict[str, Household] = {}

    def bootstrap(self) -> None:
        assign_genesis_household_ids(self.agents)
        grouped: dict[str, list[str]] = defaultdict(list)
        for agent in self.agents.alive():
            if agent.household_id is None:
                raise RuntimeError(f"genesis household was not assigned for {agent.agent_id}")
            grouped[agent.household_id].append(agent.agent_id)
        for household_id, members in sorted(grouped.items()):
            ordered = tuple(sorted(members))
            home_place_id = self.agents[ordered[0]].home_place_id
            head = min(
                ordered,
                key=lambda agent_id: (-self.agents[agent_id].age_years, agent_id),
            )
            place = self.world.place(home_place_id)
            tenure: Literal["own", "rent", "shelter"] = (
                "shelter" if place.type == "shelter" else "rent"
            )
            row = Household(
                household_id,
                0,
                None,
                home_place_id,
                ordered,
                head,
                tenure,
                int(place.rent_cents),
                self._baseline(ordered),
            )
            self.households[household_id] = row
            for agent_id in ordered:
                self.agents[agent_id].household_id = household_id

    def _baseline(self, member_ids: Sequence[str]) -> dict[str, int]:
        if self.ledger is None:
            return {agent_id: self.agents[agent_id].wealth_cents for agent_id in member_ids}
        return {agent_id: self.ledger.liquid(agent_id) for agent_id in member_ids}

    def dump(self) -> dict[str, object]:
        return {"households": copy.deepcopy(self.households)}

    def load(self, state: Mapping[str, object]) -> None:
        rows = state.get("households")
        if not isinstance(rows, Mapping):
            raise ValueError("household checkpoint is invalid")
        self.households = copy.deepcopy(cast(dict[str, Household], rows))
        for agent in self.agents:
            agent.household_id = None
        for household in self.households.values():
            if household.dissolved_at_tick is None:
                for agent_id in household.member_ids:
                    if agent_id in self.agents.agents:
                        self.agents[agent_id].household_id = household.household_id

    def form(
        self,
        member_ids: Sequence[str],
        tick: int,
        *,
        reason: str,
    ) -> tuple[Household, Sequence[Event]]:
        members = tuple(sorted(set(member_ids)))
        if not members:
            raise ValueError("a household needs at least one member")
        for agent_id in members:
            if agent_id not in self.agents.agents or not self.agents[agent_id].alive:
                raise ValueError(f"household member is not a living agent: {agent_id}")
        combined_income = sum(self.income_cents_for(agent_id, tick) for agent_id in members)
        home = (
            None
            if self.housing is None
            else self.housing.find_affordable_home(combined_income, tick)
        )
        home_place_id = home or self.agents[members[0]].home_place_id
        place = self.world.place(home_place_id)
        household_id = det_id("hh", "demography.household", tick, reason, *members)
        head = min(
            members,
            key=lambda agent_id: (-self.income_cents_for(agent_id, tick), agent_id),
        )
        tenure: Literal["own", "rent", "shelter"] = "shelter" if place.type == "shelter" else "rent"
        row = Household(
            household_id,
            tick,
            None,
            home_place_id,
            members,
            head,
            tenure,
            int(place.rent_cents),
            self._baseline(members),
        )
        self.households[household_id] = row
        events: list[Event] = []
        for agent_id in members:
            prior = self.of(agent_id)
            if prior is not None and prior.household_id != household_id:
                events.append(self.leave(agent_id, f"reformed:{reason}", tick))
            agent = self.agents[agent_id]
            agent.household_id = household_id
            agent.home_place_id = home_place_id
            self._locate(agent_id, home_place_id)
        self.world.freeze_occupancy()
        event = _stage(
            self.log,
            self.clock,
            tick,
            HOUSEHOLD_FORMED,
            {
                "household_id": household_id,
                "member_ids": list(members),
                "home_place_id": home_place_id,
                "tenure": tenure,
                "rent_cents": row.rent_cents,
                "head_agent_id": head,
                "reason": reason,
            },
            subjects=members,
        )
        return row, (*events, event)

    def join(self, agent_id: str, household_id: str, reason: str, tick: int) -> Event:
        row = self.households[household_id]
        if row.dissolved_at_tick is not None:
            raise ValueError("cannot join a dissolved household")
        prior = self.of(agent_id)
        if prior is not None and prior.household_id != household_id:
            self.leave(agent_id, f"joined:{reason}", tick)
        members = tuple(sorted({*row.member_ids, agent_id}))
        self.households[household_id] = Household(
            row.household_id,
            row.formed_at_tick,
            None,
            row.home_place_id,
            members,
            row.head_agent_id
            if row.head_agent_id is not None
            else (None if row.tenure == "shelter" else agent_id),
            row.tenure,
            row.rent_cents,
            {**row.joint_baseline_cents, agent_id: self._baseline((agent_id,))[agent_id]},
            row.arrears_cents,
        )
        agent = self.agents[agent_id]
        agent.household_id = household_id
        agent.home_place_id = row.home_place_id
        self._locate(agent_id, row.home_place_id)
        self.world.freeze_occupancy()
        return _stage(
            self.log,
            self.clock,
            tick,
            HOUSEHOLD_JOINED,
            {"agent_id": agent_id, "household_id": household_id, "reason": reason},
            actor_id=agent_id,
            subjects=(agent_id, household_id),
        )

    def _locate(self, agent_id: str, place_id: str) -> None:
        place = self.world.place(place_id)
        self.world.locations[agent_id] = Location(
            place.place_id,
            place.district_id,
            place.x,
            place.y,
        )

    def leave(self, agent_id: str, reason: str, tick: int) -> Event:
        row = self.of(agent_id)
        if row is None:
            raise ValueError("agent does not belong to a live household")
        self._remove_member(row, agent_id)
        self.agents[agent_id].household_id = None
        return _stage(
            self.log,
            self.clock,
            tick,
            HOUSEHOLD_LEFT,
            {
                "agent_id": agent_id,
                "household_id": row.household_id,
                "reason": reason,
            },
            actor_id=agent_id,
            subjects=(agent_id, row.household_id),
        )

    def _remove_member(self, row: Household, agent_id: str) -> None:
        members = tuple(member for member in row.member_ids if member != agent_id)
        head = (
            row.head_agent_id if row.head_agent_id in members else (members[0] if members else None)
        )
        self.households[row.household_id] = Household(
            row.household_id,
            row.formed_at_tick,
            row.dissolved_at_tick,
            row.home_place_id,
            members,
            head,
            row.tenure,
            row.rent_cents,
            row.joint_baseline_cents,
            row.arrears_cents,
        )

    def dissolve(self, household_id: str, reason: str, tick: int) -> Sequence[Event]:
        row = self.households[household_id]
        if row.dissolved_at_tick is not None:
            return ()
        events: list[Event] = []
        members = row.member_ids
        for agent_id in members:
            if self.agents[agent_id].household_id == household_id:
                self.agents[agent_id].household_id = None
                events.append(
                    _stage(
                        self.log,
                        self.clock,
                        tick,
                        HOUSEHOLD_LEFT,
                        {
                            "agent_id": agent_id,
                            "household_id": household_id,
                            "reason": reason,
                        },
                        actor_id=agent_id,
                        subjects=(agent_id, household_id),
                    )
                )
        self.households[household_id] = Household(
            row.household_id,
            row.formed_at_tick,
            tick,
            row.home_place_id,
            (),
            None,
            row.tenure,
            row.rent_cents,
            row.joint_baseline_cents,
            row.arrears_cents,
        )
        events.append(
            _stage(
                self.log,
                self.clock,
                tick,
                HOUSEHOLD_DISSOLVED,
                {
                    "household_id": household_id,
                    "reason": reason,
                    "members_reassigned": [],
                },
                subjects=(household_id, *members),
            )
        )
        return tuple(events)

    def of(self, agent_id: str) -> Household | None:
        household_id = (
            None if agent_id not in self.agents.agents else self.agents[agent_id].household_id
        )
        if household_id is None:
            return None
        row = self.households.get(household_id)
        return None if row is None or row.dissolved_at_tick is not None else row

    def head_of(self, household_id: str) -> str | None:
        row = self.households.get(household_id)
        return None if row is None or row.dissolved_at_tick is not None else row.head_agent_id

    def income_cents_for(self, agent_id: str, tick: int) -> int:
        if self.employment is None:
            return max(0, self.agents[agent_id].wealth_cents)
        return max(0, self.employment.income_cents(agent_id, tick))

    def income_cents(self, household_id: str, tick: int) -> int:
        row = self.households[household_id]
        return sum(self.income_cents_for(agent_id, tick) for agent_id in row.member_ids)

    def spare_capacity(self, household_id: str) -> bool:
        row = self.households[household_id]
        return len(row.member_ids) < self.world.place(row.home_place_id).capacity

    def state_household(self, tick: int) -> Household:
        household_id, place_id = self.state_household_identity()
        existing = self.households.get(household_id)
        if existing is not None and existing.dissolved_at_tick is None:
            return existing
        place = self.world.place(place_id)
        row = Household(
            household_id,
            tick,
            None,
            place.place_id,
            (),
            None,
            "shelter",
            0,
        )
        self.households[household_id] = row
        return row

    def state_household_identity(self) -> tuple[str, str]:
        shelters = self.world.places_of_type("shelter")
        if not shelters:
            raise RuntimeError("state household requires a shelter place")
        place = shelters[0]
        household_id = det_id("hh", "demography.state", place.place_id)
        return household_id, place.place_id

    def update_arrears(self, household_id: str, cents: int) -> Household:
        row = self.households[household_id]
        updated = Household(
            row.household_id,
            row.formed_at_tick,
            row.dissolved_at_tick,
            row.home_place_id,
            row.member_ids,
            row.head_agent_id,
            row.tenure,
            row.rent_cents,
            row.joint_baseline_cents,
            max(0, cents),
        )
        self.households[household_id] = updated
        return updated

    def advance_independence(
        self,
        tick: int,
        *,
        age_step_years: float,
    ) -> Sequence[Event]:
        del age_step_years
        events: list[Event] = []
        for agent in stable(self.agents.alive(), key=lambda row: row.agent_id):
            household = self.of(agent.agent_id)
            if (
                household is None
                or len(household.member_ids) <= 1
                or agent.age_years < self.cfg.leave_home_age
                or self.income_cents_for(agent.agent_id, tick)
                < self.cfg.independence_threshold_cents
            ):
                continue
            events.append(self.leave(agent.agent_id, "independence", tick))
            _household, formed = self.form(
                (agent.agent_id,),
                tick,
                reason="independence",
            )
            events.extend(formed)
        return tuple(events)

    @mechanism(
        "custody_default",
        entails=CUSTODY_ENTAILS,
        config_key="mechanisms.custody_default",
    )
    def custody_parent(self, a_id: str, b_id: str, household_id: str, tick: int) -> str:
        if self.custody_mode == "coin_flip":
            return (
                a_id if self.rng.get("demog.courtship", household_id, tick).random() < 0.5 else b_id
            )
        return min(
            (a_id, b_id),
            key=lambda agent_id: (-self.income_cents_for(agent_id, tick), agent_id),
        )


@dataclass(slots=True)
class Courtship:
    a_id: str
    b_id: str
    started_tick: int
    latest: dict[str, int] = field(default_factory=dict)
    proposals: dict[str, int] = field(default_factory=dict)


class CourtshipRegistry:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        agents: AgentPopulation,
        households: HouseholdRegistry,
        graph: SocialGraphPort,
        cfg: DemographySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.agents = agents
        self.households = households
        self.graph = graph
        self.cfg = cfg
        self.rows: dict[tuple[str, str], Courtship] = {}
        self.pending: list[tuple[ActionType, str, str]] = []

    def queue(self, action_type: ActionType, actor_id: str, target_id: str) -> None:
        self.pending.append((action_type, actor_id, target_id))

    def _key(self, a_id: str, b_id: str) -> tuple[str, str]:
        return (a_id, b_id) if a_id <= b_id else (b_id, a_id)

    def court(self, initiator_id: str, target_id: str, tick: int) -> Sequence[Event]:
        key = self._key(initiator_id, target_id)
        row = self.rows.get(key)
        created = row is None
        if row is None:
            row = Courtship(key[0], key[1], tick)
            self.rows[key] = row
        row.latest[initiator_id] = tick
        if not created:
            return ()
        location = self.world.locations.get(initiator_id)
        return (
            _stage(
                self.log,
                self.clock,
                tick,
                COURTSHIP_STARTED,
                {
                    "a_id": key[0],
                    "b_id": key[1],
                    "initiator_id": initiator_id,
                    "compatibility": self.compatibility(
                        self.agents[initiator_id], self.agents[target_id]
                    ),
                    "place_id": None if location is None else location.place_id,
                },
                actor_id=initiator_id,
                subjects=key,
            ),
        )

    def mutual(self, a_id: str, b_id: str, tick: int) -> bool:
        row = self.rows.get(self._key(a_id, b_id))
        if row is None or set(row.latest) != {a_id, b_id}:
            return False
        window = self.cfg.courtship_window_sim_days * self.clock.profile.ticks_per_sim_day
        return all(tick - value <= window for value in row.latest.values())

    def compatibility(self, a: AgentState, b: AgentState) -> float:
        age = 1.0 - min(1.0, abs(a.age_years - b.age_years) / self.cfg.age_norm_years)
        trait_names = tuple(a.traits.__dataclass_fields__)
        trait = 1.0 - sum(
            abs(float(getattr(a.traits, name)) - float(getattr(b.traits, name)))
            for name in trait_names
        ) / len(trait_names)
        tie = self.graph.strength(a.agent_id, b.agent_id)
        denom = max(1, abs(a.wealth_cents), abs(b.wealth_cents))
        econ = 1.0 - min(1.0, abs(a.wealth_cents - b.wealth_cents) / denom)
        weights = self.cfg.compatibility_weights
        return round(
            weights["age"] * age
            + weights["traits"] * trait
            + weights["beliefs"] * 0.5
            + weights["tie"] * tie
            + weights["econ"] * econ,
            6,
        )

    def compatibility_narrative(self, score: float) -> str:
        if score >= 0.75:
            return "you have a lot in common with this person"
        if score >= 0.50:
            return "you share some interests with this person"
        return "you seem quite different from this person"

    def propose_union(self, a_id: str, b_id: str, tick: int) -> Sequence[Event]:
        row = self.rows.get(self._key(a_id, b_id))
        if row is None or not self.mutual(a_id, b_id, tick):
            return ()
        row.proposals[a_id] = tick
        if set(row.proposals) != {a_id, b_id}:
            return ()
        return self.confirm(a_id, b_id, tick)

    def confirm(self, a_id: str, b_id: str, tick: int) -> Sequence[Event]:
        key = self._key(a_id, b_id)
        row = self.rows[key]
        if self.graph.live_partner(a_id) is not None or self.graph.live_partner(b_id) is not None:
            return ()
        household_ids = {
            household_id
            for household_id in (
                self.agents[a_id].household_id,
                self.agents[b_id].household_id,
            )
            if household_id is not None
        }
        dependants = tuple(
            sorted(
                agent.agent_id
                for agent in self.agents.alive()
                if agent.age_years < 18 and agent.household_id in household_ids
            )
        )
        household, household_events = self.households.form(
            (a_id, b_id, *dependants),
            tick,
            reason="union",
        )
        tie_event = self.graph.form(a_id, b_id, "partner", "union", tick)
        union = _stage(
            self.log,
            self.clock,
            tick,
            UNION_FORMED,
            {
                "partner_ids": list(key),
                "household_id": household.household_id,
                "courtship_ticks": tick - row.started_tick,
            },
            subjects=key,
        )
        ended = _stage(
            self.log,
            self.clock,
            tick,
            COURTSHIP_ENDED,
            {
                "a_id": key[0],
                "b_id": key[1],
                "outcome": "union",
                "duration_ticks": tick - row.started_tick,
            },
            subjects=key,
        )
        del self.rows[key]
        return (
            *household_events,
            *((tie_event,) if tie_event is not None else ()),
            union,
            ended,
        )

    def expire(self, tick: int) -> Sequence[Event]:
        window = self.cfg.courtship_window_sim_days * self.clock.profile.ticks_per_sim_day
        events: list[Event] = []
        for key, row in sorted(tuple(self.rows.items())):
            latest = max(row.latest.values(), default=row.started_tick)
            if tick - latest <= window:
                continue
            events.append(
                _stage(
                    self.log,
                    self.clock,
                    tick,
                    COURTSHIP_ENDED,
                    {
                        "a_id": key[0],
                        "b_id": key[1],
                        "outcome": "drifted",
                        "duration_ticks": tick - row.started_tick,
                    },
                    subjects=key,
                )
            )
            del self.rows[key]
        return tuple(events)

    def process(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for action_type, actor_id, target_id in sorted(
            self.pending,
            key=lambda item: (item[0].value, item[1], item[2]),
        ):
            actor = self.agents.agents.get(actor_id)
            if actor is None or not actor.alive:
                continue
            if action_type != ActionType.HAVE_CHILD_INTENT:
                target = self.agents.agents.get(target_id)
                if target is None or not target.alive:
                    continue
            if action_type == ActionType.COURT:
                events.extend(self.court(actor_id, target_id, tick))
            elif action_type == ActionType.PROPOSE_UNION:
                events.extend(self.propose_union(actor_id, target_id, tick))
            elif action_type == ActionType.HAVE_CHILD_INTENT:
                self.agents[actor_id].fertility_intent_tick = tick
            elif action_type == ActionType.DISSOLVE_UNION:
                events.extend(self.dissolve_union(actor_id, target_id, tick))
        self.pending.clear()
        events.extend(self.expire(tick))
        return tuple(events)

    def dissolve_union(self, initiator_id: str, target_id: str, tick: int) -> Sequence[Event]:
        household = self.households.of(initiator_id)
        if household is None or household.household_id != self.agents[target_id].household_id:
            return ()
        savepoint = self.log.savepoint()
        household_snapshot = self.households.dump()
        agent_snapshots = {
            agent_id: copy.deepcopy(self.agents[agent_id]) for agent_id in household.member_ids
        }
        location_snapshots = {
            agent_id: copy.deepcopy(self.world.locations.get(agent_id))
            for agent_id in household.member_ids
        }
        graph_snapshot = _snapshot_object(self.graph)
        ledger = self.households.ledger
        ledger_snapshot = None if ledger is None else ledger.dump()
        try:
            return self._dissolve_union(household, initiator_id, target_id, tick)
        except Exception:
            try:
                if ledger is not None and ledger_snapshot is not None:
                    ledger.load(ledger_snapshot)
                _restore_object(self.graph, graph_snapshot)
                for agent_id, agent_snapshot in agent_snapshots.items():
                    self.agents.agents[agent_id] = copy.deepcopy(agent_snapshot)
                self.households.load(household_snapshot)
                for agent_id, location_snapshot in location_snapshots.items():
                    if location_snapshot is None:
                        self.world.locations.pop(agent_id, None)
                    else:
                        self.world.locations[agent_id] = copy.deepcopy(location_snapshot)
                self.world.freeze_occupancy()
            finally:
                self.log.rollback_to(savepoint)
            raise

    def _dissolve_union(
        self,
        household: Household,
        initiator_id: str,
        target_id: str,
        tick: int,
    ) -> Sequence[Event]:
        dependants = tuple(
            sorted(
                agent_id
                for agent_id in household.member_ids
                if agent_id not in {initiator_id, target_id}
                and self.agents[agent_id].age_years < 18
            )
        )
        other_adults = tuple(
            sorted(
                agent_id
                for agent_id in household.member_ids
                if agent_id not in {initiator_id, target_id, *dependants}
                and self.agents[agent_id].alive
            )
        )
        custody_parent = self.households.custody_parent(
            initiator_id,
            target_id,
            household.household_id,
            tick,
        )
        split_legs, split_txn_id = self._joint_wealth_split(
            household,
            initiator_id,
            target_id,
            tick,
        )
        event = _stage(
            self.log,
            self.clock,
            tick,
            UNION_DISSOLVED,
            {
                "partner_ids": list(sorted((initiator_id, target_id))),
                "initiator_id": initiator_id,
                "reason": "unilateral",
                "split_txn_id": split_txn_id,
                "dependants": list(dependants),
                "custody": {agent_id: custody_parent for agent_id in dependants},
            },
            actor_id=initiator_id,
            subjects=(initiator_id, target_id, *dependants),
        )
        if split_legs:
            actual = str(
                cast(LedgerReadPort, self.households.ledger).post_transaction(
                    split_legs,
                    tick=tick,
                    cause=event,
                )
            )
            if actual != split_txn_id:
                raise RuntimeError("dissolution split transaction ordinal diverged")
        tie_ended = self.graph.end_pair(
            initiator_id,
            target_id,
            "partner",
            "dissolution",
            tick,
        )
        events = [
            event,
            *((tie_ended,) if tie_ended is not None else ()),
            *self.households.dissolve(household.household_id, "dissolution", tick),
        ]
        for parent_id in sorted((initiator_id, target_id)):
            _, formed = self.households.form(
                (parent_id, *dependants) if parent_id == custody_parent else (parent_id,),
                tick,
                reason="dissolution",
            )
            events.extend(formed)
        for adult_id in other_adults:
            _, formed = self.households.form(
                (adult_id,),
                tick,
                reason="dissolution",
            )
            events.extend(formed)
        return tuple(events)

    def _joint_wealth_split(
        self,
        household: Household,
        a_id: str,
        b_id: str,
        tick: int,
    ) -> tuple[Sequence[Any], str | None]:
        ledger = self.households.ledger
        if ledger is None:
            return (), None
        current = {agent_id: ledger.liquid(agent_id) for agent_id in (a_id, b_id)}
        gains = {
            agent_id: max(
                0,
                current[agent_id] - int(household.joint_baseline_cents.get(agent_id, 0)),
            )
            for agent_id in (a_id, b_id)
        }
        total = sum(gains.values())
        shares = ledger.allocate(total, ((a_id, 1), (b_id, 1)))
        donor = max(
            (a_id, b_id), key=lambda agent_id: (gains[agent_id] - shares[agent_id], agent_id)
        )
        receiver = b_id if donor == a_id else a_id
        amount = max(0, gains[donor] - shares[donor])
        if amount == 0:
            return (), None
        donor_accounts = ledger.accounts_of(donor)
        receiver_accounts = ledger.accounts_of(receiver)
        if not donor_accounts or not receiver_accounts:
            raise RuntimeError("dissolution split requires ledger accounts for both partners")
        legs = ledger.transfer(
            donor_accounts[0],
            receiver_accounts[0],
            amount,
            "transfer",
        )
        return legs, str(ledger.next_txn_id(tick))


class RelationalResolver:
    slot: Final[InstitutionSlot] = InstitutionSlot.COMMUNICATION
    handles: Final[frozenset[ActionType]] = frozenset(
        {
            ActionType.COURT,
            ActionType.PROPOSE_UNION,
            ActionType.DISSOLVE_UNION,
            ActionType.HAVE_CHILD_INTENT,
        }
    )

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        households: HouseholdRegistry,
        courtships: CourtshipRegistry,
        graph: SocialGraphPort,
        cfg: DemographySettings,
        agents: AgentPopulation,
        incarceration: IncarcerationPort | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.households = households
        self.courtships = courtships
        self.graph = graph
        self.cfg = cfg
        self.agents = agents
        self.incarceration = incarceration

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        actor = self.agents[action.actor_id]
        target_id = _target(action)
        if not actor.alive:
            return GateFailure("capability", "dead agents cannot form relationships")
        if action.type == ActionType.HAVE_CHILD_INTENT:
            lo, hi = self.cfg.fertility.band
            if not lo <= actor.age_years <= hi:
                return GateFailure("capability", "actor is outside the fertile band")
            if self.incarceration is not None and self.incarceration.is_incarcerated(
                actor.agent_id
            ):
                return GateFailure("capability", "incarcerated agents cannot express intent")
            return None
        if not target_id or target_id not in self.agents.agents:
            return GateFailure("capability", "relationship target does not exist")
        target = self.agents[target_id]
        if not target.alive:
            return GateFailure("capability", "relationship target is not alive")
        if action.type in {ActionType.COURT, ActionType.PROPOSE_UNION}:
            if actor.age_years < 18 or target.age_years < 18:
                return GateFailure("capability", "courtship requires two adults")
            if (
                self.graph.live_partner(actor.agent_id) is not None
                or self.graph.live_partner(target_id) is not None
            ):
                return GateFailure("capability", "courtship requires unpartnered agents")
        if (
            action.type == ActionType.DISSOLVE_UNION
            and self.graph.live_partner(actor.agent_id) != target_id
        ):
            return GateFailure("capability", "dissolution requires a live partner tie")
        return None

    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        if action.type != ActionType.COURT:
            return None
        target_id = _target(action)
        co_located = any(
            getattr(row, "agent_id", None) == target_id
            for row in getattr(ctx.observation, "co_located", ())
        )
        related = self.graph.strength(action.actor_id, target_id) > 0
        if not co_located and not related:
            return GateFailure(
                "locality",
                "courtship requires co-location or an existing relationship",
            )
        return None

    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        del action, ctx
        return None

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        del tick, ctx
        for row in stable(
            actions, key=lambda item: (item.action.actor_id, str(item.action.action_id))
        ):
            target_id = _target(row.action, row.validated_params)
            self.courtships.queue(row.action.type, row.action.actor_id, target_id)
        return ()

    def options_for(
        self, action_type: ActionType, ctx: ValidationContext
    ) -> tuple[Mapping[str, Any], ...]:
        actor_id = str(getattr(ctx.state, "agent_id", ""))
        if action_type == ActionType.HAVE_CHILD_INTENT:
            return ({},)
        actor = self.agents.agents.get(actor_id)
        if actor is None or not actor.alive:
            return ()
        partner_id = self.graph.live_partner(actor_id)
        if action_type == ActionType.DISSOLVE_UNION:
            if partner_id is None:
                return ()
            partner = self.agents.agents.get(partner_id)
            return () if partner is None or not partner.alive else ({"target_id": partner_id},)
        if actor.age_years < 18 or partner_id is not None:
            return ()
        actor_location = self.world.locations.get(actor_id)
        return tuple(
            {"target_id": agent.agent_id}
            for agent in self.agents.alive()
            if agent.agent_id != actor_id
            and agent.age_years >= 18
            and self.graph.live_partner(agent.agent_id) is None
            and (
                action_type != ActionType.COURT
                or self.graph.strength(actor_id, agent.agent_id) > 0
                or (
                    actor_location is not None
                    and (target_location := self.world.locations.get(agent.agent_id)) is not None
                    and target_location.place_id == actor_location.place_id
                )
            )
        )


@dataclass(frozen=True, slots=True)
class Pregnancy:
    mother_id: str
    father_id: str
    conceived_tick: int
    due_tick: int
    hazard: float
    draw: float


class Fertility:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        agents: AgentPopulation,
        households: HouseholdRegistry,
        graph: SocialGraphPort,
        beliefs: BeliefPriorPort,
        runtime: RuntimeOverlay,
        cfg: DemographySettings,
        demographic_acceleration: float,
        heritability_beliefs: float,
        hazard_mode: str,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.agents = agents
        self.households = households
        self.graph = graph
        self.beliefs = beliefs
        self.runtime = runtime
        self.cfg = cfg
        self.demographic_acceleration = demographic_acceleration
        self.heritability_beliefs = heritability_beliefs
        self.hazard_mode = hazard_mode
        self.pregnancies: dict[str, Pregnancy] = {}
        self._income_cache_tick: int | None = None
        self._income_distribution: tuple[int, ...] = ()

    @mechanism(
        "fertility_hazard",
        entails=FERTILITY_ENTAILS,
        config_key="mechanisms.fertility_hazard",
    )
    def hazard(self, mother: AgentState, tick: int) -> float:
        lo, hi = self.cfg.fertility.band
        if not mother.alive or not lo <= mother.age_years <= hi:
            return 0.0
        distance = (mother.age_years - self.cfg.fertility.peak_age) / 7.0
        base = 0.00035 * math.exp(-0.5 * distance * distance) * self.demographic_acceleration
        household = self.households.of(mother.agent_id)
        income = (
            0 if household is None else self.households.income_cents(household.household_id, tick)
        )
        if self._income_cache_tick != tick:
            self._income_distribution = tuple(
                sorted(
                    self.households.income_cents(row.household_id, tick)
                    for row in self.households.households.values()
                    if row.dissolved_at_tick is None
                )
            )
            self._income_cache_tick = tick
        incomes = self._income_distribution
        rank = sum(value < income for value in incomes)
        q = 0.5 if not incomes else rank / max(1, len(incomes) - 1)
        income_multiplier = (
            1.0
            if self.hazard_mode == "uniform"
            else self.cfg.fertility.kappa_income["a"] + self.cfg.fertility.kappa_income["b"] * q
        )
        partner = self.graph.live_partner(mother.agent_id)
        partner_multiplier = 1.0 if partner is not None else self.cfg.fertility.phi_single
        parity = sum(
            agent.alive
            and (agent.mother_id == mother.agent_id or agent.father_id == mother.agent_id)
            for agent in self.agents
        )
        parity_multiplier = self.cfg.fertility.kappa_parity[min(parity, 5)]
        window = self.cfg.fertility.intent_window_sim_days * self.clock.profile.ticks_per_sim_day
        intent = (
            mother.fertility_intent_tick is not None
            and tick - mother.fertility_intent_tick <= window
        )
        intent_multiplier = 1.0 + self.cfg.fertility.iota_intent * int(intent)
        benefit = self.runtime.cents("welfare.child_benefit_cents", tick)
        median_wage = 3_600_000
        policy_multiplier = (
            1.0
            if self.hazard_mode == "uniform"
            else 1.0 + self.cfg.fertility.psi_child_benefit * benefit / max(1, median_wage)
        )
        health_multiplier = max(0.0, min(1.0, mother.health))
        housing_multiplier = (
            self.cfg.fertility.kappa_housing_penalty
            if household is None or not self.households.spare_capacity(household.household_id)
            else 1.0
        )
        return max(
            0.0,
            min(
                1.0,
                base
                * income_multiplier
                * partner_multiplier
                * parity_multiplier
                * intent_multiplier
                * policy_multiplier
                * health_multiplier
                * housing_multiplier,
            ),
        )

    def draw(self, mother: AgentState, tick: int) -> bool:
        hazard = self.hazard(mother, tick)
        draw = self.rng.get("demog.conception", mother.agent_id, tick).random()
        return draw < hazard / self.clock.profile.ticks_per_sim_day

    def scan(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for mother in stable(self.agents.alive(), key=lambda agent: agent.agent_id):
            if mother.agent_id in self.pregnancies:
                continue
            father_id = self.graph.live_partner(mother.agent_id)
            if father_id is None or father_id <= mother.agent_id:
                continue
            hazard = self.hazard(mother, tick)
            draw = self.rng.get("demog.conception", mother.agent_id, tick).random()
            if draw < hazard / self.clock.profile.ticks_per_sim_day:
                events.extend(
                    self.conceive(
                        mother.agent_id,
                        father_id,
                        tick,
                        hazard=hazard,
                        draw=draw,
                    )
                )
        return tuple(events)

    def conceive(
        self,
        mother_id: str,
        father_id: str,
        tick: int,
        *,
        hazard: float | None = None,
        draw: float | None = None,
    ) -> Sequence[Event]:
        if mother_id in self.pregnancies:
            return ()
        resolved_hazard = self.hazard(self.agents[mother_id], tick) if hazard is None else hazard
        resolved_draw = (
            self.rng.get("demog.conception", mother_id, tick).random() if draw is None else draw
        )
        due_tick = (
            tick + self.cfg.fertility.gestation_sim_days * self.clock.profile.ticks_per_sim_day
        )
        self.pregnancies[mother_id] = Pregnancy(
            mother_id,
            father_id,
            tick,
            due_tick,
            resolved_hazard,
            resolved_draw,
        )
        return (
            _stage(
                self.log,
                self.clock,
                tick,
                CONCEPTION,
                {
                    "mother_id": mother_id,
                    "father_id": father_id,
                    "due_tick": due_tick,
                    "hazard": resolved_hazard,
                    "draw": resolved_draw,
                },
                actor_id=mother_id,
                subjects=(mother_id, father_id),
            ),
        )

    def advance(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for mother_id, pregnancy in sorted(tuple(self.pregnancies.items())):
            if pregnancy.due_tick > tick:
                continue
            loss_draw = self.rng.get(
                "demog.pregnancy_loss", mother_id, pregnancy.conceived_tick
            ).random()
            if loss_draw < self.cfg.fertility.loss_base:
                events.append(
                    _stage(
                        self.log,
                        self.clock,
                        tick,
                        PREGNANCY_ENDED,
                        {
                            "mother_id": mother_id,
                            "outcome": "loss",
                            "child_id": None,
                            "gestation_ticks": tick - pregnancy.conceived_tick,
                        },
                        actor_id=mother_id,
                        subjects=(mother_id, pregnancy.father_id),
                    )
                )
            else:
                events.extend(self._birth(pregnancy, tick))
            del self.pregnancies[mother_id]
        return tuple(events)

    def terminate_for_parent(self, parent_id: str, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for mother_id, pregnancy in sorted(tuple(self.pregnancies.items())):
            if parent_id not in {pregnancy.mother_id, pregnancy.father_id}:
                continue
            events.append(
                _stage(
                    self.log,
                    self.clock,
                    tick,
                    PREGNANCY_ENDED,
                    {
                        "mother_id": mother_id,
                        "outcome": "loss",
                        "child_id": None,
                        "gestation_ticks": tick - pregnancy.conceived_tick,
                    },
                    actor_id=mother_id,
                    subjects=(pregnancy.mother_id, pregnancy.father_id),
                )
            )
            del self.pregnancies[mother_id]
        return tuple(events)

    def _birth(self, pregnancy: Pregnancy, tick: int) -> Sequence[Event]:
        mother = self.agents[pregnancy.mother_id]
        father = self.agents[pregnancy.father_id]
        child_id = _numeric_agent_id(
            "demography.birth",
            pregnancy.mother_id,
            pregnancy.father_id,
            pregnancy.conceived_tick,
        )
        traits = inherit_traits(mother, father, self.rng, child_id)
        skills = {
            skill: round(
                max(
                    0.0,
                    min(
                        1.0,
                        (mother.skills[skill] + father.skills[skill]) / 4.0,
                    ),
                ),
                6,
            )
            for skill in SKILLS
        }
        household = self.households.of(mother.agent_id)
        home_place_id = mother.home_place_id if household is None else household.home_place_id
        child = AgentState(
            agent_id=child_id,
            display_name=f"Child {child_id[-6:]}",
            age_years=0.0,
            traits=traits,
            needs=Needs(),
            skills=skills,
            home_place_id=home_place_id,
            education_level="none",
            employment_status="child",
            reflex_profile=derive_reflex_profile(traits),
            born_tick=tick,
            household_id=None if household is None else household.household_id,
            mother_id=mother.agent_id,
            father_id=father.agent_id,
            generation=max(mother.generation, father.generation) + 1,
        )
        self.agents.add(child)
        if self.households.ledger is not None:
            self.households.ledger.ensure_agent_account(child_id, tick)
        place = self.world.place(home_place_id)
        self.world.locations[child_id] = Location(
            place.place_id,
            place.district_id,
            place.x,
            place.y,
        )
        self.world.freeze_occupancy()
        household_events: Sequence[Event] = ()
        if household is not None:
            household_events = (
                self.households.join(
                    child_id,
                    household.household_id,
                    "birth",
                    tick,
                ),
            )
        mother.needs.social = min(1.0, mother.needs.social + 0.1)
        father.needs.social = min(1.0, father.needs.social + 0.1)
        ended = _stage(
            self.log,
            self.clock,
            tick,
            PREGNANCY_ENDED,
            {
                "mother_id": mother.agent_id,
                "outcome": "birth",
                "child_id": child_id,
                "gestation_ticks": tick - pregnancy.conceived_tick,
            },
            actor_id=mother.agent_id,
            subjects=(mother.agent_id, father.agent_id, child_id),
        )
        born = _stage(
            self.log,
            self.clock,
            tick,
            AGENT_BORN,
            {
                "agent_id": child_id,
                "display_name": child.display_name,
                "age_years": child.age_years,
                "born_tick": tick,
                "home_place_id": home_place_id,
                "household_id": child.household_id,
                "mother_id": mother.agent_id,
                "father_id": father.agent_id,
                "generation": child.generation,
                "traits": traits.as_dict(),
                "education_level": child.education_level,
            },
            actor_id=child_id,
            subjects=(mother.agent_id, father.agent_id, child_id),
        )
        priors = self.beliefs.priors_at_birth(
            child_id,
            mother.agent_id,
            father.agent_id,
        )
        if any(proposition.startswith("fact.") for proposition, _, _ in priors):
            raise RuntimeError("birth priors may not contain fact propositions")
        self.beliefs.apply_priors(
            child_id,
            priors,
            tick=tick,
            source_ref=f"parents:{mother.agent_id},{father.agent_id}",
        )
        inherited = _stage(
            self.log,
            self.clock,
            tick,
            BELIEF_PRIORS_INHERITED,
            {
                "child_id": child_id,
                "mother_id": mother.agent_id,
                "father_id": father.agent_id,
                "heritability_beliefs": self.heritability_beliefs,
                "propositions": [
                    {
                        "proposition": proposition,
                        "value": value,
                        "confidence": confidence,
                    }
                    for proposition, value, confidence in priors
                ],
            },
            subjects=(mother.agent_id, father.agent_id, child_id),
        )
        tie_events = tuple(
            event
            for event in (
                self.graph.form(mother.agent_id, child_id, "kin", "birth", tick),
                self.graph.form(father.agent_id, child_id, "kin", "birth", tick),
            )
            if event is not None
        )
        return (ended, born, *household_events, inherited, *tie_events)


class ChildCosts:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        agents: AgentPopulation,
        households: HouseholdRegistry,
        ledger: LedgerReadPort | None,
        runtime: RuntimeOverlay,
        cfg: DemographySettings,
        supplier_account: Callable[[int], str | None] | None = None,
        government_account: Callable[[int], str | None] | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.agents = agents
        self.households = households
        self.ledger = ledger
        self.runtime = runtime
        self.cfg = cfg
        self.supplier_account = supplier_account
        self.government_account = government_account

    def charge(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        state_household_id, _state_place_id = self.households.state_household_identity()
        for household in stable(
            (row for row in self.households.households.values() if row.dissolved_at_tick is None),
            key=lambda row: row.household_id,
        ):
            children = tuple(
                sorted(
                    agent_id
                    for agent_id in household.member_ids
                    if self.agents[agent_id].alive and self.agents[agent_id].age_years < 18
                )
            )
            if not children:
                continue
            amount = sum(self._daily_cost(self.agents[child_id]) for child_id in children)
            benefit_monthly = self.runtime.cents(
                "welfare.child_benefit_cents",
                tick,
            )
            benefit = min(
                amount,
                benefit_monthly * len(children) // self.clock.profile.days_per_sim_month,
            )
            due = max(0, amount - benefit)
            paid = 0
            txn_id: str | None = None
            payment_legs: list[Any] = []
            head = household.head_agent_id
            supplier = None if self.supplier_account is None else self.supplier_account(tick)
            government = None if self.government_account is None else self.government_account(tick)
            state_care = household.household_id == state_household_id
            household_due = 0 if state_care else due
            government_due = benefit + (due if state_care else 0)
            if self.ledger is not None and supplier is not None:
                if head is not None:
                    accounts = self.ledger.accounts_of(head)
                    if accounts:
                        paid = min(household_due, self.ledger.liquid(head))
                        if paid:
                            payment_legs.extend(
                                self.ledger.transfer(
                                    accounts[0],
                                    supplier,
                                    paid,
                                    "purchase",
                                )
                            )
                if government_due and government is not None:
                    payment_legs.extend(
                        self.ledger.government_transfer(
                            supplier,
                            government_due,
                        )
                    )
                if payment_legs:
                    txn_id = str(self.ledger.next_txn_id(tick))
            arrears = household.arrears_cents + household_due - paid
            updated = self.households.update_arrears(household.household_id, arrears)
            charged = _stage(
                self.log,
                self.clock,
                tick,
                CHILD_COST_CHARGED,
                {
                    "household_id": household.household_id,
                    "child_ids": list(children),
                    "amount_cents": due,
                    "benefit_offset_cents": benefit,
                    "txn_id": txn_id,
                    "arrears_cents": updated.arrears_cents,
                },
                actor_id=head,
                subjects=(household.household_id, *children),
            )
            if payment_legs and self.ledger is not None:
                actual = str(
                    self.ledger.post_transaction(
                        payment_legs,
                        tick=tick,
                        cause=charged,
                        allow_negative=(
                            frozenset({government})
                            if government_due and government is not None
                            else frozenset()
                        ),
                    )
                )
                if actual != txn_id:
                    raise RuntimeError("child-cost transaction ordinal diverged")
                if government_due:
                    self.ledger.record_government_spending(government_due, tick)
            events.append(charged)
            if updated.arrears_cents > (self.cfg.child.arrears_tolerance_sim_days * max(1, amount)):
                for child_id in children:
                    child = self.agents[child_id]
                    child.health = max(0.0, child.health - 0.002)
                    if child.health < self.cfg.child.welfare_threshold_health:
                        events.extend(self.state_intervention(child_id, tick))
        return tuple(events)

    def _daily_cost(self, child: AgentState) -> int:
        stage = stage_for_age(child.age_years)
        multiplier = self.cfg.child.age_multiplier.get(stage, 0.0)
        return round(self.cfg.child.base_cost_cents_per_sim_day * multiplier)

    def arrears(self, household_id: str) -> int:
        return self.households.households[household_id].arrears_cents

    def state_intervention(self, child_id: str, tick: int) -> Sequence[Event]:
        prior = self.households.of(child_id)
        state = self.households.state_household(tick)
        joined = self.households.join(child_id, state.household_id, "state_care", tick)
        event = _stage(
            self.log,
            self.clock,
            tick,
            STATE_CARE_STARTED,
            {
                "child_id": child_id,
                "from_household_id": None if prior is None else prior.household_id,
                "to_household_id": state.household_id,
                "reason": "welfare_threshold",
                "cost_cents": self.cfg.child.base_cost_cents_per_sim_day,
            },
            subjects=(child_id, state.household_id),
        )
        return (joined, event)


@dataclass(frozen=True, slots=True)
class Estate:
    decedent_id: str
    escrow_account_id: str
    gross_cents: int
    debts_cents: int
    written_off_cents: int
    tax_cents: int
    distributable_cents: int
    heirs: tuple[tuple[str, int], ...]
    escheated_cents: int


@dataclass(slots=True)
class DeathSettlementContext:
    rollback_actions: list[Callable[[], None]] = field(default_factory=list)
    cause: str | None = None
    cause_event: Event | None = None
    paid_cents: int = 0
    written_off_cents: int = 0
    creditors: list[Mapping[str, object]] = field(default_factory=list)
    tax_cents: int = 0
    distributable_cents: int = 0
    escheated_cents: int = 0
    heirs: list[tuple[str, int]] = field(default_factory=list)
    txn_ids: list[str] = field(default_factory=list)
    residual_cents: int = 0

    def add_rollback(self, action: Callable[[], None]) -> None:
        self.rollback_actions.append(action)

    def rollback(self) -> None:
        for action in reversed(self.rollback_actions):
            action()

    def commit(self) -> None:
        self.rollback_actions.clear()


class EstateSettler:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        agents: AgentPopulation,
        households: HouseholdRegistry,
        estate: EstatePort,
        ledger: LedgerReadPort,
        housing: HousingPort,
        graph: SocialGraphPort,
        memories: MemoryArchivePort,
        fertility: Fertility,
        cfg: DemographySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.agents = agents
        self.households = households
        self.estate = estate
        self.ledger = ledger
        self.housing = housing
        self.graph = graph
        self.memories = memories
        self.fertility = fertility
        self.cfg = cfg
        self._wealth_cache_tick: int | None = None
        self._wealth_distribution: tuple[int, ...] = ()

    @mechanism(
        "mortality_hazard",
        entails=MORTALITY_ENTAILS,
        config_key="mechanisms.mortality_hazard",
    )
    def mortality_hazard(self, agent: AgentState, tick: int) -> float:
        if not agent.alive:
            return 0.0
        age_component = 0.000001 + 0.0000002 * math.exp(min(12.0, 0.095 * agent.age_years))
        wealths = self._wealths_at(tick)
        wealth_rank = sum(value < agent.wealth_cents for value in wealths)
        wealth_pct = 0.5 if len(wealths) < 2 else wealth_rank / (len(wealths) - 1)
        health_component = 2.0 - max(0.0, min(1.0, agent.health))
        wealth_component = 1.25 - 0.5 * wealth_pct
        district_crime = self._district_crime(agent)
        return min(
            0.25,
            age_component * health_component * wealth_component * (1.0 + district_crime),
        )

    def _wealths_at(self, tick: int) -> tuple[int, ...]:
        if self._wealth_cache_tick != tick:
            self._wealth_distribution = tuple(
                sorted(row.wealth_cents for row in self.agents.alive())
            )
            self._wealth_cache_tick = tick
        return self._wealth_distribution

    def _district_crime(self, agent: AgentState) -> float:
        location = self.world.locations.get(agent.agent_id)
        if location is None:
            return 0.0
        district = next(
            (row for row in self.world.districts if row.district_id == location.district_id),
            None,
        )
        return 0.0 if district is None else float(getattr(district, "crime_rate", 0.0))

    def mortality_draw(self, agent: AgentState, tick: int) -> tuple[float, float]:
        hazard = self.mortality_hazard(agent, tick)
        draw = self.rng.get("demog.mortality", agent.agent_id, tick).random()
        _stage(
            self.log,
            self.clock,
            tick,
            MORTALITY_HAZARD_DRAWN,
            {
                "agent_id": agent.agent_id,
                "hazard": hazard,
                "draw": draw,
                "components": {
                    "age": agent.age_years,
                    "health": agent.health,
                    "wealth_pct": self._wealth_percentile(agent, tick),
                    "district_crime": self._district_crime(agent),
                },
                "routed_mode": agent.cognition_mode,
            },
            actor_id=agent.agent_id,
            subjects=(agent.agent_id,),
        )
        return hazard, draw

    def _wealth_percentile(self, agent: AgentState, tick: int) -> float:
        wealths = self._wealths_at(tick)
        if len(wealths) < 2:
            return 0.5
        return sum(value < agent.wealth_cents for value in wealths) / (len(wealths) - 1)

    def settle(self, decedent_id: str, cause: str, tick: int) -> tuple[Estate, Sequence[Event]]:
        agent = self.agents[decedent_id]
        if not agent.alive:
            raise ValueError("agent is already dead")
        savepoint = self.log.savepoint()
        agent_snapshot = copy.deepcopy(agent)
        household_snapshot = self.households.dump()
        location_snapshot = copy.deepcopy(self.world.locations.get(decedent_id))
        graph_snapshot = _snapshot_object(self.graph)
        memory_snapshot = _snapshot_object(self.memories)
        pregnancy_snapshot = copy.deepcopy(self.fertility.pregnancies)
        settlement = DeathSettlementContext(cause=cause)
        settlement.add_rollback(lambda: self._restore_agent(decedent_id, agent_snapshot))
        settlement.add_rollback(lambda: self.households.load(household_snapshot))
        settlement.add_rollback(lambda: self._restore_location(decedent_id, location_snapshot))
        settlement.add_rollback(lambda: _restore_object(self.graph, graph_snapshot))
        settlement.add_rollback(lambda: _restore_object(self.memories, memory_snapshot))
        settlement.add_rollback(lambda: self._restore_pregnancies(pregnancy_snapshot))
        try:
            agent.alive = False
            agent.died_at_tick = tick
            agent.death_cause = cause
            agent.employment_status = "dead"
            household = self.households.of(decedent_id)
            dependants = self._dependants(decedent_id)
            gross = max(0, self.estate.gross_cents(decedent_id))
            heirs = () if cause == "emigrated" else self.intestacy_shares(decedent_id, gross)
            case = self.estate.case_for(decedent_id, tick)
            escrow = self.estate.estate_account_id(decedent_id, tick)
            opened = _stage(
                self.log,
                self.clock,
                tick,
                ESTATE_OPENED,
                {
                    "decedent_id": decedent_id,
                    "escrow_account_id": escrow,
                    "gross_cents": gross,
                    "open_orders": self.estate.open_order_count(decedent_id),
                    "open_loans": self.estate.open_loan_count(decedent_id),
                    "dependants": list(dependants),
                    "case": case,
                },
                subjects=(decedent_id, *dependants),
            )
            settlement.cause_event = opened
            pregnancy_events = self.fertility.terminate_for_parent(decedent_id, tick)
            delegated = tuple(
                self.estate.settle_death(
                    decedent_id,
                    tick,
                    heirs=heirs,
                    ctx=settlement,
                )
            )
            debts = _stage(
                self.log,
                self.clock,
                tick,
                ESTATE_DEBTS_SETTLED,
                {
                    "decedent_id": decedent_id,
                    "paid_cents": settlement.paid_cents,
                    "written_off_cents": settlement.written_off_cents,
                    "creditors": list(settlement.creditors),
                    "txn_ids": list(settlement.txn_ids),
                },
                subjects=(decedent_id,),
            )
            distributed = _stage(
                self.log,
                self.clock,
                tick,
                ESTATE_DISTRIBUTED,
                {
                    "decedent_id": decedent_id,
                    "tax_cents": settlement.tax_cents,
                    "distributable_cents": settlement.distributable_cents,
                    "heirs": [
                        {"heir_id": heir_id, "cents": cents} for heir_id, cents in settlement.heirs
                    ],
                    "escheated_cents": settlement.escheated_cents,
                    "txn_ids": list(settlement.txn_ids),
                },
                subjects=(decedent_id, *(heir_id for heir_id, _ in heirs)),
            )
            self.housing.vacate(decedent_id, tick)
            household_events: list[Event] = []
            if household is not None:
                household_events.append(
                    self.households.leave(
                        decedent_id,
                        "death" if cause != "emigrated" else "emigration",
                        tick,
                    )
                )
                remaining = self.households.households[household.household_id]
                if not remaining.member_ids:
                    household_events.extend(
                        self.households.dissolve(
                            household.household_id,
                            "death" if cause != "emigrated" else "emigration",
                            tick,
                        )
                    )
                for dependant_id in dependants:
                    if self.agents[dependant_id].alive and self.households.of(dependant_id) is None:
                        state = self.households.state_household(tick)
                        household_events.append(
                            self.households.join(
                                dependant_id,
                                state.household_id,
                                "death",
                                tick,
                            )
                        )
            self.memories.archive_agent(decedent_id, tick)
            tie_events = tuple(
                self.graph.end_all_for(
                    decedent_id,
                    "emigration" if cause == "emigrated" else "death",
                    tick,
                )
            )
            bereavement_events = () if cause == "emigrated" else self.bereave(decedent_id, tick)
            if settlement.residual_cents != 0:
                raise RuntimeError("estate escrow did not close at zero")
            closed = _stage(
                self.log,
                self.clock,
                tick,
                ESTATE_CLOSED,
                {
                    "decedent_id": decedent_id,
                    "escrow_account_id": escrow,
                    "residual_cents": 0,
                    "steps_completed": 8,
                    "total_txn_ids": list(settlement.txn_ids),
                },
                subjects=(decedent_id,),
            )
            died = _stage(
                self.log,
                self.clock,
                tick,
                AGENT_DIED,
                {
                    "agent_id": decedent_id,
                    "cause": cause,
                    "age_years": agent.age_years,
                    "estate_value_cents": gross,
                    "debts_cents": settlement.paid_cents,
                    "written_off_cents": settlement.written_off_cents,
                    "tax_cents": settlement.tax_cents,
                    "heirs": [
                        {"heir_id": heir_id, "cents": cents} for heir_id, cents in settlement.heirs
                    ],
                    "escheated_cents": settlement.escheated_cents,
                    "txn_ids": list(settlement.txn_ids),
                    "case": case,
                    "household_id": None if household is None else household.household_id,
                    "dependants": list(dependants),
                    "obituary_eligible": cause != "emigrated",
                },
                actor_id=decedent_id,
                subjects=(decedent_id, *dependants),
            )
            settlement.commit()
            estate = Estate(
                decedent_id,
                escrow,
                gross,
                settlement.paid_cents,
                settlement.written_off_cents,
                settlement.tax_cents,
                settlement.distributable_cents,
                tuple(settlement.heirs),
                settlement.escheated_cents,
            )
            return estate, (
                opened,
                *pregnancy_events,
                *delegated,
                debts,
                distributed,
                *household_events,
                *tie_events,
                *bereavement_events,
                closed,
                died,
            )
        except Exception:
            settlement.rollback()
            self.log.rollback_to(savepoint)
            raise

    def _restore_agent(self, agent_id: str, snapshot: AgentState) -> None:
        self.agents.agents[agent_id] = copy.deepcopy(snapshot)

    def _restore_location(self, agent_id: str, snapshot: Location | None) -> None:
        if snapshot is None:
            self.world.locations.pop(agent_id, None)
        else:
            self.world.locations[agent_id] = copy.deepcopy(snapshot)
        self.world.freeze_occupancy()

    def _restore_pregnancies(self, snapshot: Mapping[str, Pregnancy]) -> None:
        self.fertility.pregnancies = copy.deepcopy(dict(snapshot))

    def _dependants(self, agent_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                agent.agent_id
                for agent in self.agents.alive()
                if agent.age_years < 18
                and (agent.mother_id == agent_id or agent.father_id == agent_id)
            )
        )

    def intestacy_shares(
        self, decedent_id: str, distributable_cents: int
    ) -> tuple[tuple[str, int], ...]:
        if distributable_cents < 0:
            raise ValueError("distributable estate cannot be negative")
        partner = self.graph.live_partner(decedent_id)
        children = tuple(
            sorted(
                agent.agent_id
                for agent in self.agents.alive()
                if agent.mother_id == decedent_id or agent.father_id == decedent_id
            )
        )
        if partner is not None and children:
            weights = ((partner, len(children)), *((child, 1) for child in children))
        elif partner is not None:
            weights = ((partner, 1),)
        elif children:
            weights = tuple((child, 1) for child in children)
        else:
            agent = self.agents[decedent_id]
            parents = tuple(
                sorted(
                    parent_id
                    for parent_id in (agent.mother_id, agent.father_id)
                    if parent_id is not None
                    and parent_id in self.agents.agents
                    and self.agents[parent_id].alive
                )
            )
            if parents:
                weights = tuple((parent, 1) for parent in parents)
            else:
                siblings = tuple(
                    sorted(
                        row.agent_id
                        for row in self.agents.alive()
                        if row.agent_id != decedent_id
                        and (
                            (agent.mother_id is not None and row.mother_id == agent.mother_id)
                            or (agent.father_id is not None and row.father_id == agent.father_id)
                        )
                    )
                )
                weights = tuple((sibling, 1) for sibling in siblings)
        if not weights or distributable_cents == 0:
            return ()
        allocated = self.ledger.allocate(distributable_cents, weights)
        rows = tuple(sorted(allocated.items()))
        if sum(cents for _, cents in rows) != distributable_cents:
            raise RuntimeError("intestacy shares do not close to the distributable estate")
        return rows

    def bereave(self, decedent_id: str, tick: int) -> Sequence[Event]:
        bereaved = self.graph.strong_ties(
            decedent_id,
            self.cfg.bereavement.strong_tie_threshold,
        )
        for agent_id in bereaved:
            if agent_id not in self.agents.agents or not self.agents[agent_id].alive:
                continue
            agent = self.agents[agent_id]
            agent.health = max(0.0, agent.health + self.cfg.bereavement.health_delta)
            agent.needs.social = max(
                0.0,
                agent.needs.social + self.cfg.bereavement.social_need_delta,
            )
            agent.importance_since_reflection += 1.0
        if not bereaved:
            return ()
        return (
            _stage(
                self.log,
                self.clock,
                tick,
                BEREAVEMENT_APPLIED,
                {
                    "decedent_id": decedent_id,
                    "bereaved_ids": list(bereaved),
                    "health_delta": self.cfg.bereavement.health_delta,
                    "social_need_delta": self.cfg.bereavement.social_need_delta,
                    "salience_boost_ticks": self.cfg.bereavement.salience_boost_ticks,
                },
                subjects=(decedent_id, *bereaved),
            ),
        )


class Migration:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        agents: AgentPopulation,
        households: HouseholdRegistry,
        estate: EstateSettler,
        beliefs: BeliefPriorPort,
        runtime: RuntimeOverlay,
        housing: HousingPort,
        cfg: DemographySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.agents = agents
        self.households = households
        self.estate = estate
        self.beliefs = beliefs
        self.runtime = runtime
        self.housing = housing
        self.cfg = cfg
        self._arrival_carry = 0

    def arrive(self, tick: int) -> Sequence[Event]:
        quota = max(0, int(self.runtime.get("migration.quota_per_sim_year", tick)))
        self._arrival_carry += quota
        months_per_year = max(
            1,
            self.clock.profile.days_per_sim_year // self.clock.profile.days_per_sim_month,
        )
        count, self._arrival_carry = divmod(self._arrival_carry, months_per_year)
        if count == 0:
            return ()
        events: list[Event] = []
        cohort_id = det_id("coh", "demography.migration", tick)
        means = (
            population_mean_traits(self.agents)
            if self.agents.alive()
            else Traits(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        )
        for index in range(count):
            agent_id = _numeric_agent_id("demography.migrant", tick, index)
            if agent_id in self.agents.agents:
                continue
            stream = self.rng.numpy("demog.migrant", agent_id)
            traits = type(means)(
                **{
                    name: max(
                        0.0,
                        min(1.0, getattr(means, name) + float(stream.normal(0.0, 0.08))),
                    )
                    for name in means.__dataclass_fields__
                }
            )
            skill_premium = self.cfg.migration.origin_profile.skill_premium
            skills = {
                skill: round(
                    max(0.0, min(1.0, 0.35 + skill_premium + float(stream.normal(0, 0.08)))),
                    6,
                )
                for skill in SKILLS
            }
            wealth = max(
                0,
                self.cfg.migration.origin_profile.wealth_offset_cents,
            )
            home = self.housing.find_affordable_home(wealth, tick)
            if home is None:
                home = self.households.state_household(tick).home_place_id
            agent = AgentState(
                agent_id=agent_id,
                display_name=f"Newcomer {agent_id[-6:]}",
                age_years=float(stream.integers(18, 56)),
                traits=traits,
                needs=Needs(),
                skills=skills,
                home_place_id=home,
                education_level="secondary",
                employment_status="unemployed",
                reflex_profile=derive_reflex_profile(traits),
                wealth_cents=wealth,
                born_tick=tick,
            )
            self.agents.add(agent)
            if self.households.ledger is not None:
                self.households.ledger.ensure_agent_account(agent_id, tick)
            household, household_events = self.households.form(
                (agent_id,),
                tick,
                reason="migration",
            )
            priors = self.beliefs.priors_for_migrant(
                agent_id,
                self.cfg.migration.origin_profile.belief_offsets,
            )
            self.beliefs.apply_priors(
                agent_id,
                priors,
                tick=tick,
                source_ref=f"migration:{cohort_id}",
            )
            place = self.world.place(household.home_place_id)
            self.world.locations[agent_id] = Location(
                place.place_id,
                place.district_id,
                place.x,
                place.y,
            )
            self.world.freeze_occupancy()
            events.extend(household_events)
            events.append(
                _stage(
                    self.log,
                    self.clock,
                    tick,
                    MIGRATION_IN,
                    {
                        "agent_id": agent_id,
                        "cohort_id": cohort_id,
                        "origin_profile": self.cfg.migration.origin_profile.model_dump(),
                        "arrival_wealth_cents": wealth,
                        "skills": skills,
                        "belief_priors": [
                            {
                                "proposition": proposition,
                                "value": value,
                                "confidence": confidence,
                            }
                            for proposition, value, confidence in priors
                        ],
                        "home_place_id": household.home_place_id,
                    },
                    actor_id=agent_id,
                    subjects=(agent_id,),
                )
            )
        return tuple(events)

    @mechanism(
        "emigration_hazard",
        entails=EMIGRATION_ENTAILS,
        config_key="mechanisms.emigration_hazard",
    )
    def emigration_hazard(self, agent: AgentState, tick: int) -> float:
        del tick
        if not agent.alive:
            return 0.0
        wealth_penalty = 1.5 if agent.wealth_cents <= 0 else 0.75
        tie_penalty = 1.5 if not self.estate.graph.strong_ties(agent.agent_id, 0.25) else 0.7
        return min(
            1.0,
            self.cfg.migration.base_emig_per_sim_day * wealth_penalty * tie_penalty,
        )

    def scan_departures(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for agent in stable(self.agents.alive(), key=lambda row: row.agent_id):
            hazard = self.emigration_hazard(agent, tick)
            draw = self.rng.get("demog.emigration", agent.agent_id, tick).random()
            if draw < hazard / self.clock.profile.ticks_per_sim_day:
                events.extend(self.depart(agent.agent_id, tick))
        return tuple(events)

    def depart(self, agent_id: str, tick: int) -> Sequence[Event]:
        exit_wealth = max(0, self.estate.ledger.liquid(agent_id))
        strong_ties = self.estate.graph.strong_ties(
            agent_id,
            self.estate.cfg.bereavement.strong_tie_threshold,
        )
        estate, settlement_events = self.estate.settle(agent_id, "emigrated", tick)
        ties_severed = sum(event.kind == TIE_ENDED for event in settlement_events)
        event = _stage(
            self.log,
            self.clock,
            tick,
            MIGRATION_OUT,
            {
                "agent_id": agent_id,
                "hazard_components": {
                    "wealth_cents": self.agents[agent_id].wealth_cents,
                    "strong_ties": len(strong_ties),
                },
                "exit_wealth_cents": exit_wealth,
                "ties_severed": ties_severed,
                "debts_settled_cents": estate.debts_cents,
                "debts_defaulted_cents": estate.written_off_cents,
            },
            actor_id=agent_id,
            subjects=(agent_id,),
        )
        return (*settlement_events, event)


class DemographyInstitution:
    phase: Final[int] = 8

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        agents: AgentPopulation,
        households: HouseholdRegistry,
        courtships: CourtshipRegistry,
        estate: EstateSettler,
        fertility: Fertility,
        child_costs: ChildCosts,
        migration: Migration,
        cfg: DemographySettings,
        demographic_acceleration: float,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.agents = agents
        self.households = households
        self.courtships = courtships
        self.estate = estate
        self.fertility = fertility
        self.child_costs = child_costs
        self.migration = migration
        self.cfg = cfg
        self.demographic_acceleration = demographic_acceleration

    async def run(self, tick: int) -> Sequence[Event]:
        before = len(self.log.staged())
        # Fixed C20 order: partnering and household effects.
        self.courtships.process(tick)
        age_step_years = (
            self.demographic_acceleration
            / self.clock.profile.days_per_sim_year
            / self.clock.profile.ticks_per_sim_day
        )
        self.households.advance_independence(
            tick,
            age_step_years=age_step_years,
        )
        # Conception, gestation advance, and birth.
        self.fertility.scan(tick)
        self.fertility.advance(tick)
        # Daily child costs.
        if tick % self.clock.profile.ticks_per_sim_day == 0:
            self.child_costs.charge(tick)
        # Monthly migration in, then migration out.
        month_ticks = self.clock.profile.days_per_sim_month * self.clock.profile.ticks_per_sim_day
        if tick > 0 and tick % month_ticks == 0:
            self.migration.arrive(tick)
        self.migration.scan_departures(tick)
        # Ageing, mortality, and atomic death settlement are last.
        for agent in stable(self.agents.alive(), key=lambda row: row.agent_id):
            advance_age(
                agent,
                1 / self.clock.profile.ticks_per_sim_day,
                demographic_acceleration=self.demographic_acceleration,
                days_per_sim_year=self.clock.profile.days_per_sim_year,
            )
        for agent in stable(self.agents.alive(), key=lambda row: row.agent_id):
            hazard, draw = self.estate.mortality_draw(agent, tick)
            if draw < hazard / self.clock.profile.ticks_per_sim_day:
                self.estate.settle(agent.agent_id, "mortality", tick)
        return self.log.staged()[before:]
