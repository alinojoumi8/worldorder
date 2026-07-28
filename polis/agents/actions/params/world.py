from __future__ import annotations

from polis.agents.actions.params.base import ActionParams, Cents, PlaceId


class MoveToParams(ActionParams):
    place_id: PlaceId


class IdleParams(ActionParams):
    pass


class SleepParams(ActionParams):
    place_id: PlaceId | None = None


class EatParams(ActionParams):
    sku: str | None = None
    place_id: PlaceId | None = None


class RentHomeParams(ActionParams):
    place_id: PlaceId
    offered_rent_cents: Cents
