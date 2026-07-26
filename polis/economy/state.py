from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from polis.agents.state import AgentPopulation
from polis.economy.exchange.models import ExchangeState
from polis.economy.invariants import check_money, m1_cents
from polis.economy.ledger import Ledger
from polis.economy.venture_state import VentureState
from polis.kernel.invariants import Violation


def _int_history(value: Mapping[object, object]) -> dict[int, int]:
    return {int(cast(Any, tick)): int(cast(Any, amount)) for tick, amount in value.items()}


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
    founded_tick: int = 0
    failed_tick: int | None = None
    lending_frozen: bool = False
    capital_ratio_bp: int = 10_000


@dataclass(slots=True)
class LoanApplicationState:
    application_id: str
    borrower_id: str
    lender_id: str
    requested_cents: int
    purpose: str
    term_ticks: int
    collateral: dict[str, Any]
    submitted_tick: int
    status: str = "submitted"
    score_bp: int | None = None
    offered_cents: int = 0
    offered_rate_bp: int = 0
    reason_codes: tuple[str, ...] = ()


@dataclass(slots=True)
class LoanState:
    loan_id: str
    lender_id: str
    borrower_id: str
    purpose: str
    principal_cents: int
    outstanding_cents: int
    annual_rate_bp: int
    term_ticks: int
    originated_tick: int
    matures_tick: int
    status: str
    collateral: dict[str, Any]
    collateral_value_cents: int
    credit_score_at_origination_bp: int
    lender_receivable_account_id: str
    borrower_payable_account_id: str
    payment_cents: int
    payments_n: int
    next_payment_tick: int
    accrued_interest_cents: int = 0
    accrual_remainder: int = 0
    total_interest_paid_cents: int = 0
    total_interest_scheduled_cents: int = 0
    total_interest_forgiven_cents: int = 0
    capitalised_interest_cents: int = 0
    payments_made: int = 0
    missed_since_tick: int | None = None
    defaulted_tick: int | None = None
    closed_tick: int | None = None


@dataclass(slots=True)
class LoanPaymentState:
    payment_id: str
    loan_id: str
    tick: int
    principal_cents: int
    interest_cents: int
    missed: bool


@dataclass(slots=True)
class TaxAssessmentState:
    assessment_id: str
    taxpayer_id: str
    tax_type: str
    base_cents: int
    rate_bp: int
    assessed_cents: int
    assessed_tick: int
    due_tick: int
    paid_cents: int = 0
    status: str = "assessed"


@dataclass(slots=True)
class BondState:
    symbol: str
    face_cents: int
    coupon_bp: int
    issued_tick: int
    matures_tick: int
    holder_id: str
    status: str = "outstanding"
    last_coupon_tick: int = 0


@dataclass(slots=True)
class TreasuryState:
    receipts_cents: int = 0
    spending_cents: int = 0
    debt_service_cents: int = 0
    period_receipts_cents: int = 0
    period_spending_cents: int = 0
    period_debt_service_cents: int = 0
    corporate_revenue_marks: dict[str, int] = field(default_factory=dict)
    corporate_wage_marks: dict[str, int] = field(default_factory=dict)


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
    dissolved_tick: int | None = None


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
class SkuState:
    sku: str
    category: str
    is_necessity: bool
    base_utility_bp: int
    perishable_bp_per_day: int
    durable_life_ticks: int | None
    is_service: bool
    is_capital: bool
    need_restore_bp: dict[str, int]
    gamma_units_per_year: int
    beta_bp: int
    sectors: tuple[str, ...]
    yield_units: int


@dataclass(slots=True)
class GoodsTransactionState:
    txn_id: str
    ledger_txn_id: str
    tick: int
    buyer_id: str
    seller_firm_id: str
    sku: str
    qty: int
    unit_price_cents: int
    gross_cents: int
    sales_tax_cents: int
    subsidy_cents: int


@dataclass(slots=True)
class DurableState:
    durable_id: str
    agent_id: str
    sku: str
    acquired_tick: int
    life_ticks: int
    qty: int


