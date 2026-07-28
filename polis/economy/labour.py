from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isqrt
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any, cast

import yaml

from polis.agents.actions.types import Action, ActionType
from polis.agents.state import AgentPopulation
from polis.agents.types import SKILLS, AgentState, Skill
from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.ledger import LedgerError, Leg, parse_account_id
from polis.economy.money import bp, mint
from polis.economy.state import (
    ApplicationState,
    EconomyState,
    EmploymentState,
    OfferState,
    VacancyState,
)
from polis.events.kinds import (
    APPLICATION_SCREENED,
    FIRED,
    HIRED,
    JOB_APPLICATION_SUBMITTED,
    LABOUR_SESSION_SUMMARY,
    LAYOFF_BATCH,
    OFFER_ACCEPTED,
    OFFER_EXPIRED,
    OFFER_MADE,
    PAYROLL_RUN,
    PAYROLL_SHORTFALL,
    SKILL_DECAYED,
    UNEMPLOYMENT_SPELL_ENDED,
    VACANCY_CLOSED,
    VACANCY_POSTED,
    WAGE_PAID,
    WORK_PERFORMED,
)
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.world.api import World

if TYPE_CHECKING:
    from polis.society.law import GarnishmentProtocol, WagePenaltyProtocol

Emit = Callable[[NewEvent], Event]
_EDU_BONUS_BP = {
    "none": 0,
    "primary": 100,
    "secondary": 300,
    "tertiary": 600,
    "graduate": 800,
}


@dataclass(frozen=True, slots=True)
class Occupation:
    id: str
    sectors: tuple[str, ...]
    requirements: Mapping[Skill, int]
    intensity: Mapping[Skill, int]
    weights: Mapping[Skill, int]


@dataclass(frozen=True, slots=True)
class LabourForce:
    employed: tuple[str, ...]
    unemployed: tuple[str, ...]
    nilf: tuple[str, ...]
    unemployment_bp: int
    unemployment_marginal_bp: int
    unemployment_broad_bp: int
    participation_bp: int
    vacancy_rate_bp: int


def _skill_bp(agent: AgentState, skill: Skill) -> int:
    return max(0, min(10_000, round(agent.skills.get(skill, 0.0) * 10_000)))


def load_occupations(path: str | Path = "configs/occupations.yaml") -> dict[str, Occupation]:
    catalogue_path = Path(path)
    if not catalogue_path.is_absolute():
        catalogue_path = Path(__file__).resolve().parents[2] / catalogue_path
    payload = yaml.safe_load(catalogue_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("occupations"), Mapping):
        raise ValueError("occupation catalogue must contain an occupations mapping")
    result: dict[str, Occupation] = {}
    for occupation_id, raw in sorted(cast(Mapping[str, Any], payload["occupations"]).items()):
        if not isinstance(raw, Mapping):
            raise ValueError(f"occupation {occupation_id} must be a mapping")
        requirements = _skill_mapping(raw.get("requirements", {}), occupation_id)
        intensity = _skill_mapping(raw.get("intensity", {}), occupation_id)
        weights = _skill_mapping(raw.get("weights", {}), occupation_id)
        sectors = raw.get("sectors", ())
        if not isinstance(sectors, Sequence) or isinstance(sectors, (str, bytes)):
            raise ValueError(f"occupation {occupation_id} sectors must be a sequence")
        result[str(occupation_id)] = Occupation(
            str(occupation_id),
            tuple(str(item) for item in sectors),
            requirements,
            intensity,
            weights,
        )
    if not result:
        raise ValueError("occupation catalogue cannot be empty")
    return result


def _skill_mapping(value: object, occupation_id: object) -> dict[Skill, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"occupation {occupation_id} skill profile must be a mapping")
    result: dict[Skill, int] = {}
    for raw_skill, raw_level in value.items():
        if raw_skill not in SKILLS:
            raise ValueError(f"occupation {occupation_id} has unknown skill {raw_skill}")
        level = int(raw_level)
        if not 0 <= level <= 10_000:
            raise ValueError(f"occupation {occupation_id} skill level is outside 0..10000")
        result[cast(Skill, raw_skill)] = level
    return result


def skill_value_bp(agent: AgentState, occupation: Occupation) -> int:
    weight_sum = sum(occupation.weights.values())
    if weight_sum <= 0:
        return 10_000
    return (
        sum(occupation.weights.get(skill, 0) * _skill_bp(agent, skill) for skill in SKILLS)
        // weight_sum
    )


