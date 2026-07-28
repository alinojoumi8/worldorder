from uuid import UUID

from polis.config.settings import SocietySettings
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.society.media.checker import (
    RESOLVERS,
    ClaimChecker,
    FactObservation,
    MemoryCheckContext,
)
from polis.society.media.news import Claim


def test_closed_resolver_set_and_as_of_purity() -> None:
    assert len(RESOLVERS) == 15
    facts = MemoryCheckContext(
        {
            ("firm.solvent", "fm_one"): (
                FactObservation(10, True, (4,)),
                FactObservation(20, False, (9,)),
            )
        }
    )
    checker = ClaimChecker(
        ctx=facts,
        log=EventLog(UUID(int=18), MemoryEventSink()),
        cfg=SocietySettings(),
        clock=Clock(PROFILES["microscope"]),
    )
    claim = Claim(
        "cl_one",
        "The firm was solvent.",
        "fm_one",
        "firm.solvent",
        True,
        10,
        (4,),
    )
    before, _ = checker.check(claim, "article", "ar_one", 10)
    after, _ = checker.check(claim, "article", "ar_one", 5_010)
    assert before == after
    assert before.verdict == "supported"
    assert before.matched_event_seqs == (4,)


def test_numeric_tolerance_and_unverifiable_aggregate() -> None:
    facts = MemoryCheckContext({("macro.cpi", "economy"): (FactObservation(1, 100.0, (1,)),)})
    checker = ClaimChecker(
        ctx=facts,
        log=EventLog(UUID(int=19), MemoryEventSink()),
        cfg=SocietySettings(claim_tolerance=0.1),
        clock=Clock(PROFILES["microscope"]),
    )
    claim = Claim("cl", "CPI", "economy", "macro.cpi", 115.0, 1, ())
    result, _ = checker.check(claim, "speech", "sp_one", 1)
    assert result.verdict == "imprecise"
    unknown = Claim("cl2", "Rumour", "x", "not.closed", True, 1, ())
    unverifiable, _ = checker.check(unknown, "post", "po_one", 1)
    assert checker.aggregate((unverifiable,)) is None
