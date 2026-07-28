from datetime import datetime
from uuid import UUID

import pytest

from polis.events.kinds import RUN_STARTED, TICK_STARTED
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import GENESIS_PREV_HASH, NewEvent
from polis.events.verify import verify_batch

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
SIM_TIME = datetime(2026, 1, 1)


@pytest.mark.asyncio
async def test_event_log_seals_and_commits_one_chain() -> None:
    sink = MemoryEventSink()
    log = EventLog(RUN_ID, sink)
    first = log.stage(
        NewEvent(RUN_STARTED, {"config_hash": "abc", "seed": 7}),
        tick=0,
        sim_time=SIM_TIME,
    )
    second = log.stage(NewEvent(TICK_STARTED, {"tick": 1}), tick=1, sim_time=SIM_TIME)
    result = await log.commit(1)
    assert first.prev_hash == GENESIS_PREV_HASH
    assert second.prev_hash == first.hash
    assert result.persisted == 2
    assert result.chain_hash == second.hash
    assert verify_batch(sink.events).ok


@pytest.mark.asyncio
async def test_same_events_produce_same_terminal_hash() -> None:
    async def one() -> str:
        sink = MemoryEventSink()
        log = EventLog(RUN_ID, sink)
        for tick in range(10):
            log.stage(
                NewEvent(TICK_STARTED, {"tick": tick}),
                tick=tick,
                sim_time=SIM_TIME,
            )
            await log.commit(tick)
        return log.chain_hash

    assert await one() == await one()


class FailingSink:
    async def append(self, events: object) -> None:
        del events
        raise OSError("simulated store outage")


@pytest.mark.asyncio
async def test_failed_commit_rolls_back_chain_head() -> None:
    log = EventLog(RUN_ID, FailingSink())
    log.stage(NewEvent(TICK_STARTED, {"tick": 1}), tick=1, sim_time=SIM_TIME)
    with pytest.raises(OSError):
        await log.commit(1)
    assert log.last_seq == 0
    assert log.chain_hash == GENESIS_PREV_HASH
    assert not log.staged()


@pytest.mark.asyncio
async def test_savepoints_reject_stale_and_foreign_batches() -> None:
    log = EventLog(RUN_ID, MemoryEventSink())
    stale = log.savepoint()
    log.stage(NewEvent(TICK_STARTED, {"tick": 1}), tick=1, sim_time=SIM_TIME)
    await log.commit(1)

    with pytest.raises(ValueError, match="current staged batch"):
        log.rollback_to(stale)

    foreign = EventLog(RUN_ID, MemoryEventSink()).savepoint()
    with pytest.raises(ValueError, match="current staged batch"):
        log.rollback_to(foreign)
