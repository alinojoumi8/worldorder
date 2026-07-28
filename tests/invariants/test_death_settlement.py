from __future__ import annotations

import pytest

from polis.agents.actions import ActionType, make_action
from polis.economy.credit import LoanDecision, LoanRequest, originate
from polis.economy.invariants import check_ledger, check_money
from polis.economy.ledger import parse_account_id
from polis.economy.state import EconomyWorldState
from polis.economy.venture_state import BankruptcyCaseState
from polis.events.kinds import (
    AGENT_DIED,
    ESTATE_CLOSED,
    ESTATE_DEBTS_SETTLED,
    ESTATE_DISTRIBUTED,
    ESTATE_OPENED,
    HOUSEHOLD_LEFT,
    LOAN_PAYMENT_MADE,
    LOAN_REPAID,
    LOAN_WRITTEN_OFF,
    PAYROLL_SHORTFALL,
    WAGE_PAID,
)
from polis.events.types import NewEvent
from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_plain_death_closes_to_zero_and_preserves_money() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    decedent = next(
        agent
        for agent in result.population.alive()
        if settler.estate.case_for(agent.agent_id, 2) == "C"
        and not settler.intestacy_shares(agent.agent_id, 1)
    )
    money_before = result.economy.ledger.base_money_imbalance_cents()
    estate, events = settler.settle(decedent.agent_id, "mortality", 2)
    result.economy.sync_denormalised(result.population)
    result.economy.ledger.commit_tick(2)

    assert estate.escheated_cents + estate.tax_cents == estate.gross_cents
    assert settler.ledger.liquid(decedent.agent_id) == 0
    assert result.economy.ledger.balance(estate.escrow_account_id) == 0
    assert not result.economy.ledger.is_open(estate.escrow_account_id)
    assert {ESTATE_OPENED, ESTATE_DEBTS_SETTLED, ESTATE_DISTRIBUTED, ESTATE_CLOSED, AGENT_DIED} <= {
        event.kind for event in events
    }
    assert check_ledger(result.economy.ledger).invariant_id == "INV-LEDGER"
    assert check_money(result.economy.ledger, result.economy).invariant_id == "INV-MONEY"
    assert result.economy.ledger.base_money_imbalance_cents() == money_before


@pytest.mark.asyncio
async def test_death_settles_accrued_wages_before_closing_accounts() -> None:
    result = await demography_result(ticks=5)
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    employment = next(
        row
        for row in result.economy.employments.values()
        if row.ended_tick is None and settler.estate.case_for(row.agent_id, 6) != "A"
    )
    employment.accrued_wage_cents = 1_000

    _estate, events = settler.settle(employment.agent_id, "mortality", 6)
    result.economy.sync_denormalised(result.population)

    assert employment.accrued_wage_cents == 0
    assert employment.ended_tick == 6
    assert any(event.kind == WAGE_PAID for event in events)
    assert check_money(result.economy.ledger, result.economy).invariant_id == "INV-MONEY"


@pytest.mark.asyncio
async def test_terminal_wage_reopens_a_missing_deposit_account() -> None:
    result = await demography_result(ticks=5)
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    employment = next(
        row
        for row in result.economy.employments.values()
        if row.ended_tick is None and settler.estate.case_for(row.agent_id, 6) != "A"
    )
    treasury = next(
        account
        for account in result.economy.ledger.accounts_of("gv_treasury")
        if parse_account_id(account)[0] == "dep"
    )
    cause = settler.log.stage(
        NewEvent(
            HOUSEHOLD_LEFT,
            {
                "agent_id": employment.agent_id,
                "household_id": result.population[employment.agent_id].household_id,
                "reason": "test_account_cleanup",
            },
            actor_id=employment.agent_id,
            subject_ids=(employment.agent_id,),
        ),
        tick=6,
        sim_time=settler.clock.sim_time_at(6),
    )
    for account in result.economy.ledger.accounts_of(employment.agent_id):
        if parse_account_id(account)[0] != "dep":
            continue
        balance = result.economy.ledger.balance(account)
        if balance:
            result.economy.ledger.post_transaction(
                result.economy.ledger.transfer(account, treasury, balance, "transfer"),
                tick=6,
                cause=cause,
            )
        result.economy.ledger.close_account(account, tick=6)
    employment.accrued_wage_cents = 1_000

    _estate, events = settler.settle(employment.agent_id, "mortality", 6)

    assert any(event.kind == WAGE_PAID for event in events)
    assert employment.accrued_wage_cents == 0


