from __future__ import annotations

import pytest

from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.limits import LimitConfig, LimitSet

AGENT = "ag_0000000000000000"


def test_tick_buckets_and_action_slots_are_independent() -> None:
    limits = LimitSet(
        LimitConfig(
            requests_per_tick=2,
            requests_per_second=10,
            recall_queries_per_tick=1,
            action_slots=1,
        ),
        now=lambda: 0.0,
    )
    limits.charge(AGENT, "request", 4)
    limits.charge(AGENT, "request", 4)
    with pytest.raises(ProtocolError) as request_error:
        limits.charge(AGENT, "request", 4)
    assert request_error.value.code is ErrorCode.RATE_LIMITED

    limits.charge(AGENT, "recall", 4)
    with pytest.raises(ProtocolError) as recall_error:
        limits.charge(AGENT, "recall", 4)
    assert recall_error.value.code is ErrorCode.RATE_LIMITED

    assert limits.slot_take(AGENT, 4) == 0
    with pytest.raises(ProtocolError) as slot_error:
        limits.slot_take(AGENT, 4)
    assert slot_error.value.code is ErrorCode.NO_SLOTS
    assert limits.slots_remaining(AGENT, 5) == 1


def test_strike_ladder_throttles_suspends_and_revokes() -> None:
    limits = LimitSet(LimitConfig(suspension_ticks=20))

    for tick in range(10):
        limits.strike(AGENT, tick, "schema")
    assert limits.status(AGENT, 9)["throttled_until_tick"] == 109

    for tick in range(10, 25):
        limits.strike(AGENT, tick, "schema")
    assert limits.status(AGENT, 24)["suspended_until_tick"] == 44
    with pytest.raises(ProtocolError) as suspended:
        limits.charge(AGENT, "request", 25)
    assert suspended.value.code is ErrorCode.SUSPENDED

    for suspension in range(2):
        start = 50 + suspension * 40
        for offset in range(25):
            limits.strike(AGENT, start + offset, "schema")
    assert limits.status(AGENT, 120)["revoked"] is True


def test_five_bad_signatures_trigger_immediate_suspension() -> None:
    limits = LimitSet()
    for _ in range(5):
        limits.strike(AGENT, 7, "signature")

    assert limits.status(AGENT, 7)["suspended_until_tick"] == 247


def test_tick_counters_are_evicted_when_the_clock_advances() -> None:
    limits = LimitSet(LimitConfig(action_slots=2))
    for tick in range(100):
        limits.charge(AGENT, "recall", tick)
        limits.slot_take(AGENT, tick)

    assert all(key[1] == 99 for key in limits._tick_counts)
    assert all(key[1] == 99 for key in limits._slots)
    before = len(limits._slots)
    assert limits.slots_remaining("ag_1111111111111111", 99) == 2
    assert len(limits._slots) == before


def test_releasing_a_slot_is_idempotent_and_restores_capacity() -> None:
    limits = LimitSet(LimitConfig(action_slots=1))
    limits.slot_take(AGENT, 3)

    limits.slot_release(AGENT, 3)
    limits.slot_release(AGENT, 3)

    assert limits.slots_remaining(AGENT, 3) == 1


def test_availability_errors_report_only_strikes_in_the_current_window() -> None:
    limits = LimitSet()
    for _ in range(25):
        limits.strike(AGENT, 0, "schema")

    with pytest.raises(ProtocolError) as suspended:
        limits.charge(AGENT, "request", 100)

    assert suspended.value.code is ErrorCode.SUSPENDED
    assert suspended.value.strikes == 0

    for suspension_tick in (250, 500):
        for _ in range(25):
            limits.strike(AGENT, suspension_tick, "schema")

    with pytest.raises(ProtocolError) as revoked:
        limits.slot_take(AGENT, 600)

    assert revoked.value.code is ErrorCode.REVOKED
    assert revoked.value.strikes == 0
