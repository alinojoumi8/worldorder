from __future__ import annotations

import socket
from datetime import datetime
from pathlib import Path
from random import Random

import pytest

from polis.agents.genesis import generate_agents
from polis.config.settings import Settings, load_settings
from polis.economy.banking import BankingEngine, WithdrawalRequest
from polis.economy.central import CentralContext, resolve_failure
from polis.economy.credit import (
    BorrowerState,
    CreditContext,
    CreditEngine,
    LoanDecision,
    LoanRequest,
    MarketState,
    borrower_state,
    capital_cents,
    credit_score_bp,
    decide,
    decide_with_underwriting,
    originate,
    schedule,
    write_off_loan,
)
from polis.economy.fiscal import (
    FiscalContext,
    convert_arrears,
    finance_deficit,
    government_transfer_legs,
    treasury_account,
)
from polis.economy.genesis import create_economy
from polis.economy.invariants import m0_cents, m1_cents
from polis.economy.ledger import bank_of
from polis.economy.state import EconomyWorldState, TaxAssessmentState
from polis.events.kinds import (
    BANK_FAILED,
    BANK_RUN_DETECTED,
    BOND_AUCTION_CLEARED,
    BOND_ISSUED,
    LOAN_ORIGINATED,
    LOAN_PAYMENT_MADE,
    LOAN_WRITTEN_OFF,
    TRANSFER_PAID,
    WITHDRAWAL_REFUSED,
)
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import NewEvent
from polis.kernel.rng import RngRegistry
from polis.llm.router import LLMRouter
from polis.simulation import run_id_for
from polis.world.generator import generate_world

ROOT = Path(__file__).resolve().parents[3]


def configured() -> Settings:
    return load_settings(ROOT / "configs" / "m2-smoke.yaml")


def economy_fixture() -> tuple[Settings, object, object, object, RngRegistry, EventLog]:
    settings = configured()
    rng = RngRegistry(settings.run.seed)
    world = generate_world(settings.world, rng)
    population = generate_agents(settings.population, world, rng)
    log = EventLog(run_id_for(settings), MemoryEventSink())
    result = create_economy(
        settings,
        population,
        world,
        rng,
        run_id_for(settings),
        emit=lambda draft: log.stage(
            draft,
            tick=0,
            sim_time=datetime(2025, 1, 1),
        ),
    )
    return settings, world, population, result.state, rng, log


def test_credit_score_has_neutral_laplace_history_and_hand_components() -> None:
    request = LoanRequest("ag_a", "bk_01", 100_000, "consumer", 360, {}, 50_000)
    borrower = BorrowerState(
        360,
        3_600_000,
        360_000,
        0,
        0,
        500_000,
        2_000_000,
        False,
        False,
    )

    score, components = credit_score_bp(
        borrower,
        request,
        MarketState(3_600_000, 360),
    )

    assert components == {
        "income_stability": 10_000,
        "dti": 1_000,
        "history": 5_000,
        "leverage": 2_500,
        "coverage": 5_000,
    }
    assert score == 7_875


@pytest.mark.asyncio
async def test_optional_llm_underwriting_uses_stub_and_records_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _world, population, economy, _rng, _log = economy_fixture()
    settings = settings.model_copy(
        update={"banking": settings.banking.model_copy(update={"underwriting": "llm"})}
    )
    context = CreditContext(settings, population, economy)
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    request = LoanRequest(
        firm.firm_id,
        lender_id,
        100_000,
        "corporate",
        360,
        {"kind": "firm_capital"},
        100_000,
        "working capital",
    )
    router = LLMRouter(settings=settings, run_id=run_id_for(settings))

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is forbidden in the StubProvider test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    decision = await decide_with_underwriting(
        request,
        borrower_state(firm.firm_id, context),
        economy.banks[lender_id],
        tick=1,
        ctx=context,
        router=router,
    )

    assert decision.llm_call_id is not None
    assert len(decision.llm_call_id) == 36
    await router.close()


def test_amortisation_schedule_closes_every_random_principal_exactly() -> None:
    random = Random(20260726)
    for _ in range(250):
        principal = random.randint(1, 100_000_000)
        rate = random.randint(0, 4_000)
        periods = random.randint(1, 300)
        rows = schedule(principal, rate, periods * 30, 30)

        assert sum(row.principal_cents for row in rows) == principal
        assert rows[-1].outstanding_after_cents == 0
        assert all(row.principal_cents > 0 and row.interest_cents >= 0 for row in rows)


