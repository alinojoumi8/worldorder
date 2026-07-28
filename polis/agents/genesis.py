from __future__ import annotations

from typing import Final

import numpy as np

from polis.agents.state import AgentPopulation
from polis.agents.types import (
    SKILLS,
    AgentState,
    EducationLevel,
    EmploymentStatus,
    Needs,
    ReflexProfile,
    Traits,
)
from polis.config.settings import PopulationSettings
from polis.kernel.rng import RngRegistry
from polis.world.api import Location, World

_TRAIT_NAMES: Final = tuple(Traits.__dataclass_fields__)
_TRAIT_DECIMALS: Final = 12
_CORRELATION: Final = np.array(
    [
        [1.00, 0.12, 0.18, 0.08, -0.10, 0.14, -0.08, 0.05, 0.16, 0.02],
        [0.12, 1.00, 0.05, 0.18, -0.25, -0.12, -0.28, 0.14, 0.24, 0.22],
        [0.18, 0.05, 1.00, 0.15, -0.08, 0.20, 0.05, 0.10, 0.20, 0.00],
        [0.08, 0.18, 0.15, 1.00, -0.18, -0.16, -0.10, 0.42, -0.02, 0.20],
        [-0.10, -0.25, -0.08, -0.18, 1.00, -0.16, 0.18, -0.08, -0.05, -0.12],
        [0.14, -0.12, 0.20, -0.16, -0.16, 1.00, 0.22, -0.12, 0.25, -0.08],
        [-0.08, -0.28, 0.05, -0.10, 0.18, 0.22, 1.00, -0.10, -0.08, -0.08],
        [0.05, 0.14, 0.10, 0.42, -0.08, -0.12, -0.10, 1.00, -0.02, 0.24],
        [0.16, 0.24, 0.20, -0.02, -0.05, 0.25, -0.08, -0.02, 1.00, 0.02],
        [0.02, 0.22, 0.00, 0.20, -0.12, -0.08, -0.08, 0.24, 0.02, 1.00],
    ],
    dtype=np.float64,
)


def _traits(rng: RngRegistry, agent_id: str) -> Traits:
    stream = rng.numpy("agent.traits", agent_id)
    values = np.clip(
        stream.multivariate_normal(np.full(10, 0.5), _CORRELATION * 0.0225),
        0.0,
        1.0,
    )
    # LAPACK implementations can differ in the final few bits of the decomposition
    # used by multivariate_normal. Quantise at the simulation boundary so genesis,
    # event payloads, and downstream decisions remain platform-stable.
    quantized = (round(float(value), _TRAIT_DECIMALS) for value in values)
    return Traits(**dict(zip(_TRAIT_NAMES, quantized, strict=True)))


def _age(rng: RngRegistry, agent_id: str) -> float:
    stream = rng.get("agent.age", agent_id)
    bucket = stream.random()
    if bucket < 0.18:
        return float(stream.randint(0, 17))
    if bucket < 0.73:
        return float(stream.randint(18, 64))
    return float(stream.randint(65, 88))


def _education(age: float, conscientiousness: float) -> EducationLevel:
    if age < 6:
        return "none"
    if age < 14:
        return "primary"
    if age < 18:
        return "secondary"
    if age < 22:
        return "tertiary"
    if conscientiousness > 0.82 and age >= 25:
        return "graduate"
    return "tertiary" if conscientiousness > 0.46 else "secondary"


def _employment(age: float) -> EmploymentStatus:
    if age < 16:
        return "child"
    if age < 23:
        return "student"
    if age >= 67:
        return "retired"
    return "unemployed"


def _reflex(traits: Traits) -> ReflexProfile:
    return ReflexProfile(
        temperature=round(0.15 + 0.35 * (1 - traits.conscientiousness), 6),
        sleep_weight=round(0.8 + 0.4 * traits.conscientiousness, 6),
        eat_weight=round(0.9 + 0.2 * (1 - traits.time_preference), 6),
        social_weight=round(0.5 + traits.extraversion, 6),
        study_weight=round(0.4 + 0.8 * traits.conscientiousness, 6),
        explore_weight=round(0.3 + 0.8 * traits.openness, 6),
    )


def derive_reflex_profile(traits: Traits) -> ReflexProfile:
    return _reflex(traits)


def stage_for_age(age_years: float) -> str:
    if age_years < 6:
        return "infant"
    if age_years < 12:
        return "child"
    if age_years < 18:
        return "adolescent"
    if age_years < 65:
        return "adult"
    return "elder"


def advance_age(
    agent: AgentState,
    elapsed_sim_days: float,
    *,
    demographic_acceleration: float,
    days_per_sim_year: int,
) -> float:
    if elapsed_sim_days < 0:
        raise ValueError("elapsed_sim_days must be non-negative")
    agent.age_years += elapsed_sim_days * demographic_acceleration / days_per_sim_year
    return agent.age_years


def population_mean_traits(population: AgentPopulation) -> Traits:
    rows = population.alive()
    if not rows:
        raise ValueError("cannot compute trait means for an empty population")
    values = {
        name: sum(getattr(agent.traits, name) for agent in rows) / len(rows)
        for name in Traits.__dataclass_fields__
    }
    return Traits(**values)


def inherit_traits(
    mother: AgentState,
    father: AgentState,
    rng: RngRegistry,
    child_id: str,
) -> Traits:
    stream = rng.numpy("demog.traits", child_id)
    values = {}
    for name in Traits.__dataclass_fields__:
        centre = (getattr(mother.traits, name) + getattr(father.traits, name)) / 2
        values[name] = round(
            min(1.0, max(0.0, centre + float(stream.normal(0.0, 0.05)))),
            6,
        )
    return Traits(**values)


def mark_dead(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("C20 EstateSettler is the only supported death path")


def generate_agents(
    settings: PopulationSettings,
    world: World,
    rng: RngRegistry,
) -> AgentPopulation:
    homes = world.places_of_type("home")
    shelters = world.places_of_type("shelter")
    fallback = shelters or world.places_of_type("park") or world.places
    residences = homes or fallback
    agents: dict[str, AgentState] = {}
    for index in range(settings.initial_agents):
        agent_id = f"ag_{index:04d}"
        traits = _traits(rng, agent_id)
        age = _age(rng, agent_id)
        home = residences[index % len(residences)]
        skills = {
            skill: round(
                min(
                    1.0,
                    max(
                        0.0,
                        0.08
                        + 0.55 * traits.conscientiousness
                        + rng.get("agent.skills", f"{agent_id}|{skill}").random() * 0.2,
                    ),
                ),
                6,
            )
            for skill in SKILLS
        }
        agent = AgentState(
            agent_id=agent_id,
            display_name=f"Citizen {index:04d}",
            age_years=age,
            traits=traits,
            needs=Needs(),
            skills=skills,
            home_place_id=home.place_id,
            education_level=_education(age, traits.conscientiousness),
            employment_status=_employment(age),
            reflex_profile=_reflex(traits),
        )
        agents[agent_id] = agent
        world.locations[agent_id] = Location(
            home.place_id,
            home.district_id,
            home.x,
            home.y,
        )
    world.freeze_occupancy()
    return AgentPopulation(agents)


initialise_population = generate_agents
