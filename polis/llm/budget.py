from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from polis.config.settings import LLMBudgetSettings


class Admission(StrEnum):
    PERMIT = "permit"
    DEGRADE = "degrade"
    HALT = "halt"


@dataclass(slots=True)
class _Usage:
    calls: int = 0
    tokens: int = 0
    usd: Decimal = Decimal(0)


class BudgetGuard:
    def __init__(self, settings: LLMBudgetSettings) -> None:
        self.settings = settings
        self.tick = -1
        self.usage: dict[str, _Usage] = {}
        self.cumulative_usd = Decimal(0)
        self.binding_constraint: str | None = None

    def begin_tick(self, tick: int) -> None:
        if tick != self.tick:
            self.tick = tick
            self.usage = {line: _Usage() for line in self.settings.lines}
            self.binding_constraint = None

    def admit(self, line: str, est_in: int, est_out: int, est_usd: Decimal) -> Admission:
        cap = self.settings.lines[line]
        usage = self.usage[line]
        reason: str | None = None
        if self.cumulative_usd + est_usd > (
            self.settings.usd_per_run * self.settings.usd_halt_multiple
        ):
            self.binding_constraint = "run.usd_halt"
            return Admission.HALT
        if usage.calls + 1 > cap.calls_per_tick:
            reason = f"{line}.calls"
        elif usage.tokens + est_in + est_out > cap.tokens_per_tick:
            reason = f"{line}.tokens"
        elif self.cumulative_usd + est_usd > self.settings.usd_per_run:
            reason = "run.usd"
        if reason is None:
            return Admission.PERMIT
        self.binding_constraint = reason
        return Admission.HALT if self.settings.on_exhaustion == "halt" else Admission.DEGRADE

    def charge(
        self,
        line: str,
        *,
        tokens_in: int,
        tokens_out: int,
        usd: Decimal,
    ) -> None:
        usage = self.usage[line]
        usage.calls += 1
        usage.tokens += tokens_in + tokens_out
        usage.usd += usd
        self.cumulative_usd += usd
