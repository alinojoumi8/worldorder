from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

from polis.agents.actions.types import ActionType, make_action
from polis.agents.genesis import generate_agents
from polis.agents.state import AgentPopulation
from polis.config.settings import Settings, load_settings
from polis.economy.banking import BankingEngine
from polis.economy.central import CentralContext, settle_banks
from polis.economy.credit import LoanDecision, LoanRequest, capital_cents, originate
from polis.economy.exchange.engine import ExchangeEngine
from polis.economy.genesis import create_economy
from polis.economy.ledger import parse_account_id
from polis.economy.state import EconomyState, EmploymentState, InventoryState
from polis.economy.venture_state import CapTableState, ClaimState, FundingRoundState
from polis.economy.ventures import VentureEngine, venture_waterfall
from polis.events.kinds import (
    ACQUISITION_APPROVED,
    ACQUISITION_COMPLETED,
    ASSET_SALE,
    ASSETS_LIQUIDATED,
    BANK_FAILED,
    BANKRUPTCY_DISCHARGED,
    BANKRUPTCY_FILED,
    DIVIDEND_PAID,
    FIRED,
    INTEGRATION_COMPLETED,
    ORDER_CANCELLED,
    ROUND_CLOSED,
    SECURITY_DELISTED,
    TRADE_EXECUTED,
)
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.rng import RngRegistry
from polis.llm.router import LLMRouter
from polis.simulation import run_id_for
from polis.world.generator import generate_world

ROOT = Path(__file__).resolve().parents[3]


def configured(
    *,
    mechanisms: Mapping[str, str] | None = None,
    venture_overrides: Mapping[str, object] | None = None,
) -> Settings:
    venture_settings: dict[str, object] = {
        "enabled": True,
        "term_sheet_days": 3,
    }
    venture_settings.update(venture_overrides or {})
    overrides: dict[str, object] = {
        "exchange": {"enabled": True},
        "ventures": venture_settings,
        "labour": {"severance_periods_bp": 10_000},
        "bankruptcy": {
            "enabled": True,
            "liquidation_days": 3,
            "stay_max_days": 5,
            "insolvency_persist_days": 2,
        },
    }
    if mechanisms is not None:
        overrides["mechanisms"] = dict(mechanisms)
    return load_settings(
        ROOT / "configs" / "m2-smoke.yaml",
        overrides=overrides,
    )


def build(
    settings: Settings | None = None,
) -> tuple[Settings, AgentPopulation, EconomyState, VentureEngine, EventLog]:
    settings = settings or configured()
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


async def found_startup(
    engine: VentureEngine,
    economy: EconomyState,
    log: EventLog,
    *,
    actor_id: str,
    tick: int,
    name: str,
) -> str:
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


def test_venture_valuation_ablation_uses_the_investor_view() -> None:
    async def issued_pre_money(settings: Settings) -> int:
        _settings, population, economy, engine, log = build(settings)
        founder, investor = list(population)[:2]
        startup_id = await found_startup(
            engine,
            economy,
            log,
            actor_id=founder.agent_id,
            tick=1,
            name="Valuation Target",
        )
        term = make_action(
            actor_id=investor.agent_id,
            tick=2,
            action_type=ActionType.ISSUE_TERM_SHEET,
            params={
                "startup_id": next(
                    row.startup_id
                    for row in economy.ventures.startups.values()
                    if row.firm_id == startup_id
                ),
                "investor_id": investor.agent_id,
                "pre_money_cents": 100_000,
                "amount_cents": 5_000,
                "security": "preferred",
                "liq_pref_bp": 10_000,
                "participating": False,
                "pro_rata": True,
                "board_seat": False,
                "option_pool_bp": 0,
                "anti_dilution": "broad_weighted",
            },
        )
        await engine.resolve((term,), 2, emit_at(log, 2))
        return next(iter(economy.ventures.term_sheets.values())).pre_money_cents

    blended = asyncio.run(issued_pre_money(configured()))
    investor_only = asyncio.run(
        issued_pre_money(configured(mechanisms={"venture_valuation": "off"}))
    )
    assert blended == 50_050_000
    assert investor_only == 100_000


