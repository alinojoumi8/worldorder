from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.kinds import TICK_COMPLETED
from polis.events.verify import verify_batch
from polis.kernel.invariants import InvariantRunner, Severity, Violation
from polis.kernel.tick import TickLoop
from polis.living_city import run_living_city
from polis.llm.router import LLMRouter
from polis.run_identity import build_run_identity

GOLDEN_100_HASH = "b5123f9404f3149d93baa91ba6d4b47257d9e8a119781f20b7f17eb502ba09d7"


@pytest.mark.determinism
@pytest.mark.asyncio
async def test_frozen_50_agent_100_tick_golden_run() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 100}},
    )

    result = await run_living_city(
        settings,
        run_identity=build_run_identity(settings, code_git_sha="0" * 40),
    )

    assert result.report.chain_hash == GOLDEN_100_HASH
    assert result.report.events == 10_332
    assert len(result.events) == 10_386
    assert len(result.memory) == 16
    assert verify_batch(result.events).ok


@pytest.mark.asyncio
async def test_genesis_halt_report_counts_every_committed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_closed = False
    close_router = LLMRouter.close

    async def record_router_close(router: LLMRouter) -> None:
        nonlocal router_closed
        router_closed = True
        await close_router(router)

    def halt_at_genesis(
        _runner: InvariantRunner,
        tick: int,
        _state: object,
    ) -> tuple[Violation]:
        assert tick == 0
        return (
            Violation(
                invariant_id="INV-TEST",
                expected="valid genesis",
                actual="invalid genesis",
                detail={},
                severity=Severity.HALT,
            ),
        )

    monkeypatch.setattr(InvariantRunner, "run", halt_at_genesis)
    monkeypatch.setattr(LLMRouter, "close", record_router_close)
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "run": {"ticks": 1, "scale": 1},
            "population": {"initial_agents": 1},
        },
    )

    result = await run_living_city(settings)

    assert result.report.status == "halted"
    assert result.report.last_tick == 0
    assert result.report.events == len(result.events)
    assert result.report.events > 2
    completed = next(event for event in result.events if event.kind == TICK_COMPLETED)
    assert completed.payload["event_count"] == len(result.events)
    assert router_closed
    assert verify_batch(result.events).ok


@pytest.mark.asyncio
async def test_router_closes_when_genesis_completion_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_closed = False
    close_router = LLMRouter.close

    async def record_router_close(router: LLMRouter) -> None:
        nonlocal router_closed
        router_closed = True
        await close_router(router)

    async def fail_genesis(_loop: TickLoop) -> None:
        raise RuntimeError("genesis commit failed")

    monkeypatch.setattr(LLMRouter, "close", record_router_close)
    monkeypatch.setattr(TickLoop, "complete_genesis_tick", fail_genesis)
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "run": {"ticks": 1, "scale": 1},
            "population": {"initial_agents": 1},
        },
    )

    with pytest.raises(RuntimeError, match="genesis commit failed"):
        await run_living_city(settings)

    assert router_closed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fast_50_agent_500_tick_smoke() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))

    result = await run_living_city(settings, collect_events=False)

    latest = result.metrics.latest()
    assert result.report.ticks == 500
    assert result.report.status == "completed"
    assert result.population.population() == 50
    assert latest["sys.actions.unique"].value >= 2
    assert 500 <= latest["sys.cognition.deliberate_share"].value <= 1_000
    assert latest["city.wellbeing_mean"].value > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_metrics_only_retention_keeps_only_sampled_cognition_traces() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 10, "retention": "metrics_only"}},
    )

    result = await run_living_city(settings, collect_events=False)

    assert 0 < len(result.traces) < 25
