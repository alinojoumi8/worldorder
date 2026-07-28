from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.demography_support import demography_result


@dataclass(slots=True)
class IncomePort:
    values: dict[str, int]

    def income_cents(self, agent_id: str, tick: int) -> int:
        del tick
        return self.values.get(agent_id, 0)


@pytest.mark.asyncio
async def test_fertility_intent_and_acceleration_scale_the_hazard() -> None:
    result = await demography_result()
    assert result.demography is not None
    fertility = result.demography.institution.fertility
    mother = next(
        agent
        for agent in result.population.alive()
        if fertility.cfg.fertility.band[0] <= agent.age_years <= fertility.cfg.fertility.band[1]
    )
    mother.fertility_intent_tick = None
    fertility.hazard_mode = "uniform"
    fertility.demographic_acceleration = 1.0
    base = fertility.hazard(mother, 2)
    mother.fertility_intent_tick = 2
    intended = fertility.hazard(mother, 2)
    fertility.demographic_acceleration = 2.0
    accelerated = fertility.hazard(mother, 2)

    assert intended == pytest.approx(base * (1 + fertility.cfg.fertility.iota_intent))
    assert accelerated == pytest.approx(intended * 2)


@pytest.mark.asyncio
async def test_uniform_ablation_removes_income_ranking_only() -> None:
    result = await demography_result()
    assert result.demography is not None
    fertility = result.demography.institution.fertility
    households = result.demography.households
    mother = next(
        agent
        for agent in result.population.alive()
        if fertility.cfg.fertility.band[0] <= agent.age_years <= fertility.cfg.fertility.band[1]
    )
    incomes = {agent.agent_id: 0 for agent in result.population}
    households.employment = IncomePort(incomes)
    fertility.hazard_mode = "uniform"
    low = fertility.hazard(mother, 2)
    incomes[mother.agent_id] = 10_000_000
    high = fertility.hazard(mother, 2)

    assert high == low
