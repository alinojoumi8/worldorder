from __future__ import annotations

import pytest

from polis.economy.fiscal import treasury_account
from polis.economy.invariants import check_money
from polis.events.kinds import CHILD_COST_CHARGED, STATE_CARE_STARTED
from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_child_costs_use_real_balanced_ledger_transactions() -> None:
    result = await demography_result()
    assert result.economy is not None
    events = tuple(event for event in result.events if event.kind == CHILD_COST_CHARGED)
    assert events
    assert any(event.payload["txn_id"] is not None for event in events)
    assert check_money(result.economy.ledger, result.economy).invariant_id == "INV-MONEY"


@pytest.mark.asyncio
async def test_state_care_moves_a_child_to_a_shelter_household() -> None:
    result = await demography_result()
    assert result.demography is not None
    child = next(agent for agent in result.population.alive() if agent.age_years < 18)
    events = result.demography.institution.child_costs.state_intervention(child.agent_id, 2)
    household = result.demography.households.of(child.agent_id)

    assert household is not None
    assert household.tenure == "shelter"
    assert any(event.kind == STATE_CARE_STARTED for event in events)


@pytest.mark.asyncio
async def test_state_care_and_child_benefit_are_real_government_spending() -> None:
    result = await demography_result()
    assert result.demography is not None
    assert result.economy is not None
    child = next(agent for agent in result.population.alive() if agent.age_years < 18)
    result.demography.institution.child_costs.state_intervention(child.agent_id, 2)
    treasury = treasury_account(result.economy)
    before = result.economy.ledger.balance(treasury)

    events = result.demography.institution.child_costs.charge(3)

    household = result.demography.households.of(child.agent_id)
    assert household is not None
    charged = next(
        event
        for event in events
        if event.kind == CHILD_COST_CHARGED
        and event.payload["household_id"] == household.household_id
    )
    assert charged.payload["txn_id"] is not None
    assert household.arrears_cents == 0
    assert result.economy.ledger.balance(treasury) < before
    assert check_money(result.economy.ledger, result.economy).invariant_id == "INV-MONEY"
