from __future__ import annotations

from collections.abc import Sequence

from polis.agents.actions.protocol import SLOT_ORDER, ResolutionContext
from polis.agents.actions.registry import ResolverRegistry
from polis.agents.actions.types import ValidatedAction
from polis.events.types import Event


class ActionDispatcher:
    def __init__(self, registry: ResolverRegistry) -> None:
        self.registry = registry

    def dispatch(
        self,
        validated: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> tuple[Event, ...]:
        events: list[Event] = []
        by_slot = self.registry.by_slot
        for slot in SLOT_ORDER:
            resolver = by_slot.get(slot)
            if resolver is None:
                continue
            batch = tuple(
                sorted(
                    (action for action in validated if action.action.type in resolver.handles),
                    key=lambda action: (
                        action.action.actor_id,
                        str(action.action.action_id),
                    ),
                )
            )
            events.extend(resolver.resolve(batch, tick, ctx))
        return tuple(events)