@pytest.mark.asyncio
async def test_terminal_wage_missing_firm_is_an_audited_shortfall() -> None:
    result = await demography_result(ticks=5)
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    employment = next(row for row in result.economy.employments.values() if row.ended_tick is None)
    employment.accrued_wage_cents = 1_000
    result.economy.firms.pop(employment.firm_id)

    events = settler.estate._settle_accrued_wage(
        employment,
        6,
        lambda draft: settler.log.stage(
            draft,
            tick=6,
            sim_time=settler.clock.sim_time_at(6),
        ),
    )

    assert [event.kind for event in events] == [PAYROLL_SHORTFALL]
    assert employment.accrued_wage_cents == 0


@pytest.mark.asyncio
async def test_death_records_unfunded_terminal_wage_claim_without_creating_money() -> None:
    result = await demography_result(ticks=5)
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    employment = next(
        row
        for row in result.economy.employments.values()
        if row.ended_tick is None and settler.estate.case_for(row.agent_id, 6) != "A"
    )
    firm = result.economy.firms[employment.firm_id]
    employment.accrued_wage_cents = result.economy.ledger.liquid(firm.firm_id) + 1

    _estate, events = settler.settle(employment.agent_id, "mortality", 6)
    result.economy.sync_denormalised(result.population)

    shortfall = next(event for event in events if event.kind == PAYROLL_SHORTFALL)
    assert shortfall.payload["unpaid_employment_ids"] == [employment.employment_id]
    assert employment.accrued_wage_cents == 0
    assert check_money(result.economy.ledger, result.economy).invariant_id == "INV-MONEY"


@pytest.mark.asyncio
async def test_insolvent_death_writes_off_credit_without_moving_cash() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    port = settler.estate
    borrower = next(
        agent for agent in result.population.alive() if port.case_for(agent.agent_id, 2) == "C"
    )
    lender = min(bank.bank_id for bank in result.economy.banks.values() if not bank.is_central)

    def emit(draft: NewEvent):
        return settler.log.stage(
            draft,
            tick=2,
            sim_time=settler.clock.sim_time_at(2),
        )

    originated = originate(
        LoanRequest(
            borrower.agent_id,
            lender,
            1_000_000,
            "consumer",
            360,
            {},
            1_000_000,
        ),
        LoanDecision(True, 8_000, {}, 1_000_000, 500, 360, ()),
        2,
        ctx=port.credit,
        emit=emit,
    )
    cause = originated[0]
    treasury = next(
        account
        for account in result.economy.ledger.accounts_of("gv_treasury")
        if parse_account_id(account)[0] == "dep"
    )
    for source in result.economy.ledger.accounts_of(borrower.agent_id):
        if parse_account_id(source)[0] != "dep":
            continue
        cents = result.economy.ledger.balance(source)
        if cents:
            result.economy.ledger.post_transaction(
                result.economy.ledger.transfer(source, treasury, cents, "transfer"),
                tick=2,
                cause=cause,
            )
    result.economy.sync_denormalised(result.population)
    result.economy.ledger.commit_tick(2)
    money_before = sum(
        account.balance_cents
        for account in result.economy.ledger.accounts()
        if account.code in {"cash", "dep", "esc"}
    )

    estate, events = settler.settle(borrower.agent_id, "mortality", 3)
    money_after = sum(
        account.balance_cents
        for account in result.economy.ledger.accounts()
        if account.code in {"cash", "dep", "esc"}
    )

    assert estate.written_off_cents == 1_000_000
    assert any(event.kind == LOAN_WRITTEN_OFF for event in events)
    assert money_after == money_before


@pytest.mark.asyncio
async def test_fully_recovered_estate_loan_reports_payment_and_repayment() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    port = settler.estate
    borrower = max(
        (agent for agent in result.population.alive() if port.case_for(agent.agent_id, 2) == "C"),
        key=lambda agent: result.economy.ledger.liquid(agent.agent_id),
    )
    lender = min(bank.bank_id for bank in result.economy.banks.values() if not bank.is_central)

    def emit(draft: NewEvent):
        return settler.log.stage(
            draft,
            tick=2,
            sim_time=settler.clock.sim_time_at(2),
        )

    originate(
        LoanRequest(
            borrower.agent_id,
            lender,
            1_000,
            "consumer",
            360,
            {},
            1_000_000,
        ),
        LoanDecision(True, 8_000, {}, 1_000, 500, 360, ()),
        2,
        ctx=port.credit,
        emit=emit,
    )

    estate, events = settler.settle(borrower.agent_id, "mortality", 3)

    assert estate.debts_cents == 1_000
    assert any(event.kind == LOAN_PAYMENT_MADE for event in events)
    assert any(event.kind == LOAN_REPAID for event in events)


