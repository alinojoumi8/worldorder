from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from polis.agents.genesis import generate_agents
from polis.config.settings import Settings, load_settings
from polis.events.kinds import (
    ACTION_VALIDATED,
    EXTERNAL_ACTION_SUBMITTED,
    EXTERNAL_AGENT_NATURALISED,
    EXTERNAL_AGENT_REGISTERED,
    EXTERNAL_ARENA_INVALIDATED,
    EXTERNAL_DEADLINE_MISSED,
    EXTERNAL_GATEWAY_DEGRADED,
    EXTERNAL_INJECTION_FLAGGED,
    EXTERNAL_REGISTRATION_REJECTED,
    EXTERNAL_SESSION_OPENED,
    EXTERNAL_SIM_AWARE_FLAGGED,
    SALIENCE_SCORED,
)
from polis.external import ExternalAction, ExternalDecisionBatch, ExternalLifecycleRequest
from polis.gateway.sdk.canonical import agent_id_for
from polis.kernel.rng import RngRegistry
from polis.living_city import run_living_city
from polis.world.generator import generate_world


class FakeExternalPort:
    def __init__(
        self,
        agent_id: str,
        *,
        submit: bool,
        miss_ticks: set[int] | None = None,
        fail_ticks: set[int] | None = None,
        extras: dict[str, Any] | None = None,
        audit: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.submit = submit
        self.miss_ticks = miss_ticks or set()
        self.fail_ticks = fail_ticks or set()
        self.extras = extras or {}
        self.audit = audit or {}
        self.observations: dict[int, bytes] = {}
        self.drains: list[int] = []
        self.opened: list[int] = []

    def controlled_agent_ids(self) -> tuple[str, ...]:
        return (self.agent_id,)

    async def open_tick(
        self,
        tick: int,
        *,
        sim_time: str,
        decision_deadline_ms: int,
        seal_margin_ms: int,
    ) -> None:
        assert sim_time
        assert decision_deadline_ms > seal_margin_ms
        self.opened.append(tick)

    async def publish_observation(self, tick: int, agent_id: str, blob: bytes) -> bool:
        assert agent_id == self.agent_id
        self.observations[tick] = blob
        return True

    async def drain_actions(
        self,
        tick: int,
        *,
        timeout_ms: int,
    ) -> ExternalDecisionBatch:
        assert timeout_ms > 0
        self.drains.append(tick)
        if tick in self.fail_ticks:
            raise OSError("simulated Redis timeout")
        if not self.submit or tick in self.miss_ticks:
            return ExternalDecisionBatch()
        return ExternalDecisionBatch(
            actions=(
                ExternalAction(
                    agent_id=self.agent_id,
                    action_id=str(UUID(int=tick)),
                    tick=tick,
                    nonce=tick - 1,
                    type="IDLE",
                    params={},
                    reasoning="wait",
                    speech=None,
                    extras=self.extras,
                    sig="0" * 128,
                    session_id="ses_test",
                    received_ms=10,
                    audit=self.audit,
                ),
            )
        )

    async def drain_lifecycle(
        self,
        tick: int,
        *,
        timeout_ms: int,
    ) -> tuple[ExternalLifecycleRequest, ...]:
        del tick, timeout_ms
        return ()

    async def publish_admission(
        self,
        agent_id: str,
        status: dict[str, object],
    ) -> None:
        del agent_id, status


class RegistrationPort:
    def __init__(self) -> None:
        self.pubkey = "34" * 32
        self.agent_id = agent_id_for(self.pubkey)
        self.controlled: set[str] = set()
        self.admission: dict[str, object] | None = None
        self.opened_ticks: list[int] = []
        self._registration_pending = True
        self._session_pending = True

    def controlled_agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.controlled))

    async def open_tick(
        self,
        tick: int,
        *,
        sim_time: str,
        decision_deadline_ms: int,
        seal_margin_ms: int,
    ) -> None:
        del sim_time, decision_deadline_ms, seal_margin_ms
        self.opened_ticks.append(tick)

    async def publish_observation(self, tick: int, agent_id: str, blob: bytes) -> bool:
        del tick, agent_id, blob
        return True

    async def drain_actions(
        self,
        tick: int,
        *,
        timeout_ms: int,
    ) -> ExternalDecisionBatch:
        del timeout_ms
        if self.agent_id not in self.controlled:
            return ExternalDecisionBatch()
        return ExternalDecisionBatch(
            actions=(
                ExternalAction(
                    self.agent_id,
                    str(UUID(int=tick + 100)),
                    tick,
                    tick - 1,
                    "IDLE",
                    {},
                    None,
                    None,
                    {},
                    "1" * 128,
                    "ses_registration",
                    10,
                ),
            )
        )

    async def drain_lifecycle(
        self,
        tick: int,
        *,
        timeout_ms: int,
    ) -> tuple[ExternalLifecycleRequest, ...]:
        del timeout_ms
        if not self._registration_pending:
            if not self._session_pending:
                return ()
            self._session_pending = False
            return (
                ExternalLifecycleRequest(
                    "session_open",
                    self.agent_id,
                    {
                        "session_id": "ses_registration",
                        "custody": "operator",
                        "delegate_pubkey": None,
                        "ttl_s": 60,
                        "transport": "rest",
                        "sdk_version": "test",
                        "protocol_version": 1,
                        "expires_unix_ms": 100_000,
                        "client": {"transport": "rest", "sdk_version": "test"},
                    },
                    "",
                    tick,
                ),
            )
        self._registration_pending = False
        return (
            ExternalLifecycleRequest(
                "register",
                self.agent_id,
                {
                    "protocol_version": 1,
                    "pubkey": self.pubkey,
                    "display_name": "Nikos",
                    "operator": "test@example.org",
                    "contact": "https://example.org",
                    "declared_model": "test",
                    "declared_model_version": "1",
                    "declared_scaffold": "test/1",
                    "scaffold_notes": "fixture",
                    "memory": "ours",
                    "sdk_version": "test",
                    "requested_embodiment": "cohort_matched",
                    "conformance_token": "cft_test",
                },
                "2" * 128,
                tick,
            ),
        )

    async def publish_admission(
        self,
        agent_id: str,
        status: dict[str, object],
    ) -> None:
        self.admission = status
        if status.get("status") == "admitted":
            self.controlled.add(agent_id)


