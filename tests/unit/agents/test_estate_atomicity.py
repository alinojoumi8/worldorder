from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

import pytest

from polis.events.types import Event
from tests.demography_support import demography_result


class FailAfterDelegation:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate

    def case_for(self, agent_id: str, tick: int) -> Any:
        return self.delegate.case_for(agent_id, tick)

    def estate_account_id(self, agent_id: str, tick: int) -> str:
        return self.delegate.estate_account_id(agent_id, tick)

    def gross_cents(self, agent_id: str) -> int:
        return self.delegate.gross_cents(agent_id)

    def open_order_count(self, agent_id: str) -> int:
        return self.delegate.open_order_count(agent_id)

    def open_loan_count(self, agent_id: str) -> int:
        return self.delegate.open_loan_count(agent_id)

    def settle_death(
        self,
        agent_id: str,
        tick: int,
        *,
        heirs: Sequence[tuple[str, int]] | None,
        ctx: Any,
    ) -> Sequence[Event]:
        self.delegate.settle_death(agent_id, tick, heirs=heirs, ctx=ctx)
        raise RuntimeError("injected settlement failure")


@pytest.mark.asyncio
async def test_estate_failure_rolls_back_events_economy_and_agent() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    decedent = next(
        agent
        for agent in result.population.alive()
        if settler.estate.case_for(agent.agent_id, 2) == "C"
    )
    before_economy = copy.deepcopy(result.economy.dump())
    before_agent = copy.deepcopy(decedent)
    before_staged = settler.log.staged()
    settler.estate = FailAfterDelegation(settler.estate)

    with pytest.raises(RuntimeError, match="injected settlement failure"):
        settler.settle(decedent.agent_id, "mortality", 2)

    assert result.economy.dump() == before_economy
    assert result.population[decedent.agent_id] == before_agent
    assert settler.log.staged() == before_staged
