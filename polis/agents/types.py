from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Final, Literal

Skill = Literal[
    "manual",
    "operations",
    "sales",
    "finance",
    "engineering",
    "research",
    "law",
    "medicine",
    "teaching",
    "writing",
    "design",
    "management",
    "negotiation",
    "persuasion",
]
SKILLS: Final[tuple[Skill, ...]] = (
    "manual",
    "operations",
    "sales",
    "finance",
    "engineering",
    "research",
    "law",
    "medicine",
    "teaching",
    "writing",
    "design",
    "management",
    "negotiation",
    "persuasion",
)
CognitionMode = Literal["reflex", "deliberate", "reflect"]
EducationLevel = Literal["none", "primary", "secondary", "tertiary", "graduate"]
EmploymentStatus = Literal[
    "child",
    "student",
    "employed",
    "unemployed",
    "self_employed",
    "retired",
    "dead",
]


@dataclass(frozen=True, slots=True)
class Traits:
    openness: float
    conscientiousness: float
    extraversion: float
    agreeableness: float
    neuroticism: float
    risk_tolerance: float
    time_preference: float
    altruism: float
    ambition: float
    honesty: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(slots=True)
class Needs:
    energy: float = 0.8
    hunger: float = 0.8
    security: float = 0.7
    social: float = 0.6
    esteem: float = 0.6
    purpose: float = 0.6

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def clamp(self) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, min(1.0, max(0.0, float(getattr(self, name)))))


@dataclass(frozen=True, slots=True)
class ReflexProfile:
    temperature: float
    sleep_weight: float
    eat_weight: float
    social_weight: float
    study_weight: float
    explore_weight: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(slots=True)
class AgentState:
    agent_id: str
    display_name: str
    age_years: float
    traits: Traits
    needs: Needs
    skills: dict[Skill, float]
    home_place_id: str
    education_level: EducationLevel
    employment_status: EmploymentStatus
    reflex_profile: ReflexProfile
    health: float = 0.9
    wealth_cents: int = 0
    reputation: float = 0.5
    goals: list[str] = field(default_factory=list)
    identity_summary: str = ""
    cognition_mode: CognitionMode = "reflex"
    last_observation_hash: str = ""
    expectation_features: frozenset[str] = frozenset()
    seen_situations: set[str] = field(default_factory=set)
    last_reflection_tick: int = -1_000_000
    importance_since_reflection: float = 0.0
    alive: bool = True

    @property
    def wellbeing(self) -> float:
        need_mean = sum(self.needs.as_dict().values()) / 6
        return round(100 * (0.65 * need_mean + 0.35 * self.health), 2)

    def decay_needs(self, ticks_per_day: int) -> None:
        scale = 1 / ticks_per_day
        self.needs.energy -= 0.12 * scale
        self.needs.hunger -= 0.15 * scale
        self.needs.security -= 0.05 * scale
        self.needs.social -= 0.3 * scale
        self.needs.esteem -= 0.1 * scale
        self.needs.purpose -= 0.05 * scale
        self.needs.clamp()

    def restore(self, need: str, amount: float) -> None:
        if need not in self.needs.__dataclass_fields__:
            raise ValueError(f"unknown need: {need}")
        setattr(self.needs, need, getattr(self.needs, need) + amount)
        self.needs.clamp()