@dataclass(slots=True)
class BasketState:
    version: int
    quantities: dict[str, int]
    base_prices_cents: dict[str, int]
    fixed_tick: int


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
    skus: dict[str, SkuState] = field(default_factory=dict)
    goods_transactions: list[GoodsTransactionState] = field(default_factory=list)
    goods_sales_by_tick: dict[int, dict[str, int]] = field(default_factory=dict)
    goods_price_qty_by_tick: dict[int, dict[str, int]] = field(default_factory=dict)
    goods_price_value_by_tick: dict[int, dict[str, int]] = field(default_factory=dict)
    goods_last_price_cents: dict[str, int] = field(default_factory=dict)
    food_on_hand: dict[str, dict[str, int]] = field(default_factory=dict)
    durables: dict[str, DurableState] = field(default_factory=dict)
    basket: BasketState | None = None
    cpi_history_bp: dict[int, int] = field(default_factory=dict)
    cpi_core_history_bp: dict[int, int] = field(default_factory=dict)
    cpi_fisher_history_bp: dict[int, int] = field(default_factory=dict)
    cpi_category_history_bp: dict[str, dict[int, int]] = field(default_factory=dict)
    initial_inventory_value_cents: int = 0
    gross_income_by_tick: dict[int, dict[str, int]] = field(default_factory=dict)
    gross_wages_by_tick: dict[int, dict[str, int]] = field(default_factory=dict)
    loan_applications: dict[str, LoanApplicationState] = field(default_factory=dict)
    loans: dict[str, LoanState] = field(default_factory=dict)
    loan_payments: list[LoanPaymentState] = field(default_factory=list)
    tax_assessments: dict[str, TaxAssessmentState] = field(default_factory=dict)
    bonds: dict[str, BondState] = field(default_factory=dict)
    bond_holdings_cents: dict[str, dict[str, int]] = field(default_factory=dict)
    treasury: TreasuryState = field(default_factory=TreasuryState)
    exchange: ExchangeState = field(default_factory=ExchangeState)
    ventures: VentureState = field(default_factory=VentureState)
    policy_rate_bp: int = 400
    pending_policy_rate: tuple[int, int] | None = None

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
                holdings = sum(self.bond_holdings_cents.get(bank.bank_id, {}).values())
                bank.capital_cents = self.ledger.net_worth(bank.bank_id) + holdings

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
            "skus": {sku: asdict(row) for sku, row in sorted(self.skus.items())},
            "goods_transactions": [asdict(row) for row in self.goods_transactions],
            "goods_sales_by_tick": {
                tick: dict(sorted(values.items()))
                for tick, values in sorted(self.goods_sales_by_tick.items())
            },
            "goods_price_qty_by_tick": {
                tick: dict(sorted(values.items()))
                for tick, values in sorted(self.goods_price_qty_by_tick.items())
            },
            "goods_price_value_by_tick": {
                tick: dict(sorted(values.items()))
                for tick, values in sorted(self.goods_price_value_by_tick.items())
            },
            "goods_last_price_cents": dict(sorted(self.goods_last_price_cents.items())),
            "food_on_hand": {
                agent_id: dict(sorted(values.items()))
                for agent_id, values in sorted(self.food_on_hand.items())
            },
            "durables": {
                durable_id: asdict(row) for durable_id, row in sorted(self.durables.items())
            },
            "basket": asdict(self.basket) if self.basket is not None else None,
            "cpi_history_bp": dict(sorted(self.cpi_history_bp.items())),
            "cpi_core_history_bp": dict(sorted(self.cpi_core_history_bp.items())),
            "cpi_fisher_history_bp": dict(sorted(self.cpi_fisher_history_bp.items())),
            "cpi_category_history_bp": {
                category: dict(sorted(values.items()))
                for category, values in sorted(self.cpi_category_history_bp.items())
            },
            "initial_inventory_value_cents": self.initial_inventory_value_cents,
            "gross_income_by_tick": {
                tick: dict(sorted(values.items()))
                for tick, values in sorted(self.gross_income_by_tick.items())
            },
            "gross_wages_by_tick": {
                tick: dict(sorted(values.items()))
                for tick, values in sorted(self.gross_wages_by_tick.items())
            },
            "loan_applications": {
                row_id: asdict(row) for row_id, row in sorted(self.loan_applications.items())
            },
            "loans": {row_id: asdict(row) for row_id, row in sorted(self.loans.items())},
            "loan_payments": [asdict(row) for row in self.loan_payments],
            "tax_assessments": {
                row_id: asdict(row) for row_id, row in sorted(self.tax_assessments.items())
            },
            "bonds": {symbol: asdict(row) for symbol, row in sorted(self.bonds.items())},
            "bond_holdings_cents": {
                holder_id: dict(sorted(values.items()))
                for holder_id, values in sorted(self.bond_holdings_cents.items())
            },
            "treasury": asdict(self.treasury),
            "exchange": self.exchange.dump(),
            "ventures": self.ventures.dump(),
            "policy_rate_bp": self.policy_rate_bp,
            "pending_policy_rate": self.pending_policy_rate,
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
        skus = state.get("skus", {})
        goods_transactions = state.get("goods_transactions", ())
        goods_sales_by_tick = state.get("goods_sales_by_tick", {})
        goods_price_qty_by_tick = state.get("goods_price_qty_by_tick", {})
        goods_price_value_by_tick = state.get("goods_price_value_by_tick", {})
        goods_last_price_cents = state.get("goods_last_price_cents", {})
        food_on_hand = state.get("food_on_hand", {})
        durables = state.get("durables", {})
        basket = state.get("basket")
        cpi_history_bp = state.get("cpi_history_bp", {})
        cpi_core_history_bp = state.get("cpi_core_history_bp", {})
        cpi_fisher_history_bp = state.get("cpi_fisher_history_bp", {})
        cpi_category_history_bp = state.get("cpi_category_history_bp", {})
        gross_income_by_tick = state.get("gross_income_by_tick", {})
        gross_wages_by_tick = state.get("gross_wages_by_tick", {})
        loan_applications = state.get("loan_applications", {})
        loans = state.get("loans", {})
        loan_payments = state.get("loan_payments", ())
        tax_assessments = state.get("tax_assessments", {})
        bonds = state.get("bonds", {})
        bond_holdings_cents = state.get("bond_holdings_cents", {})
        treasury = state.get("treasury", {})
        exchange = state.get("exchange", {})
        ventures = state.get("ventures", {})
        pending_policy_rate = state.get("pending_policy_rate")
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
            skus,
            goods_sales_by_tick,
            goods_price_qty_by_tick,
            goods_price_value_by_tick,
            goods_last_price_cents,
            food_on_hand,
            durables,
            cpi_history_bp,
            cpi_core_history_bp,
            cpi_fisher_history_bp,
            cpi_category_history_bp,
            gross_income_by_tick,
            gross_wages_by_tick,
            loan_applications,
            loans,
            tax_assessments,
            bonds,
            bond_holdings_cents,
            treasury,
            exchange,
            ventures,
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
        self.skus = {
            str(sku): SkuState(
                **{
                    **dict(row),
                    "sectors": tuple(cast(Any, row).get("sectors", ())),
                }
            )
            for sku, row in sorted(skus.items())
            if isinstance(row, Mapping)
        }
        if not isinstance(goods_transactions, list | tuple):
            raise ValueError("economy goods transactions must be a sequence")
        self.goods_transactions = [
            GoodsTransactionState(**dict(row))
            for row in goods_transactions
            if isinstance(row, Mapping)
        ]
        self.goods_sales_by_tick = {
            int(tick): {str(inventory_id): int(qty) for inventory_id, qty in row.items()}
            for tick, row in sorted(goods_sales_by_tick.items())
            if isinstance(row, Mapping)
        }
        self.goods_price_qty_by_tick = {
            int(tick): {str(sku): int(qty) for sku, qty in row.items()}
            for tick, row in sorted(goods_price_qty_by_tick.items())
            if isinstance(row, Mapping)
        }
        self.goods_price_value_by_tick = {
            int(tick): {str(sku): int(value) for sku, value in row.items()}
            for tick, row in sorted(goods_price_value_by_tick.items())
            if isinstance(row, Mapping)
        }
        self.goods_last_price_cents = {
            str(sku): int(price) for sku, price in sorted(goods_last_price_cents.items())
        }
        if self.goods_transactions and not self.goods_price_qty_by_tick:
            for transaction in self.goods_transactions:
                qty = self.goods_price_qty_by_tick.setdefault(transaction.tick, {})
                value = self.goods_price_value_by_tick.setdefault(transaction.tick, {})
                qty[transaction.sku] = qty.get(transaction.sku, 0) + transaction.qty
                value[transaction.sku] = (
                    value.get(transaction.sku, 0) + transaction.unit_price_cents * transaction.qty
                )
            for tick, qty_by_sku in sorted(self.goods_price_qty_by_tick.items()):
                for sku, sku_qty in qty_by_sku.items():
                    self.goods_last_price_cents[sku] = (
                        self.goods_price_value_by_tick[tick][sku] // sku_qty
                    )
        self.food_on_hand = {
            str(agent_id): {str(sku): int(qty) for sku, qty in row.items()}
            for agent_id, row in sorted(food_on_hand.items())
            if isinstance(row, Mapping)
        }
        self.durables = {
            str(durable_id): DurableState(**dict(row))
            for durable_id, row in sorted(durables.items())
            if isinstance(row, Mapping)
        }
        self.basket = BasketState(**dict(basket)) if isinstance(basket, Mapping) else None
        self.cpi_history_bp = _int_history(cpi_history_bp)
        self.cpi_core_history_bp = _int_history(cpi_core_history_bp)
        self.cpi_fisher_history_bp = _int_history(cpi_fisher_history_bp)
        self.cpi_category_history_bp = {
            str(category): _int_history(values)
            for category, values in sorted(cpi_category_history_bp.items())
            if isinstance(values, Mapping)
        }
        self.initial_inventory_value_cents = int(state.get("initial_inventory_value_cents", 0))
        self.gross_income_by_tick = {
            int(tick): {str(agent_id): int(cents) for agent_id, cents in row.items()}
            for tick, row in sorted(gross_income_by_tick.items())
            if isinstance(row, Mapping)
        }
        self.gross_wages_by_tick = {
            int(tick): {str(agent_id): int(cents) for agent_id, cents in row.items()}
            for tick, row in sorted(gross_wages_by_tick.items())
            if isinstance(row, Mapping)
        }
        self.loan_applications = {
            str(row_id): LoanApplicationState(
                **{
                    **dict(row),
                    "reason_codes": tuple(cast(Any, row).get("reason_codes", ())),
                }
            )
            for row_id, row in sorted(loan_applications.items())
            if isinstance(row, Mapping)
        }
        self.loans = {
            str(row_id): LoanState(**dict(row))
            for row_id, row in sorted(loans.items())
            if isinstance(row, Mapping)
        }
        if not isinstance(loan_payments, list | tuple):
            raise ValueError("economy loan payments must be a sequence")
        self.loan_payments = [
            LoanPaymentState(**dict(row)) for row in loan_payments if isinstance(row, Mapping)
        ]
        self.tax_assessments = {
            str(row_id): TaxAssessmentState(**dict(row))
            for row_id, row in sorted(tax_assessments.items())
            if isinstance(row, Mapping)
        }
        self.bonds = {
            str(symbol): BondState(**dict(row))
            for symbol, row in sorted(bonds.items())
            if isinstance(row, Mapping)
        }
        self.bond_holdings_cents = {
            str(holder_id): {str(symbol): int(value) for symbol, value in row.items()}
            for holder_id, row in sorted(bond_holdings_cents.items())
            if isinstance(row, Mapping)
        }
        self.treasury = TreasuryState(**dict(treasury))
        self.exchange = ExchangeState.load(cast(Mapping[str, Any], exchange))
        self.ventures = VentureState.load(cast(Mapping[str, Any], ventures))
        self.policy_rate_bp = int(state.get("policy_rate_bp", 400))
        self.pending_policy_rate = (
            (int(pending_policy_rate[0]), int(pending_policy_rate[1]))
            if isinstance(pending_policy_rate, list | tuple) and len(pending_policy_rate) == 2
            else None
        )


