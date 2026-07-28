from __future__ import annotations

from typing import Any

import pytest

from polis.agents.demography import EstateSettler
from polis.agents.genesis import mark_dead
from tests.demography_support import demography_result


class RecordingEstatePort:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.calls: list[tuple[str, int]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def settle_death(
        self,
        decedent_id: str,
        tick: int,
        *,
        heirs: tuple[tuple[str, int], ...],
        ctx: object,
    ) -> tuple[object, ...]:
        self.calls.append((decedent_id, tick))
        return tuple(
            self.inner.settle_death(  # type: ignore[union-attr]
                decedent_id,
                tick,
                heirs=heirs,
                ctx=ctx,
            )
        )


@pytest.mark.asyncio
async def test_c20_delegates_the_economic_waterfall_exactly_once_at_runtime() -> None:
    result = await demography_result()
    assert result.demography is not None
    original = result.demography.institution.estate
    port = RecordingEstatePort(original.estate)
    settler = EstateSettler(
        log=original.log,
        clock=original.clock,
        rng=original.rng,
        world=original.world,
        agents=original.agents,
        households=original.households,
        estate=port,  # type: ignore[arg-type]
        ledger=original.ledger,
        housing=original.housing,
        graph=original.graph,
        memories=original.memories,
        fertility=original.fertility,
        cfg=original.cfg,
    )
    decedent = next(
        agent
        for agent in result.population.alive()
        if original.estate.case_for(agent.agent_id, 2) == "C"
    )

    settler.settle(decedent.agent_id, "mortality", 2)

    assert port.calls == [(decedent.agent_id, 2)]


def test_m1_mark_dead_path_is_disabled_at_m5() -> None:
    with pytest.raises(RuntimeError, match="EstateSettler"):
        mark_dead(None, None, None, None)  # type: ignore[arg-type]
