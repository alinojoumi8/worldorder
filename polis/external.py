"""Engine-facing port for externally controlled citizens.

This module is intentionally transport-neutral.  The engine depends on this
small port; the composition root adapts Redis wire records to it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from polis.config.mechanisms import mechanism


@mechanism(
    "gateway.embodiment",
    entails=(
        "An external citizen's starting age, traits, skills, education, balance, household "
        "and home place are drawn from the same distribution as a native immigrant. "
        "Therefore any external-agent outcome advantage cannot follow from its starting "
        "endowment, and any claim about scaffold quality must be read against the "
        "embodiment mode in force."
    ),
)
def cohort_matched_embodiment() -> None:
    """Declare the C20 embodiment mechanism used by gateway admissions."""


@dataclass(frozen=True, slots=True)
class ExternalAction:
    agent_id: str
    action_id: str
    tick: int
    nonce: int
    type: str
    params: Mapping[str, Any]
    reasoning: str | None
    speech: str | None
    extras: Mapping[str, Any]
    sig: str
    session_id: str
    received_ms: int
    audit: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalDecisionBatch:
    """External actions ordered by ``(agent_id, action_id, nonce)``."""

    actions: tuple[ExternalAction, ...] = ()
    degraded_reason: str | None = None
    resumed_agent_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExternalLatencyRow:
    agent_id: str
    tick: int
    observation_pushed_ms: int
    action_received_ms: int | None
    decision_ms: int | None
    missed: bool


@dataclass(frozen=True, slots=True)
class ExternalLifecycleRequest:
    """A lifecycle request returned in the port's canonical lifecycle order."""

    request_type: str
    agent_id: str
    declaration: Mapping[str, Any]
    sig: str
    queued_tick: int
    reason: str | None = None
    revoked_by: str | None = None


class ExternalDecisionPort(Protocol):
    def controlled_agent_ids(self) -> Sequence[str]:
        """Return controlled citizens in ascending canonical agent-ID order."""

    def latency_rows(self) -> Sequence[ExternalLatencyRow]:
        """Return transport timing rows that are deliberately outside the hash chain."""

    def clear_latency_rows(self) -> None:
        """Forget latency rows only after their out-of-chain persistence succeeds."""

    async def open_tick(
        self,
        tick: int,
        *,
        sim_time: str,
        decision_deadline_ms: int,
        seal_margin_ms: int,
    ) -> None:
        """Expose an accepting window without leaking transport concerns into the engine."""

    async def publish_observation(self, tick: int, agent_id: str, blob: bytes) -> bool:
        """Publish the exact canonical PHASE 1 observation bytes."""

    async def drain_actions(self, tick: int, *, timeout_ms: int) -> ExternalDecisionBatch:
        """Drain once, ordered by ``(agent_id, action_id, nonce)``, before the timeout."""

    async def drain_lifecycle(
        self,
        tick: int,
        *,
        timeout_ms: int,
    ) -> tuple[ExternalLifecycleRequest, ...]:
        """Drain once in PHASE 7, ordered by ``(agent_id, queued_tick, type, sig)``."""

    async def publish_admission(
        self,
        agent_id: str,
        status: Mapping[str, Any],
    ) -> None:
        """Publish the engine's admission result for the read-only gateway."""
