from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from polis.config.runtime import RuntimeOverlay
from polis.config.settings import Settings
from polis.economy.credit import CreditContext, write_off_loan
from polis.economy.exchange.engine import ExchangeEngine
from polis.economy.fiscal import (
    FiscalContext,
    finance_deficit,
    government_transfer_legs,
)
from polis.economy.labour import progressive_income_tax_cents
from polis.economy.ledger import Leg, account_id, bank_of, parse_account_id
from polis.economy.money import allocate, bp
from polis.economy.state import EconomyState, LoanState
from polis.events.kinds import (
    FIRED,
    LOAN_PAYMENT_MADE,
    LOAN_REPAID,
    PAYROLL_SHORTFALL,
    WAGE_PAID,
)
from polis.events.types import Event, NewEvent

Emit = Callable[[NewEvent], Event]


@dataclass(slots=True)
class EconomyLedgerPort:
    """C20's narrow read/write ledger port, backed by the canonical C11 ledger."""

    economy: EconomyState
    settings: Settings
    population: Any
    emit_at: Callable[[int, NewEvent], Event]

    def balance(self, account_id: str) -> int:
        return self.economy.ledger.balance(account_id)

    def liquid(self, owner_id: str) -> int:
        return self.economy.ledger.liquid(owner_id)

    def accounts_of(self, owner_id: str) -> tuple[str, ...]:
        return self.economy.ledger.accounts_of(owner_id)

    def transfer(self, src: str, dst: str, amount_cents: int, reason: str) -> list[Leg]:
        return self.economy.ledger.transfer(src, dst, amount_cents, reason)

    def government_transfer(self, dst: str, amount_cents: int) -> list[Leg]:
        return list(government_transfer_legs(dst, amount_cents, self.economy))

    def post_transaction(
        self,
        legs: Sequence[Any],
        *,
        tick: int,
        cause: Any,
        allow_negative: frozenset[str] = frozenset(),
    ) -> Any:
        return self.economy.ledger.post_transaction(
            legs,
            tick=tick,
            cause=cause,
            allow_negative=allow_negative,
        )

    def next_txn_id(self, tick: int) -> Any:
        return self.economy.ledger.next_txn_id(tick)

    def record_government_spending(self, amount_cents: int, tick: int) -> None:
        self.economy.treasury.spending_cents += amount_cents
        self.economy.treasury.period_spending_cents += amount_cents
        finance_deficit(
            tick,
            ctx=FiscalContext(self.settings, self.population, self.economy),
            emit=lambda draft: self.emit_at(tick, draft),
        )
        self.economy.sync_denormalised(self.population)

    def allocate(
        self,
        pool_cents: int,
        weights: Sequence[tuple[str, int]],
    ) -> dict[str, int]:
        return allocate(pool_cents, weights)

    def ensure_agent_account(self, agent_id: str, tick: int) -> str:
        existing = tuple(
            account
            for account in self.economy.ledger.accounts_of(agent_id)
            if parse_account_id(account)[0] == "dep" and self.economy.ledger.is_open(account)
        )
        if existing:
            return min(existing)
        bank_id = min(
            bank.bank_id
            for bank in self.economy.banks.values()
            if not bank.is_central and bank.status == "active"
        )
        return self.economy.ledger.open_account(
            "dep",
            agent_id,
            "agent",
            bank_id=bank_id,
            tick=tick,
        )


@dataclass(slots=True)
class EconomyEmploymentPort:
    economy: EconomyState

    def income_cents(self, agent_id: str, tick: int) -> int:
        del tick
        return sum(
            employment.wage_cents
            for employment in self.economy.employments.values()
            if employment.agent_id == agent_id and employment.ended_tick is None
        )


