from __future__ import annotations

from types import MappingProxyType

from polis.agents.actions import ActionType, ValidationContext, make_action
from polis.agents.actions.params import PARAMS_MODELS
from polis.agents.actions.params.media import ClaimParams, ClaimReference
from polis.society.law import (
    LawLegalityOracle,
    MemoryCrimeRepository,
    MnpiIndex,
    Obligation,
    ObligationIndex,
)
from tests.law_support import Memories, checker, clock, event, law_cfg, log, runtime


def test_five_derived_crimes_occur_with_zero_commit_crime_actions() -> None:
    event_log = log()
    configured_clock = clock()
    configured_runtime = runtime()
    cfg = law_cfg()
    repo = MemoryCrimeRepository()
    memories = Memories({("ag_actor", 7), ("ag_actor", 9)})
    fact_checker = checker(
        event_log,
        facts=(("firm.profitable", "fi_one", 99, False, (6,)),),
    )
    obligations = ObligationIndex(lambda _agent_id: 500)
    obligations.add(Obligation("ob_one", "ag_actor", "ba_one", 500, 99))
    oracle = LawLegalityOracle(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        mnpi=MnpiIndex(
            memories=memories,
            cfg=cfg,
            clock=configured_clock,
            events=(event(7, 90, 9010, subjects=("fi_one",)),),
            issuer_for_symbol=lambda _symbol: "fi_one",
        ),
        obligations=obligations,
        checker=fact_checker,
        memories=memories,
        repo=repo,
        cfg=cfg,
    )
    false_claim = ClaimParams(
        claim_id="cl_false",
        text="The firm is profitable.",
        refers_to=ClaimReference(
            entity_id="fi_one",
            predicate="firm.profitable",
            value=True,
            as_of_tick=99,
        ),
        sourced_to_event_seqs=(9,),
    )
    actions = (
        make_action(
            actor_id="ag_actor",
            tick=100,
            action_type=ActionType.PITCH,
            params={
                "startup_id": "fi_one",
                "investor_id": "ag_victim",
                "ask_cents": 500,
                "pre_money_ask_cents": 1_000,
                "deck_text": "profitable",
            },
            ordinal=1,
        ),
        make_action(
            actor_id="ag_actor",
            tick=100,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "ONE",
                "side": "buy",
                "order_type": "market",
                "qty": 1,
            },
            ordinal=2,
        ),
        make_action(
            actor_id="ag_actor",
            tick=100,
            action_type=ActionType.IDLE,
            params={},
            ordinal=3,
        ),
        make_action(
            actor_id="ag_actor",
            tick=100,
            action_type=ActionType.DEFAULT,
            params={"loan_id": "lo_one", "amount_cents": 500},
            ordinal=4,
        ),
        make_action(
            actor_id="ag_actor",
            tick=100,
            action_type=ActionType.TESTIFY,
            params={
                "case_id": "ca_one",
                "statement": "The firm is profitable.",
                "claims": (false_claim.model_dump(),),
            },
            ordinal=5,
        ),
    )
    action_ids = [str(action.action_id) for action in actions]
    repositories = {
        "claims_by_action": {action_ids[0]: (false_claim,)},
        "reliance_by_action": {
            action_ids[0]: {
                "counterparty_id": "ag_victim",
                "amount_cents": 500,
            }
        },
        "transfers_by_action": {
            action_ids[2]: {
                "has_firm_authority": True,
                "from_owner_id": "fi_one",
                "to_owner_id": "ag_actor",
                "amount_cents": 300,
                "reason": "transfer",
            }
        },
    }
    assert all(action.type is not ActionType.COMMIT_CRIME for action in actions)

    for action in actions:
        oracle.assess(
            action,
            PARAMS_MODELS[action.type].model_validate(action.params),
            ValidationContext(
                observation=object(),
                state=object(),
                tick=100,
                repositories=MappingProxyType(repositories),
            ),
        )

    assert {crime.type for crime in repo.all()} == {
        "fraud",
        "insider_trading",
        "embezzlement",
        "contract_breach",
        "perjury",
    }
    assert all(0 <= crime.tick < 300 for crime in repo.all())
