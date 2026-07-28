from __future__ import annotations

import pytest

from polis.events.kinds import MIGRATION_IN
from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_monthly_migrants_have_numeric_ids_and_zero_ties() -> None:
    result = await demography_result(ticks=31)
    assert result.demography is not None
    migrant_ids = tuple(
        str(event.payload["agent_id"]) for event in result.events if event.kind == MIGRATION_IN
    )
    assert migrant_ids
    for agent_id in migrant_ids:
        assert agent_id.removeprefix("ag_").isdigit()
        assert result.demography.graph.strong_ties(agent_id, 0.0) == ()