def test_acquisition_anchor_and_synergy_ablations_change_runtime_behavior() -> None:
    async def default_offer(settings: Settings) -> tuple[int, int]:
        _settings, population, economy, engine, log = build(settings)
        acquirer_founder, target_founder = list(population)[:2]
        acquirer_id = await found_startup(
            engine,
            economy,
            log,
            actor_id=acquirer_founder.agent_id,
            tick=1,
            name="Anchor Buyer",
        )
        target_id = await found_startup(
            engine,
            economy,
            log,
            actor_id=target_founder.agent_id,
            tick=2,
            name="Anchor Target",
        )
        proposal = make_action(
            actor_id=acquirer_founder.agent_id,
            tick=3,
            action_type=ActionType.ACQUIRE,
            params={
                "acquirer_id": acquirer_id,
                "target_id": target_id,
                "consideration": "stock",
                "stock_ratio_bp": 10_000,
                "integration_mode": "standalone",
                "financing": "share_issue",
            },
        )
        await engine.resolve((proposal,), 3, emit_at(log, 3))
        deal = next(iter(economy.ventures.acquisitions.values()))
        anchor = engine._acquisition_anchor_cents(target_id)
        return deal.offer_cents, anchor

    anchored_offer, anchored_base = asyncio.run(default_offer(configured()))
    unanchored_offer, unanchored_base = asyncio.run(
        default_offer(configured(mechanisms={"ma_valuation_anchor": "off"}))
    )
    assert anchored_offer > anchored_base
    assert unanchored_offer == unanchored_base

    def realised_delta(settings: Settings) -> int:
        _settings, _population, economy, engine, _log = build(settings)
        firms = [row for row in economy.firms.values() if row.firm_id != "fm_broker"]
        acquirer, target = firms[:2]
        acquirer.capital_cents = 100
        target.capital_cents = 100
        acquirer.productivity_bp = 10_000
        target.productivity_bp = 8_000
        return engine._transfer_productive_assets(
            target,
            acquirer,
            apply_synergy=True,
        )

    with_synergy = configured(
        venture_overrides={"integration_synergy_bp": 1_000},
    )
    without_synergy = configured(
        mechanisms={"ventures_integration_synergy": "off"},
        venture_overrides={"integration_synergy_bp": 1_000},
    )
    assert realised_delta(with_synergy) == 1_000
    assert realised_delta(without_synergy) == 0


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


def test_absorb_integration_pays_redundancy_and_transfers_loan_obligor() -> None:
    settings, population, economy, engine, log = build()
    agents = list(population)
    acquirer_founder, target_founder = agents[:2]
    acquirer_id = asyncio.run(
        found_startup(
            engine,
            economy,
            log,
            actor_id=acquirer_founder.agent_id,
            tick=1,
            name="Integration Acquirer",
        )
    )
    target_id = asyncio.run(
        found_startup(
            engine,
            economy,
            log,
            actor_id=target_founder.agent_id,
            tick=2,
            name="Integration Target",
        )
    )
    employment_rows = [
        EmploymentState(
            "emp_acquirer_engineer",
            agents[2].agent_id,
            acquirer_id,
            "engineer",
            1_000,
            1,
            9_000,
        ),
        *[
            EmploymentState(
                f"emp_target_engineer_{index}",
                agents[index + 3].agent_id,
                target_id,
                "engineer",
                1_000,
                index + 1,
                5_000 + index * 500,
            )
            for index in range(4)
        ],
        EmploymentState(
            "emp_target_designer",
            agents[7].agent_id,
            target_id,
            "designer",
            1_000,
            1,
            8_000,
        ),
    ]
    for row in employment_rows:
        economy.employments[row.employment_id] = row
        population[row.agent_id].employment_status = "employed"
    economy.firms[acquirer_id].headcount = 1
    economy.firms[target_id].headcount = 5
    target_inventory = InventoryState(
        target_id,
        "integration-sku",
        quantity=7,
        unit_cost_cents=25,
        price_cents=40,
    )
    economy.inventory[f"{target_id}:integration-sku"] = target_inventory
    target_capital = economy.firms[target_id].capital_cents
    acquirer_capital = economy.firms[acquirer_id].capital_cents

    lender_id = next(iter(economy.banks))
    loan_request = LoanRequest(
        target_id,
        lender_id,
        5_000,
        "corporate",
        360,
        {},
        5_000,
        "integration fixture",
    )
    originate(
        loan_request,
        LoanDecision(True, 8_000, {}, 5_000, 500, 360, ()),
        2,
        ctx=engine.credit_context,
        emit=emit_at(log, 2),
    )
    loan = next(row for row in economy.loans.values() if row.borrower_id == target_id)
    old_payable = loan.borrower_payable_account_id

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

    fired = [event for event in events if event.kind == FIRED]
    assert len(fired) == 1
    assert fired[0].payload["reason"] == "acquisition"
    assert fired[0].payload["severance_cents"] == 1_000
    assert deal.drag_along_applied is True
    assert economy.firms[target_id].headcount == 0
    assert economy.firms[acquirer_id].headcount == 5
    retained = [
        row
        for row in employment_rows
        if row.employment_id != fired[0].payload["employment_id"] and row.firm_id == acquirer_id
    ]
    assert len(retained) == 5
    assert all(row.wage_cents == 1_000 for row in retained)
    assert target_inventory.quantity == 0
    assert economy.inventory[f"{acquirer_id}:integration-sku"].quantity == 7
    assert economy.firms[target_id].capital_cents == 0
    assert economy.firms[acquirer_id].capital_cents == acquirer_capital + target_capital

    integration = next(event for event in events if event.kind == INTEGRATION_COMPLETED)
    assert integration.payload["headcount_retained"] == 4
    assert integration.payload["redundancies"] == 1
    assert integration.payload["loans_transferred"] == [loan.loan_id]
    assert loan.borrower_id == acquirer_id
    assert loan.borrower_payable_account_id != old_payable
    assert economy.ledger.balance(old_payable) == 0
    old_account = next(
        account for account in economy.ledger.accounts() if account.account_id == old_payable
    )
    assert old_account.closed_tick == 4
    assert economy.ledger.balance(loan.borrower_payable_account_id) == -5_000
    assert any(entry.reason == "transfer" for entry in economy.ledger.entries())
    assert economy.ledger.global_balance_cents() == 0