class EconomyEstatePort:
    """C15-owned settlement bridge used by C20's atomic death transaction.

    The bridge owns order cancellation, employment termination, debt recovery/write-off,
    estate tax, cash distribution and firm-interest transfer. C20 supplies the trigger and
    heir weights and owns household, relationship, memory and lifecycle effects.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        runtime: RuntimeOverlay,
        economy: EconomyState,
        exchange: ExchangeEngine,
        credit: CreditContext,
        emit_at: Callable[[int, NewEvent], Event],
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self.economy = economy
        self.exchange = exchange
        self.credit = credit
        self.emit_at = emit_at

    def case_for(self, agent_id: str, tick: int) -> Literal["A", "B", "C", "D"]:
        del tick
        if any(
            row.entity_id == agent_id and row.status == "open"
            for row in self.economy.ventures.bankruptcies.values()
        ):
            return "A"
        if self._owns_firm(agent_id):
            return "D"
        debt = sum(
            row.outstanding_cents
            for row in self._loans(agent_id)
            if row.status not in {"repaid", "written_off"}
        )
        return "B" if debt > self.economy.ledger.liquid(agent_id) else "C"

    def estate_account_id(self, agent_id: str, tick: int) -> str:
        bank_id = next(
            (
                bank_of(value)
                for value in self._liquid_accounts(agent_id)
                if bank_of(value) is not None
            ),
            None,
        )
        if bank_id is None:
            bank_id = min(
                bank.bank_id
                for bank in self.economy.banks.values()
                if not bank.is_central and bank.status == "active"
            )
        return account_id(
            "esc",
            agent_id,
            bank_id=bank_id,
            ref=f"estate-{tick}",
        )

    def gross_cents(self, agent_id: str) -> int:
        return sum(
            max(0, self.economy.ledger.balance(value)) for value in self._liquid_accounts(agent_id)
        )

    def open_order_count(self, agent_id: str) -> int:
        return sum(
            row.trader_id == agent_id and row.status in {"open", "partial"}
            for row in self.economy.exchange.orders.values()
        )

    def open_loan_count(self, agent_id: str) -> int:
        return sum(
            row.borrower_id == agent_id and row.status not in {"repaid", "written_off"}
            for row in self.economy.loans.values()
        )

    def settle_death(
        self,
        agent_id: str,
        tick: int,
        *,
        heirs: Sequence[tuple[str, int]] | None,
        ctx: Any,
    ) -> Sequence[Event]:
        snapshot = copy.deepcopy(self.economy.dump())
        ctx.add_rollback(lambda: self.economy.load(snapshot))
        cause_event = ctx.cause_event
        if cause_event is None:
            raise RuntimeError("estate settlement requires the persisted ESTATE_OPENED event")

        def emit(draft: NewEvent) -> Event:
            return self.emit_at(tick, draft)

        events: list[Event] = []

        # 1. Release exchange reservations before valuing or moving the estate.
        cancelled, _order_ids, _released_cents, _released_shares = self.exchange.cancel_entity(
            agent_id,
            tick,
            emit,
            initiator="death",
        )
        events.extend(cancelled)

        # 2. End employment and stop future wage accrual.
        events.extend(
            self._terminate_employment(
                agent_id,
                tick,
                emit,
                settle_accrued=getattr(ctx, "cause", None) != "emigrated",
            )
        )

        case = self.case_for(agent_id, tick)
        if case == "A":
            # The open bankruptcy case owns the waterfall and the accounts stay open.
            ctx.residual_cents = 0
            return tuple(events)

        escrow_account = self.estate_account_id(agent_id, tick)
        _code, _owner, escrow_bank, escrow_ref = parse_account_id(escrow_account)
        if escrow_bank is None or escrow_ref is None:
            raise RuntimeError("estate escrow account is not canonical")
        self.economy.ledger.open_account(
            "esc",
            agent_id,
            "estate",
            bank_id=escrow_bank,
            ref=escrow_ref,
            tick=tick,
        )
        ctx.txn_ids.extend(
            self._sweep_deposits_to_escrow(
                agent_id,
                escrow_account,
                tick,
                cause=cause_event,
            )
        )

        # 3. Firm and security interests pass without creating or destroying cash/shares.
        self._transfer_interests(agent_id, tuple(heirs or ()))

        # 4. Recover secured/unsecured loan principal, then book the lender loss.
        for loan in self._loans(agent_id):
            if loan.status in {"repaid", "written_off"} or loan.outstanding_cents <= 0:
                continue
            available = self._estate_liquid(agent_id)
            recovery = min(available, loan.outstanding_cents)
            if recovery:
                event = self._pay_loan(
                    loan,
                    recovery,
                    tick,
                    emit,
                    cause=ctx.cause_event,
                )
                events.append(event)
                ctx.paid_cents += recovery
                ctx.txn_ids.append(str(event.payload["txn_id"]))
            residual = loan.outstanding_cents
            if residual:
                written = write_off_loan(
                    loan.loan_id,
                    residual,
                    recovery,
                    tick,
                    ctx=self.credit,
                    emit=emit,
                )
                events.extend(written)
                ctx.written_off_cents += residual
                ctx.txn_ids.extend(
                    str(event.payload["txn_id"])
                    for event in written
                    if event.payload.get("txn_id") is not None
                )
            ctx.creditors.append(
                {
                    "creditor_id": loan.lender_id,
                    "loan_id": loan.loan_id,
                    "paid_cents": recovery,
                    "written_off_cents": residual,
                }
            )

        # Emigration carries the remaining balance outside the resident estate path.
        if getattr(ctx, "cause", None) == "emigrated":
            escrow_balance = self.economy.ledger.balance(escrow_account)
            if escrow_balance:
                destination = self._ensure_account(
                    agent_id,
                    "dep",
                    escrow_bank,
                    tick,
                )
                txn_id = self.economy.ledger.post_transaction(
                    self.economy.ledger.transfer(
                        escrow_account,
                        destination,
                        escrow_balance,
                        "transfer",
                    ),
                    tick=tick,
                    cause=cause_event,
                )
                ctx.txn_ids.append(str(txn_id))
            self.economy.ledger.close_account(escrow_account, tick=tick)
            ctx.residual_cents = 0
            return tuple(events)

        # 5. Apply estate tax before allocating the exact residual.
        gross_residual = self._estate_liquid(agent_id)
        tax_rate = self.runtime.bp("tax.inheritance_bp", tick)
        tax = bp(gross_residual, tax_rate)
        if tax:
            txn_ids = self._move_owner_value(
                agent_id,
                "gv_treasury",
                tax,
                "tax",
                tick,
                cause=cause_event,
            )
            ctx.txn_ids.extend(txn_ids)
        ctx.tax_cents = tax

        distributable = self._estate_liquid(agent_id)
        weights = tuple((heir_id, weight) for heir_id, weight in heirs or () if weight > 0)
        if weights and distributable:
            shares = allocate(distributable, weights)
            ctx.heirs = list(sorted(shares.items()))
            for heir_id, cents in sorted(shares.items()):
                if cents:
                    ctx.txn_ids.extend(
                        self._move_owner_value(
                            agent_id,
                            heir_id,
                            cents,
                            "transfer",
                            tick,
                            cause=cause_event,
                        )
                    )
            ctx.distributable_cents = distributable
        elif distributable:
            ctx.txn_ids.extend(
                self._move_owner_value(
                    agent_id,
                    "gv_treasury",
                    distributable,
                    "transfer",
                    tick,
                    cause=cause_event,
                )
            )
            ctx.escheated_cents = distributable

        residual = self._estate_liquid(agent_id)
        if residual:
            raise RuntimeError(f"estate left {residual} liquid cents for {agent_id}")
        if self.economy.ledger.balance(escrow_account) != 0:
            raise RuntimeError(f"estate escrow did not drain for {agent_id}")
        self.economy.ledger.close_account(escrow_account, tick=tick)
        self._close_zero_accounts(agent_id, tick)
        ctx.residual_cents = 0
        return tuple(events)

    def _estate_liquid(self, owner_id: str) -> int:
        return sum(
            self.economy.ledger.balance(account) for account in self._liquid_accounts(owner_id)
        )

    def _sweep_deposits_to_escrow(
        self,
        owner_id: str,
        escrow_account: str,
        tick: int,
        *,
        cause: Event,
    ) -> list[str]:
        txn_ids: list[str] = []
        for source in self._liquid_accounts(owner_id):
            if source == escrow_account or parse_account_id(source)[0] != "dep":
                continue
            cents = max(0, self.economy.ledger.balance(source))
            if cents == 0:
                continue
            txn_id = self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(
                    source,
                    escrow_account,
                    cents,
                    "transfer",
                ),
                tick=tick,
                cause=cause,
            )
            txn_ids.append(str(txn_id))
        return txn_ids

    def _loans(self, agent_id: str) -> tuple[LoanState, ...]:
        return tuple(
            sorted(
                (row for row in self.economy.loans.values() if row.borrower_id == agent_id),
                key=lambda row: row.loan_id,
            )
        )

    def _owns_firm(self, agent_id: str) -> bool:
        return any(
            row.founder_id == agent_id and row.status not in {"dissolved", "acquired"}
            for row in self.economy.firms.values()
        ) or any(
            row.holder_id == agent_id and row.shares > 0
            for row in self.economy.ventures.cap_table.values()
        )

    def _terminate_employment(
        self,
        agent_id: str,
        tick: int,
        emit: Emit,
        *,
        settle_accrued: bool,
    ) -> tuple[Event, ...]:
        events: list[Event] = []
        for employment in sorted(
            self.economy.employments.values(),
            key=lambda row: row.employment_id,
        ):
            if employment.agent_id != agent_id or employment.ended_tick is not None:
                continue
            wage_events = (
                self._settle_accrued_wage(employment, tick, emit) if settle_accrued else ()
            )
            events.extend(wage_events)
            employment.ended_tick = tick
            firm = self.economy.firms.get(employment.firm_id)
            if firm is not None:
                firm.headcount = max(0, firm.headcount - 1)
            events.append(
                emit(
                    NewEvent(
                        FIRED,
                        {
                            "employment_id": employment.employment_id,
                            "agent_id": agent_id,
                            "firm_id": employment.firm_id,
                            "reason": "death",
                            "severance_cents": 0,
                            "notice_ticks": 0,
                        },
                        actor_id=employment.firm_id,
                        subject_ids=(agent_id,),
                    )
                )
            )
        return tuple(events)

    def _settle_accrued_wage(
        self,
        employment: Any,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        gross = employment.accrued_wage_cents
        if gross <= 0:
            return ()
        firm = self.economy.firms[employment.firm_id]
        income_tax = progressive_income_tax_cents(
            gross,
            self.settings.treasury.tax.income_brackets,
        )
        employer_tax = bp(gross, self.settings.treasury.tax.payroll_employer_bp)
        total_cost = gross + employer_tax
        available = self.economy.ledger.liquid(firm.firm_id)
        if available < total_cost:
            employment.accrued_wage_cents = 0
            return (
                emit(
                    NewEvent(
                        PAYROLL_SHORTFALL,
                        {
                            "firm_id": firm.firm_id,
                            "required_cents": total_cost,
                            "available_cents": available,
                            "unpaid_employment_ids": [employment.employment_id],
                            "accrued_claim_cents": gross,
                        },
                        actor_id=firm.firm_id,
                        subject_ids=(employment.agent_id,),
                    )
                ),
            )
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
                    "net_cents": gross - income_tax,
                    "hours_bp": employment.hours_bp,
                    "txn_id": str(expected),
                },
                actor_id=firm.firm_id,
                subject_ids=(employment.agent_id,),
            )
        )
        destination = next(
            account
            for account in self.economy.ledger.accounts_of(employment.agent_id)
            if parse_account_id(account)[0] == "dep" and self.economy.ledger.is_open(account)
        )
        legs: list[Leg] = []
        if gross > income_tax:
            legs.extend(
                self.economy.ledger.transfer(
                    firm.ledger_account_id,
                    destination,
                    gross - income_tax,
                    "wage",
                )
            )
        tax_total = income_tax + employer_tax
        if tax_total:
            treasury = next(
                account
                for account in self.economy.ledger.accounts_of("gv_treasury")
                if parse_account_id(account)[0] == "dep" and self.economy.ledger.is_open(account)
            )
            legs.extend(
                self.economy.ledger.transfer(
                    firm.ledger_account_id,
                    treasury,
                    tax_total,
                    "tax",
                )
            )
        transaction_id = self.economy.ledger.post_transaction(
            legs,
            tick=tick,
            cause=wage_event,
        )
        if transaction_id != expected:
            raise RuntimeError("terminal wage transaction ordinal diverged")
        employment.total_paid_cents += gross
        employment.accrued_wage_cents = 0
        firm.cumulative_wage_cents += gross
        income = self.economy.gross_income_by_tick.setdefault(tick, {})
        income[employment.agent_id] = income.get(employment.agent_id, 0) + gross
        wages = self.economy.gross_wages_by_tick.setdefault(tick, {})
        wages[employment.agent_id] = wages.get(employment.agent_id, 0) + gross
        return (wage_event,)

    def _pay_loan(
        self,
        loan: LoanState,
        cents: int,
        tick: int,
        emit: Emit,
        *,
        cause: Event | None,
    ) -> Event:
        source = (
            self._source_with_balance(loan.borrower_id, cents)
            if loan.lender_id == "gv_treasury"
            else self._fund_lender_deposit(
                loan.borrower_id,
                loan.lender_id,
                cents,
                tick,
                cause=cause,
            )
        )
        expected = self.economy.ledger.next_txn_id(tick)
        event = emit(
            NewEvent(
                LOAN_PAYMENT_MADE,
                {
                    "loan_id": loan.loan_id,
                    "payment_no": loan.payments_made + 1,
                    "principal_cents": cents,
                    "interest_cents": 0,
                    "outstanding_after_cents": loan.outstanding_cents - cents,
                    "txn_id": str(expected),
                },
                actor_id=loan.borrower_id,
                subject_ids=(loan.lender_id,),
            )
        )
        if loan.lender_id == "gv_treasury":
            treasury = self._ensure_liquid_account("gv_treasury", bank_of(source), tick)
            legs = self.economy.ledger.transfer(source, treasury, cents, "tax")
        else:
            bank = self.economy.banks[loan.lender_id]
            legs = [
                Leg(source, -1, cents, "loan"),
                Leg(bank.deposit_liability_account_id, 1, cents, "loan"),
            ]
        legs.extend(
            (
                Leg(loan.lender_receivable_account_id, -1, cents, "loan"),
                Leg(loan.borrower_payable_account_id, 1, cents, "loan"),
            )
        )
        transaction_id = self.economy.ledger.post_transaction(legs, tick=tick, cause=event)
        if transaction_id != expected:
            raise RuntimeError("estate loan payment ordinal diverged")
        loan.outstanding_cents -= cents
        loan.payments_made += 1
        if loan.outstanding_cents == 0:
            loan.status = "repaid"
            loan.closed_tick = tick
            emit(
                NewEvent(
                    LOAN_REPAID,
                    {
                        "loan_id": loan.loan_id,
                        "total_interest_cents": loan.total_interest_paid_cents,
                        "ticks_to_repay": tick - loan.originated_tick,
                        "early": tick < loan.matures_tick,
                    },
                    actor_id=loan.borrower_id,
                    subject_ids=(loan.lender_id,),
                )
            )
        return event

    def _source_with_balance(self, owner_id: str, cents: int) -> str:
        source = next(
            (
                account
                for account in self._liquid_accounts(owner_id)
                if self.economy.ledger.balance(account) >= cents
            ),
            None,
        )
        if source is None:
            raise RuntimeError(f"no liquid account can fund {cents} cents for {owner_id}")
        return source

    def _fund_lender_deposit(
        self,
        owner_id: str,
        lender_id: str,
        cents: int,
        tick: int,
        *,
        cause: Event | None,
    ) -> str:
        target = self._ensure_liquid_account(owner_id, lender_id, tick)
        shortfall = cents - max(0, self.economy.ledger.balance(target))
        if shortfall <= 0:
            return target
        self._move_between_owner_accounts(owner_id, target, shortfall, tick, cause=cause)
        if self.economy.ledger.balance(target) < cents:
            raise RuntimeError(f"cannot fund estate debt account for {owner_id}")
        return target

    def _move_owner_value(
        self,
        source_owner: str,
        destination_owner: str,
        cents: int,
        reason: str,
        tick: int,
        *,
        cause: Any,
    ) -> list[str]:
        remaining = cents
        txn_ids: list[str] = []
        for source in self._liquid_accounts(source_owner):
            if remaining <= 0:
                break
            available = max(0, self.economy.ledger.balance(source))
            amount = min(remaining, available)
            if amount <= 0:
                continue
            destination = self._matching_destination(destination_owner, source, tick)
            transaction_id = self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(source, destination, amount, reason),
                tick=tick,
                cause=cause,
            )
            txn_ids.append(str(transaction_id))
            remaining -= amount
        if remaining:
            raise RuntimeError(f"{source_owner} estate is short by {remaining} cents")
        return txn_ids

    def _move_between_owner_accounts(
        self,
        owner_id: str,
        target: str,
        cents: int,
        tick: int,
        *,
        cause: Event | None,
    ) -> None:
        remaining = cents
        if cause is None:
            raise RuntimeError("estate consolidation requires a persisted cause event")
        for source in self._liquid_accounts(owner_id):
            if source == target or remaining <= 0:
                continue
            available = max(0, self.economy.ledger.balance(source))
            amount = min(remaining, available)
            if amount <= 0:
                continue
            self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(source, target, amount, "transfer"),
                tick=tick,
                cause=cause,
            )
            remaining -= amount

    def _matching_destination(self, owner_id: str, source: str, tick: int) -> str:
        source_code, _owner, source_bank, _ref = parse_account_id(source)
        code: Literal["cash", "dep"] = "cash" if source_code == "cash" else "dep"
        bank_id = None if code == "cash" else source_bank
        return self._ensure_account(owner_id, code, bank_id, tick)

    def _ensure_liquid_account(
        self,
        owner_id: str,
        preferred_bank: str | None,
        tick: int,
    ) -> str:
        if preferred_bank is not None:
            return self._ensure_account(owner_id, "dep", preferred_bank, tick)
        accounts = self._liquid_accounts(owner_id)
        if accounts:
            return accounts[0]
        return self._ensure_account(owner_id, "cash", None, tick)

    def _ensure_account(
        self,
        owner_id: str,
        code: Literal["cash", "dep"],
        bank_id: str | None,
        tick: int,
    ) -> str:
        candidate = account_id(code, owner_id, bank_id=bank_id)
        if self.economy.ledger.is_open(candidate):
            return candidate
        owner_type = (
            "government"
            if owner_id == "gv_treasury"
            else "firm"
            if owner_id in self.economy.firms
            else "agent"
        )
        return self.economy.ledger.open_account(
            code,
            owner_id,
            owner_type,
            bank_id=bank_id,
            tick=tick,
        )

    def _liquid_accounts(self, owner_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                account
                for account in self.economy.ledger.accounts_of(owner_id)
                if parse_account_id(account)[0] in {"cash", "dep", "esc"}
                and self.economy.ledger.is_open(account)
            )
        )

    def _close_zero_accounts(self, owner_id: str, tick: int) -> None:
        for value in self.economy.ledger.accounts_of(owner_id):
            if self.economy.ledger.is_open(value) and self.economy.ledger.balance(value) == 0:
                self.economy.ledger.close_account(value, tick=tick)

    def _transfer_interests(
        self,
        agent_id: str,
        heirs: tuple[tuple[str, int], ...],
    ) -> None:
        recipients = heirs or (("gv_treasury", 1),)
        for holding in tuple(self.economy.exchange.holdings.values()):
            if holding.holder_id != agent_id or holding.qty <= 0:
                continue
            allocations = allocate(holding.qty, recipients)
            for recipient, qty in sorted(allocations.items()):
                if qty:
                    target = self.economy.exchange.holding(recipient, holding.symbol)
                    target.qty += qty
            holding.qty = 0
            holding.reserved_qty = 0
            holding.locked_qty = 0
        for key, row in tuple(sorted(self.economy.ventures.cap_table.items())):
            if row.holder_id != agent_id or row.shares <= 0:
                continue
            allocations = allocate(row.shares, recipients)
            del self.economy.ventures.cap_table[key]
            for recipient, shares in sorted(allocations.items()):
                if shares <= 0:
                    continue
                target_key = self.economy.ventures.cap_key(
                    row.firm_id,
                    recipient,
                    row.share_class,
                )
                cap_target = self.economy.ventures.cap_table.get(target_key)
                if cap_target is None:
                    cap_target = copy.deepcopy(row)
                    cap_target.holder_id = recipient
                    cap_target.shares = 0
                    self.economy.ventures.cap_table[target_key] = cap_target
                cap_target.shares += shares
        primary = min(recipients, key=lambda row: (-row[1], row[0]))[0]
        for firm in self.economy.firms.values():
            if firm.founder_id == agent_id:
                firm.founder_id = primary