def test_origination_creates_inside_money_without_changing_m0_or_net_worth() -> None:
    settings, _world, population, economy, _rng, log = economy_fixture()
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    context = CreditContext(settings, population, economy)
    request = LoanRequest(
        firm.firm_id,
        lender_id,
        100_000,
        "corporate",
        360,
        {"kind": "firm_capital"},
        200_000,
    )
    decision = decide(
        request,
        borrower_state(firm.firm_id, context),
        economy.banks[lender_id],
        ctx=context,
    )
    assert decision.approved
    before = (
        m0_cents(economy.ledger),
        m1_cents(economy.ledger),
        capital_cents(lender_id, economy),
        economy.ledger.net_worth(firm.firm_id),
    )

    events = originate(
        request,
        decision,
        1,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )

    assert [event.kind for event in events] == [LOAN_ORIGINATED]
    assert m0_cents(economy.ledger) == before[0]
    assert m1_cents(economy.ledger) == before[1] + decision.offered_cents
    assert capital_cents(lender_id, economy) == before[2]
    assert economy.ledger.net_worth(firm.firm_id) == before[3]
    assert economy.ledger.global_balance_cents() == 0
    assert all(value == 0 for value in economy.ledger.deposit_imbalances().values())


def test_underwriting_reports_distinct_bank_constraint_reasons() -> None:
    settings, _world, population, economy, _rng, _log = economy_fixture()
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    bank = economy.banks[lender_id]
    bank.lending_frozen = True
    context = CreditContext(settings, population, economy)
    request = LoanRequest(
        firm.firm_id,
        lender_id,
        100_000,
        "corporate",
        360,
        {},
        200_000,
    )

    decision = decide(
        request,
        borrower_state(firm.firm_id, context),
        bank,
        ctx=context,
    )

    assert not decision.approved
    assert "capital_ratio" in decision.reason_codes


def test_repayment_destroys_inside_money_and_realises_only_interest_as_income() -> None:
    settings, _world, population, economy, _rng, log = economy_fixture()
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    context = CreditContext(settings, population, economy)
    request = LoanRequest(
        firm.firm_id,
        lender_id,
        100_000,
        "corporate",
        360,
        {},
        200_000,
    )
    decision = decide(
        request,
        borrower_state(firm.firm_id, context),
        economy.banks[lender_id],
        ctx=context,
    )
    originate(
        request,
        decision,
        1,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )
    loan = next(iter(economy.loans.values()))
    loan.accrued_interest_cents = 500
    loan.next_payment_tick = 31
    before = (
        m1_cents(economy.ledger),
        capital_cents(lender_id, economy),
        economy.ledger.net_worth(firm.firm_id),
    )

    events = CreditEngine(context).amortise(
        31,
        lambda draft: log.stage(
            draft,
            tick=31,
            sim_time=datetime(2025, 2, 1),
        ),
    )
    payment = next(event for event in events if event.kind == LOAN_PAYMENT_MADE)
    principal = int(payment.payload["principal_cents"])
    interest = int(payment.payload["interest_cents"])

    assert m1_cents(economy.ledger) == before[0] - principal - interest
    assert capital_cents(lender_id, economy) == before[1] + interest
    assert economy.ledger.net_worth(firm.firm_id) == before[2] - interest


def test_final_payment_classifies_capitalised_interest_as_paid() -> None:
    settings, _world, population, economy, _rng, log = economy_fixture()
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    context = CreditContext(settings, population, economy)
    request = LoanRequest(
        firm.firm_id,
        lender_id,
        100_000,
        "corporate",
        360,
        {},
        200_000,
    )
    decision = decide(
        request,
        borrower_state(firm.firm_id, context),
        economy.banks[lender_id],
        ctx=context,
    )
    originate(
        request,
        decision,
        1,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )
    loan = next(iter(economy.loans.values()))
    loan.accrued_interest_cents = 500
    loan.missed_since_tick = 1
    engine = CreditEngine(context)
    engine._transition_missed(
        loan,
        31,
        lambda draft: log.stage(
            draft,
            tick=31,
            sim_time=datetime(2025, 2, 1),
        ),
    )
    loan.payments_n = 1
    loan.next_payment_tick = 31

    engine.amortise(
        31,
        lambda draft: log.stage(
            draft,
            tick=31,
            sim_time=datetime(2025, 2, 1),
        ),
    )

    assert loan.status == "repaid"
    assert loan.total_interest_paid_cents == 500
    assert (
        EconomyWorldState(
            population,
            economy,
            ticks_per_year=360,
        ).interest_imbalance_cents()
        == 0
    )


