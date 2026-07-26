from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from polis.config.errors import ConfigError
from polis.kernel.clock import Clock, SimDuration


@dataclass(frozen=True, slots=True)
class Cadence:
    id: str
    spec: str
    phase: Literal[0, 7, 8, 9]
    owner: str
    align: Literal["day", "week", "month", "quarter", "year", "epoch", "session"] = "day"
    offset: SimDuration = field(default_factory=SimDuration)


class Scheduler:
    name = "scheduler"

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._cadences: dict[str, Cadence] = {}

    def register(self, cadence: Cadence) -> None:
        if cadence.id in self._cadences:
            raise ConfigError(f"duplicate cadence: {cadence.id}")
        self._cadences[cadence.id] = cadence

    def registered(self) -> tuple[Cadence, ...]:
        return tuple(self._cadences[key] for key in sorted(self._cadences))

    def fires(self, cadence_id: str, tick: int) -> bool:
        cadence = self._cadences[cadence_id]
        interval = self.clock.ticks_for(SimDuration.parse(cadence.spec))
        offset = self.clock.ticks_for(cadence.offset)
        return tick >= offset and (tick - offset) % max(1, interval) == 0

    def due(self, tick: int) -> tuple[str, ...]:
        return tuple(cadence.id for cadence in self.registered() if self.fires(cadence.id, tick))

    def due_for_phase(self, tick: int, phase: int) -> tuple[str, ...]:
        return tuple(
            cadence.id
            for cadence in self.registered()
            if cadence.phase == phase and self.fires(cadence.id, tick)
        )

    def next_fire(self, cadence_id: str, after_tick: int) -> int:
        candidate = after_tick + 1
        while not self.fires(cadence_id, candidate):
            candidate += 1
        return candidate

    def dump(self) -> Mapping[str, Any]:
        return {
            "cadences": [
                {
                    "id": item.id,
                    "spec": item.spec,
                    "phase": item.phase,
                    "owner": item.owner,
                    "align": item.align,
                }
                for item in self.registered()
            ]
        }

    def load(self, state: Mapping[str, Any]) -> None:
        expected = [item.id for item in self.registered()]
        actual = [item["id"] for item in state["cadences"]]
        if actual != expected:
            raise ConfigError("checkpoint cadence registry differs from current run")
