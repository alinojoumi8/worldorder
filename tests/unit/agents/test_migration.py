from __future__ import annotations

import pytest

from polis.events.kinds import MIGRATION_IN, MIGRATION_OUT, PAYROLL_SHORTFALL
from polis.society.graph import Tie
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


@pytest.mark.asyncio
async def test_emigration_reports_ties_severed_from_the_settlement() -> None:
    result = await demography_result()
    assert result.demography is not None
    migration = result.demography.institution.migration
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda row: row.agent_id,
    )
    emigrant, partner = adults[:2]
    result.demography.graph.repo.put(
        Tie(
            emigrant.agent_id,
            partner.agent_id,
            "partner",
            0.8,
            0.5,
            0.8,
            2,
            None,
            2,
        )
    )

    events = migration.depart(emigrant.agent_id, 2)
    migrated = next(event for event in events if event.kind == MIGRATION_OUT)

    assert migrated.payload["hazard_components"]["strong_ties"] == 1
    assert migrated.payload["ties_severed"] == 1


@pytest.mark.asyncio
async def test_emigration_clears_and_reports_terminal_wage_claims() -> None:
    result = await demography_result(ticks=5)
    assert result.demography is not None and result.economy is not None
    migration = result.demography.institution.migration
    employment = next(
        row
        for row in result.economy.employments.values()
        if row.ended_tick is None and migration.estate.estate.case_for(row.agent_id, 6) != "A"
    )
    employment.accrued_wage_cents = 1_234

    events = migration.depart(employment.agent_id, 6)

    shortfall = next(event for event in events if event.kind == PAYROLL_SHORTFALL)
    assert shortfall.payload["accrued_claim_cents"] == 1_234
    assert employment.accrued_wage_cents == 0


@pytest.mark.asyncio
async def test_migration_arrival_uses_default_traits_when_no_residents_remain() -> None:
    result = await demography_result()
    assert result.demography is not None
    migration = result.demography.institution.migration
    for agent in result.population:
        agent.alive = False

    events = migration.arrive(30)

    assert any(event.kind == MIGRATION_IN for event in events)