def test_writeoff_forgives_only_unresolved_capitalised_interest() -> None:
    settings, _world, population, economy, _rng, log = economy_fixture()
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    context = CreditContext(settings, population, economy)
    request = LoanRequest(
        firm.firm_id,
        lender_id,
        100_000,
        "corporate",
        360,
        {},
        200_000,
    )
    decision = decide(
        request,
        borrower_state(firm.firm_id, context),
        economy.banks[lender_id],
        ctx=context,
    )
    originate(
        request,
        decision,
        1,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )
    loan = next(iter(economy.loans.values()))
    loan.accrued_interest_cents = 500
    loan.missed_since_tick = 1
    CreditEngine(context)._transition_missed(
        loan,
        31,
        lambda draft: log.stage(
            draft,
            tick=31,
            sim_time=datetime(2025, 2, 1),
        ),
    )
    loan.total_interest_paid_cents = 200

    write_off_loan(
        loan.loan_id,
        loan.outstanding_cents,
        0,
        31,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=31,
            sim_time=datetime(2025, 2, 1),
        ),
    )

    assert loan.status == "written_off"
    assert loan.total_interest_forgiven_cents == 300
    assert (
        EconomyWorldState(
            population,
            economy,
            ticks_per_year=360,
        ).interest_imbalance_cents()
        == 0
    )


def test_writeoff_moves_no_money_and_transfers_the_loss_to_bank_and_borrower() -> None:
    settings, _world, population, economy, _rng, log = economy_fixture()
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    context = CreditContext(settings, population, economy)
    request = LoanRequest(
        firm.firm_id,
        lender_id,
        100_000,
        "corporate",
        360,
        {},
        200_000,
    )
    decision = decide(
        request,
        borrower_state(firm.firm_id, context),
        economy.banks[lender_id],
        ctx=context,
    )
    originate(
        request,
        decision,
        1,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )
    loan = next(iter(economy.loans.values()))
    before = (
        m0_cents(economy.ledger),
        m1_cents(economy.ledger),
        capital_cents(lender_id, economy),
        economy.ledger.net_worth(firm.firm_id),
    )

    events = write_off_loan(
        loan.loan_id,
        loan.outstanding_cents,
        0,
        2,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=2,
            sim_time=datetime(2025, 1, 3),
        ),
    )

    assert [event.kind for event in events] == [LOAN_WRITTEN_OFF]
    assert m0_cents(economy.ledger) == before[0]
    assert m1_cents(economy.ledger) == before[1]
    assert capital_cents(lender_id, economy) == before[2] - decision.offered_cents
    assert economy.ledger.net_worth(firm.firm_id) == before[3] + decision.offered_cents


def test_treasury_overdraft_is_closed_by_a_balanced_primary_bond_auction() -> None:
    settings, _world, population, economy, _rng, log = economy_fixture()
    fiscal = FiscalContext(settings, population, economy)
    recipient = next(iter(population))
    recipient_deposit = next(
        account.account_id
        for account in economy.ledger.accounts()
        if account.owner_id == recipient.agent_id and account.code == "dep"
    )
    treasury = treasury_account(economy)
    cents = 250_000
    transfer = log.stage(
        NewEvent(
            TRANSFER_PAID,
            {
                "recipient_id": recipient.agent_id,
                "programme": "welfare",
                "cents": cents,
                "txn_id": str(economy.ledger.next_txn_id(1)),
            },
        ),
        tick=1,
        sim_time=datetime(2025, 1, 2),
    )
    economy.ledger.post_transaction(
        government_transfer_legs(recipient_deposit, cents, economy),
        tick=1,
        cause=transfer,
        allow_negative=frozenset({treasury}),
    )
    assert economy.ledger.balance(treasury) < 0

    events = finance_deficit(
        1,
        ctx=fiscal,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )

    assert {event.kind for event in events} == {BOND_ISSUED, BOND_AUCTION_CLEARED}
    assert economy.ledger.balance(treasury) >= 0
    assert economy.bonds
    assert economy.ledger.global_balance_cents() == 0


