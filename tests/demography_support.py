from __future__ import annotations

from pathlib import Path

from polis.config.settings import load_settings
from polis.living_city import LivingCityResult, run_living_city


async def demography_result(*, ticks: int = 1) -> LivingCityResult:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={"run": {"ticks": ticks}},
    )
    result = await run_living_city(settings)
    assert result.report.status == "completed"
    assert result.demography is not None
    assert result.economy is not None
    return result
