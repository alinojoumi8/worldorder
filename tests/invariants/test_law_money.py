from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from polis.agents.actions import (
    ActionType,
    ResolutionContext,
    ValidatedAction,
    ValidationContext,
    make_action,
)
from polis.agents.actions.params.law import CommitCrimeParams
from polis.kernel.rng import RngRegistry
from polis.society.graph import SocialGraph
from polis.society.law import (
    CourtCase,
    CourtService,
    Judgment,
    LawLegalityOracle,
    LawResolver,
    MemoryCourtRepository,
    MemoryCrimeRepository,
    MnpiIndex,
    ObligationIndex,
    PenaltyService,
    PoliceService,
)
from polis.society.polity import OfficeRegister
from polis.society.protocols import BeliefChannel
from tests.law_support import (
    Memories,
    Offices,
    RecordingLawLedger,
    checker,
    clock,
    law_cfg,
    log,
    runtime,
    world,
)


def _total(ledger: RecordingLawLedger) -> int:
    return sum(ledger.balances.values())


def test_fine_shortfall_becomes_a_paid_garnishment_receivable() -> None:
    event_log = log()
    cases = MemoryCourtRepository()
    crimes = MemoryCrimeRepository()
    cases.add(
        CourtCase(
            "ca_one",
            "criminal",
            "government",
            "ag_defendant",
            None,
            "fraud",
            0,
            1,
        )
    )
    ledger = RecordingLawLedger({"ag_defendant": 100, "government": 0})
    penalties = PenaltyService(
        log=event_log,
        clock=clock(),
        runtime=runtime(),
        ledger=ledger,
        cases=cases,
        crimes=crimes,
        cfg=law_cfg(),
    )
    initial = _total(ledger)
    judgment = Judgment("guilty", (), 1_000, 0, 0, 0, 0, (), "bench", None)

    penalties.apply("ca_one", judgment, 1)
    assert _total(ledger) == initial
    assert penalties.outstanding("ag_defendant") == 900

    ledger.balances["ag_defendant"] += 500
    after_payroll = _total(ledger)
    assert penalties.garnish("ag_defendant", 500, 2) == 100
    assert penalties.outstanding("ag_defendant") == 800
    assert _total(ledger) == after_payroll
    assert ledger.balances["government"] == 200


def test_settlement_and_public_defender_fees_conserve_every_cent() -> None:
    event_log = log()
    cases = MemoryCourtRepository()
    cases.add(
        CourtCase(
            "ca_one",
            "civil",
            "ag_plaintiff",
            "ag_defendant",
            None,
            "negligence",
            500,
            1,
        )
    )
    ledger = RecordingLawLedger(
        {
            "ag_defendant": 500,
            "ag_plaintiff": 0,
            "government": 400,
            "ag_lawyer": 0,
        }
    )
    service = CourtService(
        log=event_log,
        clock=clock(),
        runtime=runtime(),
        repo=cases,
        crimes=MemoryCrimeRepository(),
        ledger=ledger,
        offices=cast(OfficeRegister, Offices()),
        memories=Memories(),
        checker=checker(event_log),
        cfg=law_cfg(),
        wealth_percentile=lambda _agent_id: 0.0,
        available_lawyers=lambda _tick: (("ag_lawyer", 200, 0.8),),
        skill_law=lambda _agent_id: 0.8,
    )
    initial = _total(ledger)

    from polis.agents.actions.params.law import SettleParams

    service.settle(
        "ca_one",
        SettleParams(case_id="ca_one", amount_cents=300),
        2,
        offered_by="ag_defendant",
    )
    assert _total(ledger) == initial
    assert ledger.balances["ag_plaintiff"] == 300

    cases.add(
        CourtCase(
            "ca_two",
            "criminal",
            "government",
            "ag_poor",
            None,
            "theft",
            0,
            2,
        )
    )
    service.assign_public_defender("ca_two", "ag_poor", 3)
    assert _total(ledger) == initial
    assert ledger.balances["ag_lawyer"] == 200


def test_theft_commission_moves_existing_money_without_minting() -> None:
    event_log = log()
    configured_clock = clock()
    configured_world = world()
    configured_runtime = runtime()
    cfg = law_cfg()
    repo = MemoryCrimeRepository()
    memories = Memories()
    fact_checker = checker(event_log)
    oracle = LawLegalityOracle(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        mnpi=MnpiIndex(
            memories=memories,
            cfg=cfg,
            clock=configured_clock,
            events=(),
        ),
        obligations=ObligationIndex(),
        checker=fact_checker,
        memories=memories,
        repo=repo,
        cfg=cfg,
    )
    action = make_action(
        actor_id="ag_thief",
        tick=1,
        action_type=ActionType.COMMIT_CRIME,
        params={
            "crime_type": "theft",
            "victim_id": "ag_victim",
            "amount_cents": 400,
        },
    )
    params = CommitCrimeParams.model_validate(action.params)
    legality = oracle.assess(
        action,
        params,
        ValidationContext(observation=object(), state=object(), tick=1),
    )
    ledger = RecordingLawLedger({"ag_victim": 250, "ag_thief": 0})
    police = PoliceService(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        repo=repo,
        world=configured_world,
        cfg=cfg,
    )
    cases = MemoryCourtRepository()
    courts = CourtService(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        repo=cases,
        crimes=repo,
        ledger=ledger,
        offices=cast(OfficeRegister, Offices()),
        memories=memories,
        checker=fact_checker,
        cfg=cfg,
    )
    penalties = PenaltyService(
        log=event_log,
        clock=configured_clock,
        runtime=configured_runtime,
        ledger=ledger,
        cases=cases,
        crimes=repo,
        cfg=cfg,
    )
    resolver = LawResolver(
        log=event_log,
        clock=configured_clock,
        rng=RngRegistry(19),
        world=configured_world,
        police=police,
        courts=courts,
        penalties=penalties,
        graph=cast(SocialGraph, SimpleNamespace()),
        beliefs=cast(BeliefChannel, SimpleNamespace()),
        offices=cast(OfficeRegister, Offices()),
        runtime=configured_runtime,
        ledger=ledger,
        cfg=cfg,
    )
    initial = _total(ledger)

    resolver.resolve(
        (ValidatedAction(action, params, legality, 0),),
        1,
        ResolutionContext(emit=lambda _draft: event_log.staged()[-1]),
    )

    assert ledger.balances == {"ag_victim": 0, "ag_thief": 250}
    assert _total(ledger) == initial
    assert repo.get(legality.crime_id or "").amount_cents == 250  # type: ignore[union-attr]
