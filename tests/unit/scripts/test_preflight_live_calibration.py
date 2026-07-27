from pathlib import Path
from typing import cast

import pytest

from polis.config.settings import load_settings
from polis.living_city import LivingCityResult
from polis.llm.providers.base import ProviderTransient
from scripts.preflight_live_calibration import percentile, run_resumable


def test_percentile_is_deterministic_and_bounded() -> None:
    values = [30, 10, 20, 40]
    assert percentile(values, 0) == 10
    assert percentile(values, 0.5) == 20
    assert percentile(values, 0.95) == 30
    assert percentile(values, 1) == 40
    assert percentile([], 0.5) == 0


@pytest.mark.asyncio
async def test_live_calibration_resumes_transient_failures_from_cache() -> None:
    calls = 0
    expected = cast(LivingCityResult, object())

    async def runner(settings, *, collect_events, lane_concurrency_overrides):
        nonlocal calls
        del settings, collect_events
        assert lane_concurrency_overrides == {"reasoning": 2}
        calls += 1
        if calls < 3:
            raise ProviderTransient("retry")
        return expected

    result = await run_resumable(
        load_settings(Path("configs/smoke.yaml")),
        attempts=3,
        retry_delay_seconds=0,
        concurrency_overrides={"reasoning": 2},
        runner=runner,
    )

    assert result is expected
    assert calls == 3