class EconomyWorldState:
    """Kernel invariant view combining M1 population with the M2 ledger."""

    def __init__(
        self,
        population: AgentPopulation,
        economy: EconomyState,
        *,
        ticks_per_year: int,
    ) -> None:
        self.population_state = population
        self.economy = economy
        self.ticks_per_year = ticks_per_year

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

    def price_inflation_yoy_bp(self) -> int | None:
        current = self.economy.cpi_history_bp.get(self.tick)
        prior = self.economy.cpi_history_bp.get(self.tick - self.ticks_per_year)
        if current is None or prior is None or prior <= 0:
            return None
        return 10_000 * (current - prior) // prior

    def interest_imbalance_cents(self) -> int:
        return sum(
            abs(
                loan.total_interest_scheduled_cents
                - loan.total_interest_paid_cents
                - loan.total_interest_forgiven_cents
            )
            for loan in self.economy.loans.values()
            if loan.status in {"repaid", "written_off"}
        )

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

    def order_invariant_failures(self) -> Mapping[str, object]:
        state = self.economy.exchange
        failures: dict[str, object] = {}
        open_orders = [
            order for order in state.orders.values() if order.status in {"open", "partial"}
        ]
        expected_reserved: dict[tuple[str, str], int] = {}
        expected_escrow: dict[str, int] = {}
        books: dict[str, dict[str, list[int]]] = {}
        for order in open_orders:
            if order.remaining_qty <= 0:
                failures[f"order:{order.order_id}:remaining"] = order.remaining_qty
            if order.reserved_cents < 0 or order.reserved_qty < 0:
                failures[f"order:{order.order_id}:reservation"] = {
                    "cents": order.reserved_cents,
                    "qty": order.reserved_qty,
                }
            expected_escrow[order.trader_id] = (
                expected_escrow.get(order.trader_id, 0) + order.reserved_cents
            )
            if order.side == "sell" and "opens_short" not in order.flags:
                key = (order.trader_id, order.symbol)
                expected_reserved[key] = expected_reserved.get(key, 0) + order.reserved_qty
            if order.limit_price_cents is not None:
                book = books.setdefault(order.symbol, {"buy": [], "sell": []})
                book[order.side].append(order.limit_price_cents)
        for short in state.shorts.values():
            if short.status == "open":
                expected_escrow[short.trader_id] = (
                    expected_escrow.get(short.trader_id, 0) + short.collateral_cents
                )
        for ipo in state.ipos.values():
            if ipo.status != "announced":
                continue
            for investor_id, (qty, limit_price) in ipo.indications.items():
                expected_escrow[investor_id] = (
                    expected_escrow.get(investor_id, 0) + qty * limit_price
                )
        for holding in state.holdings.values():
            expected = expected_reserved.get((holding.holder_id, holding.symbol), 0)
            if holding.reserved_qty != expected:
                failures[f"holding:{holding.holder_id}:{holding.symbol}:reserved"] = {
                    "expected": expected,
                    "actual": holding.reserved_qty,
                }
        actual_escrow: dict[str, int] = {}
        for account in self.economy.ledger.accounts():
            if account.code == "esc":
                actual_escrow[account.owner_id] = (
                    actual_escrow.get(account.owner_id, 0) + account.balance_cents
                )
        for owner_id in sorted(set(expected_escrow) | set(actual_escrow)):
            expected = expected_escrow.get(owner_id, 0)
            actual = actual_escrow.get(owner_id, 0)
            if expected != actual:
                failures[f"escrow:{owner_id}"] = {
                    "expected": expected,
                    "actual": actual,
                }
        for symbol, book in sorted(books.items()):
            if book["buy"] and book["sell"] and max(book["buy"]) >= min(book["sell"]):
                failures[f"book:{symbol}:crossed"] = {
                    "best_bid": max(book["buy"]),
                    "best_ask": min(book["sell"]),
                }
        return failures

    def share_invariant_failures(self) -> Mapping[str, object]:
        state = self.economy.exchange
        failures: dict[str, object] = {}
        for holding in state.holdings.values():
            if holding.reserved_qty < 0 or holding.locked_qty < 0:
                failures[f"holding:{holding.holder_id}:{holding.symbol}"] = {
                    "reserved_qty": holding.reserved_qty,
                    "locked_qty": holding.locked_qty,
                }
        for symbol, security in sorted(state.securities.items()):
            actual = sum(row.qty for row in state.holdings.values() if row.symbol == symbol)
            if actual != security.shares_outstanding:
                failures[f"security:{symbol}"] = {
                    "expected": security.shares_outstanding,
                    "actual": actual,
                }
        return failures

    def cap_table_invariant_failures(self) -> Mapping[str, object]:
        ventures = self.economy.ventures
        failures: dict[str, object] = {}
        for key, row in sorted(ventures.cap_table.items()):
            if row.shares < 0:
                failures[f"row:{key}"] = row.shares
        for startup in ventures.startups.values():
            shares = ventures.shares(startup.firm_id)
            if startup.status == "active" and shares <= 0:
                failures[f"startup:{startup.startup_id}"] = shares
        return failures

    def chain_ok(self) -> bool:
        return self.population_state.chain_ok()
