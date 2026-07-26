from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from polis.agents.actions.types import ActionType, make_action
from polis.agents.genesis import generate_agents
from polis.config.settings import Settings, load_settings
from polis.economy.exchange.book import call_auction, uncross
from polis.economy.exchange.engine import ExchangeEngine
from polis.economy.exchange.models import OrderState
from polis.economy.genesis import create_economy
from polis.events.kinds import (
    FORCED_LIQUIDATION,
    MARGIN_CALL,
    ORDER_CANCELLED,
    ORDER_EXPIRED,
    TRADE_EXECUTED,
)
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.rng import RngRegistry
from polis.simulation import run_id_for
from polis.world.generator import generate_world

ROOT = Path(__file__).resolve().parents[3]


def order(
    order_id: str,
    side: str,
    price: int,
    qty: int,
    ordinal: int,
    trader_id: str,
) -> OrderState:
    return OrderState(
        order_id=order_id,
        symbol="ACME",
        trader_id=trader_id,
        side=side,  # type: ignore[arg-type]
        order_type="limit",
        qty=qty,
        remaining_qty=qty,
        limit_price_cents=price,
        submitted_tick=1,
        arrival_ordinal=ordinal,
    )


def configured(*, maintenance_margin_bp: int = 3_000) -> Settings:
    return load_settings(
        ROOT / "configs" / "m2-smoke.yaml",
        overrides={
            "exchange": {
                "enabled": True,
                "max_order_qty_bp": 10_000,
                "commission_bp": 20,
                "maintenance_margin_bp": maintenance_margin_bp,
            }
        },
    )


def build(
    *,
    maintenance_margin_bp: int = 3_000,
) -> tuple[Settings, object, object, ExchangeEngine, EventLog]:
    settings = configured(maintenance_margin_bp=maintenance_margin_bp)
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
    engine = ExchangeEngine(settings, population, genesis.state, rng)
    return settings, population, genesis.state, engine, log


def test_uncross_prefers_volume_then_imbalance_proximity_and_low_price() -> None:
    orders = [
        order("b1", "buy", 105, 10, 0, "buyer-a"),
        order("b2", "buy", 100, 10, 1, "buyer-b"),
        order("s1", "sell", 95, 8, 2, "seller-a"),
        order("s2", "sell", 100, 12, 3, "seller-b"),
    ]
    assert uncross(orders, 99) == (100, 20)


def test_call_auction_obeys_price_time_priority_and_prevents_self_trade() -> None:
    orders = [
        order("b-first", "buy", 100, 5, 0, "buyer-a"),
        order("b-second", "buy", 100, 5, 1, "buyer-b"),
        order("self-ask", "sell", 100, 2, 2, "buyer-a"),
        order("ask", "sell", 100, 8, 3, "seller"),
    ]
    fills = call_auction(orders, 100)
    assert fills[0].buy_order_id == "b-first"
    assert all(
        next(row for row in orders if row.order_id == fill.buy_order_id).trader_id
        != next(row for row in orders if row.order_id == fill.sell_order_id).trader_id
        for fill in fills
    )


def test_exchange_reserves_settles_and_releases_exactly() -> None:
    _settings, population, economy, engine, log = build()
    issuer = next(firm for firm in economy.firms.values() if firm.firm_id != "fm_broker")
    seller_id = issuer.founder_id
    buyer_id = next(agent.agent_id for agent in population if agent.agent_id != seller_id)

    def emit_at(tick: int):
        return lambda draft: log.stage(
            draft,
            tick=tick,
            sim_time=datetime(2025, 1, 1) + timedelta(days=tick),
        )

    engine.list_security(
        symbol="ACME",
        issuer_firm_id=issuer.firm_id,
        shares_outstanding=1_000,
        listing_price_cents=100,
        tick=0,
        emit=emit_at(0),
        holders={seller_id: 1_000},
    )
    actions = (
        make_action(
            actor_id=buyer_id,
            tick=1,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "ACME",
                "side": "buy",
                "order_type": "limit",
                "qty": 10,
                "limit_price_cents": 105,
            },
        ),
        make_action(
            actor_id=seller_id,
            tick=1,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "ACME",
                "side": "sell",
                "order_type": "limit",
                "qty": 10,
                "limit_price_cents": 100,
            },
        ),
    )
    before_balance = economy.ledger.global_balance_cents()
    events = engine.resolve(actions, 1, emit_at(1))

    assert sum(event.kind == TRADE_EXECUTED for event in events) == 1
    assert sum(event.kind == ORDER_EXPIRED for event in events) == 0
    assert economy.exchange.holding(buyer_id, "ACME").qty == 10
    assert economy.exchange.holding(seller_id, "ACME").qty == 990
    assert (
        sum(
            holding.qty
            for holding in economy.exchange.holdings.values()
            if holding.symbol == "ACME"
        )
        == economy.exchange.securities["ACME"].shares_outstanding
    )
    assert all(holding.reserved_qty >= 0 for holding in economy.exchange.holdings.values())
    assert economy.ledger.global_balance_cents() == before_balance == 0
    assert all(
        account.balance_cents >= 0 for account in economy.ledger.accounts() if account.code == "esc"
    )
    assert economy.exchange.dump() == type(economy.exchange).load(economy.exchange.dump()).dump()


