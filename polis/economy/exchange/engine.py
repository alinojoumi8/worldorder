from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any, cast

from polis.agents.actions.types import (
    Action,
    ActionType,
    make_legacy_action,
)
from polis.agents.state import AgentPopulation
from polis.config.settings import Settings
from polis.economy.exchange.book import Fill, call_auction, continuous_matches, crosses
from polis.economy.exchange.models import (
    ExchangeState,
    IpoState,
    OhlcvState,
    OrderState,
    SecurityState,
    ShortPositionState,
    TradeState,
)
from polis.economy.ledger import LedgerError, Leg, account_id, bank_of, parse_account_id
from polis.economy.money import allocate
from polis.economy.state import EconomyState
from polis.events.kinds import (
    ACCOUNT_OPENED,
    BOOK_SNAPSHOT,
    BORROW_FEE_CHARGED,
    CIRCUIT_BREAKER_TRIGGERED,
    FORCED_LIQUIDATION,
    INDEX_COMPUTED,
    IPO_ANNOUNCED,
    IPO_COMPLETED,
    IPO_INDICATION,
    IPO_PRICED,
    MARGIN_CALL,
    OHLCV_COMPUTED,
    ORDER_CANCELLED,
    ORDER_EXPIRED,
    ORDER_FILLED,
    ORDER_PARTIALLY_FILLED,
    ORDER_REJECTED,
    ORDER_SUBMITTED,
    SECURITY_DELISTED,
    SECURITY_LISTED,
    SESSION_CLOSED,
    SESSION_OPENED,
    SHORT_COVERED,
    SHORT_OPENED,
    TRADE_EXECUTED,
    TRADING_RESUMED,
)
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry

Emit = Callable[[NewEvent], Event]


def _bp_ceil(value: int, rate_bp: int) -> int:
    return (value * rate_bp + 9_999) // 10_000


def _coalesce(legs: Iterable[Leg]) -> list[Leg]:
    totals: dict[tuple[str, int, str], int] = defaultdict(int)
    for leg in legs:
        totals[(leg.account_id, leg.direction, leg.reason)] += leg.amount_cents
    return [
        Leg(account, direction, amount, reason)
        for (account, direction, reason), amount in sorted(totals.items())
        if amount > 0
    ]


