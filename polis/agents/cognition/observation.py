from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from polis.agents.state import AgentPopulation
from polis.config.canon import canonical_bytes, sha256_hex
from polis.world.api import World


@dataclass(frozen=True, slots=True)
class SelfView:
    needs: dict[str, float]
    health: float
    wealth_cents: int
    employment_status: str
    education_level: str
    skills: dict[str, float]
    goals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlaceView:
    place_id: str | None
    name: str
    type: str
    district_id: str
    in_transit: bool
    eta_ticks: int | None
    legal_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentBrief:
    agent_id: str
    display_name: str
    relationship: str


@dataclass(frozen=True, slots=True)
class Observation:
    agent_id: str
    tick: int
    sim_time: datetime
    self_state: SelfView
    place: PlaceView
    co_located: tuple[AgentBrief, ...]
    inbox: tuple[dict[str, str], ...]
    feed: tuple[dict[str, str], ...]
    news: tuple[dict[str, str], ...]
    market: None
    employer: None
    offers: tuple[dict[str, str], ...]
    obligations: tuple[dict[str, str], ...]
    stakes: float
    digest_features: frozenset[str]
    digest_hash: str

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["sim_time"] = self.sim_time.isoformat()
        row["digest_features"] = sorted(self.digest_features)
        return row


def _legal_actions(place_type: str, in_transit: bool) -> tuple[str, ...]:
    if in_transit:
        return ("IDLE", "MOVE_TO", "NULL_ACTION")
    actions = ["IDLE", "MOVE_TO", "NULL_ACTION"]
    if place_type in {"home", "shelter", "hospital"}:
        actions.append("SLEEP")
    if place_type in {"home", "shop", "bar", "shelter"}:
        actions.append("EAT")
    if place_type in {"home", "school", "university"}:
        actions.append("STUDY")
    return tuple(sorted(actions))


def build_observations(
    population: AgentPopulation,
    world: World,
    *,
    tick: int,
    sim_time: datetime,
) -> dict[str, Observation]:
    observations: dict[str, Observation] = {}
    for agent in population.alive():
        location = world.locations[agent.agent_id]
        place = world.place(location.place_id) if location.place_id is not None else None
        co_located = tuple(
            AgentBrief(other_id, population[other_id].display_name, "co-located")
            for other_id in world.occupancy(location.place_id or "")
            if other_id != agent.agent_id
        )[:12]
        place_type = place.type if place is not None else "transit"
        features = frozenset(
            {
                f"place:{place_type}",
                f"district:{location.district_id}",
                f"energy:{int(agent.needs.energy * 4)}",
                f"hunger:{int(agent.needs.hunger * 4)}",
                f"social:{int(agent.needs.social * 4)}",
                f"company:{min(3, len(co_located))}",
                f"status:{agent.employment_status}",
            }
        )
        digest_hash = sha256_hex(canonical_bytes(sorted(features)))
        observation = Observation(
            agent_id=agent.agent_id,
            tick=tick,
            sim_time=sim_time,
            self_state=SelfView(
                agent.needs.as_dict(),
                agent.health,
                agent.wealth_cents,
                agent.employment_status,
                agent.education_level,
                {str(skill): value for skill, value in agent.skills.items()},
                tuple(agent.goals),
            ),
            place=PlaceView(
                place.place_id if place is not None else None,
                place.name if place is not None else "In transit",
                place_type,
                location.district_id,
                location.in_transit,
                (
                    location.travel_ticks - location.path_cursor
                    if location.in_transit
                    and location.travel_ticks is not None
                    and location.path_cursor is not None
                    else None
                ),
                _legal_actions(place_type, location.in_transit),
            ),
            co_located=co_located,
            inbox=(),
            feed=(),
            news=(),
            market=None,
            employer=None,
            offers=(),
            obligations=(),
            stakes=0.0,
            digest_features=features,
            digest_hash=digest_hash,
        )
        observations[agent.agent_id] = observation
    return observations
