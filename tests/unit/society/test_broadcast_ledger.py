from datetime import UTC, datetime
from uuid import UUID

from polis.economy.ledger import Ledger, Leg
from polis.events.types import Event
from polis.society.ledger import EconomyLedgerAdapter


def cause(run_id: UUID, tick: int, seq: int) -> Event:
    return Event(
        seq,
        run_id,
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


def test_broadcast_adapter_posts_one_balanced_venue_fee() -> None:
    run_id = UUID(int=8)
    ledger = Ledger(run_id)
    payer = ledger.open_account("cash", "ag_payer", "agent", tick=0)
    payee = ledger.open_account("cash", "ag_owner", "agent", tick=0)
    issuance = ledger.open_account("iss", "bk_cb", "central_bank", tick=0)
    ledger.issue_base_money(
        (
            Leg(payer, 1, 1_000, "issuance"),
            Leg(issuance, -1, 1_000, "issuance"),
        ),
        tick=0,
        cause=cause(run_id, 0, 0),
    )
    adapter = EconomyLedgerAdapter(ledger)
    expected = adapter.next_broadcast_txn_id(1)

    actual = adapter.post_broadcast_fee(
        payer_id="ag_payer",
        payee_id="ag_owner",
        amount_cents=250,
        txn_id=expected,
        tick=1,
        cause=cause(run_id, 1, 1),
    )

    assert actual == expected
    assert ledger.balance(payer) == 750
    assert ledger.balance(payee) == 250
    assert ledger.global_balance_cents() == 0
    assert adapter.can_pay_broadcast("ag_payer", "ag_owner", 751) is False
