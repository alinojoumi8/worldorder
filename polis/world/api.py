from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from polis.config.canon import canonical_bytes, sha256_hex
from polis.kernel.clock import ClockProfile

PlaceType = Literal[
    "home",
    "office",
    "factory",
    "shop",
    "school",
    "university",
    "bank",
    "exchange",
    "town_hall",
    "courthouse",
    "police",
    "prison",
    "hospital",
    "park",
    "bar",
    "newsroom",
    "studio",
    "shelter",
]


@dataclass(frozen=True, slots=True)
class District:
    district_id: str
    name: str
    archetype: str
    bbox: tuple[int, int, int, int]
    land_value_cents: int
    school_quality: float
    crime_rate: float
    amenity_score: float

    def contains(self, x: int, y: int) -> bool:
        x0, y0, x1, y1 = self.bbox
        return x0 <= x < x1 and y0 <= y < y1


@dataclass(frozen=True, slots=True)
class Place:
    place_id: str
    district_id: str
    type: PlaceType
    name: str
    x: int
    y: int
    capacity: int
    owner_id: str | None = None
    rent_cents: int = 0
    open_hours: tuple[int, int] = (0, 24)


@dataclass(slots=True)
class Location:
    place_id: str | None
    district_id: str
    x: int
    y: int
    dest_place_id: str | None = None
    origin_place_id: str | None = None
    path_cursor: int | None = None
    travel_ticks: int | None = None

    @property
    def in_transit(self) -> bool:
        return self.place_id is None


_SCHEDULES: dict[str, tuple[tuple[int, int], frozenset[int]]] = {
    "home": ((0, 24), frozenset(range(7))),
    "park": ((0, 24), frozenset(range(7))),
    "shelter": ((0, 24), frozenset(range(7))),
    "police": ((0, 24), frozenset(range(7))),
    "prison": ((0, 24), frozenset(range(7))),
    "hospital": ((0, 24), frozenset(range(7))),
    "office": ((8, 18), frozenset(range(5))),
    "bank": ((8, 18), frozenset(range(5))),
    "town_hall": ((8, 18), frozenset(range(5))),
    "newsroom": ((8, 18), frozenset(range(5))),
    "factory": ((6, 22), frozenset(range(6))),
    "studio": ((6, 22), frozenset(range(6))),
    "shop": ((8, 21), frozenset(range(7))),
    "bar": ((17, 26), frozenset(range(7))),
    "school": ((8, 15), frozenset(range(5))),
    "university": ((8, 20), frozenset(range(6))),
    "courthouse": ((9, 17), frozenset(range(5))),
    "exchange": ((9, 16), frozenset(range(5))),
}

_AFFORDANCES: dict[str, frozenset[str]] = {
    "SLEEP": frozenset({"home", "shelter", "hospital", "prison"}),
    "EAT": frozenset({"home", "shop", "bar", "shelter", "prison"}),
    "STUDY": frozenset({"school", "university", "home", "prison"}),
}


