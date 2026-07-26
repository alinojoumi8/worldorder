from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from polis.economy.invariants import (
    check_money,
    issued_base_money_cents,
    m0_cents,
    m1_cents,
)
from polis.economy.ledger import Ledger, Leg
from polis.events.types import Event
from polis.kernel.invariants import Ok

RUN_ID = UUID("33333333-3333-3333-3333-333333333333")


def cause(tick: int = 0) -> Event:
    return Event(
        tick,
        RUN_ID,
        tick,
        datetime(2025, 1, 1, tzinfo=UTC),
        1002,
        None,
        (),
        None,
        {"tick": tick},
        None,
        "0" * 64,
        "1" * 64,
    )


def test_genesis_satisfies_global_base_and_deposit_identities() -> None:
    ledger = Ledger(RUN_ID)
    issuance = ledger.open_account("iss", "bk_cb", "central_bank", tick=0)
    reserves = ledger.open_account("res", "bk_one", "bank", tick=0)
    bank_liability = ledger.open_account("dpl", "bk_one", "bank", tick=0)
    deposit = ledger.open_account("dep", "ag_a", "agent", bank_id="bk_one", tick=0)
    receivable = ledger.open_account("lnr", "bk_one", "bank", ref="ln_1", tick=0)
    payable = ledger.open_account("lnp", "ag_a", "agent", ref="ln_1", tick=0)
    ledger.issue_base_money(
        (
            Leg(reserves, 1, 10_000, "issuance"),
            Leg(issuance, -1, 10_000, "issuance"),
        ),
        tick=0,
        cause=cause(),
    )
    ledger.post_transaction(
        (
            Leg(receivable, 1, 4_000, "loan"),
            Leg(bank_liability, -1, 4_000, "loan"),
            Leg(deposit, 1, 4_000, "loan"),
            Leg(payable, -1, 4_000, "loan"),
        ),
        tick=0,
        cause=cause(),
    )
    assert isinstance(check_money(ledger), Ok)
    assert m0_cents(ledger) == 10_000
    assert issued_base_money_cents(ledger) == 10_000
    assert m1_cents(ledger) == 4_000