class ExchangeEngine:
    """Deterministic, reservation-backed exchange implementation."""

    def __init__(
        self,
        settings: Settings,
        population: AgentPopulation,
        economy: EconomyState,
        rng: RngRegistry,
    ) -> None:
        self.settings = settings
        self.population = population
        self.economy = economy
        self.rng = rng

    @property
    def state(self) -> ExchangeState:
        return self.economy.exchange

    def resolve(
        self,
        actions: Sequence[Action],
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        if not self.settings.exchange.enabled:
            return ()
        events: list[Event] = []
        events.extend(self._bootstrap_listing(tick, emit))
        exchange_actions = [
            action
            for action in actions
            if action.type
            in {
                ActionType.CANCEL_ORDER,
                ActionType.SUBMIT_ORDER,
                ActionType.SHORT,
                ActionType.IPO_LIST,
            }
        ]
        open_now, opening, closing = self._session_state(tick)
        if opening:
            events.append(self._open_session(tick, emit))
        for action in sorted(
            (row for row in exchange_actions if row.type == ActionType.CANCEL_ORDER),
            key=lambda row: (row.actor_id, str(row.action_id)),
        ):
            events.extend(self._cancel_action(action, tick, emit))
        for action in sorted(
            (row for row in exchange_actions if row.type == ActionType.IPO_LIST),
            key=lambda row: (row.actor_id, str(row.action_id)),
        ):
            events.extend(self._announce_ipo(action, tick, emit))

        new_orders = [
            action
            for action in exchange_actions
            if action.type in {ActionType.SUBMIT_ORDER, ActionType.SHORT}
        ]
        new_orders.extend(self._forced_cover_actions(tick))
        by_symbol: dict[str, list[Action]] = defaultdict(list)
        for action in new_orders:
            by_symbol[str(action.params.get("symbol", "")).upper()].append(action)
        for symbol, rows in sorted(by_symbol.items()):
            ordered = sorted(rows, key=lambda row: (row.actor_id, str(row.action_id)))
            self.rng.get("exchange.arrival", symbol, tick).shuffle(ordered)
            for ordinal, action in enumerate(ordered):
                order, admitted_events = self._admit(
                    action,
                    symbol,
                    ordinal,
                    tick,
                    open_now,
                    emit,
                )
                events.extend(admitted_events)
                if (
                    order is not None
                    and self.settings.clock.profile == "microscope"
                    and not opening
                    and not closing
                ):
                    events.extend(self._match_continuous(order, tick, emit))

        if self.settings.clock.profile == "chronicle" or opening or closing:
            for symbol in sorted(self.state.securities):
                events.extend(self._match_call(symbol, tick, emit))
        events.extend(self._close_ipos(tick, emit))
        events.extend(self._resume_halts(tick, emit))
        events.extend(self._mark_shorts(tick, emit))
        if self.settings.clock.profile == "chronicle" or closing:
            events.extend(self._close_session(tick, emit))
        return tuple(events)

    def _bootstrap_listing(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        listing_day = self.settings.exchange.bootstrap_listing_day
        if (
            listing_day is None
            or tick != listing_day * self.settings.clock.ticks_per_sim_day
            or self.state.securities
        ):
            return ()
        firm = next(
            (
                row
                for row in sorted(self.economy.firms.values(), key=lambda item: item.firm_id)
                if row.status == "active" and row.firm_id != "fm_broker"
            ),
            None,
        )
        if firm is None:
            return ()
        return self.list_security(
            symbol="POLS",
            issuer_firm_id=firm.firm_id,
            shares_outstanding=self.settings.exchange.bootstrap_shares,
            listing_price_cents=self.settings.exchange.bootstrap_price_cents,
            tick=tick,
            emit=emit,
            holders={firm.founder_id: self.settings.exchange.bootstrap_shares},
        )

    def list_security(
        self,
        *,
        symbol: str,
        issuer_firm_id: str,
        shares_outstanding: int,
        listing_price_cents: int,
        tick: int,
        emit: Emit,
        holders: dict[str, int] | None = None,
        ipo_round_id: str | None = None,
        lockup_until_tick: int | None = None,
    ) -> tuple[Event, ...]:
        symbol = symbol.upper()
        if symbol in self.state.securities:
            raise ValueError(f"security already exists: {symbol}")
        if shares_outstanding <= 0 or listing_price_cents <= 0:
            raise ValueError("listing shares and price must be positive")
        allocation = holders or {self.economy.firms[issuer_firm_id].founder_id: shares_outstanding}
        if sum(allocation.values()) != shares_outstanding:
            raise ValueError("initial holdings must equal shares outstanding")
        security = SecurityState(
            symbol=symbol,
            issuer_firm_id=issuer_firm_id,
            security_class="common",
            shares_outstanding=shares_outstanding,
            listed_tick=tick,
            listing_price_cents=listing_price_cents,
            last_price_cents=listing_price_cents,
            reference_price_cents=listing_price_cents,
            ipo_round_id=ipo_round_id,
            lockup_until_tick=lockup_until_tick,
        )
        self.state.securities[symbol] = security
        for holder_id, qty in sorted(allocation.items()):
            holding = self.state.holding(holder_id, symbol)
            holding.qty += qty
            holding.avg_cost_cents = listing_price_cents
            if (
                lockup_until_tick is not None
                and holder_id == self.economy.firms[issuer_firm_id].founder_id
            ):
                holding.locked_qty = qty
        self._reset_index_divisor()
        return (
            emit(
                NewEvent(
                    SECURITY_LISTED,
                    {
                        "symbol": symbol,
                        "issuer_firm_id": issuer_firm_id,
                        "class": "common",
                        "shares_outstanding": shares_outstanding,
                        "listing_price_cents": listing_price_cents,
                        "ipo_round_id": ipo_round_id,
                        "lockup_until_tick": lockup_until_tick,
                    },
                    subject_ids=(issuer_firm_id,),
                )
            ),
        )

    def delist(
        self,
        symbol: str,
        reason: str,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        security = self.state.securities[symbol]
        events: list[Event] = []
        for order in sorted(self.state.orders.values(), key=lambda row: row.order_id):
            if order.symbol == symbol and order.status in {"open", "partial"}:
                events.extend(self._cancel(order, tick, "session", emit, expired=False))
        security.status = "delisted"
        security.delisted_tick = tick
        holders_n = sum(
            1
            for holding in self.state.holdings.values()
            if holding.symbol == symbol and holding.qty != 0
        )
        events.append(
            emit(
                NewEvent(
                    SECURITY_DELISTED,
                    {
                        "symbol": symbol,
                        "reason": reason,
                        "final_price_cents": security.last_price_cents,
                        "holders_n": holders_n,
                    },
                    subject_ids=(security.issuer_firm_id,),
                )
            )
        )
        self._reset_index_divisor()
        return tuple(events)

    def cancel_entity(
        self,
        entity_id: str,
        tick: int,
        emit: Emit,
        *,
        initiator: str = "stay",
    ) -> tuple[tuple[Event, ...], tuple[str, ...], int, dict[str, int]]:
        events: list[Event] = []
        order_ids: list[str] = []
        released_cents = 0
        released_shares: dict[str, int] = defaultdict(int)
        for order in sorted(self.state.orders.values(), key=lambda row: row.order_id):
            if order.trader_id != entity_id or order.status not in {"open", "partial"}:
                continue
            order_ids.append(order.order_id)
            released_cents += order.reserved_cents
            released_shares[order.symbol] += order.reserved_qty
            events.extend(self._cancel(order, tick, initiator, emit, expired=False))
        return (
            tuple(events),
            tuple(order_ids),
            released_cents,
            dict(sorted(released_shares.items())),
        )

    def _session_state(self, tick: int) -> tuple[bool, bool, bool]:
        if self.settings.clock.profile == "chronicle":
            return True, True, True
        hour = tick % self.settings.clock.ticks_per_sim_day
        return 9 <= hour < 16, hour == 9, hour == 16

    def _open_session(self, tick: int, emit: Emit) -> Event:
        session_id = f"xs_{tick:010d}"
        self.state.session_id = session_id
        self.state.session_tick = tick
        for security in self.state.securities.values():
            security.reference_price_cents = security.last_price_cents
            security.breaker_count = 0
        return emit(
            NewEvent(
                SESSION_OPENED,
                {
                    "session_id": session_id,
                    "tick": tick,
                    "symbols": sorted(
                        symbol
                        for symbol, row in self.state.securities.items()
                        if row.status == "listed"
                    ),
                    "opening_auction": {},
                    "reference_prices": {
                        symbol: row.reference_price_cents
                        for symbol, row in sorted(self.state.securities.items())
                        if row.status == "listed"
                    },
                },
            )
        )

    def _close_session(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for order in sorted(self.state.orders.values(), key=lambda row: row.order_id):
            if order.status in {"open", "partial"}:
                events.extend(self._cancel(order, tick, "session", emit, expired=True))
        session_trades = [trade for trade in self.state.trades if trade.tick == tick]
        for symbol, security in sorted(self.state.securities.items()):
            if security.status != "listed":
                continue
            events.extend(self._market_data(symbol, tick, emit))
        events.extend(self._index(tick, emit))
        events.append(
            emit(
                NewEvent(
                    SESSION_CLOSED,
                    {
                        "session_id": self.state.session_id or f"xs_{tick:010d}",
                        "tick": tick,
                        "closing_auction": {},
                        "trades_n": len(session_trades),
                        "volume": sum(row.qty for row in session_trades),
                        "notional_cents": sum(row.qty * row.price_cents for row in session_trades),
                    },
                )
            )
        )
        self.state.session_id = None
        self.state.session_tick = None
        return tuple(events)

    def _admit(
        self,
        action: Action,
        symbol: str,
        arrival_ordinal: int,
        tick: int,
        session_open: bool,
        emit: Emit,
    ) -> tuple[OrderState | None, tuple[Event, ...]]:
        if action.type == ActionType.SUBMIT_ORDER and "ipo" in action.params.get("flags", ()):
            return None, self._ipo_indication(action, symbol, tick, emit)
        forced_liquidation = "forced_liquidation" in action.params.get("flags", ())
        if self._under_stay(action.actor_id) and not forced_liquidation:
            return None, (self._reject(action, symbol, "stay", {}, emit),)
        security = self.state.securities.get(symbol)
        if security is None or security.status != "listed":
            return None, (self._reject(action, symbol, "no_security", {}, emit),)
        if not session_open:
            return None, (self._reject(action, symbol, "session_closed", {}, emit),)
        if security.halt_until_tick is not None and tick < security.halt_until_tick:
            return None, (self._reject(action, symbol, "halted", {}, emit),)
        qty = int(action.params.get("qty", 0))
        max_qty = max(
            1,
            security.shares_outstanding * self.settings.exchange.max_order_qty_bp // 10_000,
        )
        if qty <= 0 or qty > max_qty:
            return None, (
                self._reject(action, symbol, "rate_limit", {"max_order_qty": max_qty}, emit),
            )
        side = cast(
            str, action.params.get("side", "sell" if action.type == ActionType.SHORT else "")
        )
        if side not in {"buy", "sell"}:
            return None, (self._reject(action, symbol, "invalid_side", {}, emit),)
        order_type = cast(str, action.params.get("order_type", "limit"))
        if order_type not in {"limit", "market"}:
            return None, (self._reject(action, symbol, "invalid_order_type", {}, emit),)
        raw_limit = action.params.get("limit_price_cents")
        limit_price = int(raw_limit) if raw_limit is not None else None
        if order_type == "limit" and limit_price is None:
            return None, (self._reject(action, symbol, "invalid_price", {}, emit),)
        lower, upper = self._band(security)
        if limit_price is not None and (
            limit_price <= 0
            or limit_price % self.settings.exchange.tick_size_cents != 0
            or not lower <= limit_price <= upper
        ):
            return None, (
                self._reject(
                    action,
                    symbol,
                    "price_band",
                    {"lower_cents": lower, "upper_cents": upper},
                    emit,
                ),
            )
        flags = tuple(str(value) for value in action.params.get("flags", ()))
        if action.type == ActionType.SHORT:
            flags = tuple(sorted({*flags, "opens_short"}))
            aggregate_short = sum(
                max(0, -holding.qty)
                for holding in self.state.holdings.values()
                if holding.symbol == symbol
            )
            short_cap = security.shares_outstanding * self.settings.exchange.max_short_bp // 10_000
            if aggregate_short + qty > short_cap:
                return None, (
                    self._reject(action, symbol, "short_cap", {"cap_qty": short_cap}, emit),
                )
        order_id = f"or_{str(action.action_id).replace('-', '')[:24]}"
        reserve_price = limit_price if limit_price is not None else upper
        reserved_cents = 0
        reserved_qty = 0
        events: list[Event] = []
        if side == "buy":
            reserved_cents = self._reserve_required(qty, reserve_price)
            try:
                deposit = self._deposit_account(action.actor_id)
            except LedgerError:
                return None, (self._reject(action, symbol, "insufficient_reservation", {}, emit),)
            if self.economy.ledger.balance(deposit) < reserved_cents:
                return None, (
                    self._reject(
                        action,
                        symbol,
                        "insufficient_reservation",
                        {"required_cents": reserved_cents},
                        emit,
                    ),
                )
            escrow, opened = self._escrow_account(action.actor_id, "exchange", tick, emit)
            events.extend(opened)
        elif "opens_short" in flags:
            collateral = int(action.params.get("collateral_cents", 0))
            required = _bp_ceil(
                qty * reserve_price,
                self.settings.exchange.initial_margin_bp,
            )
            if collateral < required:
                return None, (
                    self._reject(
                        action,
                        symbol,
                        "insufficient_reservation",
                        {"required_collateral_cents": required},
                        emit,
                    ),
                )
            try:
                deposit = self._deposit_account(action.actor_id)
            except LedgerError:
                return None, (self._reject(action, symbol, "insufficient_reservation", {}, emit),)
            if self.economy.ledger.balance(deposit) < collateral:
                return None, (self._reject(action, symbol, "insufficient_reservation", {}, emit),)
            escrow, opened = self._escrow_account(
                action.actor_id,
                f"margin-{symbol}",
                tick,
                emit,
            )
            events.extend(opened)
            reserved_cents = collateral
        else:
            holding = self.state.holding(action.actor_id, symbol)
            locked = 0 if forced_liquidation else holding.locked_qty
            available = holding.qty - holding.reserved_qty - locked
            if qty > max(0, available):
                reason = "lockup" if holding.locked_qty else "insufficient_reservation"
                return None, (
                    self._reject(
                        action,
                        symbol,
                        reason,
                        {"available_qty": max(0, available)},
                        emit,
                    ),
                )
            holding.reserved_qty += qty
            reserved_qty = qty

        order = OrderState(
            order_id=order_id,
            symbol=symbol,
            trader_id=action.actor_id,
            side=cast(Any, side),
            order_type=cast(Any, order_type),
            qty=qty,
            remaining_qty=qty,
            limit_price_cents=limit_price,
            submitted_tick=tick,
            arrival_ordinal=arrival_ordinal,
            reserved_cents=reserved_cents,
            reserved_qty=reserved_qty,
            flags=flags,
        )
        submitted = emit(
            NewEvent(
                ORDER_SUBMITTED,
                {
                    "order_id": order_id,
                    "symbol": symbol,
                    "trader_id": action.actor_id,
                    "side": side,
                    "order_type": order_type,
                    "limit_price_cents": limit_price,
                    "qty": qty,
                    "tif": "day",
                    "reserved_cents": reserved_cents,
                    "reserved_qty": reserved_qty,
                    "arrival_ordinal": arrival_ordinal,
                },
                actor_id=action.actor_id,
                subject_ids=(symbol,),
            )
        )
        events.append(submitted)
        if reserved_cents:
            source = self._deposit_account(action.actor_id)
            reason = "escrow"
            self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(source, escrow, reserved_cents, reason),
                tick=tick,
                cause=submitted,
            )
        self.state.orders[order_id] = order
        return order, tuple(events)

    def _match_call(self, symbol: str, tick: int, emit: Emit) -> tuple[Event, ...]:
        security = self.state.securities[symbol]
        if security.status != "listed":
            return ()
        orders = [
            row
            for row in self.state.orders.values()
            if row.symbol == symbol and row.status in {"open", "partial"}
        ]
        events: list[Event] = []
        for fill in call_auction(orders, security.reference_price_cents):
            if not self._in_band(security, fill.price_cents):
                events.extend(self._trigger_breaker(security, tick, fill.price_cents, emit))
                break
            events.extend(self._settle(fill, tick, emit))
        events.extend(self._cancel_self_crosses(symbol, tick, emit))
        for order in sorted(self.state.orders.values(), key=lambda row: row.order_id):
            if (
                order.symbol == symbol
                and order.order_type == "market"
                and order.status in {"open", "partial"}
            ):
                events.extend(
                    self._cancel(
                        order,
                        tick,
                        "market_unfilled",
                        emit,
                        expired=False,
                    )
                )
        events.append(self._book_snapshot(symbol, emit))
        return tuple(events)

    def _match_continuous(
        self,
        incoming: OrderState,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        security = self.state.securities[incoming.symbol]
        resting = [
            row
            for row in self.state.orders.values()
            if row.order_id != incoming.order_id and row.submitted_tick <= tick
        ]
        events: list[Event] = []
        for order in sorted(resting, key=lambda row: row.order_id):
            if order.trader_id == incoming.trader_id and crosses(incoming, order):
                events.extend(self._cancel(order, tick, "stp", emit, expired=False))
        resting = [row for row in resting if row.status in {"open", "partial"}]
        for fill in continuous_matches(incoming, resting):
            if not self._in_band(security, fill.price_cents):
                events.extend(self._trigger_breaker(security, tick, fill.price_cents, emit))
                break
            events.extend(self._settle(fill, tick, emit))
            if incoming.status == "filled":
                break
        if incoming.order_type == "market" and incoming.status in {"open", "partial"}:
            events.extend(
                self._cancel(
                    incoming,
                    tick,
                    "market_unfilled",
                    emit,
                    expired=False,
                )
            )
        events.append(self._book_snapshot(incoming.symbol, emit))
        return tuple(events)

    def _cancel_self_crosses(
        self,
        symbol: str,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        events: list[Event] = []
        while True:
            active = [
                row
                for row in self.state.orders.values()
                if row.symbol == symbol and row.status in {"open", "partial"}
            ]
            pairs = [
                (bid, ask)
                for bid in active
                if bid.side == "buy"
                for ask in active
                if ask.side == "sell" and bid.trader_id == ask.trader_id and crosses(bid, ask)
            ]
            if not pairs:
                break
            bid, ask = min(
                pairs,
                key=lambda pair: (
                    pair[0].trader_id,
                    pair[0].order_id,
                    pair[1].order_id,
                ),
            )
            newer = max(
                (bid, ask),
                key=lambda row: (
                    row.submitted_tick,
                    row.arrival_ordinal,
                    row.order_id,
                ),
            )
            events.extend(self._cancel(newer, tick, "stp", emit, expired=False))
        return tuple(events)

    def _forced_cover_actions(self, tick: int) -> list[Action]:
        actions: list[Action] = []
        for position in sorted(
            self.state.shorts.values(),
            key=lambda row: (row.trader_id, row.symbol),
        ):
            if (
                position.status != "open"
                or position.qty <= 0
                or position.margin_deadline_tick is None
                or tick < position.margin_deadline_tick
            ):
                continue
            already_pending = any(
                order.trader_id == position.trader_id
                and order.symbol == position.symbol
                and "forced_liquidation" in order.flags
                and order.status in {"open", "partial"}
                for order in self.state.orders.values()
            )
            if already_pending:
                continue
            actions.append(
                make_legacy_action(
                    actor_id=position.trader_id,
                    tick=tick,
                    action_type=ActionType.SUBMIT_ORDER,
                    params={
                        "symbol": position.symbol,
                        "side": "buy",
                        "order_type": "market",
                        "qty": position.qty,
                        "limit_price_cents": None,
                        "flags": ["forced_liquidation"],
                    },
                    reasoning="mandatory maintenance-margin liquidation",
                )
            )
        return actions

    def _settle(self, fill: Fill, tick: int, emit: Emit) -> tuple[Event, ...]:
        buy = self.state.orders[fill.buy_order_id]
        sell = self.state.orders[fill.sell_order_id]
        qty = min(fill.qty, buy.remaining_qty, sell.remaining_qty)
        if qty <= 0 or buy.trader_id == sell.trader_id:
            return ()
        gross = fill.price_cents * qty
        commission_buy = self._commission(gross)
        commission_sell = self._commission(gross)
        buyer_escrow = self._escrow_id(buy.trader_id, "exchange")
        seller_deposit = self._deposit_account(sell.trader_id)
        broker_deposit = self._deposit_account("fm_broker")
        buyer_deposit = self._deposit_account(buy.trader_id)
        remaining_buy = buy.remaining_qty - qty
        reserve_price = buy.limit_price_cents
        if reserve_price is None:
            reserve_price = self._band(self.state.securities[buy.symbol])[1]
        remaining_required = self._reserve_required(remaining_buy, reserve_price)
        actual = gross + commission_buy
        release = max(0, buy.reserved_cents - actual - remaining_required)
        legs: list[Leg] = []
        seller_net = gross - commission_sell
        if seller_net > 0:
            legs.extend(
                self.economy.ledger.transfer(
                    buyer_escrow,
                    seller_deposit,
                    seller_net,
                    "trade",
                )
            )
        total_commission = commission_buy + commission_sell
        if total_commission > 0:
            legs.extend(
                self.economy.ledger.transfer(
                    buyer_escrow,
                    broker_deposit,
                    total_commission,
                    "trade",
                )
            )
        if release > 0:
            legs.extend(
                self.economy.ledger.transfer(
                    buyer_escrow,
                    buyer_deposit,
                    release,
                    "escrow",
                )
            )
        expected_txn = self.economy.ledger.next_txn_id(tick)
        trade_id = f"tr_{tick:010d}_{len(self.state.trades):010d}"
        trade_event = emit(
            NewEvent(
                TRADE_EXECUTED,
                {
                    "trade_id": trade_id,
                    "symbol": buy.symbol,
                    "price_cents": fill.price_cents,
                    "qty": qty,
                    "buy_order_id": buy.order_id,
                    "sell_order_id": sell.order_id,
                    "buyer_id": buy.trader_id,
                    "seller_id": sell.trader_id,
                    "aggressor": fill.aggressor,
                    "commission_buy_cents": commission_buy,
                    "commission_sell_cents": commission_sell,
                    "ledger_txn_id": str(expected_txn),
                },
                actor_id=buy.trader_id if fill.aggressor == "buy" else sell.trader_id,
                subject_ids=(buy.trader_id, sell.trader_id, buy.symbol),
            )
        )
        txn_id = self.economy.ledger.post_transaction(
            _coalesce(legs),
            tick=tick,
            cause=trade_event,
        )
        if txn_id != expected_txn:
            raise RuntimeError("exchange ledger ordinal diverged")

        buy.reserved_cents = remaining_required
        buy.remaining_qty -= qty
        buy.filled_qty += qty
        buy.filled_notional_cents += gross
        buy.commission_cents += commission_buy
        sell.remaining_qty -= qty
        sell.filled_qty += qty
        sell.filled_notional_cents += gross
        sell.commission_cents += commission_sell

        buyer_holding = self.state.holding(buy.trader_id, buy.symbol)
        old_qty = buyer_holding.qty
        buyer_holding.qty += qty
        if buyer_holding.qty > 0:
            old_positive = max(0, old_qty)
            buyer_holding.avg_cost_cents = (
                old_positive * buyer_holding.avg_cost_cents + gross
            ) // max(1, old_positive + qty)
        seller_holding = self.state.holding(sell.trader_id, sell.symbol)
        seller_holding.qty -= qty
        if "opens_short" not in sell.flags:
            seller_holding.reserved_qty -= qty
            sell.reserved_qty -= qty
        if "forced_liquidation" in sell.flags and seller_holding.locked_qty:
            seller_holding.locked_qty = max(0, seller_holding.locked_qty - qty)

        events: list[Event] = [trade_event]
        self.state.securities[buy.symbol].last_price_cents = fill.price_cents
        self.state.trades.append(
            TradeState(
                trade_id=trade_id,
                symbol=buy.symbol,
                tick=tick,
                price_cents=fill.price_cents,
                qty=qty,
                buy_order_id=buy.order_id,
                sell_order_id=sell.order_id,
                buyer_id=buy.trader_id,
                seller_id=sell.trader_id,
                aggressor=fill.aggressor,
                commission_buy_cents=commission_buy,
                commission_sell_cents=commission_sell,
                ledger_txn_id=str(txn_id),
            )
        )
        if "opens_short" in sell.flags:
            collateral = sell.reserved_cents * qty // max(1, sell.remaining_qty + qty)
            sell.reserved_cents -= collateral
            events.append(
                self._record_short(
                    sell,
                    qty,
                    fill.price_cents,
                    collateral,
                    tick,
                    emit,
                )
            )
        if old_qty < 0:
            events.extend(
                self._record_cover(
                    buy,
                    min(qty, -old_qty),
                    fill.price_cents,
                    tick,
                    emit,
                )
            )
        events.extend(self._fill_events(buy, emit))
        events.extend(self._fill_events(sell, emit))
        return tuple(events)

    def _fill_events(self, order: OrderState, emit: Emit) -> tuple[Event, ...]:
        average = order.filled_notional_cents // max(1, order.filled_qty)
        if order.remaining_qty == 0:
            order.status = "filled"
            return (
                emit(
                    NewEvent(
                        ORDER_FILLED,
                        {
                            "order_id": order.order_id,
                            "total_qty": order.qty,
                            "avg_price_cents": average,
                            "commission_cents": order.commission_cents,
                        },
                        actor_id=order.trader_id,
                    )
                ),
            )
        order.status = "partial"
        return (
            emit(
                NewEvent(
                    ORDER_PARTIALLY_FILLED,
                    {
                        "order_id": order.order_id,
                        "filled_qty": order.filled_qty,
                        "remaining_qty": order.remaining_qty,
                        "avg_price_cents": average,
                    },
                    actor_id=order.trader_id,
                )
            ),
        )

    def _cancel_action(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        order_id = str(action.params.get("order_id", ""))
        order = self.state.orders.get(order_id)
        if (
            order is None
            or order.trader_id != action.actor_id
            or order.status not in {"open", "partial"}
            or order.submitted_tick >= tick
        ):
            return ()
        return self._cancel(order, tick, "trader", emit, expired=False)

    def _cancel(
        self,
        order: OrderState,
        tick: int,
        initiator: str,
        emit: Emit,
        *,
        expired: bool,
    ) -> tuple[Event, ...]:
        if order.status not in {"open", "partial"}:
            return ()
        released_cents = order.reserved_cents
        released_qty = order.reserved_qty
        kind = ORDER_EXPIRED if expired else ORDER_CANCELLED
        payload: dict[str, Any] = {
            "order_id": order.order_id,
            "remaining_qty": order.remaining_qty,
            "released_cents": released_cents,
            "released_qty": released_qty,
        }
        if not expired:
            payload["initiator"] = initiator
        event = emit(
            NewEvent(
                kind,
                payload,
                actor_id=order.trader_id,
                subject_ids=(order.symbol,),
            )
        )
        if released_cents:
            ref = f"margin-{order.symbol}" if "opens_short" in order.flags else "exchange"
            escrow = self._escrow_id(order.trader_id, ref)
            deposit = self._deposit_account(order.trader_id)
            self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(
                    escrow,
                    deposit,
                    released_cents,
                    "escrow",
                ),
                tick=tick,
                cause=event,
            )
        if released_qty:
            holding = self.state.holding(order.trader_id, order.symbol)
            holding.reserved_qty -= released_qty
        order.reserved_cents = 0
        order.reserved_qty = 0
        order.status = "expired" if expired else "cancelled"
        return (event,)

    def _market_data(self, symbol: str, tick: int, emit: Emit) -> tuple[Event, ...]:
        security = self.state.securities[symbol]
        trades = [row for row in self.state.trades if row.symbol == symbol and row.tick == tick]
        if trades:
            prices = [row.price_cents for row in trades]
            volume = sum(row.qty for row in trades)
            value = sum(row.qty * row.price_cents for row in trades)
            row = OhlcvState(
                symbol=symbol,
                session_tick=tick,
                open_cents=trades[0].price_cents,
                high_cents=max(prices),
                low_cents=min(prices),
                close_cents=trades[-1].price_cents,
                volume=volume,
                vwap_cents=value // volume,
                trades_n=len(trades),
            )
        else:
            row = OhlcvState(
                symbol=symbol,
                session_tick=tick,
                open_cents=security.last_price_cents,
                high_cents=security.last_price_cents,
                low_cents=security.last_price_cents,
                close_cents=security.last_price_cents,
                volume=0,
                vwap_cents=None,
                trades_n=0,
            )
        self.state.ohlcv[f"{symbol}:{tick}"] = row
        return (
            emit(
                NewEvent(
                    OHLCV_COMPUTED,
                    {
                        "symbol": symbol,
                        "session_tick": tick,
                        "open_cents": row.open_cents,
                        "high_cents": row.high_cents,
                        "low_cents": row.low_cents,
                        "close_cents": row.close_cents,
                        "volume": row.volume,
                        "vwap_cents": row.vwap_cents,
                        "trades_n": row.trades_n,
                    },
                    subject_ids=(symbol,),
                )
            ),
        )

    def _index(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        constituents = [
            row
            for row in self.state.securities.values()
            if row.status == "listed" and row.security_class == "common"
        ]
        if not constituents:
            return ()
        mcap = sum(row.last_price_cents * row.shares_outstanding for row in constituents)
        if self.state.index_divisor is None or self.state.index_divisor <= 0:
            self.state.index_divisor = max(1, mcap)
        value = 10_000 * mcap // self.state.index_divisor
        self.state.index_history_bp[tick] = value
        return (
            emit(
                NewEvent(
                    INDEX_COMPUTED,
                    {
                        "index_name": "POLIS_ALL",
                        "value_bp": value,
                        "divisor": self.state.index_divisor,
                        "constituents": sorted(row.symbol for row in constituents),
                        "mcap_cents": mcap,
                    },
                )
            ),
        )

    def _book_snapshot(self, symbol: str, emit: Emit) -> Event:
        bids = [
            row
            for row in self.state.orders.values()
            if row.symbol == symbol and row.side == "buy" and row.status in {"open", "partial"}
        ]
        asks = [
            row
            for row in self.state.orders.values()
            if row.symbol == symbol and row.side == "sell" and row.status in {"open", "partial"}
        ]
        bid_levels: dict[int, int] = defaultdict(int)
        ask_levels: dict[int, int] = defaultdict(int)
        for order in bids:
            if order.limit_price_cents is not None:
                bid_levels[order.limit_price_cents] += order.remaining_qty
        for order in asks:
            if order.limit_price_cents is not None:
                ask_levels[order.limit_price_cents] += order.remaining_qty
        levels = [
            {"side": "buy", "price_cents": price, "qty": qty}
            for price, qty in sorted(bid_levels.items(), reverse=True)[:5]
        ] + [
            {"side": "sell", "price_cents": price, "qty": qty}
            for price, qty in sorted(ask_levels.items())[:5]
        ]
        return emit(
            NewEvent(
                BOOK_SNAPSHOT,
                {
                    "symbol": symbol,
                    "best_bid_cents": max(bid_levels, default=None),
                    "best_ask_cents": min(ask_levels, default=None),
                    "bid_depth": sum(bid_levels.values()),
                    "ask_depth": sum(ask_levels.values()),
                    "levels": levels,
                },
                subject_ids=(symbol,),
            )
        )

    def _announce_ipo(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        params = action.params
        firm_id = str(params.get("firm_id", ""))
        symbol = str(params.get("symbol", "")).upper()
        firm = self.economy.firms.get(firm_id)
        if (
            firm is None
            or firm.founder_id != action.actor_id
            or self._under_stay(firm_id)
            or symbol in self.state.securities
            or any(
                row.symbol == symbol and row.status == "announced"
                for row in self.state.ipos.values()
            )
        ):
            return (self._reject(action, symbol, "no_security", {"ipo": "ineligible"}, emit),)
        shares_offered = int(params.get("shares_offered", 0))
        primary = int(params.get("primary_shares", 0))
        secondary = int(params.get("secondary_shares", 0))
        low = int(params.get("price_low_cents", 0))
        high = int(params.get("price_high_cents", 0))
        underwriter = str(params.get("underwriter_bank_id", ""))
        startup = next(
            (row for row in self.economy.ventures.startups.values() if row.firm_id == firm_id),
            None,
        )
        founded_tick = startup.founded_tick if startup is not None else 0
        minimum_age_ticks = (
            self.settings.exchange.ipo_min_age_days * self.settings.clock.ticks_per_sim_day
        )
        bank = self.economy.banks.get(underwriter)
        maximum_fees = (
            _bp_ceil(
                shares_offered * high,
                self.settings.exchange.underwriting_fee_bp,
            )
            + self.settings.exchange.listing_fee_cents
        )
        if (
            shares_offered <= 0
            or primary < 0
            or secondary < 0
            or primary + secondary != shares_offered
            or low <= 0
            or high < low
            or tick - founded_tick < minimum_age_ticks
            or firm.cumulative_revenue_cents < self.settings.exchange.ipo_min_revenue_cents
            or self.economy.ledger.net_worth(firm_id) <= 0
            or bank is None
            or bank.is_central
            or bank.status != "active"
            or bank.lending_frozen
            or self.economy.ledger.liquid(firm_id) < maximum_fees
        ):
            return (
                self._reject(
                    action,
                    symbol,
                    "invalid_ipo",
                    {"ipo": "eligibility_or_fee_funding"},
                    emit,
                ),
            )
        ipo_id = f"ipo_{str(action.action_id).replace('-', '')[:20]}"
        close_tick = tick + (
            self.settings.exchange.ipo_book_days * self.settings.clock.ticks_per_sim_day
        )
        ipo = IpoState(
            ipo_id=ipo_id,
            firm_id=firm_id,
            symbol=symbol,
            shares_offered=shares_offered,
            primary_shares=primary,
            secondary_shares=secondary,
            price_low_cents=low,
            price_high_cents=high,
            underwriter_bank_id=underwriter,
            announced_tick=tick,
            book_close_tick=close_tick,
        )
        self.state.ipos[ipo_id] = ipo
        return (
            emit(
                NewEvent(
                    IPO_ANNOUNCED,
                    {
                        "firm_id": firm_id,
                        "symbol": symbol,
                        "shares_offered": shares_offered,
                        "primary_shares": primary,
                        "secondary_shares": secondary,
                        "price_low_cents": low,
                        "price_high_cents": high,
                        "underwriter_bank_id": underwriter,
                        "book_close_tick": close_tick,
                    },
                    actor_id=action.actor_id,
                    subject_ids=(firm_id,),
                )
            ),
        )

    def _ipo_indication(
        self,
        action: Action,
        symbol: str,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        ipo = next(
            (
                row
                for row in self.state.ipos.values()
                if row.symbol == symbol and row.status == "announced"
            ),
            None,
        )
        if ipo is None or tick > ipo.book_close_tick:
            return (self._reject(action, symbol, "no_security", {"ipo": "closed"}, emit),)
        qty = int(action.params.get("qty", 0))
        limit_price = int(action.params.get("limit_price_cents", 0))
        if qty <= 0 or not ipo.price_low_cents <= limit_price <= ipo.price_high_cents:
            return (self._reject(action, symbol, "price_band", {"ipo": True}, emit),)
        required = qty * limit_price
        try:
            deposit = self._deposit_account(action.actor_id)
        except LedgerError:
            return (self._reject(action, symbol, "insufficient_reservation", {}, emit),)
        if self.economy.ledger.balance(deposit) < required:
            return (
                self._reject(
                    action,
                    symbol,
                    "insufficient_reservation",
                    {"required_cents": required},
                    emit,
                ),
            )
        escrow, opened = self._escrow_account(action.actor_id, "exchange", tick, emit)
        indication = emit(
            NewEvent(
                IPO_INDICATION,
                {
                    "firm_id": ipo.firm_id,
                    "investor_id": action.actor_id,
                    "qty": qty,
                    "limit_price_cents": limit_price,
                },
                actor_id=action.actor_id,
                subject_ids=(ipo.firm_id,),
            )
        )
        self.economy.ledger.post_transaction(
            self.economy.ledger.transfer(deposit, escrow, required, "escrow"),
            tick=tick,
            cause=indication,
        )
        previous = ipo.indications.get(action.actor_id)
        if previous is not None:
            raise ValueError("one IPO indication per investor is permitted")
        ipo.indications[action.actor_id] = (qty, limit_price)
        return (*opened, indication)

    def _close_ipos(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for ipo in sorted(self.state.ipos.values(), key=lambda row: row.ipo_id):
            if ipo.status != "announced" or tick < ipo.book_close_tick:
                continue
            events.extend(self._complete_ipo(ipo, tick, emit))
        return tuple(events)

    def _complete_ipo(self, ipo: IpoState, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not ipo.indications:
            ipo.status = "failed"
            return ()
        price_levels = sorted({price for _qty, price in ipo.indications.values()}, reverse=True)
        clearing = ipo.price_low_cents
        for price in price_levels:
            demand = sum(qty for qty, limit in ipo.indications.values() if limit >= price)
            if demand >= ipo.shares_offered:
                clearing = price
                break
        offer = max(
            self.settings.exchange.tick_size_cents,
            clearing * (10_000 - self.settings.exchange.underwriter_discount_bp) // 10_000,
        )
        offer -= offer % self.settings.exchange.tick_size_cents
        eligible = [
            (investor, qty)
            for investor, (qty, limit) in sorted(ipo.indications.items())
            if limit >= offer
        ]
        total_demand = sum(qty for _investor, qty in eligible)
        sold = min(ipo.shares_offered, total_demand)
        allocations = allocate(sold, eligible)
        priced = emit(
            NewEvent(
                IPO_PRICED,
                {
                    "firm_id": ipo.firm_id,
                    "symbol": ipo.symbol,
                    "clearing_price_cents": clearing,
                    "offer_price_cents": offer,
                    "discount_bp": self.settings.exchange.underwriter_discount_bp,
                    "oversubscription_bp": 10_000 * total_demand // ipo.shares_offered,
                },
                subject_ids=(ipo.firm_id,),
            )
        )
        gross = sold * offer
        underwriting_fee = _bp_ceil(
            gross,
            self.settings.exchange.underwriting_fee_bp,
        )
        listing_fee = min(
            self.settings.exchange.listing_fee_cents, max(0, gross - underwriting_fee)
        )
        primary_sold = min(ipo.primary_shares, sold)
        secondary_sold = sold - primary_sold
        firm_deposit = self._deposit_account(ipo.firm_id)
        founder_id = self.economy.firms[ipo.firm_id].founder_id
        founder_deposit = self._deposit_account(founder_id)
        underwriter_deposit = self._deposit_account(ipo.underwriter_bank_id)
        treasury_deposit = self._deposit_account("gv_treasury")
        legs: list[Leg] = []
        for investor, allocation in sorted(allocations.items()):
            escrow = self._escrow_id(investor, "exchange")
            paid = allocation * offer
            indicated_qty, indicated_price = ipo.indications[investor]
            reserved = indicated_qty * indicated_price
            if paid:
                primary_amount = paid * primary_sold // max(1, sold)
                secondary_amount = paid - primary_amount
                if primary_amount:
                    legs.extend(
                        self.economy.ledger.transfer(
                            escrow,
                            firm_deposit,
                            primary_amount,
                            "trade",
                        )
                    )
                if secondary_amount:
                    legs.extend(
                        self.economy.ledger.transfer(
                            escrow,
                            founder_deposit,
                            secondary_amount,
                            "trade",
                        )
                    )
            release = reserved - paid
            if release:
                legs.extend(
                    self.economy.ledger.transfer(
                        escrow,
                        self._deposit_account(investor),
                        release,
                        "escrow",
                    )
                )
        for investor, (qty, limit_price) in sorted(ipo.indications.items()):
            if investor in allocations:
                continue
            reserved = qty * limit_price
            legs.extend(
                self.economy.ledger.transfer(
                    self._escrow_id(investor, "exchange"),
                    self._deposit_account(investor),
                    reserved,
                    "escrow",
                )
            )
        fee_total = underwriting_fee + listing_fee
        if fee_total:
            if underwriting_fee:
                legs.extend(
                    self.economy.ledger.transfer(
                        firm_deposit,
                        underwriter_deposit,
                        underwriting_fee,
                        "trade",
                    )
                )
            if listing_fee:
                legs.extend(
                    self.economy.ledger.transfer(
                        firm_deposit,
                        treasury_deposit,
                        listing_fee,
                        "trade",
                    )
                )
        expected_txn = self.economy.ledger.next_txn_id(tick)
        completed = emit(
            NewEvent(
                IPO_COMPLETED,
                {
                    "firm_id": ipo.firm_id,
                    "symbol": ipo.symbol,
                    "allocations": dict(sorted(allocations.items())),
                    "gross_proceeds_cents": gross,
                    "primary_cents": primary_sold * offer,
                    "secondary_cents": secondary_sold * offer,
                    "underwriting_fee_cents": underwriting_fee,
                    "listing_fee_cents": listing_fee,
                    "txn_id": str(expected_txn),
                },
                subject_ids=(ipo.firm_id,),
            )
        )
        txn_id = self.economy.ledger.post_transaction(
            _coalesce(legs),
            tick=tick,
            cause=completed,
        )
        if txn_id != expected_txn:
            raise RuntimeError("IPO ledger ordinal diverged")
        total_shares = max(
            self.settings.exchange.bootstrap_shares,
            ipo.shares_offered,
        )
        holders = dict(allocations)
        holders[founder_id] = holders.get(founder_id, 0) + total_shares - sold
        ipo.status = "completed"
        listed = self.list_security(
            symbol=ipo.symbol,
            issuer_firm_id=ipo.firm_id,
            shares_outstanding=total_shares,
            listing_price_cents=offer,
            tick=tick,
            emit=emit,
            holders=holders,
            ipo_round_id=ipo.ipo_id,
            lockup_until_tick=tick
            + self.settings.exchange.lockup_days * self.settings.clock.ticks_per_sim_day,
        )
        return (priced, completed, *listed)

    def _record_short(
        self,
        order: OrderState,
        qty: int,
        price: int,
        collateral_cents: int,
        tick: int,
        emit: Emit,
    ) -> Event:
        key = ExchangeState.holding_key(order.trader_id, order.symbol)
        existing = self.state.shorts.get(key)
        if existing is None:
            position = ShortPositionState(
                trader_id=order.trader_id,
                symbol=order.symbol,
                qty=qty,
                entry_price_cents=price,
                collateral_cents=collateral_cents,
                opened_tick=tick,
                borrow_fee_bp=self.settings.exchange.borrow_fee_bp,
            )
            self.state.shorts[key] = position
        else:
            position = existing
            position.entry_price_cents = (
                position.entry_price_cents * position.qty + price * qty
            ) // (position.qty + qty)
            position.qty += qty
            position.collateral_cents += collateral_cents
        return emit(
            NewEvent(
                SHORT_OPENED,
                {
                    "trader_id": order.trader_id,
                    "symbol": order.symbol,
                    "qty": qty,
                    "price_cents": price,
                    "borrow_fee_bp": self.settings.exchange.borrow_fee_bp,
                    "collateral_cents": collateral_cents,
                    "margin_ratio_bp": 10_000 * collateral_cents // max(1, qty * price),
                },
                actor_id=order.trader_id,
                subject_ids=(order.symbol,),
            )
        )

    def _record_cover(
        self,
        order: OrderState,
        qty: int,
        price: int,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        if qty <= 0:
            return ()
        key = ExchangeState.holding_key(order.trader_id, order.symbol)
        position = self.state.shorts.get(key)
        if position is None:
            return ()
        covered = min(qty, position.qty)
        realised = (position.entry_price_cents - price) * covered
        released = position.collateral_cents * covered // max(1, position.qty)
        position.qty -= covered
        position.collateral_cents -= released
        if position.qty == 0:
            position.status = "covered"
        event = emit(
            NewEvent(
                SHORT_COVERED,
                {
                    "trader_id": order.trader_id,
                    "symbol": order.symbol,
                    "qty": covered,
                    "price_cents": price,
                    "realised_pnl_cents": realised,
                    "fees_paid_cents": order.commission_cents,
                },
                actor_id=order.trader_id,
                subject_ids=(order.symbol,),
            )
        )
        if released:
            self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(
                    self._escrow_id(order.trader_id, f"margin-{order.symbol}"),
                    self._deposit_account(order.trader_id),
                    released,
                    "escrow",
                ),
                tick=tick,
                cause=event,
            )
        events = [event]
        if "forced_liquidation" in order.flags:
            events.append(
                emit(
                    NewEvent(
                        FORCED_LIQUIDATION,
                        {
                            "trader_id": order.trader_id,
                            "symbol": order.symbol,
                            "qty": covered,
                            "avg_price_cents": price,
                            "shortfall_cents": max(
                                0,
                                covered * price - released,
                            ),
                        },
                        actor_id=order.trader_id,
                        subject_ids=(order.symbol,),
                    )
                )
            )
        return tuple(events)

    def _mark_shorts(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        ticks_per_year = (
            self.settings.clock.ticks_per_sim_day * self.settings.clock.days_per_sim_year
        )
        for _key, position in sorted(self.state.shorts.items()):
            if position.status != "open" or position.qty <= 0:
                continue
            security = self.state.securities[position.symbol]
            mark = security.last_price_cents
            loss = max(0, (mark - position.entry_price_cents) * position.qty)
            equity = position.collateral_cents - loss
            required = _bp_ceil(
                mark * position.qty,
                self.settings.exchange.maintenance_margin_bp,
            )
            if equity < required and position.margin_deadline_tick is None:
                position.margin_deadline_tick = tick + self.settings.clock.ticks_per_sim_day
                events.append(
                    emit(
                        NewEvent(
                            MARGIN_CALL,
                            {
                                "trader_id": position.trader_id,
                                "symbol": position.symbol,
                                "equity_cents": equity,
                                "required_cents": required,
                                "deadline_tick": position.margin_deadline_tick,
                            },
                            actor_id=position.trader_id,
                            subject_ids=(position.symbol,),
                        )
                    )
                )
            if tick > position.opened_tick and tick % self.settings.clock.ticks_per_sim_day == 0:
                fee = _bp_ceil(
                    mark * position.qty,
                    position.borrow_fee_bp,
                ) // max(1, ticks_per_year // self.settings.clock.ticks_per_sim_day)
                longs = [
                    (holding.holder_id, holding.qty)
                    for holding in self.state.holdings.values()
                    if holding.symbol == position.symbol and holding.qty > 0
                ]
                source = self._deposit_account(position.trader_id)
                if fee > 0 and longs and self.economy.ledger.balance(source) >= fee:
                    distribution = allocate(fee, longs)
                    legs: list[Leg] = []
                    for holder_id, cents in sorted(distribution.items()):
                        if cents:
                            legs.extend(
                                self.economy.ledger.transfer(
                                    source,
                                    self._deposit_account(holder_id),
                                    cents,
                                    "trade",
                                )
                            )
                    expected_txn = self.economy.ledger.next_txn_id(tick)
                    event = emit(
                        NewEvent(
                            BORROW_FEE_CHARGED,
                            {
                                "trader_id": position.trader_id,
                                "symbol": position.symbol,
                                "cents": fee,
                                "distributed_to": dict(sorted(distribution.items())),
                                "txn_id": str(expected_txn),
                            },
                            actor_id=position.trader_id,
                            subject_ids=(position.symbol,),
                        )
                    )
                    txn_id = self.economy.ledger.post_transaction(
                        _coalesce(legs),
                        tick=tick,
                        cause=event,
                    )
                    if txn_id != expected_txn:
                        raise RuntimeError("borrow-fee ledger ordinal diverged")
                    events.append(event)
        return tuple(events)

    def _trigger_breaker(
        self,
        security: SecurityState,
        tick: int,
        price: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        security.breaker_count += 1
        security.halt_until_tick = tick + self.settings.exchange.halt_ticks
        move_bp = (
            10_000
            * (price - security.reference_price_cents)
            // max(1, security.reference_price_cents)
        )
        return (
            emit(
                NewEvent(
                    CIRCUIT_BREAKER_TRIGGERED,
                    {
                        "symbol": security.symbol,
                        "reference_cents": security.reference_price_cents,
                        "last_cents": price,
                        "move_bp": move_bp,
                        "band_bp": self.settings.exchange.band_bp,
                        "halt_until_tick": security.halt_until_tick,
                        "breaker_count": security.breaker_count,
                    },
                    subject_ids=(security.symbol,),
                )
            ),
        )

    def _resume_halts(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for security in sorted(self.state.securities.values(), key=lambda row: row.symbol):
            if security.halt_until_tick is None or tick < security.halt_until_tick:
                continue
            security.halt_until_tick = None
            security.reference_price_cents = security.last_price_cents
            events.append(
                emit(
                    NewEvent(
                        TRADING_RESUMED,
                        {
                            "symbol": security.symbol,
                            "reopen_auction_price_cents": security.last_price_cents,
                            "new_band_bp": (
                                5_000
                                if security.breaker_count
                                >= self.settings.exchange.max_halts_per_session
                                else self.settings.exchange.band_bp
                            ),
                        },
                        subject_ids=(security.symbol,),
                    )
                )
            )
        return tuple(events)

    def _reject(
        self,
        action: Action,
        symbol: str,
        reason: str,
        detail: dict[str, Any],
        emit: Emit,
    ) -> Event:
        return emit(
            NewEvent(
                ORDER_REJECTED,
                {
                    "trader_id": action.actor_id,
                    "symbol": symbol,
                    "reason": reason,
                    "detail": detail,
                },
                actor_id=action.actor_id,
                subject_ids=(symbol,) if symbol else (),
            )
        )

    def _deposit_account(self, owner_id: str) -> str:
        deposits = [
            value
            for value in self.economy.ledger.accounts_of(owner_id)
            if parse_account_id(value)[0] == "dep"
        ]
        if not deposits:
            raise LedgerError(f"owner has no deposit account: {owner_id}")
        return deposits[0]

    def _under_stay(self, entity_id: str) -> bool:
        return any(
            case.entity_id == entity_id and case.status == "open"
            for case in self.economy.ventures.bankruptcies.values()
        )

    def _escrow_id(self, owner_id: str, ref: str) -> str:
        deposit = self._deposit_account(owner_id)
        bank = bank_of(deposit)
        if bank is None:
            raise LedgerError("deposit account has no bank")
        return account_id("esc", owner_id, bank_id=bank, ref=ref)

    def _escrow_account(
        self,
        owner_id: str,
        ref: str,
        tick: int,
        emit: Emit,
    ) -> tuple[str, tuple[Event, ...]]:
        value = self._escrow_id(owner_id, ref)
        if self.economy.ledger.is_open(value):
            return value, ()
        _code, _owner, bank, _ref = parse_account_id(value)
        self.economy.ledger.open_account(
            "esc",
            owner_id,
            "agent" if owner_id in self.population.agents else "institution",
            bank_id=bank,
            ref=ref,
            tick=tick,
        )
        event = emit(
            NewEvent(
                ACCOUNT_OPENED,
                {
                    "account_id": value,
                    "owner_id": owner_id,
                    "owner_type": (
                        "agent" if owner_id in self.population.agents else "institution"
                    ),
                    "bank_id": bank,
                    "account_type": "escrow",
                    "code": "esc",
                },
                subject_ids=(owner_id,),
            )
        )
        return value, (event,)

    def _commission(self, notional: int) -> int:
        if self.settings.exchange.commission_bp == 0:
            return 0
        return max(
            self.settings.exchange.commission_floor_cents,
            _bp_ceil(notional, self.settings.exchange.commission_bp),
        )

    def _reserve_required(self, qty: int, price: int) -> int:
        if qty <= 0:
            return 0
        notional = qty * price
        # A limit order can fill one share at a time. Reserve the maximum
        # per-fill commission so every partial-fill/cancel path remains exact.
        return notional + qty * self._commission(price)

    def _band(self, security: SecurityState) -> tuple[int, int]:
        width = self.settings.exchange.band_bp
        reference = security.reference_price_cents
        return (
            max(
                self.settings.exchange.tick_size_cents,
                reference * (10_000 - width) // 10_000,
            ),
            max(
                self.settings.exchange.tick_size_cents,
                reference * (10_000 + width) // 10_000,
            ),
        )

    def _in_band(self, security: SecurityState, price: int) -> bool:
        lower, upper = self._band(security)
        return lower <= price <= upper

    def _reset_index_divisor(self) -> None:
        constituents = [
            row
            for row in self.state.securities.values()
            if row.status == "listed" and row.security_class == "common"
        ]
        mcap = sum(row.last_price_cents * row.shares_outstanding for row in constituents)
        if mcap > 0:
            latest_tick = max(self.state.index_history_bp, default=None)
            current_value = (
                self.state.index_history_bp[latest_tick] if latest_tick is not None else 10_000
            )
            self.state.index_divisor = max(1, 10_000 * mcap // max(1, current_value))
