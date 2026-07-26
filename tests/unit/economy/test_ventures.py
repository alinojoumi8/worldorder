from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from polis.agents.actions.types import ActionType, make_action
from polis.agents.genesis import generate_agents
from polis.config.settings import Settings, load_settings
from polis.economy.banking import BankingEngine
from polis.economy.exchange.engine import ExchangeEngine
from polis.economy.genesis import create_economy
from polis.economy.state import EmploymentState
from polis.economy.venture_state import CapTableState, ClaimState, FundingRoundState
from polis.economy.ventures import VentureEngine, venture_waterfall
from polis.events.kinds import (
    ACQUISITION_COMPLETED,
    BANKRUPTCY_DISCHARGED,
    DIVIDEND_PAID,
    FIRED,
    ROUND_CLOSED,
)
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.rng import RngRegistry
from polis.llm.router import LLMRouter
from polis.simulation import run_id_for
from polis.world.generator import generate_world

ROOT = Path(__file__).resolve().parents[3]


def configured() -> Settings:
    return load_settings(
        ROOT / "configs" / "m2-smoke.yaml",
        overrides={
            "exchange": {"enabled": True},
            "ventures": {
                "enabled": True,
                "term_sheet_days": 3,
            },
            "bankruptcy": {
                "enabled": True,
                "liquidation_days": 1,
                "stay_max_days": 3,
            },
        },
    )


