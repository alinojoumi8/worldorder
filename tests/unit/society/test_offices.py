from uuid import UUID

from polis.config.settings import PolitySettings
from polis.events.kinds import APPOINTMENT_MADE, OFFICE_ASSUMED, OFFICE_VACATED, OFFICER_REMOVED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.society.polity import OfficeRegister


def _offices(*, confirmation: float = 1.0) -> OfficeRegister:
    return OfficeRegister(
        log=EventLog(UUID(int=1822), MemoryEventSink()),
        clock=Clock(PROFILES["microscope"]),
        cfg=PolitySettings(),
        confirmation=lambda _office, _agent: confirmation,
    )


def test_term_end_is_clock_derived_and_holder_is_temporal() -> None:
    offices = _offices()
    event = offices.assume(
        "president",
        "ag_president",
        5,
        via="el_one",
        salary_cents=900_000,
    )[0]
    term_end = event.payload["term_end_tick"]

    assert event.kind == OFFICE_ASSUMED
    assert term_end > 5
    assert offices.holder("president", term_end - 1) == "ag_president"
    assert offices.holder("president", term_end) is None


def test_presidential_succession_uses_votes_then_agent_id() -> None:
    offices = _offices()
    offices.assume("council", "ag_b", 1, via="el", salary_cents=1)
    offices.assume("council", "ag_a", 1, via="el", salary_cents=1)
    offices.assume("president", "ag_p", 1, via="el", salary_cents=1)
    offices.note_votes("ag_a", 20)
    offices.note_votes("ag_b", 10)

    events = offices.vacate("president", "ag_p", "death", 2)

    assert [event.kind for event in events] == [OFFICE_VACATED, OFFICE_ASSUMED]
    assert events[0].payload["successor_id"] == "ag_a"
    assert offices.holder("president", 2) == "ag_a"


def test_appointment_requires_confirmation_and_governor_removal_needs_five_sevenths() -> None:
    rejected = _offices(confirmation=-0.1)
    rejected_events = rejected.appoint("judge", "ag_j", "ag_p", 2)
    assert [event.kind for event in rejected_events] == [APPOINTMENT_MADE]
    assert rejected.holder("judge", 2) is None

    offices = _offices()
    appointed = offices.appoint("cb_governor", "ag_g", "ag_p", 2)
    assert [event.kind for event in appointed] == [APPOINTMENT_MADE, OFFICE_ASSUMED]
    assert offices.remove("cb_governor", "ag_g", "council", 4 / 7, 3) == ()
    removed = offices.remove("cb_governor", "ag_g", "council", 5 / 7, 3)
    assert [event.kind for event in removed] == [OFFICER_REMOVED, OFFICE_VACATED]