def test_tax_arrears_repay_treasury_and_close_the_principal_exactly() -> None:
    settings, _world, population, economy, _rng, log = economy_fixture()
    taxpayer = next(iter(population))
    assessment = TaxAssessmentState(
        "tax_test",
        taxpayer.agent_id,
        "income",
        100_000,
        1_000,
        10_000,
        0,
        1,
    )
    economy.tax_assessments[assessment.assessment_id] = assessment
    fiscal = FiscalContext(settings, population, economy)
    events = convert_arrears(
        1,
        ctx=fiscal,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )
    assert events
    loan = next(loan for loan in economy.loans.values() if loan.purpose == "tax_arrears")
    loan.accrued_interest_cents = 100
    treasury_before = economy.ledger.balance(treasury_account(economy))

    payments = CreditEngine(CreditContext(settings, population, economy)).amortise(
        loan.next_payment_tick,
        lambda draft: log.stage(
            draft,
            tick=loan.next_payment_tick,
            sim_time=datetime(2028, 1, 1),
        ),
    )

    assert LOAN_PAYMENT_MADE in {event.kind for event in payments}
    assert loan.status == "repaid"
    assert loan.outstanding_cents == 0
    assert economy.ledger.balance(loan.borrower_payable_account_id) == 0
    assert economy.ledger.balance(loan.lender_receivable_account_id) == 0
    assert economy.ledger.balance(treasury_account(economy)) == treasury_before + 10_100
    assert economy.ledger.global_balance_cents() == 0
    assert all(value == 0 for value in economy.ledger.deposit_imbalances().values())


@pytest.mark.parametrize("resolution", ["assume", "liquidate"])
def test_failed_bank_resolution_preserves_performing_borrower_payables(
    resolution: str,
) -> None:
    settings, _world, population, economy, rng, log = economy_fixture()
    settings = settings.model_copy(
        update={"banking": settings.banking.model_copy(update={"resolution": resolution})}
    )
    firm = next(iter(economy.firms.values()))
    lender_id = bank_of(firm.ledger_account_id)
    assert lender_id is not None
    context = CreditContext(settings, population, economy)
    current_principal = 100_000
    current_request = LoanRequest(
        firm.firm_id,
        lender_id,
        current_principal,
        "corporate",
        360,
        {},
        current_principal,
    )
    forced = LoanDecision(True, 9_000, {}, current_principal, 500, 360, ())
    originate(
        current_request,
        forced,
        1,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )
    current = next(
        loan for loan in economy.loans.values() if loan.principal_cents == current_principal
    )
    payable = current.borrower_payable_account_id
    payable_before = economy.ledger.balance(payable)

    bad_principal = capital_cents(lender_id, economy) + 1_000_000
    bad_request = LoanRequest(
        firm.firm_id,
        lender_id,
        bad_principal,
        "corporate",
        360,
        {},
        bad_principal,
    )
    originate(
        bad_request,
        LoanDecision(True, 9_000, {}, bad_principal, 500, 360, ()),
        1,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )
    bad_loan = next(
        loan for loan in economy.loans.values() if loan.principal_cents == bad_principal
    )
    bad_loan.status = "default"
    write_off_loan(
        bad_loan.loan_id,
        bad_loan.outstanding_cents,
        0,
        2,
        ctx=context,
        emit=lambda draft: log.stage(
            draft,
            tick=2,
            sim_time=datetime(2025, 1, 3),
        ),
    )
    assert capital_cents(lender_id, economy) < 0
    assert economy.ledger.balance(payable) == payable_before

    events = resolve_failure(
        lender_id,
        2,
        ctx=CentralContext(settings, economy, rng),
        credit=context,
        emit=lambda draft: log.stage(
            draft,
            tick=2,
            sim_time=datetime(2025, 1, 3),
        ),
    )

    assert BANK_FAILED in {event.kind for event in events}
    assert economy.banks[lender_id].status == "failed"
    assert economy.ledger.balance(payable) == payable_before
    assert current.lender_id != lender_id
    assert all(
        account.balance_cents == 0
        for account in economy.ledger.accounts()
        if account.bank_id == lender_id and account.code in {"dep", "esc"}
    )
    assert economy.ledger.global_balance_cents() == 0
    assert all(value == 0 for value in economy.ledger.deposit_imbalances().values())


@pytest.mark.asyncio
async def test_withdrawal_refusal_is_observed_but_never_scripts_another_request() -> None:
    settings, _world, population, economy, rng, log = economy_fixture()
    engine = BankingEngine(settings, population, economy, rng)
    owner = next(iter(population))
    deposit = next(
        account
        for account in economy.ledger.accounts()
        if account.owner_id == owner.agent_id and account.code == "dep"
    )
    requested = deposit.balance_cents + 1

    events = engine.process_withdrawals(
        (WithdrawalRequest(owner.agent_id, str(deposit.bank_id), requested),),
        1,
        lambda draft: log.stage(
            draft,
            tick=1,
            sim_time=datetime(2025, 1, 2),
        ),
    )

    assert [event.kind for event in events] == [WITHDRAWAL_REFUSED, BANK_RUN_DETECTED]
    assert len([event for event in events if event.kind == WITHDRAWAL_REFUSED]) == 1
