from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from polis.economy.invariants import check_ledger
from polis.economy.ledger import Ledger, Leg
from polis.events.types import Event
from polis.kernel.invariants import Ok

RUN_ID = UUID("22222222-2222-2222-2222-222222222222")


def event(tick: int, seq: int) -> Event:
    return Event(
        seq,
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


@given(
    transfers=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=4),
            st.integers(min_value=0, max_value=4),
            st.integers(min_value=1, max_value=10_000),
        ),
        min_size=0,
        max_size=100,
    )
)
def test_random_affordable_transfer_sequences_keep_the_book_closed(
    transfers: list[tuple[int, int, int]],
) -> None:
    ledger = Ledger(RUN_ID)
    issuance = ledger.open_account("iss", "bk_cb", "central_bank", tick=0)
    accounts = [ledger.open_account("cash", f"ag_{index}", "agent", tick=0) for index in range(5)]
    ledger.issue_base_money(
        (
            Leg(accounts[0], 1, 1_000_000, "issuance"),
            Leg(issuance, -1, 1_000_000, "issuance"),
        ),
        tick=0,
        cause=event(0, 0),
    )
    for tick, (src_index, dst_index, requested) in enumerate(transfers, start=1):
        if src_index == dst_index:
            continue
        amount = min(requested, ledger.balance(accounts[src_index]))
        if amount == 0:
            continue
        ledger.post_transaction(
            (
                Leg(accounts[src_index], -1, amount, "transfer"),
                Leg(accounts[dst_index], 1, amount, "transfer"),
            ),
            tick=tick,
            cause=event(tick, tick),
        )
        assert isinstance(check_ledger(ledger), Ok)
