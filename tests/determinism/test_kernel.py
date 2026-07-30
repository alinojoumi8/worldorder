from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.events.verify import verify_batch
from polis.kernel.invariants import InvariantRunner, Severity, Violation
from polis.simulation import run_empty


@pytest.mark.asyncio
async def test_repeated_empty_runs_are_byte_identical() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 200}},
    )

    first = await run_empty(settings)
    second = await run_empty(settings)

    assert first.events == second.events
    assert first.report.chain_hash == second.report.chain_hash
    assert first.report.events == 400
    assert verify_batch(first.events).ok


@pytest.mark.asyncio
async def test_resume_does_not_diverge_from_continuous_run() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 200}},
    )

    continuous = await run_empty(settings)
    partial = await run_empty(settings, ticks=100)
    resumed = await run_empty(settings, ticks=200, resume_events=partial.events)

    assert resumed.events == continuous.events
    assert resumed.report.chain_hash == continuous.report.chain_hash
    assert verify_batch(resumed.events).ok


@pytest.mark.asyncio
async def test_resume_after_run_started_completes_genesis_before_tick_one() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 2}},
    )
    continuous = await run_empty(settings)

    resumed = await run_empty(
        settings,
        ticks=2,
        resume_events=continuous.events[:1],
    )

    assert resumed.events == continuous.events
    assert resumed.report.chain_hash == continuous.report.chain_hash
    assert verify_batch(resumed.events).ok


@pytest.mark.asyncio
async def test_resume_rejects_halted_genesis_and_reports_halting_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def halt_at_genesis(
        _runner: InvariantRunner,
        tick: int,
        _state: object,
    ) -> tuple[Violation, ...]:
        assert tick == 0
        return (
            Violation(
                invariant_id="INV-WARN",
                expected="warning condition",
                actual="warning observed",
                detail={},
                severity=Severity.WARN,
            ),
            Violation(
                invariant_id="INV-HALT",
                expected="valid genesis",
                actual="invalid genesis",
                detail={},
                severity=Severity.HALT,
            ),
        )

    monkeypatch.setattr(InvariantRunner, "run", halt_at_genesis)
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 1}},
    )

    halted = await run_empty(settings)

    assert halted.report.status == "halted"
    assert halted.report.halt_reason == "INV-HALT"
    with pytest.raises(ValueError, match="halted during genesis"):
        await run_empty(settings, resume_events=halted.events)


@pytest.mark.asyncio
async def test_regular_tick_report_names_the_halting_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def halt_at_tick_one(
        _runner: InvariantRunner,
        tick: int,
        _state: object,
    ) -> tuple[Violation, ...]:
        if tick == 0:
            return ()
        return (
            Violation(
                invariant_id="INV-WARN",
                expected="warning condition",
                actual="warning observed",
                detail={},
                severity=Severity.WARN,
            ),
            Violation(
                invariant_id="INV-HALT",
                expected="valid tick",
                actual="invalid tick",
                detail={},
                severity=Severity.HALT,
            ),
        )

    monkeypatch.setattr(InvariantRunner, "run", halt_at_tick_one)
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"run": {"ticks": 1}},
    )

    result = await run_empty(settings)

    assert result.report.status == "halted"
    assert result.report.halt_reason == "INV-HALT"
