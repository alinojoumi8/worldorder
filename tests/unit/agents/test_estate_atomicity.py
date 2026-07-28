from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

import pytest

from polis.events.kinds import PREGNANCY_ENDED
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
    fertility = result.demography.institution.fertility
    mother = next(
        agent
        for agent in result.population.alive()
        if agent.agent_id != decedent.agent_id
        and fertility.cfg.fertility.band[0] <= agent.age_years <= fertility.cfg.fertility.band[1]
    )
    fertility.conceive(mother.agent_id, decedent.agent_id, 1)
    before_pregnancies = copy.deepcopy(fertility.pregnancies)
    before_economy = copy.deepcopy(result.economy.dump())
    before_agent = copy.deepcopy(decedent)
    before_staged = settler.log.staged()
    settler.estate = FailAfterDelegation(settler.estate)

    with pytest.raises(RuntimeError, match="injected settlement failure"):
        settler.settle(decedent.agent_id, "mortality", 2)

    assert result.economy.dump() == before_economy
    assert result.population[decedent.agent_id] == before_agent
    assert fertility.pregnancies == before_pregnancies
    assert settler.log.staged() == before_staged


@pytest.mark.asyncio
async def test_parent_death_terminates_an_active_pregnancy() -> None:
    result = await demography_result()
    assert result.demography is not None
    settler = result.demography.institution.estate
    fertility = result.demography.institution.fertility
    father = next(
        agent
        for agent in result.population.alive()
        if settler.estate.case_for(agent.agent_id, 2) == "C"
    )
    mother = next(
        agent
        for agent in result.population.alive()
        if agent.agent_id != father.agent_id
        and fertility.cfg.fertility.band[0] <= agent.age_years <= fertility.cfg.fertility.band[1]
    )
    fertility.conceive(mother.agent_id, father.agent_id, 1)

    _estate, events = settler.settle(father.agent_id, "mortality", 2)

    assert mother.agent_id not in fertility.pregnancies
    ended = next(event for event in events if event.kind == PREGNANCY_ENDED)
    assert ended.payload["outcome"] == "loss"
