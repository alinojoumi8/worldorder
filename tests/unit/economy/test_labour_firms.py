from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from polis.agents.types import SKILLS
from polis.config.mechanisms import mechanism_manifest
from polis.config.settings import load_settings
from polis.economy.firms import production_output_micro
from polis.economy.labour import LabourMarket, labour_force, load_occupations
from polis.events.kinds import (
    HIRED,
    OFFER_ACCEPTED,
    OFFER_EXPIRED,
    OFFER_MADE,
    VACANCY_POSTED,
    WAGE_PAID,
    WORK_PERFORMED,
)
from polis.living_city import run_living_city

ROOT = Path(__file__).resolve().parents[3]


def settings():
    return load_settings(ROOT / "configs" / "m2-smoke.yaml")


def test_occupation_catalogue_uses_the_closed_fourteen_skill_vocabulary() -> None:
    catalogue = load_occupations(ROOT / "configs" / "occupations.yaml")
    used = {
        skill
        for occupation in catalogue.values()
        for profile in (
            occupation.requirements,
            occupation.intensity,
            occupation.weights,
        )
        for skill in profile
    }

    assert len(catalogue) == 18
    assert used == set(SKILLS)


def test_production_carry_preserves_fractional_output() -> None:
    carry = 0
    units = 0
    for _ in range(10):
        output = production_output_micro(
            productivity_bp=10_000,
            capital_cents=1_000_000,
            capital_ref_cents=1_000_000,
            effective_labour_bp=7_000,
            beta_capital_bp=0,
            yield_units=1,
        )
        units += (carry + output) // 1_000_000
        carry = (carry + output) % 1_000_000

    assert output == 700_000
    assert units == 7
    assert carry == 0


def test_ex_offender_penalty_never_drops_an_offer_below_minimum_wage() -> None:
    market = object.__new__(LabourMarket)
    market.settings = SimpleNamespace(
        labour=SimpleNamespace(minimum_wage_cents=1_200, offer_ttl_days=7),
        clock=SimpleNamespace(ticks_per_sim_day=24),
    )
    application = SimpleNamespace(
        application_id="app_one",
        vacancy_id="vac_one",
        agent_id="ag_worker",
        status="shortlisted",
    )
    vacancy = SimpleNamespace(
        vacancy_id="vac_one",
        firm_id="fm_one",
        status="open",
        headcount=1,
        occupation="clerk",
    )
    market.economy = SimpleNamespace(
        applications={"app_one": application},
        vacancies={"vac_one": vacancy},
        offers={},
    )
    market.wage_penalty = SimpleNamespace(wage_multiplier=lambda _agent_id: 0.5)
    action = SimpleNamespace(
        actor_id="fm_one",
        params={"application_id": "app_one", "wage_cents": 2_000},
    )

    event = market._make_offer(action, 1, lambda draft: draft)

    assert event is not None
    assert event.payload["wage_cents"] == 1_200


def test_dead_agent_cannot_accept_an_offer_after_estate_closes_accounts() -> None:
    market = object.__new__(LabourMarket)
    offer = SimpleNamespace(
        offer_id="off_dead",
        agent_id="ag_dead",
        firm_id="fm_one",
        status="open",
        made_tick=1,
        expires_tick=10,
    )
    market.population = {"ag_dead": SimpleNamespace(alive=False)}
    market.economy = SimpleNamespace(offers={offer.offer_id: offer})
    action = SimpleNamespace(actor_id="ag_dead", params={"offer_id": offer.offer_id})

    events = market._accept_offer(action, 2, lambda draft: draft)

    assert offer.status == "expired"
    assert len(events) == 1
    assert events[0].kind == OFFER_EXPIRED


def test_payroll_rebanks_a_living_worker_after_account_resolution() -> None:
    market = object.__new__(LabourMarket)
    opened: list[tuple[str, str, str, str, int]] = []

    def open_account(
        code: str,
        owner_id: str,
        owner_kind: str,
        *,
        bank_id: str,
        tick: int,
    ) -> str:
        opened.append((code, owner_id, owner_kind, bank_id, tick))
        return f"dep:{owner_id}@{bank_id}"

    market.economy = SimpleNamespace(
        ledger=SimpleNamespace(
            accounts_of=lambda _owner_id: (),
            is_open=lambda _account_id: False,
            open_account=open_account,
        ),
        banks={
            "bk_cb": SimpleNamespace(
                bank_id="bk_cb",
                is_central=True,
                status="active",
            ),
            "bk_02": SimpleNamespace(
                bank_id="bk_02",
                is_central=False,
                status="active",
            ),
        },
    )

    account = market._deposit_account("ag_worker", 17)

    assert account == "dep:ag_worker@bk_02"
    assert opened == [("dep", "ag_worker", "agent", "bk_02", 17)]


@pytest.mark.asyncio
async def test_mechanical_labour_lifecycle_has_three_tick_floor_and_exact_payroll() -> None:
    result = await run_living_city(settings(), ticks=20)
    economy = result.economy
    assert economy is not None
    by_kind = {
        kind: [event for event in result.events if event.kind == kind]
        for kind in (
            VACANCY_POSTED,
            OFFER_MADE,
            OFFER_ACCEPTED,
            HIRED,
            WORK_PERFORMED,
            WAGE_PAID,
        )
    }

    assert result.report.status == "completed"
    assert all(by_kind.values())
    first_vacancy = min(event.tick for event in by_kind[VACANCY_POSTED])
    first_work = min(event.tick for event in by_kind[WORK_PERFORMED])
    assert first_work - first_vacancy == 3
    assert sum(int(event.payload["gross_cents"]) for event in by_kind[WAGE_PAID]) == sum(
        employment.total_paid_cents for employment in economy.employments.values()
    )
    assert economy.ledger.global_balance_cents() == 0
    assert economy.ledger.materialisation_imbalance_cents() == 0
    assert all(value == 0 for value in economy.ledger.deposit_imbalances().values())

    force = labour_force(
        result.population,
        economy,
        tick=result.report.last_tick,
        search_window_ticks=28,
        retirement_age=65,
    )
    eligible = {
        agent.agent_id for agent in result.population if agent.alive and 18 <= agent.age_years < 65
    }
    assert set(force.employed) | set(force.unemployed) | set(force.nilf) == eligible
    assert not (set(force.employed) & set(force.unemployed))
    assert not (set(force.employed) & set(force.nilf))
    assert not (set(force.unemployed) & set(force.nilf))


@pytest.mark.asyncio
async def test_economy_events_and_ledger_are_deterministic() -> None:
    first = await run_living_city(settings(), ticks=12)
    second = await run_living_city(settings(), ticks=12)
    first_events = [
        (event.tick, event.kind, event.payload)
        for event in first.events
        if 5_000 <= event.kind < 9_000
    ]
    second_events = [
        (event.tick, event.kind, event.payload)
        for event in second.events
        if 5_000 <= event.kind < 9_000
    ]

    assert first_events == second_events
    assert first.economy is not None
    assert second.economy is not None
    assert first.economy.ledger.dump() == second.economy.ledger.dump()


def test_economy_mechanisms_are_declared_and_no_aggregate_match_exists() -> None:
    manifest = mechanism_manifest(settings())
    assert {
        "labour_matching",
        "labour.vacancy_visibility",
        "labour.vacancy_autopost",
        "firms.production_cobb_douglas",
        "firms.productivity_drift",
        "price_setting",
    } <= set(manifest)

    tree = ast.parse((ROOT / "polis" / "economy" / "labour.py").read_text(encoding="utf-8"))
    match_functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("match")
    ]
    assert match_functions == ["match_score_bp"]
