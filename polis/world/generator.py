from __future__ import annotations

import math
from collections import Counter
from typing import Final

import numpy as np

from polis.config.settings import WorldSettings
from polis.kernel.rng import RngRegistry
from polis.world.api import District, Place, PlaceType, World, compute_world_hash

_ARCHETYPES: Final = ("core", "uptown", "midtown", "industrial", "suburb", "periphery")
_DISTRICT_NAMES: Final = (
    "Central",
    "Northgate",
    "Midtown",
    "Works",
    "Riverside",
    "Southside",
)
_MIX: Final[dict[str, dict[PlaceType, int]]] = {
    "core": {
        "home": 6,
        "office": 22,
        "shop": 12,
        "bar": 6,
        "school": 1,
        "bank": 3,
        "exchange": 1,
        "town_hall": 1,
        "courthouse": 1,
        "police": 1,
        "hospital": 1,
        "park": 3,
        "newsroom": 1,
        "studio": 1,
    },
    "uptown": {
        "home": 26,
        "office": 6,
        "shop": 12,
        "bar": 4,
        "school": 2,
        "university": 1,
        "bank": 2,
        "police": 1,
        "hospital": 1,
        "park": 4,
        "newsroom": 1,
    },
    "midtown": {
        "home": 24,
        "office": 10,
        "factory": 2,
        "shop": 12,
        "bar": 4,
        "school": 2,
        "bank": 2,
        "police": 1,
        "park": 2,
        "newsroom": 1,
    },
    "industrial": {
        "home": 8,
        "office": 4,
        "factory": 26,
        "shop": 6,
        "bar": 2,
        "school": 1,
        "bank": 1,
        "police": 1,
        "hospital": 1,
        "park": 1,
        "studio": 8,
        "shelter": 1,
    },
    "suburb": {
        "home": 34,
        "office": 2,
        "shop": 12,
        "bar": 2,
        "school": 4,
        "bank": 1,
        "police": 1,
        "hospital": 1,
        "park": 3,
    },
    "periphery": {
        "home": 36,
        "office": 1,
        "factory": 4,
        "shop": 8,
        "bar": 2,
        "school": 3,
        "police": 1,
        "park": 3,
        "studio": 1,
        "shelter": 1,
    },
}
_CAPACITY: Final[dict[str, int]] = {
    "home": 64,
    "office": 40,
    "factory": 80,
    "shop": 25,
    "school": 200,
    "university": 400,
    "bank": 30,
    "exchange": 150,
    "town_hall": 300,
    "courthouse": 120,
    "police": 40,
    "prison": 40,
    "hospital": 150,
    "park": 100_000,
    "bar": 40,
    "newsroom": 25,
    "studio": 15,
    "shelter": 2_147_483_647,
}


def _districts(settings: WorldSettings) -> tuple[District, ...]:
    columns = math.ceil(math.sqrt(settings.districts))
    rows = math.ceil(settings.districts / columns)
    result: list[District] = []
    for index in range(settings.districts):
        column, row = index % columns, index // columns
        x0 = column * settings.width // columns
        x1 = (column + 1) * settings.width // columns
        y0 = row * settings.height // rows
        y1 = (row + 1) * settings.height // rows
        archetype = _ARCHETYPES[index % len(_ARCHETYPES)]
        result.append(
            District(
                district_id=f"ds_{index:02d}",
                name=_DISTRICT_NAMES[index % len(_DISTRICT_NAMES)],
                archetype=archetype,
                bbox=(x0, y0, x1, y1),
                land_value_cents=2_000_000 + index * 350_000,
                school_quality=round(0.45 + 0.08 * (index % 4), 3),
                crime_rate=round(0.015 + 0.006 * ((index + 2) % 4), 4),
                amenity_score=round(0.4 + 0.1 * ((index + 1) % 5), 3),
            )
        )
    return tuple(result)


def _scaled_types(archetype: str, count: int) -> tuple[PlaceType, ...]:
    expanded = tuple(
        place_type for place_type, amount in _MIX[archetype].items() for _ in range(amount)
    )
    selected = tuple(
        expanded[min(len(expanded) - 1, index * len(expanded) // count)] for index in range(count)
    )
    if archetype == "core" and selected and "prison" not in selected:
        return (*selected[:-1], "prison")
    return selected


def _terrain(
    settings: WorldSettings, rng: RngRegistry
) -> np.ndarray[tuple[int, int], np.dtype[np.int8]]:
    terrain = np.zeros((settings.height, settings.width), dtype=np.int8)
    river = rng.get("world.gen.terrain", "river")
    river_x = river.randint(
        settings.width // 4,
        max(settings.width // 4, 3 * settings.width // 4),
    )
    for y in range(settings.height):
        if y and y % 8 == 0:
            river_x = min(settings.width - 3, max(2, river_x + river.randint(-1, 1)))
        terrain[y, max(0, river_x - 1) : min(settings.width, river_x + 2)] = 3
    blocked = rng.get("world.gen.terrain", "blocked")
    for _ in range(int(settings.width * settings.height * 0.03)):
        x, y = blocked.randint(0, settings.width - 1), blocked.randint(0, settings.height - 1)
        if terrain[y, x] == 0:
            terrain[y, x] = 1
    terrain[::10, :] = np.where(terrain[::10, :] == 3, 3, 2)
    terrain[:, ::10] = np.where(terrain[:, ::10] == 3, 3, 2)
    return terrain


def generate_world(settings: WorldSettings, rng: RngRegistry) -> World:
    terrain = _terrain(settings, rng)
    districts = _districts(settings)
    occupied: set[tuple[int, int]] = set()
    places: list[Place] = []
    type_counts: Counter[str] = Counter()
    for district_index, district in enumerate(districts):
        x0, y0, x1, y1 = district.bbox
        for local_index, place_type in enumerate(
            _scaled_types(district.archetype, settings.places_per_district)
        ):
            stream = rng.get("world.gen.places", f"{district.district_id}|{local_index}")
            for _ in range(2_000):
                x, y = stream.randint(x0, x1 - 1), stream.randint(y0, y1 - 1)
                if terrain[y, x] not in {1, 3} and (x, y) not in occupied:
                    break
            else:
                raise ValueError(f"could not place {place_type} in {district.district_id}")
            occupied.add((x, y))
            terrain[y, x] = 2
            type_counts[place_type] += 1
            sequence = type_counts[place_type]
            places.append(
                Place(
                    place_id=f"pl_{place_type}_{sequence:04d}",
                    district_id=district.district_id,
                    type=place_type,
                    name=f"{district.name} {place_type.replace('_', ' ').title()} {sequence}",
                    x=x,
                    y=y,
                    capacity=_CAPACITY[place_type],
                    rent_cents=45_000 + district_index * 12_500 if place_type == "home" else 0,
                )
            )
    terrain.flags.writeable = False
    world_hash = compute_world_hash(terrain, districts, places)
    return World(
        settings.width,
        settings.height,
        terrain,
        districts,
        tuple(places),
        world_hash,
    )
