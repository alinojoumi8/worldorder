from __future__ import annotations

from polis.agents.types import SKILLS, AgentState, Needs, ReflexProfile, Traits
from polis.economy.labour import Occupation, match_score_bp
from polis.economy.state import VacancyState


def agent_with_skill(level: float) -> AgentState:
    return AgentState(
        agent_id="ag_fixture",
        display_name="Fixture",
        age_years=30,
        traits=Traits(*(0.5 for _ in range(10))),
        needs=Needs(),
        skills={skill: level for skill in SKILLS},
        home_place_id="pl_home",
        education_level="secondary",
        employment_status="unemployed",
        reflex_profile=ReflexProfile(*(0.5 for _ in range(6))),
    )


def occupation() -> Occupation:
    return Occupation(
        "operator",
        ("industrial",),
        {"manual": 5_000, "operations": 5_000},
        {"manual": 10_000, "operations": 10_000},
        {"manual": 5_000, "operations": 5_000},
    )


def vacancy() -> VacancyState:
    return VacancyState(
        "vac_fixture",
        "fm_fixture",
        "operator",
        {"manual": 5_000, "operations": 5_000},
        150_000,
        1,
        1,
        31,
        "ds_01",
    )


def test_match_score_matches_hand_arithmetic_and_is_skill_monotone() -> None:
    low = agent_with_skill(0.25)
    high = agent_with_skill(0.40)

    assert match_score_bp(low, vacancy(), occupation()) == 5_300
    assert match_score_bp(high, vacancy(), occupation()) > match_score_bp(
        low,
        vacancy(),
        occupation(),
    )


def test_recency_penalty_is_capped_and_ablatable() -> None:
    agent = agent_with_skill(0.25)
    penalised = match_score_bp(
        agent,
        vacancy(),
        occupation(),
        unemployed_ticks=10_000,
        recency_penalty=True,
    )
    unpenalised = match_score_bp(
        agent,
        vacancy(),
        occupation(),
        unemployed_ticks=10_000,
        recency_penalty=False,
    )

    assert unpenalised - penalised == 1_500
