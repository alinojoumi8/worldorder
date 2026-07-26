from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polis.economy.exchange.models import OrderState


@dataclass(frozen=True, slots=True)
class Fill:
    buy_order_id: str
    sell_order_id: str
    price_cents: int
    qty: int
    aggressor: Literal["buy", "sell", "auction"]


def bid_priority(order: OrderState) -> tuple[int, int, str]:
    price = order.limit_price_cents if order.limit_price_cents is not None else 2**62
    return (-price, order.arrival_ordinal, order.order_id)


def ask_priority(order: OrderState) -> tuple[int, int, str]:
    price = order.limit_price_cents if order.limit_price_cents is not None else 0
    return (price, order.arrival_ordinal, order.order_id)


def crosses(incoming: OrderState, resting: OrderState) -> bool:
    if incoming.order_type == "market":
        return True
    if incoming.limit_price_cents is None or resting.limit_price_cents is None:
        return True
    if incoming.side == "buy":
        return incoming.limit_price_cents >= resting.limit_price_cents
    return incoming.limit_price_cents <= resting.limit_price_cents


def uncross(orders: list[OrderState], prev_close: int) -> tuple[int | None, int]:
    active = [order for order in orders if order.status in {"open", "partial"}]
    prices = sorted(
        {
            order.limit_price_cents
            for order in active
            if order.order_type == "limit" and order.limit_price_cents is not None
        }
    )
    if not prices:
        return None, 0
    candidates: list[tuple[int, int, int, int]] = []
    for price in prices:
        demand = sum(
            order.remaining_qty
            for order in active
            if order.side == "buy"
            and (
                order.order_type == "market"
                or (order.limit_price_cents is not None and order.limit_price_cents >= price)
            )
        )
        supply = sum(
            order.remaining_qty
            for order in active
            if order.side == "sell"
            and (
                order.order_type == "market"
                or (order.limit_price_cents is not None and order.limit_price_cents <= price)
            )
        )
        candidates.append(
            (min(demand, supply), -abs(demand - supply), -abs(price - prev_close), -price)
        )
    volume, _imbalance, _proximity, negative_price = max(candidates)
    if volume <= 0:
        return None, 0
    return -negative_price, volume


def call_auction(orders: list[OrderState], prev_close: int) -> tuple[Fill, ...]:
    price, volume = uncross(orders, prev_close)
    if price is None or volume <= 0:
        return ()
    bids = sorted(
        (
            order
            for order in orders
            if order.status in {"open", "partial"}
            and order.side == "buy"
            and (
                order.order_type == "market"
                or (order.limit_price_cents is not None and order.limit_price_cents >= price)
            )
        ),
        key=bid_priority,
    )
    asks = sorted(
        (
            order
            for order in orders
            if order.status in {"open", "partial"}
            and order.side == "sell"
            and (
                order.order_type == "market"
                or (order.limit_price_cents is not None and order.limit_price_cents <= price)
            )
        ),
        key=ask_priority,
    )
    remaining = volume
    fills: list[Fill] = []
    bid_index = 0
    ask_index = 0
    local_remaining = {order.order_id: order.remaining_qty for order in (*bids, *asks)}
    while bid_index < len(bids) and ask_index < len(asks) and remaining > 0:
        bid = bids[bid_index]
        ask = asks[ask_index]
        if bid.trader_id == ask.trader_id:
            if bid.arrival_ordinal < ask.arrival_ordinal:
                ask_index += 1
            else:
                bid_index += 1
            continue
        qty = min(
            remaining,
            local_remaining[bid.order_id],
            local_remaining[ask.order_id],
        )
        if qty <= 0:
            break
        fills.append(Fill(bid.order_id, ask.order_id, price, qty, "auction"))
        remaining -= qty
        local_remaining[bid.order_id] -= qty
        local_remaining[ask.order_id] -= qty
        if local_remaining[bid.order_id] == 0:
            bid_index += 1
        if local_remaining[ask.order_id] == 0:
            ask_index += 1
    return tuple(fills)


def continuous_matches(
    incoming: OrderState,
    resting_orders: list[OrderState],
) -> tuple[Fill, ...]:
    opposite = [
        order
        for order in resting_orders
        if order.status in {"open", "partial"}
        and order.symbol == incoming.symbol
        and order.side != incoming.side
    ]
    opposite.sort(key=ask_priority if incoming.side == "buy" else bid_priority)
    remaining = incoming.remaining_qty
    fills: list[Fill] = []
    for resting in opposite:
        if remaining <= 0 or not crosses(incoming, resting):
            break
        if resting.trader_id == incoming.trader_id:
            continue
        qty = min(remaining, resting.remaining_qty)
        price = resting.limit_price_cents
        if price is None:
            price = incoming.limit_price_cents
        if price is None:
            continue
        buy_id = incoming.order_id if incoming.side == "buy" else resting.order_id
        sell_id = incoming.order_id if incoming.side == "sell" else resting.order_id
        fills.append(Fill(buy_id, sell_id, price, qty, incoming.side))
        remaining -= qty
    return tuple(fills)
