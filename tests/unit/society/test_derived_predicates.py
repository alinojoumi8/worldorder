from __future__ import annotations

from types import MappingProxyType

from polis.agents.actions import ActionType, ValidationContext, make_action
from polis.agents.actions.params.banking import DefaultParams
from polis.agents.actions.params.exchange import SubmitOrderParams
from polis.agents.actions.params.law import TestifyParams as LawTestifyParams
from polis.agents.actions.params.media import ClaimParams, ClaimReference
from polis.agents.actions.params.ventures import PitchParams
from polis.society.law import (
    ContractBreachPredicate,
    EmbezzlementPredicate,
    FraudPredicate,
    InsiderTradingPredicate,
    MnpiIndex,
    Obligation,
    ObligationIndex,
    PerjuryPredicate,
)
from tests.law_support import Memories, checker, clock, event, law_cfg, log, runtime


def _ctx(**repositories: object) -> ValidationContext:
    return ValidationContext(
        observation=object(),
        state=object(),
        tick=10,
        repositories=MappingProxyType(repositories),
    )


def _claim(*, value: object, source_seq: int = 7) -> ClaimParams:
    return ClaimParams(
        claim_id="cl_one",
        text="The firm is profitable.",
        refers_to=ClaimReference(
            entity_id="fi_one",
            predicate="firm.profitable",
            value=value,
            as_of_tick=9,
        ),
        sourced_to_event_seqs=(source_seq,),
    )


def test_fraud_requires_a_false_claim_and_reliance() -> None:
    event_log = log()
    action = make_action(
        actor_id="ag_actor",
        tick=10,
        action_type=ActionType.PITCH,
        params={
            "startup_id": "fi_one",
            "investor_id": "ag_victim",
            "ask_cents": 1_000,
            "pre_money_ask_cents": 5_000,
            "deck_text": "profitable",
        },
    )
    params = PitchParams.model_validate(action.params)
    claim = _claim(value=True)
    action_id = str(action.action_id)
    base = {
        "checker": checker(
            event_log,
            facts=(("firm.profitable", "fi_one", 9, False, (6,)),),
        ),
        "claims_by_action": {action_id: (claim,)},
        "reliance_by_action": {action_id: {"counterparty_id": "ag_victim", "amount_cents": 1_000}},
    }

    verdict = FraudPredicate().test(action, params, _ctx(**base))
    assert verdict is not None
    assert (verdict.crime_type, verdict.victim_id, verdict.amount_cents) == (
        "fraud",
        "ag_victim",
        1_000,
    )
    assert (
        FraudPredicate().test(
            action,
            params,
            _ctx(**{**base, "reliance_by_action": {}}),
        )
        is None
    )


def test_insider_trading_obeys_mnpi_and_effective_enforcement_flag() -> None:
    source = event(7, 1, 9010, subjects=("fi_one",))
    memories = Memories({("ag_actor", 7)})
    mnpi = MnpiIndex(
        memories=memories,
        cfg=law_cfg(),
        clock=clock(),
        events=(source,),
        issuer_for_symbol=lambda _symbol: "fi_one",
    )
    action = make_action(
        actor_id="ag_actor",
        tick=10,
        action_type=ActionType.SUBMIT_ORDER,
        params={
            "symbol": "ONE",
            "side": "buy",
            "order_type": "market",
            "qty": 1,
        },
    )
    params = SubmitOrderParams.model_validate(action.params)
    active = runtime()

    assert (
        InsiderTradingPredicate().test(
            action,
            params,
            _ctx(runtime=active, mnpi=mnpi),
        )
        is not None
    )
    active.enact(
        "regulation.finance.insider_trading_enforced",
        False,
        10,
        "py_disable",
        1,
        enacted_tick=9,
    )
    assert (
        InsiderTradingPredicate().test(
            action,
            params,
            _ctx(runtime=active, mnpi=mnpi),
        )
        is None
    )


def test_embezzlement_excludes_authorised_payroll_and_dividends() -> None:
    action = make_action(
        actor_id="ag_actor",
        tick=10,
        action_type=ActionType.IDLE,
        params={},
    )
    action_id = str(action.action_id)
    row = {
        "has_firm_authority": True,
        "from_owner_id": "fi_one",
        "to_owner_id": "ag_actor",
        "amount_cents": 400,
        "reason": "transfer",
    }
    verdict = EmbezzlementPredicate().test(
        action,
        DefaultParams(loan_id="lo_one", amount_cents=1),
        _ctx(transfers_by_action={action_id: row}),
    )
    assert verdict is not None and verdict.crime_type == "embezzlement"
    assert (
        EmbezzlementPredicate().test(
            action,
            DefaultParams(loan_id="lo_one", amount_cents=1),
            _ctx(transfers_by_action={action_id: {**row, "reason": "payroll"}}),
        )
        is None
    )


def test_contract_breach_requires_capacity_to_perform() -> None:
    action = make_action(
        actor_id="ag_actor",
        tick=10,
        action_type=ActionType.DEFAULT,
        params={"loan_id": "lo_one", "amount_cents": 500},
    )
    params = DefaultParams.model_validate(action.params)
    obligation = Obligation("ob_one", "ag_actor", "ba_one", 500, 9)
    funded = ObligationIndex(lambda _agent_id: 500)
    funded.add(obligation)
    insolvent = ObligationIndex(lambda _agent_id: 499)
    insolvent.add(obligation)

    assert ContractBreachPredicate().test(action, params, _ctx(obligations=funded)) is not None
    assert ContractBreachPredicate().test(action, params, _ctx(obligations=insolvent)) is None


def test_perjury_requires_first_hand_memory_of_a_contradicted_matter() -> None:
    event_log = log()
    action = make_action(
        actor_id="ag_actor",
        tick=10,
        action_type=ActionType.TESTIFY,
        params={
            "case_id": "ca_one",
            "statement": "The firm is profitable.",
            "claims": (_claim(value=True).model_dump(),),
        },
    )
    params = LawTestifyParams.model_validate(action.params)
    fact_checker = checker(
        event_log,
        facts=(("firm.profitable", "fi_one", 9, False, (6,)),),
    )

    assert (
        PerjuryPredicate().test(
            action,
            params,
            _ctx(checker=fact_checker, memories=Memories({("ag_actor", 7)})),
        )
        is not None
    )
    assert (
        PerjuryPredicate().test(
            action,
            params,
            _ctx(checker=fact_checker, memories=Memories()),
        )
        is None
    )
