from __future__ import annotations

from polis.society.law import MnpiIndex
from tests.law_support import Memories, clock, event, law_cfg


def test_mnpi_requires_kind_issuer_memory_and_window() -> None:
    source = event(7, 1, 9010, subjects=("fi_one",))
    index = MnpiIndex(
        memories=Memories({("ag_actor", 7)}),
        cfg=law_cfg(mnpi_window_sim_days=1),
        clock=clock(),
        events=(source,),
        issuer_for_symbol=lambda symbol: {"ONE": "fi_one", "TWO": "fi_two"}[symbol],
    )

    assert index.holds("ag_actor", "ONE", 24) == (True, 7)
    assert index.holds("ag_other", "ONE", 24) == (False, None)
    assert index.holds("ag_actor", "TWO", 24) == (False, None)
    assert index.holds("ag_actor", "ONE", 26) == (False, None)


def test_public_disclosure_clears_mnpi_and_replays_purely() -> None:
    source = event(7, 1, 9010, subjects=("fi_one",))
    disclosure = event(
        8,
        5,
        11010,
        subjects=("fi_one",),
        payload={"source_event_seqs": [7]},
    )
    rows = (source, disclosure)
    first = MnpiIndex(
        memories=Memories({("ag_actor", 7)}),
        cfg=law_cfg(),
        clock=clock(),
        events=rows,
        issuer_for_symbol=lambda _symbol: "fi_one",
    )
    replay = MnpiIndex(
        memories=Memories({("ag_actor", 7)}),
        cfg=law_cfg(),
        clock=clock(),
        events=tuple(rows),
        issuer_for_symbol=lambda _symbol: "fi_one",
    )

    assert first.holds("ag_actor", "ONE", 4) == (True, 7)
    assert first.holds("ag_actor", "ONE", 5) == (False, None)
    assert replay.holds("ag_actor", "ONE", 5) == first.holds("ag_actor", "ONE", 5)