def _settings(*, ticks: int = 2, naturalise_after: int = 240) -> Settings:
    return load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "run": {"name": "gateway-engine-test", "ticks": ticks, "scale": 4},
            "population": {"initial_agents": 4},
            "world": {"width": 20, "height": 20, "districts": 2, "places_per_district": 4},
            "llm": {"providers": {"stub": {"timeout_s": 3.0}}},
            "ablations": {"reflex_only": True},
            "gateway": {
                "enabled": True,
                "lifecycle": {"naturalise_after_consecutive_misses": naturalise_after},
            },
        },
    )


def _controlled_id(settings: Settings) -> str:
    rng = RngRegistry(settings.run.seed)
    world = generate_world(settings.world, rng)
    return next(iter(generate_agents(settings.population, world, rng))).agent_id


async def test_external_action_bypasses_native_cognition_and_is_deterministic() -> None:
    settings = _settings()
    agent_id = _controlled_id(settings)

    first_port = FakeExternalPort(agent_id, submit=True)
    second_port = FakeExternalPort(agent_id, submit=True)
    first = await run_living_city(settings, external_decisions=first_port)
    second = await run_living_city(settings, external_decisions=second_port)

    assert first.report.chain_hash == second.report.chain_hash
    assert first_port.drains == [1, 2]
    assert first_port.opened == [1, 2]
    assert first_port.observations == second_port.observations
    assert all(
        blob.startswith(b"{") and blob.endswith(b"}") for blob in first_port.observations.values()
    )
    assert not any(
        event.kind == SALIENCE_SCORED and event.actor_id == agent_id for event in first.events
    )
    submitted = [
        event
        for event in first.events
        if event.kind == EXTERNAL_ACTION_SUBMITTED and event.actor_id == agent_id
    ]
    assert len(submitted) == 2
    assert all(event.sig == "0" * 128 for event in submitted)


async def test_malformed_audit_extras_cannot_abort_the_tick() -> None:
    settings = _settings(ticks=1)
    agent_id = _controlled_id(settings)
    port = FakeExternalPort(
        agent_id,
        submit=True,
        audit={
            "injection": {},
            "sim_aware": {"confidence": None},
        },
    )

    result = await run_living_city(settings, external_decisions=port)

    assert result.report.status == "completed"
    assert any(event.kind == EXTERNAL_INJECTION_FLAGGED for event in result.events)
    assert any(event.kind == EXTERNAL_SIM_AWARE_FLAGGED for event in result.events)


async def test_missed_deadline_falls_back_without_stalling_and_naturalises() -> None:
    settings = _settings(ticks=1, naturalise_after=1)
    agent_id = _controlled_id(settings)
    port = FakeExternalPort(agent_id, submit=False)

    result = await asyncio.wait_for(
        run_living_city(settings, external_decisions=port),
        timeout=2,
    )

    assert any(
        event.kind == EXTERNAL_DEADLINE_MISSED and event.actor_id == agent_id
        for event in result.events
    )
    naturalised = next(
        event
        for event in result.events
        if event.kind == EXTERNAL_AGENT_NATURALISED and event.actor_id == agent_id
    )
    assert naturalised.payload["ticks_driven"] == 1
    invalidated = next(event for event in result.events if event.kind == EXTERNAL_ARENA_INVALIDATED)
    assert invalidated.payload["reason"] == "miss_rate"
    assert invalidated.payload["offending_agent_ids"] == [agent_id]
    validation = next(
        event
        for event in result.events
        if event.kind == ACTION_VALIDATED and event.actor_id == agent_id
    )
    assert validation.payload["origin"] == "fallback"


