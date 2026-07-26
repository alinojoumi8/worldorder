from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from polis.agents.state import AgentPopulation
from polis.economy.invariants import check_money, m1_cents
from polis.economy.ledger import Ledger
from polis.kernel.invariants import Violation


@dataclass(slots=True)
class BankState:
    bank_id: str
    name: str
    place_id: str
    reserve_account_id: str
    deposit_liability_account_id: str
    is_central: bool
    capital_cents: int = 0
    reserve_ratio_bp: int = 1_000
    status: str = "active"


@dataclass(slots=True)
class FirmState:
    firm_id: str
    name: str
    sector: str
    place_id: str
    founder_id: str
    ledger_account_id: str
    productivity_bp: int
    capital_cents: int = 1_000_000
    liquid_cents: int = 0
    headcount: int = 0
    target_headcount: int = 1
    cumulative_output_units: int = 0
    cumulative_revenue_cents: int = 0
    cumulative_wage_cents: int = 0
    status: str = "active"


@dataclass(slots=True)
class VacancyState:
    vacancy_id: str
    firm_id: str
    occupation: str
    skill_reqs: dict[str, int]
    wage_offer_cents: int
    headcount: int
    posted_tick: int
    expires_tick: int
    district_id: str
    min_match_score_bp: int = 5_500
    status: str = "open"
    applicants_n: int = 0


@dataclass(slots=True)
class ApplicationState:
    application_id: str
    vacancy_id: str
    agent_id: str
    asked_wage_cents: int
    submitted_tick: int
    status: str = "pending"
    match_score_bp: int | None = None
    rank: int | None = None


@dataclass(slots=True)
class OfferState:
    offer_id: str
    application_id: str
    vacancy_id: str
    firm_id: str
    agent_id: str
    wage_cents: int
    occupation: str
    made_tick: int
    expires_tick: int
    status: str = "open"


@dataclass(slots=True)
class EmploymentState:
    employment_id: str
    agent_id: str
    firm_id: str
    occupation: str
    wage_cents: int
    started_tick: int
    match_score_bp: int
    ended_tick: int | None = None
    hours_bp: int = 10_000
    accrued_wage_cents: int = 0
    accrual_remainder: int = 0
    total_paid_cents: int = 0
    last_worked_tick: int | None = None
    last_effective_labour_bp: int = 0


@dataclass(slots=True)
class InventoryState:
    firm_id: str
    sku: str
    quantity: int = 0
    unit_cost_cents: int = 1
    price_cents: int = 1
    carry_micro: int = 0
    markup_bp: int = 2_500
    units_sold_28d: int = 0


