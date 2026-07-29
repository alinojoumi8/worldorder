from __future__ import annotations

from dataclasses import replace

import pytest

from polis.events.kinds import MIGRATION_IN, MIGRATION_OUT, PAYROLL_SHORTFALL
from polis.gateway.sdk.canonical import agent_id_for
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


@pytest.mark.asyncio
async def test_external_admission_uses_the_native_immigrant_embodiment() -> None:
    result = await demography_result()
    assert result.demography is not None
    migration = result.demography.institution.migration
    pubkey = "12" * 32
    agent_id = agent_id_for(pubkey)

    agent, events = migration.admit_external(
        agent_id=agent_id,
        pubkey=pubkey,
        display_name="Nikos",
        tick=2,
    )

    assert agent.kind == "external"
    assert agent.pubkey == pubkey
    assert agent.household_id is not None
    assert agent_id in result.world.locations
    assert any(event.kind == MIGRATION_IN for event in events)
    assert result.economy is not None
    assert any(account.owner_id == agent_id for account in result.economy.ledger.accounts())


@pytest.mark.asyncio
async def test_external_admission_rejects_an_identity_not_derived_from_the_key() -> None:
    result = await demography_result()
    assert result.demography is not None
    migration = result.demography.institution.migration
    pubkey = "12" * 32

    with pytest.raises(ValueError, match="derived from its public key"):
        migration.admit_external(
            agent_id=f"ag_{'34' * 32}",
            pubkey=pubkey,
            display_name="Impostor",
            tick=2,
        )


@pytest.mark.asyncio
async def test_agent_state_enforces_external_identity_combinations() -> None:
    result = await demography_result()
    native = next(iter(result.population.alive()))
    pubkey = "12" * 32
    agent_id = agent_id_for(pubkey)

    external = replace(native, agent_id=agent_id, kind="external", pubkey=pubkey)
    assert external.agent_id == agent_id

    with pytest.raises(ValueError, match="require a public key"):
        replace(native, agent_id=agent_id, kind="external", pubkey=None)
    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        replace(native, agent_id=agent_id, kind="external", pubkey="AB" * 32)
    with pytest.raises(ValueError, match="derived from its public key"):
        replace(native, agent_id=f"ag_{'34' * 32}", kind="external", pubkey=pubkey)
    with pytest.raises(ValueError, match="native agents"):
        replace(native, pubkey=pubkey)
    with pytest.raises(ValueError, match="agent kind"):
        replace(native, kind="unknown")