def test_asset_sale_leaves_loan_with_insolvent_target_shell() -> None:
    settings, population, economy, engine, log = build()
    acquirer_founder, target_founder = list(population)[:2]
    acquirer_id = asyncio.run(
        found_startup(
            engine,
            economy,
            log,
            actor_id=acquirer_founder.agent_id,
            tick=1,
            name="Asset Buyer",
        )
    )
    target_id = asyncio.run(
        found_startup(
            engine,
            economy,
            log,
            actor_id=target_founder.agent_id,
            tick=2,
            name="Asset Seller",
        )
    )
    economy.inventory[f"{target_id}:shell-sku"] = InventoryState(
        target_id,
        "shell-sku",
        quantity=3,
        unit_cost_cents=50,
        price_cents=75,
    )
    target_deposit = economy.firms[target_id].ledger_account_id
    lender_id = parse_account_id(target_deposit)[2]
    assert lender_id is not None
    originate(
        LoanRequest(
            target_id,
            lender_id,
            20_000,
            "corporate",
            360,
            {},
            20_000,
            "asset sale fixture",
        ),
        LoanDecision(True, 8_000, {}, 20_000, 500, 360, ()),
        2,
        ctx=engine.credit_context,
        emit=emit_at(log, 2),
    )
    dividend = make_action(
        actor_id=target_founder.agent_id,
        tick=3,
        action_type=ActionType.DECLARE_DIVIDEND,
        params={"firm_id": target_id, "total_cents": 45_000},
    )
    dividend_events = asyncio.run(engine.resolve((dividend,), 3, emit_at(log, 3)))
    assert any(event.kind == DIVIDEND_PAID for event in dividend_events)

    proposal = make_action(
        actor_id=acquirer_founder.agent_id,
        tick=4,
        action_type=ActionType.ACQUIRE,
        params={
            "acquirer_id": acquirer_id,
            "target_id": target_id,
            "offer_cents": 5_000,
            "consideration": "cash",
            "stock_ratio_bp": 0,
            "integration_mode": "asset_sale",
            "financing": "cash",
        },
    )
    asyncio.run(engine.resolve((proposal,), 4, emit_at(log, 4)))
    deal = next(iter(economy.ventures.acquisitions.values()))
    tender = make_action(
        actor_id=target_founder.agent_id,
        tick=5,
        action_type=ActionType.SELL_STAKE,
        params={
            "firm_id": target_id,
            "qty": settings.ventures.founder_shares,
            "deal_id": deal.deal_id,
        },
    )
    events = asyncio.run(engine.resolve((tender,), 5, emit_at(log, 5)))
    asset_sale = next(event for event in events if event.kind == ASSET_SALE)
    assert asset_sale.payload["seller_id"] == target_id
    assert asset_sale.payload["cents"] == 5_000
    assert asset_sale.payload["txn_id"]
    assert economy.inventory[f"{target_id}:shell-sku"].quantity == 0
    assert economy.inventory[f"{acquirer_id}:shell-sku"].quantity == 3
    loan = next(row for row in economy.loans.values() if row.borrower_id == target_id)
    assert loan.borrower_id == target_id
    assert economy.firms[target_id].status == "active"
    assert economy.firms[target_id].capital_cents == 0
    assert economy.ledger.net_worth(target_id) < 0

    filing_tick = 5 + (
        settings.bankruptcy.insolvency_persist_days * settings.clock.ticks_per_sim_day
    )
    filed = asyncio.run(engine.resolve((), filing_tick, emit_at(log, filing_tick)))
    assert any(event.kind == BANKRUPTCY_FILED for event in filed)
    case = next(row for row in economy.ventures.bankruptcies.values() if row.entity_id == target_id)
    assert case.trigger == "balance_sheet"
    assert case.status == "open"
    assert economy.ledger.global_balance_cents() == 0


