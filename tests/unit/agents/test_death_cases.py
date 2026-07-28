from __future__ import annotations

import pytest

from polis.economy.state import EconomyWorldState
from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_case_d_transfers_firm_and_security_interests() -> None:
    result = await demography_result()
    assert result.demography is not None and result.economy is not None
    settler = result.demography.institution.estate
    decedent = next(
        agent
        for agent in result.population.alive()
        if settler.estate.case_for(agent.agent_id, 2) == "D"
    )
    owned_firms = tuple(
        firm.firm_id
        for firm in result.economy.firms.values()
        if firm.founder_id == decedent.agent_id
    )

    estate, _events = settler.settle(decedent.agent_id, "mortality", 2)
    view = EconomyWorldState(result.population, result.economy, ticks_per_year=365)

    assert owned_firms
    assert all(
        result.economy.firms[firm_id].founder_id != decedent.agent_id for firm_id in owned_firms
    )
    assert not any(
        row.holder_id == decedent.agent_id and row.shares > 0
        for row in result.economy.ventures.cap_table.values()
    )
    assert estate.escrow_account_id
    assert view.share_invariant_failures() == {}
    assert view.cap_table_invariant_failures() == {}
