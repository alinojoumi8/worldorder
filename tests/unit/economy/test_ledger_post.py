from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid5

import pytest

from polis.economy.ledger import (
    CommitmentLedger,
    Ledger,
    LedgerError,
    Leg,
    account_id,
    bank_of,
    parse_account_id,
)
from polis.events.types import Event

RUN_ID = UUID("11111111-1111-1111-1111-111111111111")


def cause(*, tick: int = 1, seq: int = 1, run_id: UUID = RUN_ID) -> Event:
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


def opened_ledger() -> tuple[Ledger, str, str]:
    ledger = Ledger(RUN_ID)
    src = ledger.open_account("cash", "ag_a", "agent", tick=0)
    dst = ledger.open_account("cash", "ag_b", "agent", tick=0)
    issuance = ledger.open_account("iss", "bk_cb", "central_bank", tick=0)
    ledger.issue_base_money(
        (
            Leg(src, 1, 1_000, "issuance"),
            Leg(issuance, -1, 1_000, "issuance"),
        ),
        tick=0,
        cause=cause(tick=0, seq=0),
    )
    return ledger, src, dst


def test_account_ids_are_canonical_and_parseable() -> None:
    value = account_id("esc", "ag_a", bank_id="bk_one", ref="ord_1")
    assert value == "esc:ag_a@bk_one#ord_1"
    assert parse_account_id(value) == ("esc", "ag_a", "bk_one", "ord_1")
    assert bank_of(value) == "bk_one"


def test_post_transaction_is_balanced_and_deterministic() -> None:
    ledger, src, dst = opened_ledger()
    txn = ledger.post_transaction(
        (Leg(src, -1, 250, "transfer"), Leg(dst, 1, 250, "transfer")),
        tick=1,
        cause=cause(),
    )
    assert txn == uuid5(RUN_ID, "1:0")
    assert ledger.balance(src) == 750
    assert ledger.balance(dst) == 250
    assert ledger.global_balance_cents() == 0
    assert ledger.materialisation_imbalance_cents() == 0


@pytest.mark.parametrize(
    "legs",
    [
        (),
        (Leg("cash:ag_a", 1, 1, "transfer"),),
        (
            Leg("cash:ag_a", 1, 0, "transfer"),
            Leg("cash:ag_b", -1, 0, "transfer"),
        ),
        (
            Leg("cash:ag_a", 2, 1, "transfer"),
            Leg("cash:ag_b", -1, 1, "transfer"),
        ),
        (
            Leg("cash:ag_a", 1, 2, "transfer"),
            Leg("cash:ag_b", -1, 1, "transfer"),
        ),
        (
            Leg("cash:ag_a", -1, 1, "transfer"),
            Leg("cash:ag_a", -1, 1, "transfer"),
            Leg("cash:ag_b", 1, 2, "transfer"),
        ),
    ],
)
def test_post_transaction_rejects_p1_p2_p3_and_p5(
    legs: tuple[Leg, ...],
) -> None:
    ledger, _src, _dst = opened_ledger()
    with pytest.raises(LedgerError):
        ledger.post_transaction(legs, tick=1, cause=cause())


def test_post_rejects_unknown_closed_overdrawn_and_wrong_tick() -> None:
    ledger, src, dst = opened_ledger()
    empty = ledger.open_account("cash", "ag_empty", "agent", tick=0)
    ledger.close_account(empty, tick=0)
    cases = (
        (Leg(src, -1, 1, "transfer"), Leg("cash:missing", 1, 1, "transfer")),
        (Leg(src, -1, 1, "transfer"), Leg(empty, 1, 1, "transfer")),
        (Leg(src, -1, 1_001, "transfer"), Leg(dst, 1, 1_001, "transfer")),
    )
    for legs in cases:
        with pytest.raises(LedgerError):
            ledger.post_transaction(legs, tick=1, cause=cause())
    with pytest.raises(LedgerError):
        ledger.post_transaction(
            (Leg(src, -1, 1, "transfer"), Leg(dst, 1, 1, "transfer")),
            tick=2,
            cause=cause(tick=1),
        )


def test_same_and_cross_bank_transfer_shapes() -> None:
    ledger = Ledger(RUN_ID)
    a = ledger.open_account("dep", "ag_a", "agent", bank_id="bk_one", tick=0)
    b = ledger.open_account("dep", "ag_b", "agent", bank_id="bk_one", tick=0)
    c = ledger.open_account("dep", "ag_c", "agent", bank_id="bk_two", tick=0)
    assert len(ledger.transfer(a, b, 10, "transfer")) == 2
    cross = ledger.transfer(a, c, 10, "transfer")
    assert len(cross) == 6
    assert sum(leg.direction * leg.amount_cents for leg in cross) == 0


def test_commitment_ledger_prevents_intra_tick_double_spend() -> None:
    ledger, _src, _dst = opened_ledger()
    commitments = CommitmentLedger(ledger)
    assert commitments.commit("ag_a", 700, 1)
    assert not commitments.commit("ag_a", 400, 1)
    assert commitments.available("ag_a", 1) == 300
    assert commitments.available("ag_a", 2) == 1_000


def test_checkpoint_roundtrip_restores_exact_state() -> None:
    ledger, src, dst = opened_ledger()
    ledger.post_transaction(
        (Leg(src, -1, 125, "transfer"), Leg(dst, 1, 125, "transfer")),
        tick=1,
        cause=cause(),
    )
    snapshot = ledger.dump()
    restored = Ledger(RUN_ID)
    restored.load(snapshot)
    assert restored.dump() == snapshot
    assert restored.global_balance_cents() == 0
