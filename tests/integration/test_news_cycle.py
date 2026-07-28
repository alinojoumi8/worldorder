from collections.abc import Sequence
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from polis.config.settings import BeliefSettings, SocietySettings
from polis.events.kinds import (
    ARTICLE_DISTRIBUTED,
    ARTICLE_PUBLISHED,
    ARTICLE_SPIKED,
    SPEECH_UTTERED,
)
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import NewEvent
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.beliefs import BeliefEngine, MemoryBeliefRepository
from polis.society.graph import MemoryGraphRepository, SocialGraph
from polis.society.media.checker import ClaimChecker, MemoryCheckContext
from polis.society.media.news import (
    AvailabilityIndex,
    MemoryNewsRepository,
    NewsCycle,
    Newsworthiness,
    NullNewsLedger,
    Outlet,
    OutletRegistry,
    StaffMember,
)
from polis.society.media.platform import MemoryPlatformRepository, Platform
from polis.society.protocols import NullMemoryLookup


class Router:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def gather(self, requests: object) -> list[SimpleNamespace]:
        if not isinstance(requests, Sequence):
            raise TypeError("requests must be a sequence")
        parsed = {
            "headline": "Firm remains solvent",
            "body": "The public record says the firm remains solvent.",
            "claims": [
                {
                    "claim_id": "cl_one",
                    "text": "The firm is solvent.",
                    "entity_id": "fm_one",
                    "predicate": "firm.solvent",
                    "value": True,
                    "as_of_tick": 1,
                    "sourced_to_event_seqs": [1],
                }
            ],
            "stance_proposition": "policy.tax.progressivity",
            "stance_value": 0.0,
        }
        return [
            SimpleNamespace(
                parsed_ok=not self.fail,
                parsed=None if self.fail else deepcopy(parsed),
                call_id=UUID(int=25),
            )
            for _ in requests
        ]


def cycle(*, fail: bool = False, source_count: int = 1) -> tuple[NewsCycle, EventLog]:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=25), MemoryEventSink())
    cfg = SocietySettings()
    rng = RngRegistry(25)
    graph = SocialGraph(
        log=log,
        clock=clock,
        rng=rng,
        repo=MemoryGraphRepository(),
        cfg=cfg,
    )
    sources = tuple(
        log.stage(
            NewEvent(
                SPEECH_UTTERED,
                {
                    "speaker_id": "ag_source",
                    "place_id": "pl_square",
                    "text": "The firm is solvent.",
                    "addressed_to": [],
                    "heard_by": [],
                    "topic": "business",
                    "stance_proposition": None,
                    "stance_value": None,
                    "conversation_id": f"cv_{index}",
                    "turn_index": 0,
                    "closing": False,
                    "claims": [],
                    "public": True,
                },
            ),
            tick=1,
            sim_time=clock.sim_time_at(1),
        )
        for index in range(source_count)
    )
    repo = MemoryNewsRepository(("ag_reader",))
    repo.put_outlet(Outlet("ol_one", "One", None, 0.0, 0.8, 0, None))
    repo.set_staff(
        "ol_one",
        (
            StaffMember("ag_editor", "editor", 0.9),
            StaffMember("ag_reporter", "reporter", 0.8),
        ),
    )
    outlets = OutletRegistry(log=log, repo=repo, rng=rng, cfg=cfg, clock=clock)
    memories = NullMemoryLookup()
    availability = AvailabilityIndex(
        log=log,
        clock=clock,
        memories=memories,
        graph=graph,
        public_kinds=frozenset({SPEECH_UTTERED}),
    )
    newsworthiness = Newsworthiness(
        events=lambda _since, _tick: sources,
        outlets=outlets,
        availability=availability,
        cfg=cfg,
    )
    beliefs = BeliefEngine(
        log=log,
        clock=clock,
        rng=rng,
        repo=MemoryBeliefRepository(
            entities={
                "firm": frozenset({"fm_one"}),
                "outlet": frozenset({"ol_one"}),
            }
        ),
        graph=graph,
        cfg=BeliefSettings(),
    )
    checker = ClaimChecker(
        ctx=MemoryCheckContext(),
        log=log,
        cfg=cfg,
        clock=clock,
    )
    platform = Platform(
        log=log,
        clock=clock,
        repo=MemoryPlatformRepository(),
        graph=graph,
        cfg=cfg,
    )
    return (
        NewsCycle(
            router=Router(fail=fail),  # type: ignore[arg-type]
            outlets=outlets,
            newsworthiness=newsworthiness,
            availability=availability,
            checker=checker,
            beliefs=beliefs,
            memories=memories,
            platform=platform,
            ledger=NullNewsLedger(),
            log=log,
            clock=clock,
            rng=rng,
            cfg=cfg,
        ),
        log,
    )


@pytest.mark.asyncio
async def test_news_cycle_publishes_and_distributes_valid_output() -> None:
    news, _ = cycle()
    events = await news.run_cycle(1)
    assert ARTICLE_PUBLISHED in {event.kind for event in events}
    assert ARTICLE_DISTRIBUTED in {event.kind for event in events}
    assert len(news.articles.all()) == 1


@pytest.mark.asyncio
async def test_news_write_failure_spikes_without_fallback_article() -> None:
    news, _ = cycle(fail=True)
    events = await news.run_cycle(1)
    assert [event.kind for event in events] == [ARTICLE_SPIKED]
    assert events[0].payload["reason"] == "llm_invalid"
    assert news.articles.all() == ()


@pytest.mark.asyncio
async def test_unselected_story_remains_available_for_next_cycle() -> None:
    news, _ = cycle(source_count=2)
    news.newsworthiness.score = (  # type: ignore[method-assign]
        lambda event, _outlet, _tick: float(event.seq)
    )
    await news.run_cycle(1)
    await news.run_cycle(2)
    assert len(news.articles.all()) == 2


@pytest.mark.asyncio
async def test_duplicate_distribution_and_trust_update_are_idempotent() -> None:
    news, _ = cycle()
    await news.run_cycle(1)
    article = news.articles.all()[0]
    value_after_first_exposure = news.beliefs.value(
        "ag_reader",
        "policy.tax.progressivity",
    )
    _, repeated_events = news.distribute((article,), 1)
    assert repeated_events[0].payload["reach"] == 0
    assert news.beliefs.value("ag_reader", "policy.tax.progressivity") == value_after_first_exposure

    news.articles.set_accuracy(article.article_id, 1.0, 0.0)
    first = news.update_outlet_trust(7)
    second = news.update_outlet_trust(14)
    assert first
    assert second == ()
