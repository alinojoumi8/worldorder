from datetime import datetime
from uuid import UUID

import pytest

from polis.events.kinds import RUN_STARTED, TICK_STARTED
from polis.events.log import EventLog, MemoryEventSink
from polis.events.reader import EventQuery, MemoryEventReader
from polis.events.types import NewEvent


@pytest.mark.asyncio
async def test_memory_reader_filters_and_follows_causes() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000002")
    sink = MemoryEventSink()
    log = EventLog(run_id, sink)
    root = log.stage(
        NewEvent(RUN_STARTED, {"config_hash": "x", "seed": 1}),
        tick=0,
        sim_time=datetime(2026, 1, 1),
    )
    log.stage(
        NewEvent(TICK_STARTED, {"tick": 1}, cause_seq=root.seq),
        tick=1,
        sim_time=datetime(2026, 1, 1),
    )
    await log.commit(1)
    reader = MemoryEventReader(sink)
    values = [event async for event in reader.scan(EventQuery(run_id, kinds=frozenset({1002})))]
    assert [event.kind for event in values] == [TICK_STARTED]
    assert len(await reader.by_cause(run_id, root.seq)) == 1
