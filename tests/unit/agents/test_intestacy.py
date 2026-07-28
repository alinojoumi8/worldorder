from __future__ import annotations

import random

import pytest

from tests.demography_support import demography_result


@pytest.mark.asyncio
async def test_intestacy_is_exact_for_ten_thousand_odd_and_even_estates() -> None:
    result = await demography_result()
    assert result.demography is not None
    estate = result.demography.institution.estate
    adults = sorted(
        (agent for agent in result.population.alive() if agent.age_years >= 18),
        key=lambda row: row.agent_id,
    )
    decedent, partner, child_a, child_b = adults[:4]
    result.demography.graph.form(
        decedent.agent_id,
        partner.agent_id,
        "partner",
        "test",
        2,
    )
    child_a.mother_id = decedent.agent_id
    child_b.father_id = decedent.agent_id

    stream = random.Random(20260728)
    for _ in range(10_000):
        cents = stream.randrange(0, 10_000_000)
        shares = dict(estate.intestacy_shares(decedent.agent_id, cents))
        assert sum(shares.values()) == cents
        assert set(shares) == {
            partner.agent_id,
            child_a.agent_id,
            child_b.agent_id,
        }
        assert shares[partner.agent_id] in {cents // 2, (cents + 1) // 2}
        assert (
            shares[child_a.agent_id] + shares[child_b.agent_id] == cents - shares[partner.agent_id]
        )
        assert abs(shares[child_a.agent_id] - shares[child_b.agent_id]) <= 1


@pytest.mark.asyncio
async def test_intestacy_escheats_when_no_relative_exists() -> None:
    result = await demography_result()
    assert result.demography is not None
    decedent = min(result.population.alive(), key=lambda row: row.agent_id)
    assert (
        result.demography.institution.estate.intestacy_shares(
            decedent.agent_id,
            123,
        )
        == ()
    )
