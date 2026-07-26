from __future__ import annotations

import pytest

from polis.config.settings import WorldSettings
from polis.kernel.clock import PROFILES
from polis.kernel.rng import RngRegistry
from polis.world.api import Location
from polis.world.generator import generate_world
from polis.world.movement import MoveRequest, resolve_movement


def test_world_generation_is_frozen_and_reproducible() -> None:
    settings = WorldSettings(width=40, height=40, districts=4, places_per_district=8)
    first = generate_world(settings, RngRegistry(77))
    second = generate_world(settings, RngRegistry(77))

    assert first.world_hash == second.world_hash
    assert first.places == second.places
    assert len(first.districts) == 4
    assert len(first.places) == 32
    assert not first.terrain.flags.writeable
    with pytest.raises(ValueError):
        first.terrain[0, 0] = 1


def test_chronicle_movement_is_immediate_and_occupancy_is_sorted() -> None:
    world = generate_world(
        WorldSettings(width=40, height=40, districts=4, places_per_district=8),
        RngRegistry(12),
    )
    source, target = world.places[0], world.places[-1]
    world.locations = {
        "ag_0002": Location(source.place_id, source.district_id, source.x, source.y),
        "ag_0001": Location(source.place_id, source.district_id, source.x, source.y),
    }
    world.freeze_occupancy()

    result = resolve_movement(
        world,
        (MoveRequest("ag_0002", target.place_id), MoveRequest("ag_0001", target.place_id)),
        tick=3,
        profile=PROFILES["chronicle"],
        rng=RngRegistry(12),
    )

    assert not result.blocked
    assert world.occupancy(target.place_id) == ("ag_0001", "ag_0002")
    assert all(item.arrived and item.travel_ticks == 0 for item in result.movements)
