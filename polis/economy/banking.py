from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from polis.agents.state import AgentPopulation
from polis.config.settings import Settings
from polis.economy.central import (
    CentralContext,
    apply_pending_policy,
    set_policy_rate,
    settle_banks,
)
from polis.economy.credit import (
    CreditContext,
    CreditEngine,
    LoanRequest,
    borrower_state,
    decide,
    decide_with_underwriting,
    originate,
)
from polis.economy.fiscal import FiscalContext, fiscal_step
from polis.economy.ledger import Leg, bank_of, parse_account_id
from polis.economy.money import mint
from polis.economy.state import EconomyState, LoanApplicationState
from polis.events.kinds import (
    BANK_RUN_DETECTED,
    DEPOSIT_INTEREST_PAID,
    LOAN_APPLICATION_DECIDED,
    LOAN_APPLICATION_SUBMITTED,
    WITHDRAWAL_MADE,
    WITHDRAWAL_REFUSED,
)
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.llm.router import LLMRouter

Emit = Callable[[NewEvent], Event]


@dataclass(frozen=True, slots=True)
class WithdrawalRequest:
    owner_id: str
    bank_id: str
    cents: int


class BankingEngine:
    def __init__(
        self,
        settings: Settings,
        population: AgentPopulation,
        economy: EconomyState,
        rng: RngRegistry,
        router: LLMRouter | None = None,
    ) -> None:
        self.settings = settings
        self.population = population
        self.economy = economy
        self.rng = rng
        self.router = router
        self.credit_context = CreditContext(settings, population, economy)
        self.credit = CreditEngine(self.credit_context)
        self.central_context = CentralContext(settings, economy, rng)
        self.fiscal_context = FiscalContext(settings, population, economy)

    async def step(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        apply_pending_policy(tick, self.economy)
        events: list[Event] = []
        events.extend(await self._mechanical_credit(tick, emit))
        events.extend(self.credit.accrue_interest(tick, emit))
        events.extend(self.credit.amortise(tick, emit))
        events.extend(self._pay_deposit_interest(tick, emit))
        events.extend(self.credit.write_off_due(tick, emit))
        events.extend(
            fiscal_step(
                tick,
                ctx=self.fiscal_context,
                credit=self.credit_context,
                emit=emit,
            )
        )
        events.extend(
            settle_banks(
                tick,
                ctx=self.central_context,
                credit=self.credit_context,
                emit=emit,
            )
        )
        events.extend(set_policy_rate(tick, ctx=self.central_context, emit=emit))
        return tuple(events)

    async def _mechanical_credit(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        cadence = 90 * self.settings.clock.ticks_per_sim_day
        if tick % cadence != 1:
            return ()
        events: list[Event] = []
        for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
            if firm.status != "active" or any(
                loan.borrower_id == firm.firm_id
                and loan.status in {"current", "delinquent", "default"}
                for loan in self.economy.loans.values()
            ):
                continue
            lender_id = bank_of(firm.ledger_account_id)
            if lender_id is None or lender_id not in self.economy.banks:
                continue
            principal = max(
                100_000,
                self.settings.economy.median_wage_cents * max(1, firm.target_headcount) // 24 // 2,
            )
            request = LoanRequest(
                firm.firm_id,
                lender_id,
                principal,
                "corporate",
                360 * self.settings.clock.ticks_per_sim_day,
                {"kind": "firm_capital", "firm_id": firm.firm_id},
                max(principal, firm.capital_cents),
                "working capital",
            )
            application_id = mint(
                "lnapp",
                tick,
                len(self.economy.loan_applications),
            )
            application = LoanApplicationState(
                application_id,
                firm.firm_id,
                lender_id,
                principal,
                request.purpose,
                request.term_ticks,
                dict(request.collateral),
                tick,
            )
            self.economy.loan_applications[application_id] = application
            events.append(
                emit(
                    NewEvent(
                        LOAN_APPLICATION_SUBMITTED,
                        {
                            "application_id": application_id,
                            "borrower_id": firm.firm_id,
                            "lender_id": lender_id,
                            "requested_cents": principal,
                            "purpose": request.purpose,
                            "term_ticks": request.term_ticks,
                            "collateral": dict(request.collateral),
                        },
                        actor_id=firm.firm_id,
                        subject_ids=(lender_id,),
                    )
                )
            )
            borrower = borrower_state(firm.firm_id, self.credit_context)
            if self.settings.banking.underwriting == "llm":
                if self.router is None:
                    raise RuntimeError("LLM underwriting requires an LLMRouter")
                decision = await decide_with_underwriting(
                    request,
                    borrower,
                    self.economy.banks[lender_id],
                    tick=tick,
                    ctx=self.credit_context,
                    router=self.router,
                )
            else:
                decision = decide(
                    request,
                    borrower,
                    self.economy.banks[lender_id],
                    ctx=self.credit_context,
                )
            application.status = "approved" if decision.approved else "denied"
            application.score_bp = decision.score_bp
            application.offered_cents = decision.offered_cents
            application.offered_rate_bp = decision.annual_rate_bp
            application.reason_codes = decision.reason_codes
            events.append(
                emit(
                    NewEvent(
                        LOAN_APPLICATION_DECIDED,
                        {
                            "application_id": application_id,
                            "approved": decision.approved,
                            "credit_score_bp": decision.score_bp,
                            "score_components": dict(decision.components),
                            "offered_rate_bp": decision.annual_rate_bp,
                            "offered_cents": decision.offered_cents,
                            "reason_codes": list(decision.reason_codes),
                            "llm_call_id": decision.llm_call_id,
                        },
                        actor_id=lender_id,
                        subject_ids=(firm.firm_id,),
                    )
                )
            )
            if decision.approved:
                events.extend(
                    originate(
                        request,
                        decision,
                        tick,
                        ctx=self.credit_context,
                        emit=emit,
                    )
                )
        return tuple(events)

    def _pay_deposit_interest(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        month = 30 * self.settings.clock.ticks_per_sim_day
        if tick % month != 28 * self.settings.clock.ticks_per_sim_day:
            return ()
        events: list[Event] = []
        for bank in sorted(self.economy.banks.values(), key=lambda row: row.bank_id):
            if bank.is_central or bank.status != "active":
                continue
            credits: list[tuple[str, int]] = []
            for account in self.economy.ledger.accounts():
                if (
                    account.code == "dep"
                    and account.bank_id == bank.bank_id
                    and account.balance_cents > 0
                ):
                    cents = (
                        account.balance_cents
                        * self.settings.banking.deposit_rate_bp
                        // 10_000
                        // 12
                    )
                    if cents:
                        credits.append((account.account_id, cents))
            total = sum(cents for _account, cents in credits)
            if total <= 0:
                continue
            expected = self.economy.ledger.next_txn_id(tick)
            event = emit(
                NewEvent(
                    DEPOSIT_INTEREST_PAID,
                    {
                        "bank_id": bank.bank_id,
                        "total_cents": total,
                        "accounts_n": len(credits),
                        "rate_bp": self.settings.banking.deposit_rate_bp,
                        "txn_id": str(expected),
                    },
                    actor_id=bank.bank_id,
                )
            )
            legs = [Leg(account_id, 1, cents, "interest") for account_id, cents in credits]
            legs.append(Leg(bank.deposit_liability_account_id, -1, total, "interest"))
            transaction_id = self.economy.ledger.post_transaction(
                tuple(legs),
                tick=tick,
                cause=event,
            )
            if transaction_id != expected:
                raise RuntimeError("deposit-interest transaction ordinal diverged")
            events.append(event)
        return tuple(events)

    def process_withdrawals(
        self,
        requests: Sequence[WithdrawalRequest],
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        events: list[Event] = []
        by_bank: dict[str, list[WithdrawalRequest]] = {}
        for request in requests:
            by_bank.setdefault(request.bank_id, []).append(request)
        for bank_id, rows in sorted(by_bank.items()):
            bank = self.economy.banks[bank_id]
            deposits_before = -self.economy.ledger.balance(bank.deposit_liability_account_id)
            ordered = sorted(rows, key=lambda row: (row.cents, row.owner_id))
            self.rng.get("banking.queue", bank_id, tick).shuffle(ordered)
            requested = sum(row.cents for row in ordered)
            served = 0
            refused = 0
            for position, request in enumerate(ordered, start=1):
                deposit = next(
                    (
                        account_id
                        for account_id in self.economy.ledger.accounts_of(request.owner_id)
                        if parse_account_id(account_id)[0] == "dep"
                        and parse_account_id(account_id)[2] == bank_id
                    ),
                    None,
                )
                available = max(
                    0,
                    self.economy.ledger.balance(bank.reserve_account_id),
                )
                owner_available = self.economy.ledger.balance(deposit) if deposit is not None else 0
                if deposit is None or min(available, owner_available) < request.cents:
                    refused += 1
                    events.append(
                        emit(
                            NewEvent(
                                WITHDRAWAL_REFUSED,
                                {
                                    "owner_id": request.owner_id,
                                    "bank_id": bank_id,
                                    "requested_cents": request.cents,
                                    "available_cents": min(available, owner_available),
                                    "queue_position": position,
                                },
                                actor_id=request.owner_id,
                                subject_ids=(bank_id,),
                            )
                        )
                    )
                    continue
                cash = next(
                    (
                        account_id
                        for account_id in self.economy.ledger.accounts_of(request.owner_id)
                        if parse_account_id(account_id)[0] == "cash"
                    ),
                    None,
                )
                if cash is None:
                    owner_type = "firm" if request.owner_id in self.economy.firms else "agent"
                    cash = self.economy.ledger.open_account(
                        "cash",
                        request.owner_id,
                        owner_type,
                        tick=tick,
                    )
                expected = self.economy.ledger.next_txn_id(tick)
                event = emit(
                    NewEvent(
                        WITHDRAWAL_MADE,
                        {
                            "owner_id": request.owner_id,
                            "bank_id": bank_id,
                            "cents": request.cents,
                            "txn_id": str(expected),
                        },
                        actor_id=request.owner_id,
                        subject_ids=(bank_id,),
                    )
                )
                self.economy.ledger.post_transaction(
                    (
                        Leg(deposit, -1, request.cents, "withdrawal"),
                        Leg(
                            bank.deposit_liability_account_id,
                            1,
                            request.cents,
                            "withdrawal",
                        ),
                        Leg(
                            bank.reserve_account_id,
                            -1,
                            request.cents,
                            "withdrawal",
                        ),
                        Leg(cash, 1, request.cents, "withdrawal"),
                    ),
                    tick=tick,
                    cause=event,
                )
                served += request.cents
                events.append(event)
            if refused:
                deposits_after = -self.economy.ledger.balance(bank.deposit_liability_account_id)
                events.append(
                    emit(
                        NewEvent(
                            BANK_RUN_DETECTED,
                            {
                                "bank_id": bank_id,
                                "requested_cents": requested,
                                "served_cents": served,
                                "refused_n": refused,
                                "deposits_before_cents": deposits_before,
                                "deposits_after_cents": deposits_after,
                            },
                            subject_ids=(bank_id,),
                        )
                    )
                )
        return tuple(events)
