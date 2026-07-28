from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from polis.economy.ledger import Ledger, Leg, bank_of, parse_account_id
from polis.events.kinds import OUTLET_REVENUE_BOOKED
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.society.media.news import Outlet, RevenueBooking


@dataclass(slots=True)
class EconomyLedgerAdapter:
    """Narrow, balanced bridge from society institutions to the economy ledger."""

    ledger: Ledger

    def _liquid_accounts(self, owner_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                account
                for account in self.ledger.accounts_of(owner_id)
                if parse_account_id(account)[0] in {"cash", "dep"} and self.ledger.is_open(account)
            )
        )

    def compatible_balance(self, payer_id: str, payee_id: str) -> int:
        destinations = self._liquid_accounts(payee_id)
        if not destinations:
            return 0
        return sum(
            max(0, self.ledger.balance(source))
            for source in self._liquid_accounts(payer_id)
            if any(
                (bank_of(source) is None) == (bank_of(destination) is None)
                for destination in destinations
            )
        )

    def can_pay_broadcast(
        self,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
    ) -> bool:
        return amount_cents >= 0 and self.compatible_balance(payer_id, payee_id) >= amount_cents

    def can_pay(self, payer_id: str, cents: int, payee_id: str | None = None) -> bool:
        if payee_id is not None:
            return cents >= 0 and self.compatible_balance(payer_id, payee_id) >= cents
        return (
            cents >= 0
            and sum(
                max(0, self.ledger.balance(account)) for account in self._liquid_accounts(payer_id)
            )
            >= cents
        )

    def next_broadcast_txn_id(self, tick: int) -> str:
        return str(self.ledger.next_txn_id(tick))

    def next_transfer_id(self, tick: int) -> str:
        return str(self.ledger.next_txn_id(tick))

    def transfer_legs(
        self,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
        reason: str,
    ) -> tuple[Leg, ...]:
        destinations = self._liquid_accounts(payee_id)
        if not destinations:
            raise RuntimeError(f"payee {payee_id} has no open liquid account")
        remaining = amount_cents
        legs: list[Leg] = []
        for source in self._liquid_accounts(payer_id):
            if remaining <= 0:
                break
            compatible = next(
                (
                    destination
                    for destination in destinations
                    if (bank_of(source) is None) == (bank_of(destination) is None)
                ),
                None,
            )
            if compatible is None:
                continue
            amount = min(remaining, max(0, self.ledger.balance(source)))
            if amount == 0:
                continue
            legs.extend(self.ledger.transfer(source, compatible, amount, reason))
            remaining -= amount
        if remaining:
            raise RuntimeError(f"payer {payer_id} lacks a compatible liquid account")
        totals: dict[tuple[str, int, str], int] = defaultdict(int)
        for leg in legs:
            totals[(leg.account_id, leg.direction, leg.reason)] += leg.amount_cents
        return tuple(
            Leg(account_id, direction, amount, reason)
            for (account_id, direction, reason), amount in sorted(totals.items())
        )

    def post_broadcast_fee(
        self,
        *,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
        txn_id: str,
        tick: int,
        cause: Event,
    ) -> str:
        transaction_id = self.ledger.post_transaction(
            self.transfer_legs(payer_id, payee_id, amount_cents, "rent"),
            tick=tick,
            cause=cause,
        )
        if str(transaction_id) != txn_id:
            raise RuntimeError("broadcast ledger transaction ordinal diverged")
        return str(transaction_id)

    def post_transfer(
        self,
        payer_id: str,
        payee_id: str,
        cents: int,
        *,
        reason: str,
        tick: int,
        cause: Event,
    ) -> str:
        return self.post_transfers(
            ((payer_id, payee_id, cents),),
            reason=reason,
            tick=tick,
            cause=cause,
        )

    def post_transfers(
        self,
        transfers: Sequence[tuple[str, str, int]],
        *,
        reason: str,
        tick: int,
        cause: Event,
    ) -> str:
        totals: dict[tuple[str, int, str], int] = defaultdict(int)
        for payer_id, payee_id, cents in transfers:
            for leg in self.transfer_legs(payer_id, payee_id, cents, reason):
                totals[(leg.account_id, leg.direction, leg.reason)] += leg.amount_cents
        legs = tuple(
            Leg(account_id, direction, amount, leg_reason)
            for (account_id, direction, leg_reason), amount in sorted(totals.items())
            if amount > 0
        )
        return str(
            self.ledger.post_transaction(
                legs,
                tick=tick,
                cause=cause,
            )
        )


