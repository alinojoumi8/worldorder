from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.settings import load_settings
from polis.research.gates import gate_report_bytes, gate_run
from polis.simulation import run_id_for
from polis.store.engine import Database
from polis.store.living_city import run_persistent


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gate_run_reads_log_and_ledger_and_is_deterministic() -> None:
    settings = load_settings(
        Path("configs/m2-smoke.yaml"),
        overrides={
            "run": {
                "name": "gate-run-integration",
                "seed": 8_124_001,
                "ticks": 3,
                "scale": 8,
            },
            "population": {"initial_agents": 8},
        },
    )
    run_id = run_id_for(settings)
    engine = await Database.open(settings.store, role="engine")
    await engine.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
    await engine.close()
    try:
        await run_persistent(settings)
        reader = await Database.open(settings.store, role="reader")
        try:
            first = await gate_run(reader, run_id)
            second = await gate_run(reader, run_id)
        finally:
            await reader.close()

        gates = {result["id"]: result for result in first["gates"]}
        assert gate_report_bytes(first) == gate_report_bytes(second)
        assert set(gates) == {"V1", "V2", "V3", "V4"}
        assert gates["V1"]["verdict"] == "n/a"
        assert gates["V2"]["verdict"] == "pass"
        assert gates["V2"]["statistic"]["ticks_checked"] == 4
        assert gates["V2"]["statistic"]["expected_ticks_checked"] == 4
        assert gates["V3"]["verdict"] == "fail"
        assert gates["V4"]["verdict"] == "fail"
        assert first["blocking_failures"] == ["V3", "V4"]
    finally:
        engine = await Database.open(settings.store, role="engine")
        await engine.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        await engine.close()
