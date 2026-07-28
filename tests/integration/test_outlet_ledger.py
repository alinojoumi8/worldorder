from datetime import UTC, datetime
from uuid import UUID

from polis.economy.invariants import check_ledger
from polis.economy.ledger import Ledger, Leg
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import Event
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.invariants import Ok
from polis.society.ledger import EconomyNewsLedgerAdapter
from polis.society.media.news import Outlet


def cause(run_id: UUID) -> Event:
    return Event(
        0,
        run_id,
        0,
        datetime(2025, 1, 1, tzinfo=UTC),
        1002,
        None,
        (),
        None,
        {"tick": 0},
        None,
        "0" * 64,
        "1" * 64,
    )


def test_outlet_revenue_has_named_counterparties_and_balances() -> None:
    run_id = UUID(int=23)
    ledger = Ledger(run_id)
    owners = ("fm_ad", "ag_sub", "pt_campaign", "fm_outlet")
    accounts = {owner: ledger.open_account("cash", owner, "firm", tick=0) for owner in owners}
    issuance = ledger.open_account("iss", "bk_cb", "central_bank", tick=0)
    ledger.issue_base_money(
        (
            Leg(accounts["fm_ad"], 1, 100, "issuance"),
            Leg(accounts["ag_sub"], 1, 100, "issuance"),
            Leg(accounts["pt_campaign"], 1, 100, "issuance"),
            Leg(issuance, -1, 300, "issuance"),
        ),
        tick=0,
        cause=cause(run_id),
    )
    adapter = EconomyNewsLedgerAdapter(
        ledger,
        EventLog(run_id, MemoryEventSink()),
        Clock(PROFILES["microscope"]),
        {"fm_ad": 100},
        {"ol_one": {"pt_campaign": 50}},
        subscription_price_cents=10,
    )
    booking = adapter.book_outlet_revenue(
        outlet=Outlet("ol_one", "One", "fm_outlet", 0.0, 0.8, 0, None),
        period_start_tick=1,
        tick=7,
        impressions=1_000,
        cpm_cents=40,
        subscribers=("ag_sub",),
    )
    assert booking.ad_revenue_cents == 40
    assert booking.subscription_cents == 10
    assert booking.campaign_cents == 50
    assert booking.advertisers == ("fm_ad", "pt_campaign")
    assert ledger.balance(accounts["fm_outlet"]) == 100
    assert isinstance(check_ledger(ledger), Ok)


def test_zero_advertiser_budget_books_zero_ad_revenue() -> None:
    run_id = UUID(int=24)
    ledger = Ledger(run_id)
    ledger.open_account("cash", "fm_outlet", "firm", tick=0)
    adapter = EconomyNewsLedgerAdapter(
        ledger,
        EventLog(run_id, MemoryEventSink()),
        Clock(PROFILES["microscope"]),
        {"fm_ad": 0},
        {},
    )
    booking = adapter.book_outlet_revenue(
        outlet=Outlet("ol_one", "One", "fm_outlet", 0.0, 0.8, 0, None),
        period_start_tick=1,
        tick=7,
        impressions=5_000,
        cpm_cents=40,
        subscribers=(),
    )
    assert booking.ad_revenue_cents == 0
    assert booking.txn_ids == ()
