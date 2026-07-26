from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar, Final, Literal

from polis.config.errors import ConfigError
from polis.config.settings import ClockSettings

EPOCH: Final = datetime(2100, 1, 1)


@dataclass(frozen=True, slots=True)
class ClockProfile:
    name: Literal["microscope", "chronicle"]
    ticks_per_sim_day: int
    days_per_sim_year: int
    seconds_per_tick: int
    days_per_sim_week: int = 7
    days_per_sim_month: int = 30
    days_per_sim_quarter: int = 90


PROFILES: Final[Mapping[str, ClockProfile]] = {
    "microscope": ClockProfile("microscope", 24, 360, 3600),
    "chronicle": ClockProfile("chronicle", 1, 360, 86_400),
}


def profile_from_settings(settings: ClockSettings) -> ClockProfile:
    profile = PROFILES[settings.profile]
    return ClockProfile(
        profile.name,
        settings.ticks_per_sim_day,
        settings.days_per_sim_year,
        86_400 // settings.ticks_per_sim_day,
    )


@dataclass(frozen=True, slots=True)
class SimDuration:
    years: int = 0
    quarters: int = 0
    months: int = 0
    weeks: int = 0
    days: int = 0
    hours: int = 0

    @classmethod
    def parse(cls, spec: str) -> SimDuration:
        aliases = {
            "daily": "1d",
            "weekly": "1w",
            "biweekly": "2w",
            "monthly": "1mo",
            "quarterly": "1q",
            "annually": "1y",
        }
        value = aliases.get(spec, spec)
        match = re.fullmatch(r"(\d+)(y|q|mo|w|d|h)", value)
        if not match:
            raise ConfigError(f"invalid simulated duration: {spec!r}")
        amount, unit = int(match.group(1)), match.group(2)
        names = {
            "y": "years",
            "q": "quarters",
            "mo": "months",
            "w": "weeks",
            "d": "days",
            "h": "hours",
        }
        return cls(**{names[unit]: amount})


class Clock:
    name: ClassVar[str] = "clock"

    def __init__(
        self,
        profile: ClockProfile,
        *,
        start_tick: int = 0,
        epoch: datetime = EPOCH,
    ) -> None:
        self._profile = profile
        self._tick = start_tick
        self.epoch = epoch

    @property
    def tick(self) -> int:
        return self._tick

    @property
    def sim_time(self) -> datetime:
        return self.sim_time_at(self._tick)

    @property
    def profile(self) -> ClockProfile:
        return self._profile

    def advance(self) -> int:
        self._tick += 1
        return self._tick

    def sim_time_at(self, tick: int) -> datetime:
        return self.epoch + timedelta(seconds=tick * self._profile.seconds_per_tick)

    def tick_at(self, when: datetime) -> int:
        seconds = int((when - self.epoch).total_seconds())
        return seconds // self._profile.seconds_per_tick

    def ticks_for(self, duration: SimDuration) -> int:
        days = (
            duration.years * self._profile.days_per_sim_year
            + duration.quarters * self._profile.days_per_sim_quarter
            + duration.months * self._profile.days_per_sim_month
            + duration.weeks * self._profile.days_per_sim_week
            + duration.days
        )
        return (
            days * self._profile.ticks_per_sim_day
            + duration.hours * self._profile.ticks_per_sim_day // 24
        )

    def sim_day(self, tick: int | None = None) -> int:
        return (self._tick if tick is None else tick) // self._profile.ticks_per_sim_day

    def sim_week(self, tick: int | None = None) -> int:
        return self.sim_day(tick) // 7

    def sim_month(self, tick: int | None = None) -> int:
        return self.sim_day(tick) // 30

    def sim_quarter(self, tick: int | None = None) -> int:
        return self.sim_day(tick) // 90

    def sim_year(self, tick: int | None = None) -> int:
        return self.sim_day(tick) // self._profile.days_per_sim_year

    def hour_of_day(self, tick: int | None = None) -> float:
        value = self._tick if tick is None else tick
        return (value % self._profile.ticks_per_sim_day) * (24 / self._profile.ticks_per_sim_day)

    def starts_new(
        self,
        unit: Literal["day", "week", "month", "quarter", "year"],
        tick: int,
    ) -> bool:
        if tick == 0:
            return True
        method = {
            "day": self.sim_day,
            "week": self.sim_week,
            "month": self.sim_month,
            "quarter": self.sim_quarter,
            "year": self.sim_year,
        }[unit]
        return method(tick) != method(tick - 1)

    def dump(self) -> Mapping[str, Any]:
        return {"tick": self._tick, "profile": self._profile.name}

    def load(self, state: Mapping[str, Any]) -> None:
        if state["profile"] != self._profile.name:
            raise ConfigError("checkpoint clock profile differs from current run")
        self._tick = int(state["tick"])
