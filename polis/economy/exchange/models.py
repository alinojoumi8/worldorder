from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

SecurityClass = Literal["common", "preferred", "bond"]
OrderSide = Literal["buy", "sell"]
OrderType = Literal["limit", "market"]
OrderStatus = Literal["open", "partial", "filled", "cancelled", "expired", "rejected"]


@dataclass(slots=True)
class SecurityState:
    symbol: str
    issuer_firm_id: str
    security_class: SecurityClass
    shares_outstanding: int
    listed_tick: int
    listing_price_cents: int
    last_price_cents: int
    reference_price_cents: int
    ipo_round_id: str | None = None
    lockup_until_tick: int | None = None
    status: str = "listed"
    delisted_tick: int | None = None
    halt_until_tick: int | None = None
    breaker_count: int = 0


@dataclass(slots=True)
class HoldingState:
    holder_id: str
    symbol: str
    qty: int = 0
    reserved_qty: int = 0
    locked_qty: int = 0
    avg_cost_cents: int = 0


@dataclass(slots=True)
class OrderState:
    order_id: str
    symbol: str
    trader_id: str
    side: OrderSide
    order_type: OrderType
    qty: int
    remaining_qty: int
    limit_price_cents: int | None
    submitted_tick: int
    arrival_ordinal: int
    tif: str = "day"
    status: OrderStatus = "open"
    reserved_cents: int = 0
    reserved_qty: int = 0
    filled_qty: int = 0
    filled_notional_cents: int = 0
    commission_cents: int = 0
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TradeState:
    trade_id: str
    symbol: str
    tick: int
    price_cents: int
    qty: int
    buy_order_id: str
    sell_order_id: str
    buyer_id: str
    seller_id: str
    aggressor: str
    commission_buy_cents: int
    commission_sell_cents: int
    ledger_txn_id: str


@dataclass(frozen=True, slots=True)
class OhlcvState:
    symbol: str
    session_tick: int
    open_cents: int
    high_cents: int
    low_cents: int
    close_cents: int
    volume: int
    vwap_cents: int | None
    trades_n: int


@dataclass(slots=True)
class ShortPositionState:
    trader_id: str
    symbol: str
    qty: int
    entry_price_cents: int
    collateral_cents: int
    opened_tick: int
    borrow_fee_bp: int
    margin_deadline_tick: int | None = None
    status: str = "open"


@dataclass(slots=True)
class IpoState:
    ipo_id: str
    firm_id: str
    symbol: str
    shares_offered: int
    primary_shares: int
    secondary_shares: int
    price_low_cents: int
    price_high_cents: int
    underwriter_bank_id: str
    announced_tick: int
    book_close_tick: int
    indications: dict[str, tuple[int, int]] = field(default_factory=dict)
    status: str = "announced"


@dataclass(slots=True)
class ExchangeState:
    securities: dict[str, SecurityState] = field(default_factory=dict)
    holdings: dict[str, HoldingState] = field(default_factory=dict)
    orders: dict[str, OrderState] = field(default_factory=dict)
    trades: list[TradeState] = field(default_factory=list)
    ohlcv: dict[str, OhlcvState] = field(default_factory=dict)
    shorts: dict[str, ShortPositionState] = field(default_factory=dict)
    ipos: dict[str, IpoState] = field(default_factory=dict)
    index_history_bp: dict[int, int] = field(default_factory=dict)
    index_divisor: int | None = None
    session_id: str | None = None
    session_tick: int | None = None

    @staticmethod
    def holding_key(holder_id: str, symbol: str) -> str:
        return f"{holder_id}:{symbol}"

    def holding(self, holder_id: str, symbol: str) -> HoldingState:
        key = self.holding_key(holder_id, symbol)
        row = self.holdings.get(key)
        if row is None:
            row = HoldingState(holder_id=holder_id, symbol=symbol)
            self.holdings[key] = row
        return row

    def dump(self) -> Mapping[str, Any]:
        return {
            "securities": {key: asdict(row) for key, row in sorted(self.securities.items())},
            "holdings": {key: asdict(row) for key, row in sorted(self.holdings.items())},
            "orders": {key: asdict(row) for key, row in sorted(self.orders.items())},
            "trades": [asdict(row) for row in self.trades],
            "ohlcv": {key: asdict(row) for key, row in sorted(self.ohlcv.items())},
            "shorts": {key: asdict(row) for key, row in sorted(self.shorts.items())},
            "ipos": {key: asdict(row) for key, row in sorted(self.ipos.items())},
            "index_history_bp": dict(sorted(self.index_history_bp.items())),
            "index_divisor": self.index_divisor,
            "session_id": self.session_id,
            "session_tick": self.session_tick,
        }

    @classmethod
    def load(cls, raw: Mapping[str, Any]) -> ExchangeState:
        def rows(name: str) -> Mapping[object, object]:
            value = raw.get(name, {})
            if not isinstance(value, Mapping):
                raise ValueError(f"exchange checkpoint {name} must be a mapping")
            return value

        state = cls()
        state.securities = {
            str(key): SecurityState(**dict(value))
            for key, value in sorted(rows("securities").items())
            if isinstance(value, Mapping)
        }
        state.holdings = {
            str(key): HoldingState(**dict(value))
            for key, value in sorted(rows("holdings").items())
            if isinstance(value, Mapping)
        }
        state.orders = {
            str(key): OrderState(
                **{
                    **dict(value),
                    "flags": tuple(value.get("flags", ())),
                }
            )
            for key, value in sorted(rows("orders").items())
            if isinstance(value, Mapping)
        }
        trades = raw.get("trades", ())
        if not isinstance(trades, list | tuple):
            raise ValueError("exchange checkpoint trades must be a sequence")
        state.trades = [TradeState(**dict(value)) for value in trades if isinstance(value, Mapping)]
        state.ohlcv = {
            str(key): OhlcvState(**dict(value))
            for key, value in sorted(rows("ohlcv").items())
            if isinstance(value, Mapping)
        }
        state.shorts = {
            str(key): ShortPositionState(**dict(value))
            for key, value in sorted(rows("shorts").items())
            if isinstance(value, Mapping)
        }
        state.ipos = {
            str(key): IpoState(
                **{
                    **dict(value),
                    "indications": {
                        str(investor): (int(pair[0]), int(pair[1]))
                        for investor, pair in value.get("indications", {}).items()
                        if isinstance(pair, list | tuple) and len(pair) == 2
                    },
                }
            )
            for key, value in sorted(rows("ipos").items())
            if isinstance(value, Mapping)
        }
        state.index_history_bp = {
            int(cast(Any, tick)): int(cast(Any, value))
            for tick, value in rows("index_history_bp").items()
        }
        divisor = raw.get("index_divisor")
        state.index_divisor = int(divisor) if divisor is not None else None
        session_id = raw.get("session_id")
        state.session_id = str(session_id) if session_id is not None else None
        session_tick = raw.get("session_tick")
        state.session_tick = int(session_tick) if session_tick is not None else None
        return state