@dataclass(slots=True)
class EconomyState:
    ledger: Ledger
    banks: dict[str, BankState]
    firms: dict[str, FirmState]
    vacancies: dict[str, VacancyState] = field(default_factory=dict)
    applications: dict[str, ApplicationState] = field(default_factory=dict)
    offers: dict[str, OfferState] = field(default_factory=dict)
    employments: dict[str, EmploymentState] = field(default_factory=dict)
    inventory: dict[str, InventoryState] = field(default_factory=dict)
    skill_last_used_tick: dict[str, dict[str, int]] = field(default_factory=dict)

    def cached_net_worth_cents(self) -> Mapping[str, int]:
        result = {firm_id: firm.liquid_cents for firm_id, firm in sorted(self.firms.items())}
        result.update(
            {
                bank_id: bank.capital_cents
                for bank_id, bank in sorted(self.banks.items())
                if not bank.is_central
            }
        )
        return result

    def sync_denormalised(self, population: AgentPopulation) -> None:
        for agent in population:
            agent.wealth_cents = self.ledger.net_worth(agent.agent_id)
        for firm in self.firms.values():
            firm.liquid_cents = self.ledger.net_worth(firm.firm_id)
        for bank in self.banks.values():
            if not bank.is_central:
                bank.capital_cents = self.ledger.net_worth(bank.bank_id)

    def dump(self) -> Mapping[str, Any]:
        return {
            "ledger": self.ledger.dump(),
            "banks": {bank_id: asdict(bank) for bank_id, bank in sorted(self.banks.items())},
            "firms": {firm_id: asdict(firm) for firm_id, firm in sorted(self.firms.items())},
            "vacancies": {
                vacancy_id: asdict(vacancy)
                for vacancy_id, vacancy in sorted(self.vacancies.items())
            },
            "applications": {
                application_id: asdict(application)
                for application_id, application in sorted(self.applications.items())
            },
            "offers": {offer_id: asdict(offer) for offer_id, offer in sorted(self.offers.items())},
            "employments": {
                employment_id: asdict(employment)
                for employment_id, employment in sorted(self.employments.items())
            },
            "inventory": {
                inventory_id: asdict(inventory)
                for inventory_id, inventory in sorted(self.inventory.items())
            },
            "skill_last_used_tick": {
                agent_id: dict(sorted(values.items()))
                for agent_id, values in sorted(self.skill_last_used_tick.items())
            },
        }

    def load(self, state: Mapping[str, Any]) -> None:
        ledger = state.get("ledger")
        banks = state.get("banks")
        firms = state.get("firms")
        vacancies = state.get("vacancies", {})
        applications = state.get("applications", {})
        offers = state.get("offers", {})
        employments = state.get("employments", {})
        inventory = state.get("inventory", {})
        skill_last_used_tick = state.get("skill_last_used_tick", {})
        if not isinstance(ledger, Mapping):
            raise ValueError("economy checkpoint ledger must be a mapping")
        if not isinstance(banks, Mapping) or not isinstance(firms, Mapping):
            raise ValueError("economy checkpoint institutions must be mappings")
        projections = (
            vacancies,
            applications,
            offers,
            employments,
            inventory,
            skill_last_used_tick,
        )
        if any(not isinstance(item, Mapping) for item in projections):
            raise ValueError("economy checkpoint projections must be mappings")
        self.ledger.load(ledger)
        self.banks = {
            str(bank_id): BankState(**dict(row))
            for bank_id, row in sorted(banks.items())
            if isinstance(row, Mapping)
        }
        self.firms = {
            str(firm_id): FirmState(**dict(row))
            for firm_id, row in sorted(firms.items())
            if isinstance(row, Mapping)
        }
        self.vacancies = {
            str(row_id): VacancyState(**dict(row))
            for row_id, row in sorted(vacancies.items())
            if isinstance(row, Mapping)
        }
        self.applications = {
            str(row_id): ApplicationState(**dict(row))
            for row_id, row in sorted(applications.items())
            if isinstance(row, Mapping)
        }
        self.offers = {
            str(row_id): OfferState(**dict(row))
            for row_id, row in sorted(offers.items())
            if isinstance(row, Mapping)
        }
        self.employments = {
            str(row_id): EmploymentState(**dict(row))
            for row_id, row in sorted(employments.items())
            if isinstance(row, Mapping)
        }
        self.inventory = {
            str(row_id): InventoryState(**dict(row))
            for row_id, row in sorted(inventory.items())
            if isinstance(row, Mapping)
        }
        self.skill_last_used_tick = {
            str(agent_id): {str(skill): int(last_tick) for skill, last_tick in row.items()}
            for agent_id, row in sorted(skill_last_used_tick.items())
            if isinstance(row, Mapping)
        }


class EconomyWorldState:
    """Kernel invariant view combining M1 population with the M2 ledger."""

    def __init__(self, population: AgentPopulation, economy: EconomyState) -> None:
        self.population_state = population
        self.economy = economy

    @property
    def tick(self) -> int:
        return self.population_state.tick

    @tick.setter
    def tick(self, value: int) -> None:
        self.population_state.tick = value

    def money_supply_cents(self) -> int:
        return m1_cents(self.economy.ledger)

    def total_balances_cents(self) -> int:
        return m1_cents(self.economy.ledger)

    def ledger_imbalance_cents(self) -> int:
        result = check_money(self.economy.ledger, self)
        if not isinstance(result, Violation):
            return 0
        for value in result.detail.values():
            if isinstance(value, int):
                return value or 1
            if isinstance(value, Mapping):
                return sum(abs(int(item)) for item in value.values()) or 1
        return 1

    def cached_net_worth_cents(self) -> Mapping[str, int]:
        values = dict(self.economy.cached_net_worth_cents())
        values.update({agent.agent_id: agent.wealth_cents for agent in self.population_state})
        return values

    def population(self) -> int:
        return self.population_state.population()

    def initial_population(self) -> int:
        return self.population_state.initial_population()

    def action_type_counts(self) -> Mapping[str, int]:
        return self.population_state.action_type_counts()

    def chain_ok(self) -> bool:
        return self.population_state.chain_ok()