async def test_drain_timeout_emits_gateway_degraded_and_falls_back() -> None:
    settings = _settings(ticks=1)
    agent_id = _controlled_id(settings)
    port = FakeExternalPort(agent_id, submit=True, fail_ticks={1})

    result = await run_living_city(settings, external_decisions=port)

    degraded = next(event for event in result.events if event.kind == EXTERNAL_GATEWAY_DEGRADED)
    assert degraded.payload["reason"] == "drain_timeout"
    assert degraded.payload["affected_agent_ids"] == [agent_id]
    assert any(
        event.kind == EXTERNAL_DEADLINE_MISSED and event.actor_id == agent_id
        for event in result.events
    )
    validation = next(
        event
        for event in result.events
        if event.kind == ACTION_VALIDATED and event.actor_id == agent_id
    )
    assert validation.payload["origin"] == "fallback"


async def test_v8_does_not_invalidate_at_exact_miss_threshold() -> None:
    settings = _settings(ticks=20)
    agent_id = _controlled_id(settings)
    port = FakeExternalPort(agent_id, submit=True, miss_ticks={1})

    result = await run_living_city(settings, external_decisions=port)

    assert (
        sum(
            event.kind == EXTERNAL_DEADLINE_MISSED and event.actor_id == agent_id
            for event in result.events
        )
        == 1
    )
    assert (
        sum(
            event.kind == EXTERNAL_ACTION_SUBMITTED and event.actor_id == agent_id
            for event in result.events
        )
        == 19
    )
    assert not any(event.kind == EXTERNAL_ARENA_INVALIDATED for event in result.events)


async def test_registration_is_embodied_by_c20_in_phase_seven() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={
            "run": {"name": "gateway-registration-test", "ticks": 2},
            "llm": {"providers": {"stub": {"timeout_s": 3.0}}},
            "ablations": {"reflex_only": True},
            "gateway": {"enabled": True},
        },
    )
    port = RegistrationPort()

    result = await run_living_city(settings, external_decisions=port)

    admitted = result.population[port.agent_id]
    assert admitted.kind == "external"
    assert admitted.pubkey == port.pubkey
    assert admitted.household_id is not None
    assert port.opened_ticks == [1, 2]
    assert port.admission is not None and port.admission["status"] == "admitted"
    assert any(
        event.kind == EXTERNAL_AGENT_REGISTERED and event.actor_id == port.agent_id
        for event in result.events
    )
    assert any(
        event.kind == EXTERNAL_SESSION_OPENED and event.actor_id == port.agent_id
        for event in result.events
    )


async def test_malformed_session_declaration_is_degraded_without_halting() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={
            "run": {"name": "gateway-malformed-session-test", "ticks": 2},
            "llm": {"providers": {"stub": {"timeout_s": 3.0}}},
            "ablations": {"reflex_only": True},
            "gateway": {"enabled": True},
        },
    )
    port = RegistrationPort()
    original_drain = port.drain_lifecycle

    async def malformed_on_second_tick(
        tick: int,
        *,
        timeout_ms: int,
    ) -> tuple[ExternalLifecycleRequest, ...]:
        if tick == 2:
            return (
                ExternalLifecycleRequest(
                    "session_open",
                    port.agent_id,
                    {"session_id": None, "ttl_s": "invalid"},
                    "",
                    tick,
                ),
            )
        return await original_drain(tick, timeout_ms=timeout_ms)

    port.drain_lifecycle = malformed_on_second_tick  # type: ignore[method-assign]

    result = await run_living_city(settings, external_decisions=port)

    assert result.report.status == "completed"
    degraded = [event for event in result.events if event.kind == EXTERNAL_GATEWAY_DEGRADED]
    assert degraded
    assert degraded[-1].payload["reason"] == "malformed_lifecycle:session_open"


async def test_malformed_registration_declaration_is_rejected_without_halting() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={
            "run": {"name": "gateway-malformed-registration-test", "ticks": 2},
            "llm": {"providers": {"stub": {"timeout_s": 3.0}}},
            "ablations": {"reflex_only": True},
            "gateway": {"enabled": True},
        },
    )
    port = RegistrationPort()

    async def malformed_on_first_tick(
        tick: int,
        *,
        timeout_ms: int,
    ) -> tuple[ExternalLifecycleRequest, ...]:
        del timeout_ms
        if tick != 1:
            return ()
        return (
            ExternalLifecycleRequest(
                "register",
                port.agent_id,
                ["not-a-mapping"],  # type: ignore[arg-type]
                "",
                tick,
            ),
        )

    port.drain_lifecycle = malformed_on_first_tick  # type: ignore[method-assign]

    result = await run_living_city(settings, external_decisions=port)

    assert result.report.status == "completed"
    rejected = [event for event in result.events if event.kind == EXTERNAL_REGISTRATION_REJECTED]
    assert rejected
    assert rejected[-1].payload["reason"] == "bad_declaration"
    assert port.admission == {
        "status": "rejected",
        "agent_id": port.agent_id,
        "reason": "bad_declaration",
    }