def test_public_squeeze_out_transfers_all_holdings_and_delists() -> None:
    _settings, population, economy, engine, log = build()
    acquirer_founder, target_founder, minority = list(population)[:3]
    acquirer_id = asyncio.run(
        found_startup(
            engine,
            economy,
            log,
            actor_id=acquirer_founder.agent_id,
            tick=1,
            name="Public Buyer",
        )
    )
    target_id = asyncio.run(
        found_startup(
            engine,
            economy,
            log,
            actor_id=target_founder.agent_id,
            tick=2,
            name="Public Target",
        )
    )
    engine.exchange.list_security(
        symbol="SQZ",
        issuer_firm_id=target_id,
        shares_outstanding=1_000,
        listing_price_cents=10,
        tick=2,
        emit=emit_at(log, 2),
        holders={
            target_founder.agent_id: 900,
            minority.agent_id: 100,
        },
    )
    proposal = make_action(
        actor_id=acquirer_founder.agent_id,
        tick=3,
        action_type=ActionType.ACQUIRE,
        params={
            "acquirer_id": acquirer_id,
            "target_id": target_id,
            "offer_cents": 10_000,
            "consideration": "cash",
            "stock_ratio_bp": 0,
            "integration_mode": "standalone",
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
            "qty": 900,
            "deal_id": deal.deal_id,
        },
    )
    events = asyncio.run(engine.resolve((tender,), 4, emit_at(log, 4)))

    approval = next(event for event in events if event.kind == ACQUISITION_APPROVED)
    assert approval.payload["squeeze_out_applied"] is True
    assert deal.squeeze_out_applied is True
    assert engine.exchange.state.holding(acquirer_id, "SQZ").qty == 1_000
    assert engine.exchange.state.holding(target_founder.agent_id, "SQZ").qty == 0
    assert engine.exchange.state.holding(minority.agent_id, "SQZ").qty == 0
    assert engine.exchange.state.securities["SQZ"].status == "delisted"
    assert any(event.kind == SECURITY_DELISTED for event in events)
    assert economy.ledger.global_balance_cents() == 0


