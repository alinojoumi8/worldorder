from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from polis.config.errors import RuntimeOverlayError
from polis.config.settings import Settings


@dataclass(frozen=True, slots=True)
class Enactment:
    parameter: str
    value: Any
    enacted_tick: int
    effective_tick: int
    policy_id: str
    event_seq: int


class RuntimeConfig:
    name: ClassVar[str] = "runtime_config"

    def __init__(self, base: Settings) -> None:
        self.base = base
        self._history: dict[str, list[Enactment]] = defaultdict(list)

    def _static(self, parameter: str) -> Any:
        current: Any = self.base
        for part in parameter.split("."):
            if not hasattr(current, part):
                raise RuntimeOverlayError(f"unknown runtime parameter: {parameter}")
            current = getattr(current, part)
        return current

    def get(self, parameter: str, tick: int) -> Any:
        candidates = [item for item in self._history[parameter] if item.effective_tick <= tick]
        if not candidates:
            return self._static(parameter)
        return max(candidates, key=lambda item: (item.effective_tick, item.event_seq)).value

    def enact(
        self,
        parameter: str,
        value: Any,
        effective_tick: int,
        policy_id: str,
        event_seq: int,
        *,
        enacted_tick: int = 0,
    ) -> None:
        self._static(parameter)
        if effective_tick <= enacted_tick:
            raise RuntimeOverlayError(
                f"effective_tick {effective_tick} must be after enacted_tick {enacted_tick}"
            )
        self._history[parameter].append(
            Enactment(
                parameter,
                value,
                enacted_tick,
                effective_tick,
                policy_id,
                event_seq,
            )
        )
        self._history[parameter].sort(key=lambda item: (item.effective_tick, item.event_seq))

    def history(self, parameter: str) -> tuple[Enactment, ...]:
        return tuple(self._history[parameter])

    def snapshot(self, tick: int) -> Mapping[str, Any]:
        return {key: self.get(key, tick) for key in sorted(self._history)}

    def dump(self) -> Mapping[str, Any]:
        return {
            key: [asdict(item) for item in values] for key, values in sorted(self._history.items())
        }

    def load(self, state: Mapping[str, Any]) -> None:
        self._history.clear()
        for key in sorted(state):
            self._history[key] = [Enactment(**item) for item in state[key]]
