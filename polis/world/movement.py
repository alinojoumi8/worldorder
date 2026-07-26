from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from polis.kernel.clock import ClockProfile
from polis.kernel.rng import RngRegistry
from polis.world.api import Location, World


@dataclass(frozen=True, slots=True)
class MoveRequest:
    agent_id: str
    place_id: str


@dataclass(frozen=True, slots=True)
class Movement:
    agent_id: str
    from_place_id: str | None
    to_place_id: str
    travel_ticks: int
    arrived: bool


@dataclass(frozen=True, slots=True)
class BlockedMove:
    agent_id: str
    place_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class MovementResult:
    movements: tuple[Movement, ...]
    blocked: tuple[BlockedMove, ...]


def resolve_movement(
    world: World,
    requests: Iterable[MoveRequest],
    *,
    tick: int,
    profile: ClockProfile,
    rng: RngRegistry,
) -> MovementResult:
    arrivals: defaultdict[str, list[tuple[str, str | None, int]]] = defaultdict(list)
    movements: list[Movement] = []
    blocked: list[BlockedMove] = []

    for agent_id, location in sorted(world.locations.items()):
        if not location.in_transit:
            continue
        if location.path_cursor is None or location.travel_ticks is None:
            raise ValueError(f"invalid transit state for {agent_id}")
        location.path_cursor += 1
        if location.path_cursor >= location.travel_ticks and location.dest_place_id is not None:
            arrivals[location.dest_place_id].append(
                (agent_id, location.origin_place_id, location.travel_ticks)
            )

    for request in sorted(requests, key=lambda item: (item.agent_id, item.place_id)):
        location = world.locations[request.agent_id]
        if not world.has_place(request.place_id):
            blocked.append(BlockedMove(request.agent_id, request.place_id, "unknown_place"))
            continue
        origin = location.place_id or location.origin_place_id
        if origin is None:
            blocked.append(BlockedMove(request.agent_id, request.place_id, "invalid_origin"))
            continue
        travel_ticks = world.travel_ticks(origin, request.place_id, profile)
        if travel_ticks == 0:
            arrivals[request.place_id].append((request.agent_id, origin, 0))
        else:
            target = world.place(request.place_id)
            location.place_id = None
            location.dest_place_id = request.place_id
            location.origin_place_id = origin
            location.path_cursor = 0
            location.travel_ticks = travel_ticks
            location.district_id = target.district_id
            movements.append(
                Movement(request.agent_id, origin, request.place_id, travel_ticks, False)
            )

    for place_id, claims in sorted(arrivals.items()):
        place = world.place(place_id)
        claimant_ids = {claim[0] for claim in claims}
        current = len(
            [
                agent_id
                for agent_id, item in world.locations.items()
                if item.place_id == place_id and agent_id not in claimant_ids
            ]
        )
        ordered = sorted(
            claims,
            key=lambda claim: (
                rng.seed_for("world.admission", f"{place_id}|{claim[0]}", tick),
                claim[0],
            ),
        )
        admitted = ordered[: max(0, place.capacity - current)]
        rejected = ordered[max(0, place.capacity - current) :]
        for agent_id, origin, travel_ticks in admitted:
            target = world.place(place_id)
            world.locations[agent_id] = Location(
                place_id,
                target.district_id,
                target.x,
                target.y,
            )
            movements.append(Movement(agent_id, origin, place_id, travel_ticks, True))
        for agent_id, _origin, _travel_ticks in rejected:
            blocked.append(BlockedMove(agent_id, place_id, "full"))

    world.freeze_occupancy()
    return MovementResult(
        tuple(sorted(movements, key=lambda item: (item.agent_id, item.arrived))),
        tuple(sorted(blocked, key=lambda item: item.agent_id)),
    )
