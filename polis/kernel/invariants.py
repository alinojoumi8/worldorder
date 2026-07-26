from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal, Protocol

from polis.kernel.clock import Clock


class Severity(StrEnum):
    HALT = "halt"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class Ok:
    invariant_id: str


@dataclass(frozen=True, slots=True)
class Violation:
    invariant_id: str
    expected: str
    actual: str
    detail: Mapping[str, Any]
    severity: Severity


type Result = Ok | Violation


class WorldStateView(Protocol):
    tick: int

    def money_supply_cents(self) -> int: ...

    def total_balances_cents(self) -> int: ...

    def ledger_imbalance_cents(self) -> int: ...

    def population(self) -> int: ...

    def initial_population(self) -> int: ...

    def action_type_counts(self) -> Mapping[str, int]: ...

    def chain_ok(self) -> bool: ...


@dataclass(slots=True)
class NullWorldState:
    tick: int = 0

    def money_supply_cents(self) -> int:
        return 0

    def total_balances_cents(self) -> int:
        return 0

    def ledger_imbalance_cents(self) -> int:
        return 0

    def population(self) -> int:
        return 0

    def initial_population(self) -> int:
        return 0

    def action_type_counts(self) -> Mapping[str, int]:
        return {}

    def chain_ok(self) -> bool:
        return True


class Invariant(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def severity(self) -> Severity: ...

    @property
    def frequency(self) -> Literal["tick", "sim_day", "checkpoint"]: ...

    def check(self, state: WorldStateView) -> Result: ...


@dataclass(frozen=True, slots=True)
class _FunctionInvariant:
    id: str
    severity: Severity
    frequency: Literal["tick", "sim_day", "checkpoint"]
    function: Callable[[WorldStateView], Result]

    def check(self, state: WorldStateView) -> Result:
        return self.function(state)


def _money(state: WorldStateView) -> Result:
    actual = state.total_balances_cents()
    expected = state.money_supply_cents()
    return (
        Ok("INV-MONEY")
        if actual == expected
        else Violation("INV-MONEY", str(expected), str(actual), {}, Severity.HALT)
    )


def _ledger(state: WorldStateView) -> Result:
    actual = state.ledger_imbalance_cents()
    return (
        Ok("INV-LEDGER")
        if actual == 0
        else Violation("INV-LEDGER", "0", str(actual), {}, Severity.HALT)
    )


def _population(state: WorldStateView) -> Result:
    initial = state.initial_population()
    population = state.population()
    if initial == 0 or 0.2 * initial <= population <= 5 * initial:
        return Ok("INV-POP")
    return Violation(
        "INV-POP",
        f"[{0.2 * initial}, {5 * initial}]",
        str(population),
        {},
        Severity.WARN,
    )


def _entropy(state: WorldStateView) -> Result:
    counts = state.action_type_counts()
    total = sum(counts.values())
    if total == 0:
        return Ok("INV-ENTROPY")
    entropy = -sum((count / total) * math.log(count / total) for count in counts.values() if count)
    return (
        Ok("INV-ENTROPY")
        if entropy >= 0.5
        else Violation("INV-ENTROPY", ">=0.5", str(round(entropy, 6)), {}, Severity.WARN)
    )


def _chain(state: WorldStateView) -> Result:
    return (
        Ok("INV-CHAIN")
        if state.chain_ok()
        else Violation("INV-CHAIN", "valid", "invalid", {}, Severity.HALT)
    )


INVARIANT_REGISTRY: Final[dict[str, Invariant]] = {
    item.id: item
    for item in (
        _FunctionInvariant("INV-MONEY", Severity.HALT, "tick", _money),
        _FunctionInvariant("INV-LEDGER", Severity.HALT, "tick", _ledger),
        _FunctionInvariant("INV-POP", Severity.WARN, "sim_day", _population),
        _FunctionInvariant("INV-ENTROPY", Severity.WARN, "sim_day", _entropy),
        _FunctionInvariant("INV-CHAIN", Severity.HALT, "checkpoint", _chain),
    )
}


class InvariantRunner:
    def __init__(
        self,
        clock: Clock,
        *,
        continue_on_violation: bool = False,
        enabled: frozenset[str] | None = None,
    ) -> None:
        self.clock = clock
        self.continue_on_violation = continue_on_violation
        self.enabled = enabled or frozenset(INVARIANT_REGISTRY)
        self.counts: dict[str, int] = {}

    def due(self, tick: int, *, checkpoint: bool = False) -> tuple[str, ...]:
        result = []
        for key in sorted(self.enabled):
            invariant = INVARIANT_REGISTRY[key]
            if (
                invariant.frequency == "tick"
                or (invariant.frequency == "sim_day" and self.clock.starts_new("day", tick))
                or (invariant.frequency == "checkpoint" and checkpoint)
            ):
                result.append(key)
        return tuple(result)

    def run(self, tick: int, state: WorldStateView, *, checkpoint: bool = False) -> list[Result]:
        results = [
            INVARIANT_REGISTRY[key].check(state) for key in self.due(tick, checkpoint=checkpoint)
        ]
        for result in results:
            if isinstance(result, Violation):
                self.counts[result.invariant_id] = self.counts.get(result.invariant_id, 0) + 1
        return results

    def should_halt(self, results: Sequence[Result]) -> bool:
        return not self.continue_on_violation and any(
            isinstance(result, Violation) and result.severity == Severity.HALT for result in results
        )