def test_bankruptcy_discharge_triggers_bank_failure_without_breaking_money() -> None:
    _settings, population, economy, engine, log = build()
    founder = next(iter(population))
    borrower_id = asyncio.run(
        found_startup(
            engine,
            economy,
            log,
            actor_id=founder.agent_id,
            tick=1,
            name="Cascade Borrower",
        )
    )
    borrower_deposit = economy.firms[borrower_id].ledger_account_id
    lender_id = parse_account_id(borrower_deposit)[2]
    assert lender_id is not None
    principal = max(1, capital_cents(lender_id, economy) + 1_000_000)
    originate(
        LoanRequest(
            borrower_id,
            lender_id,
            principal,
            "corporate",
            360,
            {},
            principal,
            "bank failure cascade fixture",
        ),
        LoanDecision(True, 8_000, {}, principal, 500, 360, ()),
        1,
        ctx=engine.credit_context,
        emit=emit_at(log, 1),
    )
    dividend = make_action(
        actor_id=founder.agent_id,
        tick=2,
        action_type=ActionType.DECLARE_DIVIDEND,
        params={
            "firm_id": borrower_id,
            "total_cents": economy.ledger.balance(borrower_deposit),
        },
    )
    paid = asyncio.run(engine.resolve((dividend,), 2, emit_at(log, 2)))
    assert any(event.kind == DIVIDEND_PAID for event in paid)

    filing = make_action(
        actor_id=founder.agent_id,
        tick=3,
        action_type=ActionType.FILE_BANKRUPTCY,
        params={"entity_id": borrower_id, "reason": "voluntary"},
    )
    asyncio.run(engine.resolve((filing,), 3, emit_at(log, 3)))
    case = next(
        row for row in economy.ventures.bankruptcies.values() if row.entity_id == borrower_id
    )
    discharge_tick = case.liquidation_tick
    assert discharge_tick is not None
    discharged = asyncio.run(engine.resolve((), discharge_tick, emit_at(log, discharge_tick)))
    assert any(event.kind == BANKRUPTCY_DISCHARGED for event in discharged)
    assert capital_cents(lender_id, economy) < 0
    assert economy.ledger.global_balance_cents() == 0

    resolution = settle_banks(
        discharge_tick,
        ctx=CentralContext(_settings, economy, engine.rng),
        credit=engine.credit_context,
        emit=emit_at(log, discharge_tick),
    )
    assert any(event.kind == BANK_FAILED for event in resolution)
    assert economy.banks[lender_id].status == "failed"
    assert economy.ledger.global_balance_cents() == 0
    assert all(value == 0 for value in economy.ledger.deposit_imbalances().values())


def test_negative_net_worth_agent_is_not_subject_to_balance_sheet_filing() -> None:
    settings, population, economy, engine, log = build()
    debtor, recipient = list(population)[:2]
    debtor_deposit = next(
        account_id
        for account_id in economy.ledger.accounts_of(debtor.agent_id)
        if parse_account_id(account_id)[0] == "dep"
    )
    lender_id = parse_account_id(debtor_deposit)[2]
    assert lender_id is not None
    originated = originate(
        LoanRequest(
            debtor.agent_id,
            lender_id,
            10_000,
            "consumer",
            360,
            {},
            10_000,
            "household leverage fixture",
        ),
        LoanDecision(True, 8_000, {}, 10_000, 500, 360, ()),
        1,
        ctx=engine.credit_context,
        emit=emit_at(log, 1),
    )
    recipient_deposit = next(
        account_id
        for account_id in economy.ledger.accounts_of(recipient.agent_id)
        if parse_account_id(account_id)[0] == "dep"
    )
    economy.ledger.post_transaction(
        economy.ledger.transfer(
            debtor_deposit,
            recipient_deposit,
            economy.ledger.balance(debtor_deposit),
            "transfer",
        ),
        tick=1,
        cause=originated[0],
    )
    assert economy.ledger.net_worth(debtor.agent_id) < 0

    check_tick = settings.bankruptcy.insolvency_persist_days * settings.clock.ticks_per_sim_day + 2
    events = asyncio.run(engine.resolve((), check_tick, emit_at(log, check_tick)))
    assert all(event.kind != BANKRUPTCY_FILED for event in events)
    assert all(case.entity_id != debtor.agent_id for case in economy.ventures.bankruptcies.values())
    assert economy.ledger.global_balance_cents() == 0


def test_automatic_stay_cancels_resting_orders_and_releases_shares() -> None:
    _settings, population, economy, engine, log = build()
    debtor = next(iter(population))
    issuer = next(
        firm
        for firm in economy.firms.values()
        if firm.firm_id != "fm_broker" and firm.founder_id != debtor.agent_id
    )
    engine.exchange.list_security(
        symbol="STAY",
        issuer_firm_id=issuer.firm_id,
        shares_outstanding=1_000,
        listing_price_cents=10,
        tick=0,
        emit=emit_at(log, 0),
        holders={debtor.agent_id: 100, issuer.founder_id: 900},
    )
    sell = make_action(
        actor_id=debtor.agent_id,
        tick=1,
        action_type=ActionType.SUBMIT_ORDER,
        params={
            "symbol": "STAY",
            "side": "sell",
            "order_type": "limit",
            "limit_price_cents": 12,
            "qty": 40,
        },
    )
    order, _events = engine.exchange._admit(
        sell,
        "STAY",
        0,
        1,
        True,
        emit_at(log, 1),
    )
    assert order is not None
    holding = engine.exchange.state.holding(debtor.agent_id, "STAY")
    assert holding.reserved_qty == 40

    filing = make_action(
        actor_id=debtor.agent_id,
        tick=2,
        action_type=ActionType.FILE_BANKRUPTCY,
        params={"reason": "voluntary"},
    )
    events = asyncio.run(engine.resolve((filing,), 2, emit_at(log, 2)))
    assert any(event.kind == ORDER_CANCELLED for event in events)
    assert holding.reserved_qty == 0
    assert order.status == "cancelled"
    assert economy.ledger.global_balance_cents() == 0


