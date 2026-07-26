from __future__ import annotations

from polis.config.settings import ClockSettings
from polis.kernel.clock import Clock, SimDuration, profile_from_settings
from polis.kernel.rng import RngRegistry
from polis.kernel.scheduler import Cadence, Scheduler


def test_chronicle_clock_and_scheduler_boundaries() -> None:
    clock = Clock(profile_from_settings(ClockSettings(profile="chronicle", ticks_per_sim_day=1)))
    scheduler = Scheduler(clock)
    scheduler.register(Cadence("weekly", "1w", 7, "test"))

    assert clock.sim_time_at(0).isoformat() == "2100-01-01T00:00:00"
    assert clock.ticks_for(SimDuration.parse("1y")) == 360
    assert scheduler.due(7) == ("weekly",)
    assert scheduler.next_fire("weekly", 7) == 14


def test_rng_has_a_stable_golden_sequence_and_namespaced_streams() -> None:
    registry = RngRegistry(42)
    stream = registry.get("agents", "a", 7)

    assert [stream.randint(0, 1000) for _ in range(5)] == [941, 901, 281, 949, 627]
    assert registry.seed_for("agents", "a", 7) != registry.seed_for("agents", "b", 7)
    assert (
        RngRegistry(42).get("agents", "a", 7).random()
        == RngRegistry(42).get("agents", "a", 7).random()
    )