@dataclass(slots=True)
class World:
    width: int
    height: int
    terrain: npt.NDArray[np.int8]
    districts: tuple[District, ...]
    places: tuple[Place, ...]
    world_hash: str
    locations: dict[str, Location] = field(default_factory=dict)
    _occupancy: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    _places_by_id: dict[str, Place] = field(init=False, repr=False)
    _districts_by_id: dict[str, District] = field(init=False, repr=False)

    name = "world"

    def __post_init__(self) -> None:
        self._places_by_id = {place.place_id: place for place in self.places}
        self._districts_by_id = {district.district_id: district for district in self.districts}

    def place(self, place_id: str) -> Place:
        return self._places_by_id[place_id]

    def has_place(self, place_id: str) -> bool:
        return place_id in self._places_by_id

    def district(self, district_id: str) -> District:
        return self._districts_by_id[district_id]

    def places_of_type(self, *types: str) -> tuple[Place, ...]:
        accepted = frozenset(types)
        return tuple(place for place in self.places if place.type in accepted)

    def district_at(self, x: int, y: int) -> District:
        for district in self.districts:
            if district.contains(x, y):
                return district
        return self.districts[-1]

    def occupancy(self, place_id: str) -> tuple[str, ...]:
        return self._occupancy.get(place_id, ())

    def freeze_occupancy(self) -> None:
        rows: defaultdict[str, list[str]] = defaultdict(list)
        for agent_id, location in sorted(self.locations.items()):
            if location.place_id is not None:
                rows[location.place_id].append(agent_id)
        self._occupancy = MappingProxyType(
            {place_id: tuple(sorted(ids)) for place_id, ids in sorted(rows.items())}
        )

    def is_open(self, place_id: str, tick: int, profile: ClockProfile) -> bool:
        place = self.place(place_id)
        hours, weekdays = _SCHEDULES[place.type]
        day = tick // profile.ticks_per_sim_day
        hour = (tick % profile.ticks_per_sim_day) * 24 / profile.ticks_per_sim_day
        start, end = place.open_hours if place.open_hours != (0, 24) else hours
        adjusted = hour + 24 if end > 24 and hour < start else hour
        return day % 7 in weekdays and start <= adjusted < end

    def affords(self, place_id: str | None, action: str) -> bool:
        if action in {"MOVE_TO", "IDLE", "NULL_ACTION"}:
            return True
        if place_id is None:
            return False
        allowed = _AFFORDANCES.get(action)
        return allowed is None or self.place(place_id).type in allowed

    def travel_ticks(self, from_place_id: str, to_place_id: str, profile: ClockProfile) -> int:
        if from_place_id == to_place_id or profile.ticks_per_sim_day == 1:
            return 0
        source = self.place(from_place_id)
        target = self.place(to_place_id)
        distance = abs(source.x - target.x) + abs(source.y - target.y)
        cost_per_tick = max(1, 150 * 24 // profile.ticks_per_sim_day)
        return min(4, max(1, math.ceil(distance / cost_per_tick)))

    def dump(self) -> Mapping[str, Any]:
        return {
            "world_hash": self.world_hash,
            "locations": {
                agent_id: {
                    "place_id": item.place_id,
                    "district_id": item.district_id,
                    "x": item.x,
                    "y": item.y,
                    "dest_place_id": item.dest_place_id,
                    "origin_place_id": item.origin_place_id,
                    "path_cursor": item.path_cursor,
                    "travel_ticks": item.travel_ticks,
                }
                for agent_id, item in sorted(self.locations.items())
            },
        }

    def load(self, state: Mapping[str, Any]) -> None:
        if state["world_hash"] != self.world_hash:
            raise ValueError("checkpoint world hash differs from generated world")
        rows = state["locations"]
        if not isinstance(rows, Mapping):
            raise ValueError("checkpoint locations must be a mapping")
        self.locations = {
            str(agent_id): Location(**dict(value))
            for agent_id, value in sorted(rows.items())
            if isinstance(value, Mapping)
        }
        self.freeze_occupancy()


def compute_world_hash(
    terrain: npt.NDArray[np.int8],
    districts: Iterable[District],
    places: Iterable[Place],
) -> str:
    return sha256_hex(
        canonical_bytes(
            {
                "terrain": terrain.tolist(),
                "districts": [
                    {
                        "id": item.district_id,
                        "name": item.name,
                        "archetype": item.archetype,
                        "bbox": item.bbox,
                        "land_value_cents": item.land_value_cents,
                        "school_quality": item.school_quality,
                        "crime_rate": item.crime_rate,
                        "amenity_score": item.amenity_score,
                    }
                    for item in districts
                ],
                "places": [
                    {
                        "id": item.place_id,
                        "district_id": item.district_id,
                        "type": item.type,
                        "name": item.name,
                        "x": item.x,
                        "y": item.y,
                        "capacity": item.capacity,
                        "rent_cents": item.rent_cents,
                        "open_hours": item.open_hours,
                    }
                    for item in places
                ],
            }
        )
    )