def test_listed_bankruptcy_assets_are_sliced_through_the_order_book() -> None:
    settings, population, economy, engine, log = build()
    exchange = engine.exchange
    debtor, buyer = list(population)[:2]
    issuer = next(
        firm
        for firm in economy.firms.values()
        if firm.firm_id != "fm_broker" and firm.founder_id != debtor.agent_id
    )
    exchange.list_security(
        symbol="LIQ",
        issuer_firm_id=issuer.firm_id,
        shares_outstanding=1_000,
        listing_price_cents=10,
        tick=0,
        emit=emit_at(log, 0),
        holders={debtor.agent_id: 100, issuer.founder_id: 900},
        lockup_until_tick=100,
    )
    filing = make_action(
        actor_id=debtor.agent_id,
        tick=1,
        action_type=ActionType.FILE_BANKRUPTCY,
        params={"reason": "voluntary"},
    )
    asyncio.run(engine.resolve((filing,), 1, emit_at(log, 1)))
    case = next(
        row for row in economy.ventures.bankruptcies.values() if row.entity_id == debtor.agent_id
    )
    exchange_events = []
    slice_quantities: list[int] = []
    final_tick = case.liquidation_tick or 4
    discharge_events = ()
    for tick in range(2, final_tick + 1):
        liquidation = engine.liquidation_actions(tick)
        assert liquidation
        assert all(action.params["order_type"] == "market" for action in liquidation)
        assert all("forced_liquidation" in action.params["flags"] for action in liquidation)
        quantity = sum(int(action.params["qty"]) for action in liquidation)
        slice_quantities.append(quantity)
        bid = make_action(
            actor_id=buyer.agent_id,
            tick=tick,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "LIQ",
                "side": "buy",
                "order_type": "limit",
                "limit_price_cents": 8,
                "qty": quantity,
            },
        )
        exchange_events.extend(exchange.resolve((*liquidation, bid), tick, emit_at(log, tick)))
        discharge_events = asyncio.run(engine.resolve((), tick, emit_at(log, tick)))

    assert any(event.kind == TRADE_EXECUTED for event in exchange_events)
    assert len(slice_quantities) == settings.bankruptcy.liquidation_days
    assert sum(slice_quantities) == 100
    assert slice_quantities[-1] >= max(slice_quantities[:-1])
    liquidation_event = next(
        event
        for event in discharge_events
        if event.kind == ASSETS_LIQUIDATED and event.payload.get("asset_ref") == "LIQ"
    )
    assert liquidation_event.payload["shares"] == 100
    realised = sum(
        int(event.payload["price_cents"]) * int(event.payload["qty"])
        - int(event.payload["commission_sell_cents"])
        for event in exchange_events
        if event.kind == TRADE_EXECUTED
    )
    assert liquidation_event.payload["realised_cents"] == realised
    assert 0 < realised < 1_000
    assert exchange.state.securities["LIQ"].last_price_cents == 8
    debtor_holding = exchange.state.holding(debtor.agent_id, "LIQ")
    assert debtor_holding.qty == 0
    assert debtor_holding.locked_qty == 0
    assert case.status == "discharged"
    assert economy.ledger.global_balance_cents() == 0
    assert (
        sum(row.qty for row in exchange.state.holdings.values() if row.symbol == "LIQ")
        == exchange.state.securities["LIQ"].shares_outstanding
    )