@dataclass(slots=True)
class EconomyNewsLedgerAdapter:
    """Books media income only by debiting named, liquid counterparties."""

    ledger: Ledger
    log: EventLog
    clock: Clock
    advertiser_budgets: dict[str, int]
    campaign_buys: Mapping[str, Mapping[str, int]]
    subscription_price_cents: int = 0
    _adapter: EconomyLedgerAdapter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._adapter = EconomyLedgerAdapter(self.ledger)

    def _affordable(
        self,
        payer_id: str,
        payee_id: str,
        requested: int,
        committed_cents: int = 0,
    ) -> int:
        if requested <= 0:
            return 0
        return min(
            requested,
            max(
                0,
                self._adapter.compatible_balance(payer_id, payee_id) - committed_cents,
            ),
        )

    def _aggregate(self, transfers: Sequence[tuple[str, str, int]]) -> tuple[Leg, ...]:
        totals: dict[tuple[str, int, str], int] = defaultdict(int)
        for payer_id, payee_id, cents in transfers:
            for leg in self._adapter.transfer_legs(payer_id, payee_id, cents, "purchase"):
                totals[(leg.account_id, leg.direction, leg.reason)] += leg.amount_cents
        return tuple(
            Leg(account_id, direction, amount, reason)
            for (account_id, direction, reason), amount in sorted(totals.items())
            if amount > 0
        )

    def book_outlet_revenue(
        self,
        *,
        outlet: Outlet,
        period_start_tick: int,
        tick: int,
        impressions: int,
        cpm_cents: int,
        subscribers: Sequence[str],
    ) -> RevenueBooking:
        payee_id = outlet.firm_id
        if payee_id is None:
            return RevenueBooking()
        transfers: list[tuple[str, str, int]] = []
        committed: dict[str, int] = defaultdict(int)

        def reserve(payer_id: str, requested: int) -> int:
            cents = self._affordable(
                payer_id,
                payee_id,
                requested,
                committed[payer_id],
            )
            committed[payer_id] += cents
            return cents

        advertiser_rows: list[tuple[str, int]] = []
        ad_remaining = max(0, impressions * cpm_cents // 1_000)
        for advertiser_id in sorted(self.advertiser_budgets):
            if ad_remaining <= 0:
                break
            budget = max(0, self.advertiser_budgets[advertiser_id])
            cents = reserve(advertiser_id, min(ad_remaining, budget))
            if cents <= 0:
                continue
            advertiser_rows.append((advertiser_id, cents))
            transfers.append((advertiser_id, payee_id, cents))
            ad_remaining -= cents
        subscription_rows: list[tuple[str, int]] = []
        for subscriber_id in sorted(set(subscribers)):
            cents = reserve(subscriber_id, self.subscription_price_cents)
            if cents > 0:
                subscription_rows.append((subscriber_id, cents))
                transfers.append((subscriber_id, payee_id, cents))
        campaign_rows: list[tuple[str, int]] = []
        for buyer_id, requested in sorted(self.campaign_buys.get(outlet.outlet_id, {}).items()):
            cents = reserve(buyer_id, max(0, requested))
            if cents > 0:
                campaign_rows.append((buyer_id, cents))
                transfers.append((buyer_id, payee_id, cents))
        if not transfers:
            return RevenueBooking()
        predicted = str(self.ledger.next_txn_id(tick))
        advertisers = tuple(sorted({payer for payer, _ in (*advertiser_rows, *campaign_rows)}))
        ad_cents = sum(cents for _, cents in advertiser_rows)
        subscription_cents = sum(cents for _, cents in subscription_rows)
        campaign_cents = sum(cents for _, cents in campaign_rows)
        event = self.log.stage(
            NewEvent(
                OUTLET_REVENUE_BOOKED,
                {
                    "outlet_id": outlet.outlet_id,
                    "period_start_tick": period_start_tick,
                    "impressions": impressions,
                    "cpm_cents": cpm_cents,
                    "ad_revenue_cents": ad_cents,
                    "subscription_cents": subscription_cents,
                    "campaign_cents": campaign_cents,
                    "advertisers": list(advertisers),
                    "txn_ids": [predicted],
                },
                subject_ids=(outlet.outlet_id, *advertisers),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        try:
            actual = str(
                self.ledger.post_transaction(
                    self._aggregate(transfers),
                    tick=tick,
                    cause=event,
                )
            )
            if actual != predicted:
                raise RuntimeError("media revenue ledger transaction ordinal diverged")
        except Exception:
            self.log.rollback()
            raise
        for advertiser_id, cents in advertiser_rows:
            self.advertiser_budgets[advertiser_id] -= cents
        return RevenueBooking(
            ad_cents,
            subscription_cents,
            campaign_cents,
            advertisers,
            (actual,),
            event,
        )


__all__ = ["EconomyLedgerAdapter", "EconomyNewsLedgerAdapter"]
