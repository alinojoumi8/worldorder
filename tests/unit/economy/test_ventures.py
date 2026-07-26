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
    ASSETS_LIQUIDATED,
    BANKRUPTCY_DISCHARGED,
    DIVIDEND_PAID,
    FIRED,
    ROUND_CLOSED,
    TRADE_EXECUTED,
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
                "liquidation_days": 3,
                "stay_max_days": 5,
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