def test_bankruptcy_inventory_and_capital_require_solvent_in_world_buyers() -> None:
    _settings, _population, economy, engine, log = build()
    firms = [row for row in economy.firms.values() if row.firm_id != "fm_broker"]
    debtor, buyer = firms[:2]
    buyer.sector = debtor.sector
    for firm in firms:
        if firm.sector == debtor.sector and firm.firm_id not in {debtor.firm_id, buyer.firm_id}:
            firm.status = "acquired"
    rows = [row for row in economy.inventory.values() if row.firm_id == debtor.firm_id]
    assert rows
    inventory = rows[0]
    for row in rows:
        row.quantity = 0
    inventory.quantity = 2
    inventory.unit_cost_cents = 10
    debtor.capital_cents = 1_000
    buyer_capital_before = buyer.capital_cents
    buyer_inventory_before = economy.inventory.get(f"{buyer.firm_id}:{inventory.sku}")
    buyer_units_before = buyer_inventory_before.quantity if buyer_inventory_before else 0

    filing = make_action(
        actor_id=debtor.founder_id,
        tick=1,
        action_type=ActionType.FILE_BANKRUPTCY,
        params={"entity_id": debtor.firm_id, "reason": "voluntary"},
    )
    asyncio.run(engine.resolve((filing,), 1, emit_at(log, 1)))
    case = next(
        row for row in economy.ventures.bankruptcies.values() if row.entity_id == debtor.firm_id
    )
    events = asyncio.run(
        engine.resolve(
            (),
            case.liquidation_tick or 2,
            emit_at(log, case.liquidation_tick or 2),
        )
    )

    sales = [
        event
        for event in events
        if event.kind == ASSETS_LIQUIDATED and event.payload.get("buyer_id") == buyer.firm_id
    ]
    assert {event.payload["item"] for event in sales} >= {"inventory", "capital"}
    assert all(event.payload["txn_id"] for event in sales)
    assert economy.inventory[f"{buyer.firm_id}:{inventory.sku}"].quantity == (
        buyer_units_before + 2
    )
    assert buyer.capital_cents == buyer_capital_before + 400
    assert debtor.capital_cents == 0
    assert case.status == "discharged"
    assert economy.ledger.global_balance_cents() == 0


def test_unlisted_bankruptcy_stake_is_offered_to_existing_holders() -> None:
    settings, population, economy, engine, log = build()
    founder, investor = list(population)[:2]
    found = make_action(
        actor_id=founder.agent_id,
        tick=1,
        action_type=ActionType.FOUND_COMPANY,
        params={
            "name": "Private Target",
            "sector": "services",
            "place_id": "pl_test",
            "initial_capital_cents": 10_000,
            "is_startup": True,
            "is_fund": False,
            "thesis": "Private market liquidation fixture",
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
            "pre_money_cents": 100_000_000,
            "amount_cents": 5_000,
            "security": "preferred",
            "liq_pref_bp": 10_000,
            "participating": False,
            "pro_rata": True,
            "board_seat": False,
            "option_pool_bp": 0,
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
    asyncio.run(engine.resolve((invest,), 3, emit_at(log, 3)))
    investor_row = next(
        row
        for row in economy.ventures.cap_table.values()
        if row.firm_id == startup.firm_id
        and row.holder_id == investor.agent_id
        and row.share_class == "preferred"
    )
    shares = investor_row.shares
    total_before = economy.ventures.shares(startup.firm_id)

    filing = make_action(
        actor_id=investor.agent_id,
        tick=4,
        action_type=ActionType.FILE_BANKRUPTCY,
        params={"reason": "voluntary"},
    )
    asyncio.run(engine.resolve((filing,), 4, emit_at(log, 4)))
    case = next(
        row for row in economy.ventures.bankruptcies.values() if row.entity_id == investor.agent_id
    )
    events = asyncio.run(
        engine.resolve(
            (),
            case.liquidation_tick or 7,
            emit_at(log, case.liquidation_tick or 7),
        )
    )

    sale = next(
        event
        for event in events
        if event.kind == ASSETS_LIQUIDATED
        and event.payload.get("listed") is False
        and event.payload.get("buyer_id") == founder.agent_id
    )
    assert sale.payload["shares"] == shares
    assert sale.payload["realised_cents"] > 0
    assert investor_row.shares == 0
    founder_preferred = economy.ventures.cap_table[
        economy.ventures.cap_key(startup.firm_id, founder.agent_id, "preferred")
    ]
    assert founder_preferred.shares == shares
    assert economy.ventures.shares(startup.firm_id) == total_before
    assert case.status == "discharged"
    assert economy.ledger.global_balance_cents() == 0
    assert settings.bankruptcy.unlisted_haircut_bp == 5_000
