from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from polis.agents.actions.types import Action, ActionType, make_action
from polis.agents.state import AgentPopulation
from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.firms import FirmEngine
from polis.economy.labour import (
    LabourMarket,
    Occupation,
    active_employment,
    match_score_bp,
    visibility_slice,
)
from polis.economy.state import EconomyState, FirmState
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.world.api import World

Emit = Callable[[NewEvent], Event]


@mechanism(
    "labour.vacancy_autopost",
    entails=(
        "An active firm below its target headcount posts a vacancy after a bounded "
        "fallback delay; this guarantees vacancy creation but not a match."
    ),
    config_key="mechanisms.labour_vacancy_autopost",
)
def should_autopost(
    firm: FirmState,
    economy: EconomyState,
    *,
    max_open_vacancies: int,
) -> bool:
    open_vacancies = [
        row
        for row in economy.vacancies.values()
        if row.firm_id == firm.firm_id and row.status == "open" and row.headcount > 0
    ]
    committed_headcount = firm.headcount + sum(row.headcount for row in open_vacancies)
    return (
        firm.status == "active"
        and committed_headcount < firm.target_headcount
        and len(open_vacancies) < max_open_vacancies
    )


class MechanicalPolicy:
    """Explicit classical-ABM decisions used by the reflex-only research baseline."""

    def __init__(
        self,
        settings: Settings,
        population: AgentPopulation,
        world: World,
        economy: EconomyState,
        rng: RngRegistry,
        occupations: Mapping[str, Occupation],
    ) -> None:
        self.settings = settings
        self.population = population
        self.world = world
        self.economy = economy
        self.rng = rng
        self.occupations = occupations
        self.labour = LabourMarket(settings, population, world, economy, rng, occupations)
        self.firms = FirmEngine(settings, economy, rng)

    def step(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = list(self.labour.expire(tick, emit))
        first_actions = self._first_actions(tick)
        events.extend(self.labour.resolve(first_actions, tick, emit))
        shortlisted = self.labour.screen_pending(tick, emit)
        offer_actions = self._offer_actions(shortlisted, tick)
        events.extend(self.labour.resolve(offer_actions, tick, emit))
        events.extend(self.labour.run_payroll(tick, emit))
        events.extend(self.labour.decay_unused_skills(tick, emit))
        events.extend(self.firms.run_daily(tick, emit))
        events.append(self.labour.emit_summary(tick, emit))
        self.economy.sync_denormalised(self.population)
        self.economy.ledger.commit_tick(tick)
        return tuple(events)

    def _first_actions(self, tick: int) -> tuple[Action, ...]:
        actions: list[Action] = []
        ordinals: dict[str, int] = {}

        for employment in sorted(
            self.economy.employments.values(),
            key=lambda row: row.employment_id,
        ):
            if (
                employment.started_tick <= tick
                and employment.ended_tick is None
                and self.population[employment.agent_id].alive
            ):
                actions.append(
                    self._action(
                        employment.agent_id,
                        tick,
                        ActionType.WORK,
                        {
                            "employment_id": employment.employment_id,
                            "effort_bp": 10_000,
                        },
                        ordinals,
                    )
                )

        offers_by_agent: dict[str, list[object]] = {}
        for offer in self.economy.offers.values():
            if offer.status == "open" and offer.made_tick < tick and offer.expires_tick >= tick:
                offers_by_agent.setdefault(offer.agent_id, []).append(offer)
        for agent_id, raw_offers in sorted(offers_by_agent.items()):
            if active_employment(self.economy, agent_id, tick) is not None:
                continue
            offers = sorted(
                raw_offers,
                key=lambda row: (-row.wage_cents, row.offer_id),  # type: ignore[attr-defined]
            )
            chosen = offers[0]
            actions.append(
                self._action(
                    agent_id,
                    tick,
                    ActionType.ACCEPT_OFFER,
                    {"offer_id": chosen.offer_id},  # type: ignore[attr-defined]
                    ordinals,
                )
            )

        if self.settings.mechanisms.get("labour_vacancy_autopost", "on") != "off":
            for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
                if not should_autopost(
                    firm,
                    self.economy,
                    max_open_vacancies=self.settings.labour.max_open_vacancies_per_firm,
                ):
                    continue
                occupation = self._occupation_for(firm)
                committed = firm.headcount + sum(
                    row.headcount
                    for row in self.economy.vacancies.values()
                    if row.firm_id == firm.firm_id and row.status == "open"
                )
                headcount = max(1, firm.target_headcount - committed)
                actions.append(
                    self._action(
                        firm.founder_id,
                        tick,
                        ActionType.POST_VACANCY,
                        {
                            "firm_id": firm.firm_id,
                            "occupation": occupation.id,
                            "wage_offer_cents": max(
                                self.settings.labour.minimum_wage_cents,
                                self.settings.economy.median_wage_cents // 24,
                            ),
                            "headcount": headcount,
                        },
                        ordinals,
                    )
                )

        for agent in self.population:
            if (
                not agent.alive
                or not 18 <= agent.age_years < self.settings.labour.retirement_age
                or active_employment(self.economy, agent.agent_id, tick) is not None
                or agent.employment_status in {"child", "student", "retired", "dead"}
            ):
                continue
            visible = visibility_slice(
                agent,
                self.economy,
                self.world,
                self.occupations,
                self.rng,
                tick=tick,
                limit=self.settings.labour.vacancy_visibility_k,
            )
            candidates = [
                vacancy
                for vacancy in visible
                if not any(
                    application.agent_id == agent.agent_id
                    and application.vacancy_id == vacancy.vacancy_id
                    for application in self.economy.applications.values()
                )
            ]
            if not candidates:
                continue
            vacancy = max(
                candidates,
                key=lambda row: (
                    match_score_bp(
                        agent,
                        row,
                        self.occupations[row.occupation],
                        ticks_per_sim_day=self.settings.clock.ticks_per_sim_day,
                        ticks_per_sim_year=self.settings.clock.days_per_sim_year
                        * self.settings.clock.ticks_per_sim_day,
                    ),
                    -row.posted_tick,
                    row.vacancy_id,
                ),
            )
            actions.append(
                self._action(
                    agent.agent_id,
                    tick,
                    ActionType.APPLY_FOR_JOB,
                    {
                        "vacancy_id": vacancy.vacancy_id,
                        "asked_wage_cents": vacancy.wage_offer_cents,
                    },
                    ordinals,
                )
            )
        return tuple(actions)

    def _offer_actions(
        self,
        shortlisted: Sequence[object],
        tick: int,
    ) -> tuple[Action, ...]:
        actions: list[Action] = []
        ordinals: dict[str, int] = {}
        available_by_vacancy: dict[str, int] = {
            vacancy.vacancy_id: vacancy.headcount
            for vacancy in self.economy.vacancies.values()
            if vacancy.status == "open"
        }
        ranked = sorted(
            shortlisted,
            key=lambda row: (
                row.vacancy_id,  # type: ignore[attr-defined]
                -(row.match_score_bp or 0),  # type: ignore[attr-defined]
                row.rank or 0,  # type: ignore[attr-defined]
            ),
        )
        for application in ranked:
            vacancy_id = application.vacancy_id  # type: ignore[attr-defined]
            if available_by_vacancy.get(vacancy_id, 0) <= 0:
                continue
            vacancy = self.economy.vacancies[vacancy_id]
            firm = self.economy.firms[vacancy.firm_id]
            actions.append(
                self._action(
                    firm.founder_id,
                    tick,
                    ActionType.MAKE_OFFER,
                    {
                        "application_id": application.application_id,  # type: ignore[attr-defined]
                        "wage_cents": vacancy.wage_offer_cents,
                    },
                    ordinals,
                )
            )
            available_by_vacancy[vacancy_id] -= 1
        return tuple(actions)

    def _occupation_for(self, firm: FirmState) -> Occupation:
        choices = [
            occupation
            for occupation in self.occupations.values()
            if firm.sector in occupation.sectors
        ]
        if not choices:
            choices = list(self.occupations.values())
        return sorted(choices, key=lambda row: row.id)[0]

    def _action(
        self,
        actor_id: str,
        tick: int,
        action_type: ActionType,
        params: dict[str, object],
        ordinals: dict[str, int],
    ) -> Action:
        ordinal = ordinals.get(actor_id, 0)
        ordinals[actor_id] = ordinal + 1
        return make_action(
            actor_id=actor_id,
            tick=tick,
            action_type=action_type,
            params=params,
            origin="scripted",
            reasoning="MechanicalPolicy baseline decision",
            ordinal=ordinal,
        )
