from __future__ import annotations

import pytest

from polis.economy.fiscal import government_transfer_legs
from polis.economy.ledger import parse_account_id
from polis.events.kinds import HOUSEHOLD_FORMED, UNION_DISSOLVED, UNION_FORMED
from tests.demography_support import demography_result


class _Income:
    def __init__(self, agent_id: str, cents: int) -> None:
        self.agent_id = agent_id
        self.cents = cents

    def income_cents(self, agent_id: str, tick: int) -> int:
        del tick
        return self.cents if agent_id == self.agent_id else 0


@pytest.mark.asyncio
async def test_unilateral_dissolution_splits_only_post_union_gains() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    registry = result.demography.courtships
    households = result.demography.households
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda agent: agent.agent_id,
    )
    a = adults[0]
    b = next(agent for agent in adults[1:] if agent.household_id != a.household_id)

    registry.court(a.agent_id, b.agent_id, 2)
    registry.court(b.agent_id, a.agent_id, 2)
    registry.propose_union(a.agent_id, b.agent_id, 2)
    formed_events = registry.propose_union(b.agent_id, a.agent_id, 2)
    union_event = next(event for event in formed_events if event.kind == UNION_FORMED)
    household = households.of(a.agent_id)
    partner_household = households.of(b.agent_id)
    assert household is not None
    assert partner_household is not None
    assert household.household_id == partner_household.household_id
    principal = {
        agent_id: result.economy.ledger.liquid(agent_id) for agent_id in (a.agent_id, b.agent_id)
    }

    treasury = next(
        value
        for value in result.economy.ledger.accounts_of("gv_treasury")
        if parse_account_id(value)[0] == "dep"
    )
    recipient = next(
        value
        for value in result.economy.ledger.accounts_of(a.agent_id)
        if parse_account_id(value)[0] == "dep"
    )
    result.economy.ledger.post_transaction(
        government_transfer_legs(recipient, 1_001, result.economy),
        tick=2,
        cause=union_event,
        allow_negative=frozenset({treasury}),
    )
    result.economy.sync_denormalised(result.population)
    result.economy.ledger.commit_tick(2)
    assert households.ledger is not None
    expected_gains = households.ledger.allocate(
        1_001,
        ((a.agent_id, 1), (b.agent_id, 1)),
    )

    events = registry.dissolve_union(a.agent_id, b.agent_id, 3)

    assert any(event.kind == UNION_DISSOLVED for event in events)
    assert sum(event.kind == HOUSEHOLD_FORMED for event in events) == 2
    assert result.demography.graph.live_partner(a.agent_id) is None
    assert result.economy.ledger.liquid(a.agent_id) == (
        principal[a.agent_id] + expected_gains[a.agent_id]
    )
    assert result.economy.ledger.liquid(b.agent_id) == (
        principal[b.agent_id] + expected_gains[b.agent_id]
    )


@pytest.mark.asyncio
async def test_higher_income_custody_and_coin_flip_are_deterministic() -> None:
    result = await demography_result()
    assert result.demography is not None
    households = result.demography.households
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda agent: agent.agent_id,
    )
    a, b = adults[:2]
    higher_income = min(
        (a.agent_id, b.agent_id),
        key=lambda agent_id: (-households.income_cents_for(agent_id, 2), agent_id),
    )
    household_id = a.household_id or "hh_test"

    assert households.custody_parent(a.agent_id, b.agent_id, household_id, 2) == higher_income
    households.custody_mode = "coin_flip"
    first = households.custody_parent(a.agent_id, b.agent_id, household_id, 2)
    second = households.custody_parent(a.agent_id, b.agent_id, household_id, 2)

    assert first == second
    assert first in {a.agent_id, b.agent_id}


@pytest.mark.asyncio
async def test_agent_leaves_home_when_crossing_independence_threshold() -> None:
    result = await demography_result()
    assert result.demography is not None
    households = result.demography.households
    candidate = next(
        agent
        for agent in result.population.alive()
        if (household := households.of(agent.agent_id)) is not None
        and len(household.member_ids) > 1
    )
    households.employment = _Income(
        candidate.agent_id,
        households.cfg.independence_threshold_cents,
    )
    original_household_id = candidate.household_id
    candidate.age_years = 18.0

    events = households.advance_independence(2, age_step_years=0.1)

    assert events
    assert candidate.household_id != original_household_id
    new_household = households.of(candidate.agent_id)
    assert new_household is not None
    assert new_household.member_ids == (candidate.agent_id,)
