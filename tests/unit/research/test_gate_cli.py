from __future__ import annotations

from uuid import UUID

import pytest
from typer.testing import CliRunner

from polis.cli.app import app
from polis.config.settings import Settings
from polis.research.gates import GateError
from polis.store.engine import StoreError


def test_gate_cli_reports_gate_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stored_settings(base: Settings, _run_id: UUID) -> Settings:
        return base

    async def fail_gate(_settings: Settings, _run_id: UUID) -> dict[str, object]:
        raise GateError("run is missing required gate evidence")

    monkeypatch.setattr("polis.cli.app._stored_settings", stored_settings)
    monkeypatch.setattr("polis.cli.app._gate_run_from_store", fail_gate)

    result = CliRunner().invoke(
        app,
        ["gate", "--run", "00000000-0000-0000-0000-000000000001"],
    )

    assert result.exit_code == 1
    assert "run is missing required gate evidence" in result.output
    assert "Traceback" not in result.output


def test_verify_cli_reports_missing_run_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_settings(_base: Settings, _run_id: UUID) -> Settings:
        raise StoreError("run not found")

    monkeypatch.setattr("polis.cli.app._stored_settings", fail_settings)

    result = CliRunner().invoke(
        app,
        ["verify", "00000000-0000-0000-0000-000000000001"],
    )

    assert result.exit_code == 1
    assert "run not found" in result.output
    assert "Traceback" not in result.output
