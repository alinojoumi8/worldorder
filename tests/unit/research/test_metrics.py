from __future__ import annotations

from datetime import datetime
from pathlib import Path

from polis.agents.cognition import build_observations, route_cognition
from polis.agents.genesis import generate_agents
from polis.agents.memory import MemoryStore
from polis.config.settings import (
    MemorySettings,
    PopulationSettings,
    WorldSettings,
    load_settings,
)
from polis.kernel.rng import RngRegistry
from polis.research.metrics import (
    METRICS,
    UNAVAILABLE_M1_METRICS,
    MetricCollector,
    catalogue_manifest,
)
from polis.world.generator import generate_world


def test_m1_metrics_are_system_only_and_manifested() -> None:
    assert "sys.cognition.deliberate_share" in METRICS
    assert "unemployment_rate" not in METRICS
    assert "unemployment_rate" in UNAVAILABLE_M1_METRICS
    assert len(catalogue_manifest()["city.population"]) == 64


def test_metric_snapshot_carries_event_freshness() -> None:
    rng = RngRegistry(61)
    world = generate_world(
        WorldSettings(width=40, height=40, districts=4, places_per_district=8),
        rng,
    )
    population = generate_agents(
        PopulationSettings(initial_agents=20),
        world,
        rng,
    )
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "population": {"initial_agents": 20},
            "run": {"scale": 20},
        },
    )
    observations = build_observations(
        population,
        world,
        tick=1,
        sim_time=datetime(2100, 1, 2),
    )
    memory = MemoryStore(MemorySettings())
    routing = route_cognition(
        population,
        observations,
        memory,
        settings=settings,
        rng=rng,
    )
    collector = MetricCollector()

    points = collector.collect(
        tick=1,
        as_of_seq=240,
        population=population,
        routing=routing,
        memory=memory,
        world=world,
    )

    assert len(points) == len(METRICS)
    assert all(point.as_of_seq == 240 for point in points)
    assert collector.latest()["city.population"].value == 20
