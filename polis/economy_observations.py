from __future__ import annotations

from dataclasses import replace

from polis.agents.actions.types import ActionType
from polis.agents.cognition.observation import Observation
from polis.agents.state import AgentPopulation
from polis.config.canon import canonical_bytes, sha256_hex
from polis.economy.state import EconomyState


def augment_economic_observations(
    observations: dict[str, Observation],
    population: AgentPopulation,
    economy: EconomyState,
) -> dict[str, Observation]:
    listed = tuple(
        security
        for _symbol, security in sorted(economy.exchange.securities.items())
        if security.status == "listed"
    )
    liquid_by_owner: dict[str, int] = {}
    for account in economy.ledger.accounts():
        if account.code in {"cash", "dep"}:
            liquid_by_owner[account.owner_id] = (
                liquid_by_owner.get(account.owner_id, 0) + account.balance_cents
            )
    observation_tick = min(
        (observation.tick for observation in observations.values()),
        default=0,
    )
    employment_by_agent = {
        employment.agent_id: employment
        for _employment_id, employment in sorted(economy.employments.items())
        if employment.started_tick <= observation_tick
        and (employment.ended_tick is None or employment.ended_tick > observation_tick)
    }
    offers_by_agent: dict[str, list[dict[str, str]]] = {}
    for _offer_id, offer in sorted(economy.offers.items()):
        if offer.status == "open":
            offers_by_agent.setdefault(offer.agent_id, []).append(
                {
                    "offer_id": offer.offer_id,
                    "firm_id": offer.firm_id,
                    "wage_cents": str(offer.wage_cents),
                }
            )
    open_orders_by_agent: dict[str, list[dict[str, object]]] = {}
    for _order_id, order in sorted(economy.exchange.orders.items()):
        if order.status in {"open", "partial"}:
            open_orders_by_agent.setdefault(order.trader_id, []).append(
                {
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "remaining_qty": order.remaining_qty,
                    "limit_price_cents": order.limit_price_cents,
                }
            )
    result: dict[str, Observation] = {}
    for agent_id, observation in sorted(observations.items()):
        agent = population[agent_id]
        legal_actions = set(observation.place.legal_actions)
        liquid_cents = liquid_by_owner.get(agent_id, 0)
        securities: list[dict[str, object]] = []
        has_holding = False
        has_available_holding = False
        minimum_price: int | None = None
        for security in listed:
            holding = economy.exchange.holdings.get(
                economy.exchange.holding_key(agent_id, security.symbol)
            )
            holding_qty = holding.qty if holding is not None else 0
            available_qty = (
                max(0, holding.qty - holding.reserved_qty - holding.locked_qty)
                if holding is not None
                else 0
            )
            has_holding = has_holding or holding_qty > 0
            has_available_holding = has_available_holding or available_qty > 0
            minimum_price = (
                security.last_price_cents
                if minimum_price is None
                else min(minimum_price, security.last_price_cents)
            )
            securities.append(
                {
                    "symbol": security.symbol,
                    "last_price_cents": security.last_price_cents,
                    "holding_qty": holding_qty,
                    "available_qty": available_qty,
                }
            )
        open_orders = tuple(open_orders_by_agent.get(agent_id, ()))
        if agent.age_years >= 18 and securities:
            can_buy = minimum_price is not None and liquid_cents >= minimum_price
            can_sell = has_available_holding
            if can_buy or can_sell:
                legal_actions.add(ActionType.SUBMIT_ORDER.value)
            if open_orders:
                legal_actions.add(ActionType.CANCEL_ORDER.value)

        employment = employment_by_agent.get(agent_id)
        employer = None
        if employment is not None:
            firm = economy.firms[employment.firm_id]
            employer = {
                "employment_id": employment.employment_id,
                "firm_id": firm.firm_id,
                "firm_name": firm.name,
                "occupation": employment.occupation,
                "wage_cents": employment.wage_cents,
            }
        offers = tuple(offers_by_agent.get(agent_id, ()))
        features = set(observation.digest_features)
        if securities:
            features.add(f"market:listings:{min(3, len(securities))}")
        if has_holding:
            features.add("market:holder")
        if employer is not None:
            features.add("economy:employed")
        if offers:
            features.add("economy:offer")
        digest_features = frozenset(features)
        market = {
            "liquid_cents": liquid_cents,
            "securities": tuple(securities),
            "open_orders": open_orders,
        }
        result[agent_id] = replace(
            observation,
            place=replace(
                observation.place,
                legal_actions=tuple(sorted(legal_actions)),
            ),
            market=market,
            employer=employer,
            offers=offers,
            stakes=max(
                observation.stakes,
                0.7 if "market:holder" in digest_features else 0.4 if securities else 0.0,
            ),
            digest_features=digest_features,
            digest_hash=sha256_hex(canonical_bytes(sorted(digest_features))),
        )
    return result
