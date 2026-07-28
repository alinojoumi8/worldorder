from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from polis.events.types import Event


@dataclass(frozen=True, slots=True)
class BeliefUpdate:
    proposition: str
    value: float
    confidence: float
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ArticleBrief:
    article_id: str
    outlet_id: str
    headline: str
    tick: int
    stance_proposition: str | None = None
    stance_value: float | None = None


class BeliefChannel(Protocol):
    """Narrow C17 bridge; C16 can predict but never owns belief state."""

    def apply_social(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        tick: int,
    ) -> Event | None: ...

    def predict_delta(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        channel: Literal["social", "media"],
    ) -> float: ...

    def value(self, agent_id: str, proposition: str) -> float: ...

    def confidence(self, agent_id: str, proposition: str) -> float: ...

    def population_mean(self, proposition: str) -> float: ...


class NullBeliefChannel:
    """Default bridge until C17 lands."""

    def apply_social(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        tick: int,
    ) -> Event | None:
        del agent_id, proposition, target, source_id, tick
        return None

    def predict_delta(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        channel: Literal["social", "media"],
    ) -> float:
        del agent_id, proposition, target, source_id, channel
        return 0.0

    def value(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 0.0

    def confidence(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 0.5

    def population_mean(self, proposition: str) -> float:
        del proposition
        return 0.0


class MemoryLookup(Protocol):
    def holds_memory_of(self, agent_id: str, event_seq: int) -> bool: ...

    def holders_of(self, event_seq: int) -> frozenset[str]: ...

    def retrieve_recent_texts(self, agent_id: str, tick: int, n: int) -> tuple[str, ...]: ...


class NullMemoryLookup:
    def holds_memory_of(self, agent_id: str, event_seq: int) -> bool:
        del agent_id, event_seq
        return False

    def holders_of(self, event_seq: int) -> frozenset[str]:
        del event_seq
        return frozenset()

    def retrieve_recent_texts(self, agent_id: str, tick: int, n: int) -> tuple[str, ...]:
        del agent_id, tick, n
        return ()


class OfficeLookup(Protocol):
    def holds_office(self, agent_id: str, tick: int) -> str | None: ...


class NullOfficeLookup:
    def holds_office(self, agent_id: str, tick: int) -> str | None:
        del agent_id, tick
        return None


__all__ = [
    "ArticleBrief",
    "BeliefChannel",
    "BeliefUpdate",
    "MemoryLookup",
    "NullBeliefChannel",
    "NullMemoryLookup",
    "NullOfficeLookup",
    "OfficeLookup",
]
