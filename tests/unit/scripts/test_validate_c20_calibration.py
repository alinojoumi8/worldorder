from __future__ import annotations

from types import SimpleNamespace

import pytest

from polis.events.kinds import AGENT_BORN, MIGRATION_IN
from scripts.validate_c20_calibration import DemographyCountingSink


@pytest.mark.asyncio
async def test_calibration_separates_genesis_from_lifecycle_births() -> None:
    sink = DemographyCountingSink(ticks_per_year=365, progress_ticks=0)

    await sink.append(  # type: ignore[arg-type]
        (
            SimpleNamespace(kind=AGENT_BORN, tick=0),
            SimpleNamespace(kind=AGENT_BORN, tick=30),
            SimpleNamespace(kind=MIGRATION_IN, tick=30),
        )
    )

    assert sink.genesis_births == 1
    assert sink.counts[AGENT_BORN] == 2
    assert sink.yearly[0][AGENT_BORN] == 1
    assert sink.yearly[0][MIGRATION_IN] == 1
