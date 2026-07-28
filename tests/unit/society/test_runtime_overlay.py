from pathlib import Path
from uuid import UUID

import pytest

from polis.config.errors import RuntimeOverlayError
from polis.config.runtime import RuntimeConfig
from polis.config.settings import load_settings
from polis.events.kinds import POLICY_ENACTED
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import NewEvent
from polis.kernel.checkpoint import CheckpointManager
from polis.kernel.clock import PROFILES, Clock
from polis.society.policy import project_enactment
from polis.store.blobs import LocalBlobStore


def _policy_event(log: EventLog, *, tick: int, effective_tick: int):
    return log.stage(
        NewEvent(
            POLICY_ENACTED,
            {
                "policy_id": "py_vat",
                "parameter": "tax.vat_bp",
                "old_value": 1_300,
                "new_value": 1_750,
                "effective_tick": effective_tick,
                "enacted_by": "council",
                "vote_margin": 0.4,
                "proposal_seq": 1,
            },
        ),
        tick=tick,
        sim_time=Clock(PROFILES["microscope"]).sim_time_at(tick),
    )


@pytest.mark.asyncio
async def test_polity_runtime_rebuild_and_checkpoint_roundtrip(tmp_path) -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    live = RuntimeConfig(settings)
    event = _policy_event(
        EventLog(UUID(int=1818), MemoryEventSink()),
        tick=2,
        effective_tick=5,
    )
    project_enactment(live, event)

    assert live.bp("tax.vat_bp", 4) == settings.polity.policy.tax.vat_bp
    assert live.bp("tax.vat_bp", 5) == 1_750
    recorded = live.history("tax.vat_bp")
    assert len(recorded) == 1
    assert recorded[0].value == 1_750
    assert (recorded[0].enacted_tick, recorded[0].effective_tick) == (2, 5)
    assert set(live.as_of(4)) == set(settings.polity.policy.flat())

    rebuilt = RuntimeConfig(settings)
    project_enactment(rebuilt, event)
    assert rebuilt.dump() == live.dump()
    assert rebuilt.snapshot(4) == live.snapshot(4)
    assert rebuilt.snapshot(5) == live.snapshot(5)

    manager = CheckpointManager(LocalBlobStore(tmp_path), interval=1)
    await manager.write(
        UUID(int=1818),
        5,
        last_seq=event.seq,
        chain_hash="ab" * 32,
        components=(live,),
    )
    resumed = RuntimeConfig(settings)
    await manager.restore(UUID(int=1818), 5, (resumed,))
    assert resumed.dump() == live.dump()
    assert resumed.bp("tax.vat_bp", 5) == 1_750


def test_polity_runtime_rejects_same_tick_effect() -> None:
    runtime = RuntimeConfig(load_settings(Path("configs/smoke.yaml")))
    with pytest.raises(RuntimeOverlayError):
        runtime.enact(
            "tax.vat_bp",
            1_750,
            2,
            "py_vat",
            1,
            enacted_tick=2,
        )