@pytest.mark.asyncio
async def test_case_a_defers_and_leaves_decedent_accounts_open() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    decedent = next(iter(result.population.alive()))
    result.economy.ventures.bankruptcies["bkcase_test"] = BankruptcyCaseState(
        "bkcase_test",
        decedent.agent_id,
        "agent",
        "test",
        1,
        2,
        2,
        20,
    )
    accounts = result.economy.ledger.accounts_of(decedent.agent_id)

    settler.settle(decedent.agent_id, "mortality", 3)

    assert accounts
    assert all(result.economy.ledger.is_open(account) for account in accounts)


@pytest.mark.asyncio
async def test_orders_loan_and_escheat_close_together_without_leak() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    port = settler.estate
    decedent = max(
        (
            agent
            for agent in result.population.alive()
            if port.case_for(agent.agent_id, 2) == "C"
            and not settler.intestacy_shares(agent.agent_id, 1)
        ),
        key=lambda agent: result.economy.ledger.liquid(agent.agent_id),
    )

    def emit(draft: NewEvent):
        return settler.log.stage(
            draft,
            tick=2,
            sim_time=settler.clock.sim_time_at(2),
        )

    exchange = port.exchange
    exchange.list_security(
        symbol="EST",
        issuer_firm_id=min(result.economy.firms),
        shares_outstanding=1_000,
        listing_price_cents=100,
        tick=2,
        emit=emit,
        holders={decedent.agent_id: 1_000},
    )
    order_specs = (("buy", 10, 80), ("buy", 11, 81), ("sell", 12, 120))
    for ordinal, (side, qty, price) in enumerate(order_specs):
        action = make_action(
            actor_id=decedent.agent_id,
            tick=2,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "EST",
                "side": side,
                "order_type": "limit",
                "qty": qty,
                "limit_price_cents": price,
            },
        )
        order, _events = exchange._admit(action, "EST", ordinal, 2, True, emit)
        assert order is not None

    lender = min(bank.bank_id for bank in result.economy.banks.values() if not bank.is_central)
    originated = originate(
        LoanRequest(
            decedent.agent_id,
            lender,
            1_000_000,
            "consumer",
            360,
            {},
            1_000_000,
        ),
        LoanDecision(True, 8_000, {}, 1_000_000, 500, 360, ()),
        2,
        ctx=port.credit,
        emit=emit,
    )
    treasury = next(
        account
        for account in result.economy.ledger.accounts_of("gv_treasury")
        if parse_account_id(account)[0] == "dep"
    )
    for source in result.economy.ledger.accounts_of(decedent.agent_id):
        if parse_account_id(source)[0] != "dep":
            continue
        cents = result.economy.ledger.balance(source)
        if cents:
            result.economy.ledger.post_transaction(
                result.economy.ledger.transfer(source, treasury, cents, "transfer"),
                tick=2,
                cause=originated[0],
            )
    result.economy.sync_denormalised(result.population)
    result.economy.ledger.commit_tick(2)
    money_before = result.economy.ledger.base_money_imbalance_cents()

    estate, _events = settler.settle(decedent.agent_id, "mortality", 3)
    view = EconomyWorldState(result.population, result.economy, ticks_per_year=365)

    assert port.open_order_count(decedent.agent_id) == 0
    assert estate.written_off_cents > 0
    assert estate.heirs == ()
    assert result.economy.ledger.balance(estate.escrow_account_id) == 0
    assert not result.economy.ledger.is_open(estate.escrow_account_id)
    assert view.order_invariant_failures() == {}
    assert view.share_invariant_failures() == {}
    assert check_ledger(result.economy.ledger).invariant_id == "INV-LEDGER"
    assert check_money(result.economy.ledger, result.economy).invariant_id == "INV-MONEY"
    assert result.economy.ledger.base_money_imbalance_cents() == money_before
