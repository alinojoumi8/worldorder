from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
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
    capital_cents: int = 0
    headcount: int = 0
    status: str = "active"


@dataclass(slots=True)
class EconomyState:
    ledger: Ledger
    banks: dict[str, BankState]
    firms: dict[str, FirmState]

    def cached_net_worth_cents(self) -> Mapping[str, int]:
        result = {firm_id: firm.capital_cents for firm_id, firm in sorted(self.firms.items())}
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
            firm.capital_cents = self.ledger.net_worth(firm.firm_id)
        for bank in self.banks.values():
            if not bank.is_central:
                bank.capital_cents = self.ledger.net_worth(bank.bank_id)

    def dump(self) -> Mapping[str, Any]:
        return {
            "ledger": self.ledger.dump(),
            "banks": {bank_id: asdict(bank) for bank_id, bank in sorted(self.banks.items())},
            "firms": {firm_id: asdict(firm) for firm_id, firm in sorted(self.firms.items())},
        }

    def load(self, state: Mapping[str, Any]) -> None:
        ledger = state.get("ledger")
        banks = state.get("banks")
        firms = state.get("firms")
        if not isinstance(ledger, Mapping):
            raise ValueError("economy checkpoint ledger must be a mapping")
        if not isinstance(banks, Mapping) or not isinstance(firms, Mapping):
            raise ValueError("economy checkpoint institutions must be mappings")
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
