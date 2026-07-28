from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from polis.agents.demography import (
    ChildCosts,
    CourtshipRegistry,
    DemographyInstitution,
    EstateSettler,
    Fertility,
    HouseholdRegistry,
    Migration,
    RelationalResolver,
)
from polis.agents.memory import MemoryStore
from polis.agents.state import AgentPopulation
from polis.config.runtime import RuntimeOverlay
from polis.config.settings import Settings
from polis.economy.estate import (
    EconomyEmploymentPort,
    EconomyEstatePort,
    EconomyLedgerPort,
)
from polis.economy.fiscal import treasury_account
from polis.economy.policy import MechanicalPolicy
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.rng import RngRegistry
from polis.society.beliefs import BeliefEngine, MemoryBeliefRepository
from polis.society.graph import MemoryGraphRepository, SocialGraph
from polis.world.api import World


@dataclass(slots=True)
class WorldHousingPort:
    world: World
    housing_burden: float

    def vacate(self, agent_id: str, tick: int) -> None:
        del tick
        self.world.locations.pop(agent_id, None)
        self.world.freeze_occupancy()

    def find_affordable_home(self, income_cents: int, tick: int) -> str | None:
        del tick
        homes = tuple(
            sorted(
                (
                    place
                    for place in self.world.places
                    if place.type in {"home", "shelter"}
                    and len(self.world.occupancy(place.place_id)) < place.capacity
                    and (
                        place.type == "shelter"
                        or place.rent_cents <= round(max(0, income_cents) * self.housing_burden)
                    )
                ),
                key=lambda place: (
                    place.type == "shelter",
                    place.rent_cents,
                    place.place_id,
                ),
            )
        )
        return None if not homes else homes[0].place_id


@dataclass(slots=True)
class DemographyGraphPort:
    graph: SocialGraph

    def end_all_for(self, agent_id: str, reason: str, tick: int) -> Sequence[Event]:
        return self.graph.end_all_for(agent_id, reason, tick)

    def end_pair(
        self,
        a_id: str,
        b_id: str,
        tie_type: str,
        reason: str,
        tick: int,
    ) -> Event | None:
        row = self.graph.tie(a_id, b_id, cast(Any, tie_type))
        return None if row is None else self.graph.end(row, reason, tick)

    def strong_ties(self, agent_id: str, threshold: float) -> tuple[str, ...]:
        return self.graph.strong_ties(agent_id, threshold)

    def strength(self, a_id: str, b_id: str) -> float:
        return self.graph.strength(a_id, b_id)

    def form(
        self,
        a_id: str,
        b_id: str,
        tie_type: Literal[
            "kin",
            "partner",
            "friend",
            "colleague",
            "rival",
            "creditor",
            "acquaintance",
        ],
        origin: str,
        tick: int,
    ) -> Event | None:
        return self.graph.form(a_id, b_id, tie_type, origin, tick)

    def live_partner(self, agent_id: str) -> str | None:
        return self.graph.live_partner(agent_id)

    def dump(self) -> Mapping[str, Any]:
        return {"ties": copy.deepcopy(self.graph.repo.all())}

    def load(self, state: Mapping[str, object]) -> None:
        self.graph.repo = MemoryGraphRepository(copy.deepcopy(cast(Sequence[Any], state["ties"])))


@dataclass(slots=True)
class DemographyRuntime:
    households: HouseholdRegistry
    courtships: CourtshipRegistry
    resolver: RelationalResolver
    institution: DemographyInstitution
    graph: SocialGraph
    beliefs: BeliefEngine


def build_demography_runtime(
    *,
    settings: Settings,
    log: EventLog,
    clock: Clock,
    rng: RngRegistry,
    world: World,
    population: AgentPopulation,
    memory: MemoryStore,
    runtime: RuntimeOverlay,
    economy_policy: MechanicalPolicy,
) -> DemographyRuntime:
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=rng,
        repo=MemoryGraphRepository(),
        cfg=settings.society,
    )
    graph_port = DemographyGraphPort(graph)
    beliefs = BeliefEngine(
        log=log,
        clock=clock,
        rng=rng,
        repo=MemoryBeliefRepository(),
        graph=graph,
        cfg=settings.society,
        belief_cfg=settings.beliefs,
    )

    def emit_at(tick: int, draft: NewEvent) -> Event:
        return log.stage(draft, tick=tick, sim_time=clock.sim_time_at(tick))

    economy = economy_policy.economy
    ledger = EconomyLedgerPort(economy, settings, population, emit_at)
    employment = EconomyEmploymentPort(economy)
    housing = WorldHousingPort(world, settings.demography.housing_burden)
    households = HouseholdRegistry(
        log=log,
        clock=clock,
        world=world,
        agents=population,
        housing=housing,
        ledger=ledger,
        employment=employment,
        cfg=settings.demography,
        rng=rng,
        custody_mode=settings.mechanisms.get("custody_default", "higher_income"),
    )
    households.bootstrap()
    courtships = CourtshipRegistry(
        log=log,
        clock=clock,
        rng=rng,
        world=world,
        agents=population,
        households=households,
        graph=graph_port,
        cfg=settings.demography,
    )

    estate_port = EconomyEstatePort(
        settings=settings,
        runtime=runtime,
        economy=economy,
        exchange=economy_policy.exchange,
        credit=economy_policy.banking.credit_context,
        emit_at=emit_at,
    )
    estate = EstateSettler(
        log=log,
        clock=clock,
        rng=rng,
        world=world,
        agents=population,
        households=households,
        estate=estate_port,
        ledger=ledger,
        housing=housing,
        graph=graph_port,
        memories=memory,
        cfg=settings.demography,
    )
    fertility = Fertility(
        log=log,
        clock=clock,
        rng=rng,
        world=world,
        agents=population,
        households=households,
        graph=graph_port,
        beliefs=beliefs,
        runtime=runtime,
        cfg=settings.demography,
        demographic_acceleration=settings.clock.demographic_acceleration,
        heritability_beliefs=settings.beliefs.heritability_beliefs,
        hazard_mode=settings.mechanisms.get("fertility_hazard", "income_conditional"),
    )

    def child_supplier(_tick: int) -> str | None:
        firm = next(
            (
                row
                for row in sorted(economy.firms.values(), key=lambda item: item.firm_id)
                if row.status == "active"
            ),
            None,
        )
        return None if firm is None else firm.ledger_account_id

    child_costs = ChildCosts(
        log=log,
        clock=clock,
        agents=population,
        households=households,
        ledger=ledger,
        runtime=runtime,
        cfg=settings.demography,
        supplier_account=child_supplier,
        government_account=lambda _tick: treasury_account(economy),
    )
    migration = Migration(
        log=log,
        clock=clock,
        rng=rng,
        world=world,
        agents=population,
        households=households,
        estate=estate,
        beliefs=beliefs,
        runtime=runtime,
        housing=housing,
        cfg=settings.demography,
    )
    institution = DemographyInstitution(
        log=log,
        clock=clock,
        rng=rng,
        world=world,
        agents=population,
        households=households,
        courtships=courtships,
        estate=estate,
        fertility=fertility,
        child_costs=child_costs,
        migration=migration,
        cfg=settings.demography,
        demographic_acceleration=settings.clock.demographic_acceleration,
    )
    resolver = RelationalResolver(
        log=log,
        clock=clock,
        rng=rng,
        world=world,
        households=households,
        courtships=courtships,
        graph=graph_port,
        cfg=settings.demography,
        agents=population,
    )
    return DemographyRuntime(
        households,
        courtships,
        resolver,
        institution,
        graph,
        beliefs,
    )
