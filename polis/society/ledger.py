from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from polis.economy.ledger import Ledger, Leg, bank_of, parse_account_id
from polis.events.types import Event


@dataclass(slots=True)
class EconomyLedgerAdapter:
    """Narrow, balanced bridge from society venue fees to the economy ledger."""

    ledger: Ledger

    def _liquid_accounts(self, owner_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                account
                for account in self.ledger.accounts_of(owner_id)
                if parse_account_id(account)[0] in {"cash", "dep"} and self.ledger.is_open(account)
            )
        )

    def _compatible_balance(self, payer_id: str, payee_id: str) -> int:
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
        return amount_cents >= 0 and self._compatible_balance(payer_id, payee_id) >= amount_cents

    def next_broadcast_txn_id(self, tick: int) -> str:
        return str(self.ledger.next_txn_id(tick))

    def _transfer_legs(
        self,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
    ) -> tuple[Leg, ...]:
        destinations = self._liquid_accounts(payee_id)
        if not destinations:
            raise RuntimeError(f"venue owner {payee_id} has no open liquid account")
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
            legs.extend(self.ledger.transfer(source, compatible, amount, "rent"))
            remaining -= amount
        if remaining:
            raise RuntimeError(
                f"broadcaster {payer_id} lacks a compatible liquid account for venue fee"
            )
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
            self._transfer_legs(payer_id, payee_id, amount_cents),
            tick=tick,
            cause=cause,
        )
        if str(transaction_id) != txn_id:
            raise RuntimeError("broadcast ledger transaction ordinal diverged")
        return str(transaction_id)


__all__ = ["EconomyLedgerAdapter"]
