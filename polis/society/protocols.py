from __future__ import annotations

from typing import Literal, Protocol

from polis.events.types import Event


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
