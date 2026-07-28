from __future__ import annotations

from collections.abc import Sequence

from polis.agents.actions.types import (
    ActionOutcome,
    Rejection,
    ValidatedAction,
)


class RejectionLedger:
    def __init__(self) -> None:
        self._outcomes: dict[tuple[str, int], list[ActionOutcome]] = {}

    def record_rejection(self, rejection: Rejection, tick: int) -> None:
        self._outcomes.setdefault((rejection.actor_id, tick), []).append(
            ActionOutcome(
                action_id=rejection.action_id,
                tick=tick,
                type=rejection.type,
                status="rejected",
                reason=rejection.reason,
                detail=rejection.detail or None,
                effects=(),
            )
        )

    def record_applied(
        self,
        action: ValidatedAction,
        tick: int,
        effects: Sequence[str],
    ) -> None:
        self._outcomes.setdefault((action.action.actor_id, tick), []).append(
            ActionOutcome(
                action_id=action.action.action_id,
                tick=tick,
                type=action.action.type,
                status="applied",
                reason=None,
                detail=None,
                effects=tuple(effects),
            )
        )

    def last_action_outcome(self, actor_id: str, tick: int) -> ActionOutcome | None:
        if tick <= 0:
            return None
        outcomes = self._outcomes.get((actor_id, tick - 1), ())
        return outcomes[-1] if outcomes else None

    def prune(self, tick: int) -> None:
        oldest = tick - 1
        self._outcomes = {
            key: outcomes for key, outcomes in self._outcomes.items() if key[1] >= oldest
        }
