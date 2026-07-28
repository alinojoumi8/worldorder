from __future__ import annotations

from click import unstyle
from typer.testing import CliRunner

from polis.cli.app import app


def test_gateway_command_is_implemented_and_documents_its_inputs() -> None:
    result = CliRunner().invoke(app, ["gateway", "--help"], env={"COLUMNS": "120"})
    stdout = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "not implemented" not in stdout
    assert "--config" in stdout
    assert "--run-id" in stdout


def test_run_rejects_memory_only_when_gateway_is_enabled() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--config",
            "configs/baseline.yaml",
            "--memory-only",
        ],
    )

    assert result.exit_code == 2
    assert "--memory-only is not supported with gateway.enabled" in unstyle(result.stderr)