def build() -> tuple[Settings, object, object, VentureEngine, EventLog]:
    settings = configured()
    rng = RngRegistry(settings.run.seed)
    world = generate_world(settings.world, rng)
    population = generate_agents(settings.population, world, rng)
    log = EventLog(run_id_for(settings), MemoryEventSink())
    genesis = create_economy(
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
    router = LLMRouter(settings=settings, run_id=run_id_for(settings))
    exchange = ExchangeEngine(settings, population, genesis.state, rng)
    banking = BankingEngine(settings, population, genesis.state, rng, router)
    engine = VentureEngine(
        settings,
        population,
        genesis.state,
        rng,
        router,
        exchange,
        banking.credit_context,
    )
    return settings, population, genesis.state, engine, log


def emit_at(log: EventLog, tick: int):
    return lambda draft: log.stage(
        draft,
        tick=tick,
        sim_time=datetime(2025, 1, 1) + timedelta(days=tick),
    )


def test_waterfall_is_exact_and_preference_senior() -> None:
    caps = [
        CapTableState("fm_a", "founder", "common", 1_000),
        CapTableState(
            "fm_a",
            "investor",
            "preferred",
            500,
            invested_cents=400,
            round_id="rd_1",
            liq_pref_bp=10_000,
        ),
    ]
    rounds = [
        FundingRoundState(
            "rd_1",
            "st_a",
            "seed",
            1_000,
            400,
            1_400,
            2,
            500,
            "investor",
            {"investor": 400},
            0,
            10_000,
            False,
            1,
        )
    ]
    result = venture_waterfall(1_000, caps, rounds)
    assert result["investor"] >= 400
    assert sum(result.values()) == 1_000


def test_startup_round_updates_cap_table_and_moves_exact_cash() -> None:
    _settings, population, economy, engine, log = build()
    founder, investor = list(population)[:2]
    before = economy.ledger.global_balance_cents()
    found = make_action(
        actor_id=founder.agent_id,
        tick=1,
        action_type=ActionType.FOUND_COMPANY,
        params={
            "name": "Test Startup",
            "sector": "services",
            "place_id": "pl_test",
            "initial_capital_cents": 10_000,
            "is_startup": True,
            "is_fund": False,
            "thesis": "Build deterministic tools",
        },
    )
    asyncio.run(engine.resolve((found,), 1, emit_at(log, 1)))
    startup = next(iter(economy.ventures.startups.values()))
    term = make_action(
        actor_id=investor.agent_id,
        tick=2,
        action_type=ActionType.ISSUE_TERM_SHEET,
        params={
            "startup_id": startup.startup_id,
            "investor_id": investor.agent_id,
            "pre_money_cents": 100_000,
            "amount_cents": 5_000,
            "security": "preferred",
            "liq_pref_bp": 10_000,
            "participating": False,
            "pro_rata": True,
            "board_seat": False,
            "option_pool_bp": 1_000,
            "anti_dilution": "broad_weighted",
        },
    )
    asyncio.run(engine.resolve((term,), 2, emit_at(log, 2)))
    term_state = next(iter(economy.ventures.term_sheets.values()))
    invest = make_action(
        actor_id=investor.agent_id,
        tick=3,
        action_type=ActionType.INVEST,
        params={
            "target_id": startup.startup_id,
            "cents": 5_000,
            "instrument": "round",
            "term_sheet_id": term_state.term_sheet_id,
        },
    )
    events = asyncio.run(engine.resolve((invest,), 3, emit_at(log, 3)))

    assert any(event.kind == ROUND_CLOSED for event in events)
    assert startup.total_raised_cents == 5_000
    assert economy.ventures.shares(startup.firm_id) > _settings.ventures.founder_shares
    assert economy.ledger.global_balance_cents() == before == 0
    assert economy.ventures.dump() == type(economy.ventures).load(economy.ventures.dump()).dump()


def test_cash_acquisition_and_bankruptcy_reach_terminal_states() -> None:
    settings, population, economy, engine, log = build()
    acquirer_founder, target_founder = list(population)[:2]

    async def found(actor_id: str, tick: int, name: str) -> str:
        action = make_action(
            actor_id=actor_id,
            tick=tick,
            action_type=ActionType.FOUND_COMPANY,
            params={
                "name": name,
                "sector": "services",
                "place_id": "pl_test",
                "initial_capital_cents": 30_000,
                "is_startup": True,
                "is_fund": False,
                "thesis": name,
            },
        )
        await engine.resolve((action,), tick, emit_at(log, tick))
        return next(
            row.firm_id for row in economy.ventures.startups.values() if row.founder_id == actor_id
        )

    acquirer_id = asyncio.run(found(acquirer_founder.agent_id, 1, "Acquirer"))
    target_id = asyncio.run(found(target_founder.agent_id, 2, "Target"))
    proposal = make_action(
        actor_id=acquirer_founder.agent_id,
        tick=3,
        action_type=ActionType.ACQUIRE,
        params={
            "acquirer_id": acquirer_id,
            "target_id": target_id,
            "offer_cents": 20_000,
            "consideration": "cash",
            "stock_ratio_bp": 0,
            "integration_mode": "absorb",
            "financing": "cash",
        },
    )
    asyncio.run(engine.resolve((proposal,), 3, emit_at(log, 3)))
    deal = next(iter(economy.ventures.acquisitions.values()))
    tender = make_action(
        actor_id=target_founder.agent_id,
        tick=4,
        action_type=ActionType.SELL_STAKE,
        params={
            "firm_id": target_id,
            "qty": settings.ventures.founder_shares,
            "deal_id": deal.deal_id,
        },
    )
    events = asyncio.run(engine.resolve((tender,), 4, emit_at(log, 4)))
    assert any(event.kind == ACQUISITION_COMPLETED for event in events)
    assert economy.firms[target_id].status == "acquired"

    employee = list(population)[2]
    employment = EmploymentState(
        "emp_bankruptcy_fixture",
        employee.agent_id,
        acquirer_id,
        "service",
        10_000,
        1,
        8_000,
        accrued_wage_cents=100,
    )
    economy.employments[employment.employment_id] = employment
    economy.firms[acquirer_id].headcount = 1
    employee.employment_status = "employed"

    bankruptcy = make_action(
        actor_id=acquirer_founder.agent_id,
        tick=5,
        action_type=ActionType.FILE_BANKRUPTCY,
        params={"entity_id": acquirer_id, "reason": "voluntary"},
    )
    asyncio.run(engine.resolve((bankruptcy,), 5, emit_at(log, 5)))
    case = next(
        row for row in economy.ventures.bankruptcies.values() if row.entity_id == acquirer_id
    )
    estate_cents = economy.ledger.liquid(acquirer_id)
    senior_claim = ClaimState(
        claim_id=f"cl_{case.case_id}_senior",
        case_id=case.case_id,
        creditor_id=acquirer_founder.agent_id,
        claim_cents=max(1, estate_cents // 2),
        priority_class=1,
    )
    junior_claim = ClaimState(
        claim_id=f"cl_{case.case_id}_junior",
        case_id=case.case_id,
        creditor_id=target_founder.agent_id,
        claim_cents=max(1, estate_cents),
        priority_class=4,
    )
    economy.ventures.claims[senior_claim.claim_id] = senior_claim
    economy.ventures.claims[junior_claim.claim_id] = junior_claim

    blocked_dividend = make_action(
        actor_id=acquirer_founder.agent_id,
        tick=5,
        action_type=ActionType.DECLARE_DIVIDEND,
        params={"firm_id": acquirer_id, "cents": 1},
    )
    blocked_events = asyncio.run(engine.resolve((blocked_dividend,), 5, emit_at(log, 5)))
    assert all(event.kind != DIVIDEND_PAID for event in blocked_events)
    assert case.status == "open"

    discharge_tick = 5 + settings.bankruptcy.liquidation_days
    discharged = asyncio.run(engine.resolve((), discharge_tick, emit_at(log, discharge_tick)))
    assert any(event.kind == BANKRUPTCY_DISCHARGED for event in discharged)
    assert any(event.kind == FIRED for event in discharged)
    assert senior_claim.paid_cents == senior_claim.claim_cents
    assert junior_claim.paid_cents < junior_claim.claim_cents
    assert economy.firms[acquirer_id].status == "dissolved"
    assert economy.firms[acquirer_id].dissolved_tick == discharge_tick
    assert employment.ended_tick == discharge_tick
    assert employee.employment_status == "unemployed"
    assert economy.ledger.global_balance_cents() == 0
