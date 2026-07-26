from __future__ import annotations

from collections.abc import Callable

from polis.events.types import Event


class CognitionSampler:
    def __init__(self, rate: float, seed_for: Callable[[str, str, int], int]) -> None:
        if not 0 <= rate <= 1:
            raise ValueError("sampling rate must be in [0, 1]")
        self.rate = rate
        self.seed_for = seed_for

    def keep(self, event: Event, *, routed_mode: str) -> bool:
        if routed_mode in {"deliberate", "reflect"}:
            return True
        identity = event.actor_id or ",".join(event.subject_ids) or "system"
        value = self.seed_for("cognition.sample", identity, event.tick)
        return value / (2**64 - 1) < self.rate