def test_unfilled_market_order_is_cancelled_and_releases_escrow() -> None:
    _settings, population, economy, engine, log = build()
    issuer = next(firm for firm in economy.firms.values() if firm.firm_id != "fm_broker")
    buyer_id = next(agent.agent_id for agent in population if agent.agent_id != issuer.founder_id)

    def emit_at(tick: int):
        return lambda draft: log.stage(
            draft,
            tick=tick,
            sim_time=datetime(2025, 1, 1) + timedelta(days=tick),
        )

    engine.list_security(
        symbol="EMPTY",
        issuer_firm_id=issuer.firm_id,
        shares_outstanding=1_000,
        listing_price_cents=100,
        tick=0,
        emit=emit_at(0),
        holders={issuer.founder_id: 1_000},
    )
    market_buy = make_action(
        actor_id=buyer_id,
        tick=1,
        action_type=ActionType.SUBMIT_ORDER,
        params={
            "symbol": "EMPTY",
            "side": "buy",
            "order_type": "market",
            "qty": 10,
            "limit_price_cents": None,
        },
    )
    events = engine.resolve((market_buy,), 1, emit_at(1))

    cancellation = next(event for event in events if event.kind == ORDER_CANCELLED)
    assert cancellation.payload["initiator"] == "market_unfilled"
    assert all(
        account.balance_cents == 0
        for account in economy.ledger.accounts()
        if account.owner_id == buyer_id and account.code == "esc"
    )


def test_call_auction_cancels_newer_self_cross() -> None:
    _settings, _population, economy, engine, log = build()
    issuer = next(firm for firm in economy.firms.values() if firm.firm_id != "fm_broker")
    trader_id = issuer.founder_id

    def emit_at(tick: int):
        return lambda draft: log.stage(
            draft,
            tick=tick,
            sim_time=datetime(2025, 1, 1) + timedelta(days=tick),
        )

    engine.list_security(
        symbol="SELF",
        issuer_firm_id=issuer.firm_id,
        shares_outstanding=1_000,
        listing_price_cents=100,
        tick=0,
        emit=emit_at(0),
        holders={trader_id: 1_000},
    )
    actions = (
        make_action(
            actor_id=trader_id,
            tick=1,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "SELF",
                "side": "buy",
                "order_type": "limit",
                "qty": 5,
                "limit_price_cents": 100,
            },
            ordinal=0,
        ),
        make_action(
            actor_id=trader_id,
            tick=1,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "SELF",
                "side": "sell",
                "order_type": "limit",
                "qty": 5,
                "limit_price_cents": 100,
            },
            ordinal=1,
        ),
    )
    events = engine.resolve(actions, 1, emit_at(1))

    assert any(
        event.kind == ORDER_CANCELLED and event.payload["initiator"] == "stp" for event in events
    )
    assert not any(event.kind == TRADE_EXECUTED for event in events)


def test_margin_deadline_forces_market_cover_at_next_session() -> None:
    _settings, population, economy, engine, log = build(maintenance_margin_bp=16_000)
    issuer = next(firm for firm in economy.firms.values() if firm.firm_id != "fm_broker")
    seller_id = issuer.founder_id
    others = [agent.agent_id for agent in population if agent.agent_id != seller_id]
    buyer_id, short_seller_id = others[:2]

    def emit_at(tick: int):
        return lambda draft: log.stage(
            draft,
            tick=tick,
            sim_time=datetime(2025, 1, 1) + timedelta(days=tick),
        )

    engine.list_security(
        symbol="SHORT",
        issuer_firm_id=issuer.firm_id,
        shares_outstanding=1_000,
        listing_price_cents=100,
        tick=0,
        emit=emit_at(0),
        holders={seller_id: 1_000},
    )
    opened = engine.resolve(
        (
            make_action(
                actor_id=buyer_id,
                tick=1,
                action_type=ActionType.SUBMIT_ORDER,
                params={
                    "symbol": "SHORT",
                    "side": "buy",
                    "order_type": "limit",
                    "qty": 10,
                    "limit_price_cents": 100,
                },
            ),
            make_action(
                actor_id=short_seller_id,
                tick=1,
                action_type=ActionType.SHORT,
                params={
                    "symbol": "SHORT",
                    "qty": 10,
                    "limit_price_cents": 100,
                    "collateral_cents": 1_500,
                },
            ),
        ),
        1,
        emit_at(1),
    )
    assert any(event.kind == MARGIN_CALL for event in opened)

    forced = engine.resolve(
        (
            make_action(
                actor_id=seller_id,
                tick=2,
                action_type=ActionType.SUBMIT_ORDER,
                params={
                    "symbol": "SHORT",
                    "side": "sell",
                    "order_type": "limit",
                    "qty": 10,
                    "limit_price_cents": 100,
                },
            ),
        ),
        2,
        emit_at(2),
    )

    assert any(event.kind == FORCED_LIQUIDATION for event in forced)
    position = economy.exchange.shorts[economy.exchange.holding_key(short_seller_id, "SHORT")]
    assert position.status == "covered"
    assert economy.exchange.holding(short_seller_id, "SHORT").qty == 0
