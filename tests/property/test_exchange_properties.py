from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from polis.agents.actions.types import Action, ActionType, make_action
from polis.agents.genesis import generate_agents
from polis.config.canon import canonical_bytes
from polis.config.settings import Settings, load_settings
from polis.economy.exchange.book import call_auction
from polis.economy.exchange.engine import ExchangeEngine
from polis.economy.exchange.models import OrderState
from polis.economy.genesis import create_economy
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.simulation import run_id_for
from polis.world.generator import generate_world

ROOT = Path(__file__).resolve().parents[2]


def _order(
    order_id: str,
    side: str,
    price: int,
    qty: int,
    ordinal: int,
    trader_id: str,
) -> OrderState:
    return OrderState(
        order_id=order_id,
        symbol="PROP",
        trader_id=trader_id,
        side=side,  # type: ignore[arg-type]
        order_type="limit",
        qty=qty,
        remaining_qty=qty,
        limit_price_cents=price,
        submitted_tick=1,
        arrival_ordinal=ordinal,
    )


def _build(
    *,
    commission_bp: int = 20,
    max_short_bp: int = 1_000,
    microscope: bool = False,
) -> tuple[Settings, Any, Any, ExchangeEngine, EventLog, str, str, str]:
    configured = load_settings(
        ROOT / "configs" / "m2-smoke.yaml",
        overrides={
            "run": {"scale": 12},
            "clock": (
                {"profile": "microscope", "ticks_per_sim_day": 24}
                if microscope
                else {"profile": "chronicle", "ticks_per_sim_day": 1}
            ),
            "population": {"initial_agents": 12},
            "exchange": {
                "enabled": True,
                "commission_bp": commission_bp,
                "commission_floor_cents": 0,
                "max_order_qty_bp": 10_000,
                "max_short_bp": max_short_bp,
                "lockup_days": 0,
            },
        },
    )
    rng = RngRegistry(configured.run.seed)
    world = generate_world(configured.world, rng)
    population = generate_agents(configured.population, world, rng)
    log = EventLog(run_id_for(configured), MemoryEventSink())
    economy = create_economy(
        configured,
        population,
        world,
        rng,
        run_id_for(configured),
        emit=lambda draft: log.stage(draft, tick=0, sim_time=datetime(2025, 1, 1)),
    ).state
    engine = ExchangeEngine(configured, population, economy, rng)
    issuer = next(row for row in economy.firms.values() if row.firm_id != "fm_broker")
    seller = issuer.founder_id
    others = [agent.agent_id for agent in population if agent.agent_id != seller]
    buyer, short_seller = others[:2]
    engine.list_security(
        symbol="PROP",
        issuer_firm_id=issuer.firm_id,
        shares_outstanding=1_000,
        listing_price_cents=100,
        tick=0,
        emit=_emit(log, 0),
        holders={seller: 1_000},
    )
    return configured, population, economy, engine, log, buyer, seller, short_seller


def _emit(log: EventLog, tick: int) -> Callable[[NewEvent], Event]:
    return lambda draft: log.stage(
        draft,
        tick=tick,
        sim_time=datetime(2025, 1, 1) + timedelta(days=tick),
    )


def _trade_actions(
    buyer: str,
    seller: str,
    qty: int,
    price: int,
    *,
    tick: int = 1,
) -> tuple[Action, Action]:
    return (
        make_action(
            actor_id=buyer,
            tick=tick,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "PROP",
                "side": "buy",
                "order_type": "limit",
                "qty": qty,
                "limit_price_cents": price,
            },
        ),
        make_action(
            actor_id=seller,
            tick=tick,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "PROP",
                "side": "sell",
                "order_type": "limit",
                "qty": qty,
                "limit_price_cents": price,
            },
        ),
    )


@given(
    qty=st.integers(min_value=1, max_value=100),
    price=st.integers(min_value=80, max_value=120),
)
@hyp_settings(max_examples=12, deadline=None)
def test_p1_match_cycle_leaves_no_crossed_book(qty: int, price: int) -> None:
    fills = call_auction(
        [
            _order("buy", "buy", price, qty, 0, "buyer"),
            _order("sell", "sell", price, qty, 1, "seller"),
        ],
        100,
    )
    assert sum(fill.qty for fill in fills) == qty


@given(qty=st.integers(min_value=1, max_value=100))
@hyp_settings(max_examples=8, deadline=None)
def test_p2_share_conservation(qty: int) -> None:
    _settings, _population, economy, engine, log, buyer, seller, _short = _build()
    engine.resolve(_trade_actions(buyer, seller, qty, 100), 1, _emit(log, 1))
    assert sum(row.qty for row in economy.exchange.holdings.values()) == 1_000


def test_p3_reservations_and_escrow_never_go_negative() -> None:
    _settings, _population, economy, engine, log, buyer, seller, _short = _build()
    engine.resolve(_trade_actions(buyer, seller, 37, 100), 1, _emit(log, 1))
    assert all(row.reserved_qty >= 0 for row in economy.exchange.holdings.values())
    assert all(
        account.balance_cents >= 0 for account in economy.ledger.accounts() if account.code == "esc"
    )


def test_p4_trade_ledger_and_commission_close_exactly() -> None:
    _settings, _population, economy, engine, log, buyer, seller, _short = _build()
    engine.resolve(_trade_actions(buyer, seller, 25, 100), 1, _emit(log, 1))
    trade = economy.exchange.trades[0]
    entries = [
        entry for entry in economy.ledger.entries() if str(entry.txn_id) == trade.ledger_txn_id
    ]
    assert sum(entry.direction * entry.amount_cents for entry in entries) == 0
    gross = trade.price_cents * trade.qty
    assert gross + trade.commission_buy_cents == (
        gross
        - trade.commission_sell_cents
        + trade.commission_buy_cents
        + trade.commission_sell_cents
    )