@mechanism(
    "labour_matching",
    entails=(
        "Offer probability is weakly increasing in an applicant's skills relative to the "
        "posted requirements and may fall with spell length; no aggregate matching rate exists."
    ),
    config_key="mechanisms.labour_matching",
)
def match_score_bp(
    agent: AgentState,
    vacancy: VacancyState,
    occupation: Occupation,
    *,
    unemployed_ticks: int = 0,
    ticks_per_sim_day: int = 1,
    ticks_per_sim_year: int = 360,
    ticks_worked: int = 0,
    criminal_record: int = 0,
    recency_penalty: bool = True,
) -> int:
    requirements = vacancy.skill_reqs
    if requirements:
        met = sum(
            min(_skill_bp(agent, cast(Skill, skill)), level)
            for skill, level in requirements.items()
        )
        demanded = sum(requirements.values())
        fit = 10_000 * met // max(1, demanded)
        surplus_raw = sum(
            occupation.weights.get(cast(Skill, skill), 0)
            * max(0, _skill_bp(agent, cast(Skill, skill)) - level)
            for skill, level in requirements.items()
        )
        surplus = min(2_000, surplus_raw // (10_000 * max(1, len(requirements))))
    else:
        fit = 10_000
        surplus = 0
    education = _EDU_BONUS_BP[agent.education_level]
    experience = min(1_000, ticks_worked // max(1, ticks_per_sim_year) * 200)
    reputation = int(2_000 * (agent.reputation - 0.5))
    recency = (
        -min(1_500, unemployed_ticks // max(1, ticks_per_sim_day) * 5) if recency_penalty else 0
    )
    criminal = -400 * min(3, criminal_record)
    return max(
        0,
        min(
            10_000,
            fit + surplus + education + experience + reputation + recency + criminal,
        ),
    )


@mechanism(
    "labour.vacancy_visibility",
    entails=(
        "Each searching agent can reach only a bounded seeded slice of open vacancies; "
        "applications, offers, and acceptances remain separate microdecisions."
    ),
    config_key="labour.vacancy_visibility_k",
)
def visibility_slice(
    agent: AgentState,
    economy: EconomyState,
    world: World,
    occupations: Mapping[str, Occupation],
    rng: RngRegistry,
    *,
    tick: int,
    limit: int,
) -> tuple[VacancyState, ...]:
    del occupations
    agent_place = world.locations[agent.agent_id].place_id or agent.home_place_id
    current_district = world.place(agent_place).district_id
    home_district = world.place(agent.home_place_id).district_id
    bands: list[list[VacancyState]] = [[], []]
    for vacancy in economy.vacancies.values():
        if vacancy.status != "open" or vacancy.headcount <= 0 or vacancy.expires_tick <= tick:
            continue
        target = 0 if vacancy.district_id in {current_district, home_district} else 1
        bands[target].append(vacancy)
    stream = rng.get("labour.visibility", agent.agent_id, tick)
    visible: list[VacancyState] = []
    for band in bands:
        ordered = sorted(band, key=lambda row: (row.firm_id, row.vacancy_id))
        stream.shuffle(ordered)
        visible.extend(ordered)
    return tuple(visible[: max(0, limit)])


def active_employment(economy: EconomyState, agent_id: str, tick: int) -> EmploymentState | None:
    for employment in economy.employments.values():
        if (
            employment.agent_id == agent_id
            and employment.started_tick <= tick
            and (employment.ended_tick is None or employment.ended_tick > tick)
        ):
            return employment
    return None


def labour_force(
    population: AgentPopulation,
    economy: EconomyState,
    *,
    tick: int,
    search_window_ticks: int,
    retirement_age: int,
) -> LabourForce:
    eligible = tuple(
        agent for agent in population if agent.alive and 18 <= agent.age_years < retirement_age
    )
    employment_by_agent = {
        employment.agent_id
        for employment in economy.employments.values()
        if employment.started_tick <= tick
        and (employment.ended_tick is None or employment.ended_tick > tick)
    }
    self_employed = {
        agent.agent_id for agent in eligible if agent.employment_status == "self_employed"
    }
    employed = tuple(
        sorted(
            agent.agent_id
            for agent in eligible
            if agent.agent_id in employment_by_agent or agent.agent_id in self_employed
        )
    )
    searched = {
        application.agent_id
        for application in economy.applications.values()
        if 0 <= tick - application.submitted_tick <= search_window_ticks
    }
    unemployed = tuple(
        sorted(
            agent.agent_id
            for agent in eligible
            if agent.agent_id not in employment_by_agent
            and agent.agent_id not in self_employed
            and agent.employment_status not in {"child", "student", "retired", "dead"}
            and agent.agent_id in searched
        )
    )
    labour_ids = set(employed) | set(unemployed)
    nilf = tuple(sorted(agent.agent_id for agent in eligible if agent.agent_id not in labour_ids))
    labour_n = len(employed) + len(unemployed)
    unemployment = 10_000 * len(unemployed) // max(1, labour_n)
    participation = 10_000 * labour_n // max(1, len(eligible))
    open_headcount = sum(
        vacancy.headcount
        for vacancy in economy.vacancies.values()
        if vacancy.status == "open" and vacancy.headcount > 0
    )
    vacancy_rate = 10_000 * open_headcount // max(1, open_headcount + len(employed))
    return LabourForce(
        employed,
        unemployed,
        nilf,
        unemployment,
        unemployment,
        unemployment,
        participation,
        vacancy_rate,
    )


def progressive_income_tax_cents(
    gross_cents: int,
    brackets: Sequence[tuple[int, int]],
    *,
    periods_per_year: int = 24,
) -> int:
    if gross_cents <= 0:
        return 0
    annual = gross_cents * periods_per_year
    ordered = sorted(brackets)
    annual_tax = 0
    for index, (floor, rate_bp) in enumerate(ordered):
        ceiling = ordered[index + 1][0] if index + 1 < len(ordered) else annual
        taxable = max(0, min(annual, ceiling) - floor)
        annual_tax += bp(taxable, rate_bp)
        if annual <= ceiling:
            break
    return annual_tax // periods_per_year


@mechanism(
    "skill_decay",
    entails=(
        "Skills unused for a simulation month fall at a constant proportional rate; "
        "combined with skill screening this can reduce re-employment probability."
    ),
    config_key="mechanisms.skill_decay",
)
def decay_skill_bp(level_bp: int, rate_bp: int) -> int:
    return max(0, level_bp - bp(level_bp, rate_bp))


@mechanism(
    "labour.redundancy_selection",
    entails=(
        "A payroll shortfall sheds the lowest match-score, shortest-tenure workers first; "
        "the frequency and timing of shortfalls are not fixed by this rule."
    ),
)
def redundancy_order(
    employments: Sequence[EmploymentState],
    *,
    tick: int,
) -> tuple[EmploymentState, ...]:
    return tuple(
        sorted(
            employments,
            key=lambda row: (
                row.match_score_bp * isqrt(max(1, tick - row.started_tick + 1)),
                row.started_tick,
                row.employment_id,
            ),
        )
    )


class LabourMarket:
    def __init__(
        self,
        settings: Settings,
        population: AgentPopulation,
        world: World,
        economy: EconomyState,
        rng: RngRegistry,
        occupations: Mapping[str, Occupation],
        garnishment: GarnishmentProtocol | None = None,
        wage_penalty: WagePenaltyProtocol | None = None,
    ) -> None:
        self.settings = settings
        self.population = population
        self.world = world
        self.economy = economy
        self.rng = rng
        self.occupations = occupations
        self.garnishment = garnishment
        self.wage_penalty = wage_penalty

    def resolve(self, actions: Sequence[Action], tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        order = (
            ActionType.WORK,
            ActionType.POST_VACANCY,
            ActionType.APPLY_FOR_JOB,
            ActionType.MAKE_OFFER,
            ActionType.NEGOTIATE_WAGE,
            ActionType.ACCEPT_OFFER,
            ActionType.DECLINE_OFFER,
            ActionType.QUIT_JOB,
            ActionType.FIRE_EMPLOYEE,
        )
        for action_type in order:
            batch = sorted(
                (action for action in actions if action.type == action_type),
                key=lambda row: (row.actor_id, str(row.action_id)),
            )
            for action in batch:
                events.extend(self._resolve_one(action, tick, emit))
        return tuple(events)

    def _resolve_one(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        if action.type == ActionType.POST_VACANCY:
            event = self._post_vacancy(action, tick, emit)
            return (event,) if event is not None else ()
        if action.type == ActionType.APPLY_FOR_JOB:
            event = self._apply(action, tick, emit)
            return (event,) if event is not None else ()
        if action.type == ActionType.MAKE_OFFER:
            event = self._make_offer(action, tick, emit)
            return (event,) if event is not None else ()
        if action.type == ActionType.ACCEPT_OFFER:
            return self._accept_offer(action, tick, emit)
        if action.type == ActionType.WORK:
            event = self._work(action, tick, emit)
            return (event,) if event is not None else ()
        return ()

    def _post_vacancy(self, action: Action, tick: int, emit: Emit) -> Event | None:
        firm_id = str(action.params["firm_id"])
        firm = self.economy.firms.get(firm_id)
        occupation_id = str(action.params["occupation"])
        occupation = self.occupations.get(occupation_id)
        if firm is None or occupation is None or firm.status != "active":
            return None
        open_count = sum(
            vacancy.status == "open" and vacancy.firm_id == firm_id
            for vacancy in self.economy.vacancies.values()
        )
        if open_count >= self.settings.labour.max_open_vacancies_per_firm:
            return None
        wage = max(
            self.settings.labour.minimum_wage_cents,
            int(action.params["wage_offer_cents"]),
        )
        ordinal = sum(row.posted_tick == tick for row in self.economy.vacancies.values())
        vacancy_id = mint("vac", tick, ordinal)
        vacancy = VacancyState(
            vacancy_id,
            firm_id,
            occupation_id,
            {skill: value for skill, value in occupation.requirements.items()},
            wage,
            int(action.params["headcount"]),
            tick,
            tick + self.settings.labour.vacancy_ttl_days * self.settings.clock.ticks_per_sim_day,
            self.world.place(firm.place_id).district_id,
            self.settings.labour.min_match_score_bp,
        )
        self.economy.vacancies[vacancy_id] = vacancy
        return emit(
            NewEvent(
                VACANCY_POSTED,
                {
                    "vacancy_id": vacancy.vacancy_id,
                    "firm_id": vacancy.firm_id,
                    "occupation": vacancy.occupation,
                    "skill_reqs": vacancy.skill_reqs,
                    "wage_offer_cents": vacancy.wage_offer_cents,
                    "headcount": vacancy.headcount,
                    "posted_tick": vacancy.posted_tick,
                    "expires_tick": vacancy.expires_tick,
                    "district_id": vacancy.district_id,
                },
                actor_id=action.actor_id,
                subject_ids=(firm_id,),
            )
        )

    def _apply(self, action: Action, tick: int, emit: Emit) -> Event | None:
        vacancy_id = str(action.params["vacancy_id"])
        vacancy = self.economy.vacancies.get(vacancy_id)
        if vacancy is None or vacancy.status != "open" or vacancy.headcount <= 0:
            return None
        if active_employment(self.economy, action.actor_id, tick) is not None:
            return None
        open_applications = sum(
            row.agent_id == action.actor_id and row.status in {"pending", "shortlisted", "offered"}
            for row in self.economy.applications.values()
        )
        if open_applications >= self.settings.labour.max_open_applications:
            return None
        if any(
            row.agent_id == action.actor_id
            and row.vacancy_id == vacancy_id
            and row.status in {"pending", "shortlisted", "offered", "hired"}
            for row in self.economy.applications.values()
        ):
            return None
        ordinal = sum(row.submitted_tick == tick for row in self.economy.applications.values())
        application_id = mint("app", tick, ordinal)
        asked = int(action.params.get("asked_wage_cents") or vacancy.wage_offer_cents)
        application = ApplicationState(
            application_id,
            vacancy_id,
            action.actor_id,
            asked,
            tick,
        )
        self.economy.applications[application_id] = application
        vacancy.applicants_n += 1
        return emit(
            NewEvent(
                JOB_APPLICATION_SUBMITTED,
                {
                    "application_id": application_id,
                    "vacancy_id": vacancy_id,
                    "agent_id": action.actor_id,
                    "asked_wage_cents": asked,
                    "referral_id": None,
                },
                actor_id=action.actor_id,
                subject_ids=(vacancy.firm_id,),
            )
        )

    def screen_pending(self, tick: int, emit: Emit) -> tuple[ApplicationState, ...]:
        shortlisted: list[ApplicationState] = []
        recency_enabled = self.settings.mechanisms.get("labour_recency_penalty", "on") != "off"
        for vacancy in sorted(
            self.economy.vacancies.values(),
            key=lambda row: (row.firm_id, row.vacancy_id),
        ):
            pending = [
                row
                for row in self.economy.applications.values()
                if row.vacancy_id == vacancy.vacancy_id and row.status == "pending"
            ]
            if not pending or vacancy.status != "open":
                continue
            occupation = self.occupations[vacancy.occupation]
            candidates = sorted(
                pending,
                key=lambda row: self._candidate_signature(self.population[row.agent_id]),
            )
            self.rng.get("labour.screen", vacancy.vacancy_id, tick).shuffle(candidates)
            scored = [
                (
                    match_score_bp(
                        self.population[row.agent_id],
                        vacancy,
                        occupation,
                        unemployed_ticks=max(0, tick - row.submitted_tick),
                        ticks_per_sim_day=self.settings.clock.ticks_per_sim_day,
                        ticks_per_sim_year=self.settings.clock.days_per_sim_year
                        * self.settings.clock.ticks_per_sim_day,
                        recency_penalty=recency_enabled,
                    ),
                    row,
                )
                for row in candidates
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            shortlist_n = vacancy.headcount * self.settings.labour.shortlist_multiple
            for rank, (score, application) in enumerate(scored, 1):
                keep = score >= vacancy.min_match_score_bp and rank <= shortlist_n
                application.match_score_bp = score
                application.rank = rank
                application.status = "shortlisted" if keep else "rejected"
                if keep:
                    shortlisted.append(application)
                emit(
                    NewEvent(
                        APPLICATION_SCREENED,
                        {
                            "application_id": application.application_id,
                            "match_score_bp": score,
                            "rank": rank,
                            "shortlisted": keep,
                            "reject_reason": None if keep else "below_threshold_or_rank",
                        },
                        actor_id=vacancy.firm_id,
                        subject_ids=(application.agent_id,),
                    )
                )
        return tuple(shortlisted)

    def _candidate_signature(self, agent: AgentState) -> tuple[object, ...]:
        return (
            tuple(_skill_bp(agent, skill) for skill in SKILLS),
            agent.education_level,
            round(agent.reputation * 10_000),
            round(agent.age_years * 100),
            tuple(sorted(agent.traits.as_dict().items())),
        )

    def _make_offer(self, action: Action, tick: int, emit: Emit) -> Event | None:
        application_id = str(action.params["application_id"])
        application = self.economy.applications.get(application_id)
        if application is None or application.status != "shortlisted":
            return None
        vacancy = self.economy.vacancies[application.vacancy_id]
        if vacancy.status != "open" or vacancy.headcount <= 0:
            return None
        if any(
            row.application_id == application_id and row.status == "open"
            for row in self.economy.offers.values()
        ):
            return None
        ordinal = sum(row.made_tick == tick for row in self.economy.offers.values())
        offer_id = mint("off", tick, ordinal)
        wage = max(
            self.settings.labour.minimum_wage_cents,
            int(action.params["wage_cents"]),
        )
        if self.wage_penalty is not None:
            wage = max(
                self.settings.labour.minimum_wage_cents,
                round(wage * self.wage_penalty.wage_multiplier(application.agent_id)),
            )
        offer = OfferState(
            offer_id,
            application_id,
            vacancy.vacancy_id,
            vacancy.firm_id,
            application.agent_id,
            wage,
            vacancy.occupation,
            tick,
            tick + self.settings.labour.offer_ttl_days * self.settings.clock.ticks_per_sim_day,
        )
        self.economy.offers[offer_id] = offer
        application.status = "offered"
        return emit(
            NewEvent(
                OFFER_MADE,
                {
                    "offer_id": offer_id,
                    "vacancy_id": vacancy.vacancy_id,
                    "firm_id": vacancy.firm_id,
                    "agent_id": application.agent_id,
                    "wage_cents": wage,
                    "occupation": vacancy.occupation,
                    "expires_tick": offer.expires_tick,
                },
                actor_id=action.actor_id,
                subject_ids=(application.agent_id,),
            )
        )

    def _accept_offer(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        offer_id = str(action.params["offer_id"])
        offer = self.economy.offers.get(offer_id)
        if (
            offer is None
            or offer.status != "open"
            or offer.agent_id != action.actor_id
            or offer.made_tick >= tick
            or offer.expires_tick < tick
        ):
            return ()
        if not self.population[offer.agent_id].alive:
            offer.status = "expired"
            return (
                emit(
                    NewEvent(
                        OFFER_EXPIRED,
                        {"offer_id": offer.offer_id, "agent_id": offer.agent_id},
                        actor_id=offer.firm_id,
                        subject_ids=(offer.agent_id,),
                    )
                ),
            )
        vacancy = self.economy.vacancies[offer.vacancy_id]
        if vacancy.status != "open" or vacancy.headcount <= 0:
            offer.status = "expired"
            return (
                emit(
                    NewEvent(
                        OFFER_EXPIRED,
                        {"offer_id": offer.offer_id, "agent_id": offer.agent_id},
                        actor_id=offer.firm_id,
                        subject_ids=(offer.agent_id,),
                    )
                ),
            )
        ordinal = sum(row.started_tick == tick + 1 for row in self.economy.employments.values())
        employment_id = mint("emp", tick + 1, ordinal)
        application = next(
            row
            for row in self.economy.applications.values()
            if row.vacancy_id == offer.vacancy_id and row.agent_id == offer.agent_id
        )
        employment = EmploymentState(
            employment_id,
            offer.agent_id,
            offer.firm_id,
            offer.occupation,
            offer.wage_cents,
            tick + 1,
            application.match_score_bp or 0,
        )
        self.economy.employments[employment_id] = employment
        offer.status = "accepted"
        application.status = "hired"
        vacancy.headcount -= 1
        firm = self.economy.firms[offer.firm_id]
        firm.headcount += 1
        if vacancy.headcount == 0:
            vacancy.status = "filled"
        for other in self.economy.applications.values():
            if (
                other.agent_id == offer.agent_id
                and other.application_id != application.application_id
                and other.status in {"pending", "shortlisted", "offered"}
            ):
                other.status = "withdrawn"
        self.population[offer.agent_id].employment_status = "employed"
        accepted = emit(
            NewEvent(
                OFFER_ACCEPTED,
                {
                    "offer_id": offer.offer_id,
                    "employment_id": employment_id,
                    "wage_cents": offer.wage_cents,
                },
                actor_id=offer.agent_id,
                subject_ids=(offer.firm_id,),
            )
        )
        hired = emit(
            NewEvent(
                HIRED,
                {
                    "agent_id": offer.agent_id,
                    "firm_id": offer.firm_id,
                    "employment_id": employment_id,
                    "occupation": offer.occupation,
                    "wage_cents": offer.wage_cents,
                    "match_score_bp": employment.match_score_bp,
                    "search_duration_ticks": tick - application.submitted_tick,
                },
                actor_id=offer.firm_id,
                subject_ids=(offer.agent_id,),
            )
        )
        spell = emit(
            NewEvent(
                UNEMPLOYMENT_SPELL_ENDED,
                {
                    "agent_id": offer.agent_id,
                    "duration_ticks": tick - application.submitted_tick,
                    "exit": "job",
                    "new_wage_cents": offer.wage_cents,
                    "wage_change_bp": 0,
                },
                actor_id=offer.agent_id,
            )
        )
        return accepted, hired, spell

    def _work(self, action: Action, tick: int, emit: Emit) -> Event | None:
        employment_id = str(action.params["employment_id"])
        employment = self.economy.employments.get(employment_id)
        if (
            employment is None
            or employment.agent_id != action.actor_id
            or employment.started_tick > tick
            or employment.ended_tick is not None
        ):
            return None
        agent = self.population[action.actor_id]
        occupation = self.occupations[employment.occupation]
        hours_bp = employment.hours_bp
        requested_effort = int(action.params.get("effort_bp", 10_000))
        effort_bp = max(
            0,
            min(
                requested_effort,
                int(agent.health * 10_000),
                int(agent.needs.energy * 10_000),
            ),
        )
        effective_labour = hours_bp * effort_bp * skill_value_bp(agent, occupation) // 100_000_000
        pay_period_ticks = 14 * self.settings.clock.ticks_per_sim_day
        numerator = employment.wage_cents * hours_bp + employment.accrual_remainder
        accrued = numerator // (10_000 * max(1, pay_period_ticks))
        employment.accrual_remainder = numerator % (10_000 * max(1, pay_period_ticks))
        employment.accrued_wage_cents += accrued
        employment.last_worked_tick = tick
        employment.last_effective_labour_bp = effective_labour
        skill_deltas: dict[str, float] = {}
        for skill, intensity in occupation.intensity.items():
            before = agent.skills[skill]
            delta = intensity / 10_000 * 0.00005
            agent.skills[skill] = min(1.0, before + delta)
            skill_deltas[skill] = round(agent.skills[skill] - before, 8)
            self.economy.skill_last_used_tick.setdefault(action.actor_id, {})[skill] = tick
        return emit(
            NewEvent(
                WORK_PERFORMED,
                {
                    "employment_id": employment_id,
                    "agent_id": action.actor_id,
                    "firm_id": employment.firm_id,
                    "hours_bp": hours_bp,
                    "effort_bp": effort_bp,
                    "effective_labour_bp": effective_labour,
                    "skill_deltas": skill_deltas,
                },
                actor_id=action.actor_id,
                subject_ids=(employment.firm_id,),
            )
        )

    def expire(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for offer in sorted(self.economy.offers.values(), key=lambda row: row.offer_id):
            if offer.status == "open" and offer.expires_tick < tick:
                offer.status = "expired"
                events.append(
                    emit(
                        NewEvent(
                            OFFER_EXPIRED,
                            {"offer_id": offer.offer_id, "agent_id": offer.agent_id},
                            actor_id=offer.firm_id,
                            subject_ids=(offer.agent_id,),
                        )
                    )
                )
        for vacancy in sorted(self.economy.vacancies.values(), key=lambda row: row.vacancy_id):
            if vacancy.status == "open" and vacancy.expires_tick <= tick:
                vacancy.status = "expired"
                events.append(
                    emit(
                        NewEvent(
                            VACANCY_CLOSED,
                            {
                                "vacancy_id": vacancy.vacancy_id,
                                "reason": "expired",
                                "applicants_n": vacancy.applicants_n,
                                "days_open": (tick - vacancy.posted_tick)
                                // max(1, self.settings.clock.ticks_per_sim_day),
                            },
                            actor_id=vacancy.firm_id,
                        )
                    )
                )
        return tuple(events)

    def payroll_due(self, tick: int) -> bool:
        ticks_per_day = self.settings.clock.ticks_per_sim_day
        if tick % ticks_per_day != 0:
            return False
        day = ((tick // ticks_per_day) - 1) % self.settings.clock.days_per_sim_year + 1
        return day in self.settings.labour.payroll.days

    def run_payroll(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.payroll_due(tick):
            return ()
        events: list[Event] = []
        treasury = "dep:gv_treasury@bk_cb"
        for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
            if any(
                case.entity_id == firm.firm_id and case.status == "open"
                for case in self.economy.ventures.bankruptcies.values()
            ):
                continue
            employments = [
                row
                for row in self.economy.employments.values()
                if row.firm_id == firm.firm_id and row.accrued_wage_cents > 0
            ]
            required = sum(row.accrued_wage_cents for row in employments)
            available_before = self.economy.ledger.liquid(firm.firm_id)
            txn_ids: list[str] = []
            paid_gross = 0
            paid_tax = 0
            employer_tax_total = 0
            unpaid: list[str] = []
            dead_write_off_cents = 0
            for employment in sorted(employments, key=lambda row: row.employment_id):
                gross = employment.accrued_wage_cents
                agent = self.population[employment.agent_id]
                if not agent.alive:
                    events.append(
                        emit(
                            NewEvent(
                                PAYROLL_SHORTFALL,
                                {
                                    "firm_id": firm.firm_id,
                                    "required_cents": gross,
                                    "available_cents": self.economy.ledger.liquid(firm.firm_id),
                                    "unpaid_employment_ids": [employment.employment_id],
                                    "accrued_claim_cents": gross,
                                },
                                actor_id=firm.firm_id,
                                subject_ids=(employment.agent_id,),
                            )
                        )
                    )
                    employment.accrued_wage_cents = 0
                    dead_write_off_cents += gross
                    if employment.ended_tick is None:
                        employment.ended_tick = tick
                        firm.headcount = max(0, firm.headcount - 1)
                    continue
                income_tax = progressive_income_tax_cents(
                    gross,
                    self.settings.treasury.tax.income_brackets,
                )
                employer_tax = bp(gross, self.settings.treasury.tax.payroll_employer_bp)
                net = gross - income_tax
                total_cost = gross + employer_tax
                if self.economy.ledger.liquid(firm.firm_id) < total_cost:
                    unpaid.append(employment.employment_id)
                    continue
                expected = self.economy.ledger.next_txn_id(tick)
                wage_event = emit(
                    NewEvent(
                        WAGE_PAID,
                        {
                            "employment_id": employment.employment_id,
                            "agent_id": employment.agent_id,
                            "firm_id": employment.firm_id,
                            "gross_cents": gross,
                            "income_tax_cents": income_tax,
                            "net_cents": net,
                            "hours_bp": employment.hours_bp,
                            "txn_id": str(expected),
                        },
                        actor_id=firm.firm_id,
                        subject_ids=(employment.agent_id,),
                    )
                )
                legs: list[Leg] = []
                if net:
                    legs.extend(
                        self.economy.ledger.transfer(
                            firm.ledger_account_id,
                            self._deposit_account(employment.agent_id, tick),
                            net,
                            "wage",
                        )
                    )
                tax_total = income_tax + employer_tax
                if tax_total:
                    legs.extend(
                        self.economy.ledger.transfer(
                            firm.ledger_account_id,
                            treasury,
                            tax_total,
                            "tax",
                        )
                    )
                txn_id = self.economy.ledger.post_transaction(
                    self._combine_legs(legs),
                    tick=tick,
                    cause=wage_event,
                )
                if txn_id != expected:
                    raise RuntimeError("payroll transaction ordinal diverged")
                txn_ids.append(str(txn_id))
                if self.garnishment is not None and net:
                    self.garnishment.garnish(employment.agent_id, net, tick)
                employment.total_paid_cents += gross
                income = self.economy.gross_income_by_tick.setdefault(tick, {})
                income[employment.agent_id] = income.get(employment.agent_id, 0) + gross
                wages = self.economy.gross_wages_by_tick.setdefault(tick, {})
                wages[employment.agent_id] = wages.get(employment.agent_id, 0) + gross
                employment.accrued_wage_cents = 0
                paid_gross += gross
                paid_tax += income_tax
                employer_tax_total += employer_tax
                firm.cumulative_wage_cents += gross
                events.append(wage_event)
            if unpaid:
                events.append(
                    emit(
                        NewEvent(
                            PAYROLL_SHORTFALL,
                            {
                                "firm_id": firm.firm_id,
                                "required_cents": required - dead_write_off_cents,
                                "available_cents": available_before,
                                "unpaid_employment_ids": unpaid,
                                "accrued_claim_cents": sum(
                                    self.economy.employments[row].accrued_wage_cents
                                    for row in unpaid
                                ),
                            },
                            actor_id=firm.firm_id,
                        )
                    )
                )
                events.extend(self._layoff_for_shortfall(firm.firm_id, unpaid, tick, emit))
            events.append(
                emit(
                    NewEvent(
                        PAYROLL_RUN,
                        {
                            "firm_id": firm.firm_id,
                            "period_start_tick": max(
                                0,
                                tick - 14 * self.settings.clock.ticks_per_sim_day,
                            ),
                            "period_end_tick": tick,
                            "n_employees": len(employments),
                            "gross_cents": paid_gross,
                            "income_tax_cents": paid_tax,
                            "employer_tax_cents": employer_tax_total,
                            "net_cents": paid_gross - paid_tax,
                            "txn_ids": txn_ids,
                        },
                        actor_id=firm.firm_id,
                    )
                )
            )
        return tuple(events)

    def _layoff_for_shortfall(
        self,
        firm_id: str,
        unpaid: Sequence[str],
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        firm = self.economy.firms[firm_id]
        candidates = redundancy_order(
            [
                self.economy.employments[employment_id]
                for employment_id in unpaid
                if self.economy.employments[employment_id].ended_tick is None
            ],
            tick=tick,
        )
        if not candidates:
            return ()
        before = firm.headcount
        events: list[Event] = []
        for employment in candidates:
            employment.ended_tick = tick + self.settings.labour.notice_ticks
            self.population[employment.agent_id].employment_status = "unemployed"
            firm.headcount = max(0, firm.headcount - 1)
            events.append(
                emit(
                    NewEvent(
                        FIRED,
                        {
                            "employment_id": employment.employment_id,
                            "agent_id": employment.agent_id,
                            "firm_id": firm_id,
                            "reason": "redundancy",
                            "severance_cents": 0,
                            "notice_ticks": self.settings.labour.notice_ticks,
                        },
                        actor_id=firm_id,
                        subject_ids=(employment.agent_id,),
                    )
                )
            )
        events.append(
            emit(
                NewEvent(
                    LAYOFF_BATCH,
                    {
                        "firm_id": firm_id,
                        "employment_ids": [employment.employment_id for employment in candidates],
                        "headcount_before": before,
                        "headcount_after": firm.headcount,
                        "trigger": "payroll_shortfall",
                    },
                    actor_id=firm_id,
                )
            )
        )
        return tuple(events)

    def decay_unused_skills(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        ticks_per_month = 30 * self.settings.clock.ticks_per_sim_day
        if (
            self.settings.mechanisms.get("skill_decay", "on") == "off"
            or tick % ticks_per_month != 0
        ):
            return ()
        events: list[Event] = []
        for agent in self.population:
            if not agent.alive:
                continue
            used = self.economy.skill_last_used_tick.setdefault(agent.agent_id, {})
            for skill in SKILLS:
                last_used = used.get(skill, 0)
                if tick - last_used < ticks_per_month:
                    continue
                before = _skill_bp(agent, skill)
                after = decay_skill_bp(
                    before,
                    self.settings.labour.skill_decay_bp_per_month,
                )
                if after == before:
                    continue
                agent.skills[skill] = after / 10_000
                events.append(
                    emit(
                        NewEvent(
                            SKILL_DECAYED,
                            {
                                "agent_id": agent.agent_id,
                                "skill": skill,
                                "from_level_bp": before,
                                "to_level_bp": after,
                                "ticks_unused": tick - last_used,
                            },
                            actor_id=agent.agent_id,
                        )
                    )
                )
        return tuple(events)

    def emit_summary(self, tick: int, emit: Emit) -> Event:
        open_vacancies = [row for row in self.economy.vacancies.values() if row.status == "open"]
        applications = [
            row for row in self.economy.applications.values() if row.submitted_tick == tick
        ]
        offers = [row for row in self.economy.offers.values() if row.made_tick == tick]
        hires = [row for row in self.economy.employments.values() if row.started_tick == tick + 1]
        scores = [row.match_score_bp for row in applications if row.match_score_bp is not None]
        wages = [row.wage_cents for row in offers]
        hire_wages = [row.wage_cents for row in hires]
        force = labour_force(
            self.population,
            self.economy,
            tick=tick,
            search_window_ticks=self.settings.labour.search_window_days
            * self.settings.clock.ticks_per_sim_day,
            retirement_age=self.settings.labour.retirement_age,
        )
        return emit(
            NewEvent(
                LABOUR_SESSION_SUMMARY,
                {
                    "tick": tick,
                    "vacancies_open": sum(row.headcount for row in open_vacancies),
                    "searchers": len(force.unemployed),
                    "applications": len(applications),
                    "offers": len(offers),
                    "hires": len(hires),
                    "mean_match_score_bp": sum(scores) // max(1, len(scores)),
                    "mean_offer_wage_cents": sum(wages) // max(1, len(wages)),
                    "median_hire_wage_cents": int(median(hire_wages)) if hire_wages else 0,
                },
            )
        )

    def _deposit_account(self, owner_id: str, tick: int) -> str:
        accounts = self.economy.ledger.accounts_of(owner_id)
        for account_id in accounts:
            code, _owner, _bank, _ref = parse_account_id(account_id)
            if code == "dep" and self.economy.ledger.is_open(account_id):
                return account_id
        bank_ids = tuple(
            bank.bank_id
            for bank in self.economy.banks.values()
            if not bank.is_central and bank.status == "active"
        )
        if not bank_ids:
            raise LedgerError("payroll cannot open a deposit without an active commercial bank")
        bank_id = min(bank_ids)
        return self.economy.ledger.open_account(
            "dep",
            owner_id,
            "agent",
            bank_id=bank_id,
            tick=tick,
        )

    def _combine_legs(self, legs: Sequence[Leg]) -> tuple[Leg, ...]:
        totals: dict[tuple[str, int, str], int] = {}
        for leg in legs:
            key = (leg.account_id, leg.direction, leg.reason)
            totals[key] = totals.get(key, 0) + leg.amount_cents
        return tuple(
            Leg(account_id, direction, amount, reason)
            for (account_id, direction, reason), amount in sorted(totals.items())
            if amount
        )
