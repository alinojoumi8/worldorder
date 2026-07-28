from __future__ import annotations

from polis.agents.actions.protocol import (
    SLOT_ORDER,
    InstitutionResolver,
    InstitutionSlot,
)
from polis.agents.actions.types import ActionType
from polis.config.errors import PolisError


class DuplicateHandler(PolisError):
    """Two institutions claimed the same action type."""


class DuplicateSlot(PolisError):
    """Two resolvers claimed the same deterministic institution slot."""


class ResolverRegistry:
    def __init__(self) -> None:
        self._by_type: dict[ActionType, InstitutionResolver] = {}
        self._by_slot: dict[InstitutionSlot, InstitutionResolver] = {}

    @property
    def by_slot(self) -> dict[InstitutionSlot, InstitutionResolver]:
        return dict(self._by_slot)

    def register(self, resolver: InstitutionResolver) -> None:
        duplicates = resolver.handles.intersection(self._by_type)
        if duplicates:
            names = ", ".join(sorted(action_type.value for action_type in duplicates))
            raise DuplicateHandler(f"action types already registered: {names}")
        if resolver.slot in self._by_slot:
            raise DuplicateSlot(f"institution slot already registered: {resolver.slot.name}")
        self._by_slot[resolver.slot] = resolver
        for action_type in resolver.handles:
            self._by_type[action_type] = resolver

    def for_type(self, action_type: ActionType) -> InstitutionResolver | None:
        return self._by_type.get(action_type)

    def in_slot_order(self) -> tuple[InstitutionResolver, ...]:
        return tuple(
            resolver for slot in SLOT_ORDER if (resolver := self._by_slot.get(slot)) is not None
        )

    def registered_types(self) -> frozenset[ActionType]:
        return frozenset(self._by_type)