def test_p5_fill_never_exceeds_order_quantity_and_terminal_is_final() -> None:
    _settings, _population, economy, engine, log, buyer, seller, _short = _build()
    engine.resolve(_trade_actions(buyer, seller, 11, 100), 1, _emit(log, 1))
    terminal = list(economy.exchange.orders.values())
    assert all(order.filled_qty <= order.qty and order.status == "filled" for order in terminal)
    engine.resolve((), 2, _emit(log, 2))
    assert terminal == list(economy.exchange.orders.values())


@given(price=st.integers(min_value=80, max_value=120))
@hyp_settings(max_examples=8, deadline=None)
def test_p6_prints_stay_inside_session_band(price: int) -> None:
    settings, _population, economy, engine, log, buyer, seller, _short = _build()
    engine.resolve(_trade_actions(buyer, seller, 5, price), 1, _emit(log, 1))
    trade = economy.exchange.trades[0]
    lower = 100 * (10_000 - settings.exchange.band_bp) // 10_000
    upper = 100 * (10_000 + settings.exchange.band_bp) // 10_000
    assert lower <= trade.price_cents <= upper


def test_p7_recorded_book_replay_is_byte_identical() -> None:
    rows = [
        _order("b1", "buy", 101, 6, 0, "a"),
        _order("b2", "buy", 100, 8, 1, "b"),
        _order("s1", "sell", 99, 5, 2, "c"),
        _order("s2", "sell", 100, 9, 3, "d"),
    ]
    first = call_auction(rows, 100)
    replay = call_auction(rows, 100)
    assert canonical_bytes([asdict(row) for row in first]) == canonical_bytes(
        [asdict(row) for row in replay]
    )


def test_p8_zero_commission_preserves_trader_money() -> None:
    _settings, _population, economy, engine, log, buyer, seller, _short = _build(commission_bp=0)

    def trader_money() -> int:
        return sum(
            account.balance_cents
            for account in economy.ledger.accounts()
            if account.owner_id in {buyer, seller} and account.code in {"dep", "esc"}
        )

    before = trader_money()
    engine.resolve(_trade_actions(buyer, seller, 33, 100), 1, _emit(log, 1))
    assert trader_money() == before


def test_p9_self_trades_are_never_emitted() -> None:
    rows = [
        _order("buy", "buy", 100, 10, 0, "same"),
        _order("self", "sell", 100, 5, 1, "same"),
        _order("other", "sell", 100, 10, 2, "other"),
    ]
    fills = call_auction(rows, 100)
    assert fills
    assert all(fill.buy_order_id != "buy" or fill.sell_order_id != "self" for fill in fills)


def test_p10_price_time_priority_fills_earlier_order_first() -> None:
    rows = [
        _order("first", "buy", 100, 5, 0, "first"),
        _order("second", "buy", 100, 5, 1, "second"),
        _order("ask", "sell", 100, 5, 2, "seller"),
    ]
    fills = call_auction(rows, 100)
    assert [fill.buy_order_id for fill in fills] == ["first"]


def test_p11_short_position_never_exceeds_configured_cap() -> None:
    settings, _population, economy, engine, log, buyer, _seller, short_seller = _build(
        max_short_bp=1_000
    )
    actions = (
        make_action(
            actor_id=buyer,
            tick=1,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "PROP",
                "side": "buy",
                "order_type": "limit",
                "qty": 100,
                "limit_price_cents": 100,
            },
        ),
        make_action(
            actor_id=short_seller,
            tick=1,
            action_type=ActionType.SHORT,
            params={
                "symbol": "PROP",
                "qty": 100,
                "limit_price_cents": 100,
                "collateral_cents": 15_000,
            },
        ),
    )
    engine.resolve(actions, 1, _emit(log, 1))
    aggregate = sum(max(0, -row.qty) for row in economy.exchange.holdings.values())
    assert aggregate <= 1_000 * settings.exchange.max_short_bp // 10_000


def test_p12_cancel_releases_exact_remaining_reservation() -> None:
    _settings, _population, economy, engine, log, buyer, seller, _short = _build(microscope=True)
    tick = 9
    partial = (
        _trade_actions(buyer, seller, 10, 100, tick=tick)[0],
        make_action(
            actor_id=seller,
            tick=tick,
            action_type=ActionType.SUBMIT_ORDER,
            params={
                "symbol": "PROP",
                "side": "sell",
                "order_type": "limit",
                "qty": 4,
                "limit_price_cents": 100,
            },
        ),
    )
    engine.resolve(partial, tick, _emit(log, tick))
    order = next(row for row in economy.exchange.orders.values() if row.trader_id == buyer)
    released = order.reserved_cents
    escrow = next(
        account
        for account in economy.ledger.accounts()
        if account.owner_id == buyer and account.code == "esc"
    )
    before_deposit = economy.ledger.liquid(buyer)
    cancel = make_action(
        actor_id=buyer,
        tick=tick + 1,
        action_type=ActionType.CANCEL_ORDER,
        params={"order_id": order.order_id},
    )
    engine.resolve((cancel,), tick + 1, _emit(log, tick + 1))
    assert order.status == "cancelled"
    assert order.reserved_cents == 0
    assert escrow.balance_cents == 0
    assert economy.ledger.liquid(buyer) - before_deposit == released
