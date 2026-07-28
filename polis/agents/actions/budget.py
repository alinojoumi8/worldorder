from __future__ import annotations

from typing import Literal

from polis.config.settings import ActionSettings


class SlotLedger:
    """Per-tick slot accounting shared by native and external actions."""

    def __init__(self, action_slots: int) -> None:
        if action_slots < 1:
            raise ValueError("action_slots must be at least 1")
        self.action_slots = action_slots
        self._used: dict[tuple[str, int], int] = {}

    @classmethod
    def from_settings(
        cls,
        settings: ActionSettings,
        profile: Literal["microscope", "chronicle"],
    ) -> SlotLedger:
        return cls(settings.slots_per_tick.for_profile(profile))

    def consume(self, actor_id: str, tick: int) -> int | None:
        key = (actor_id, tick)
        used = self._used.get(key, 0)
        if used >= self.action_slots:
            return None
        self._used[key] = used + 1
        return used

    def remaining(self, actor_id: str, tick: int) -> int:
        return max(0, self.action_slots - self._used.get((actor_id, tick), 0))

    def reset(self, tick: int) -> None:
        self._used = {key: used for key, used in self._used.items() if key[1] >= tick}
