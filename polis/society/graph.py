from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from polis.config.mechanisms import mechanism
from polis.config.settings import SocietySettings
from polis.events.kinds import (
    NETWORK_SNAPSHOT,
    TIE_ENDED,
    TIE_FORMED,
    TIE_TYPE_CHANGED,
    TIE_UPDATED,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.rng import RngRegistry

TieType = Literal["kin", "partner", "friend", "colleague", "rival", "creditor", "acquaintance"]
SYMMETRIC_TYPES = frozenset({"kin", "partner", "friend", "colleague", "rival", "acquaintance"})


def _pair(a_id: str, b_id: str) -> tuple[str, str]:
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if not xs or len(xs) != len(ys):
        return 0.0
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum(
        (x_value - mean_x) * (y_value - mean_y) for x_value, y_value in zip(xs, ys, strict=True)
    )
    denominator = math.sqrt(
        sum((value - mean_x) ** 2 for value in xs) * sum((value - mean_y) ** 2 for value in ys)
    )
    return 0.0 if denominator == 0 else numerator / denominator


def _powerlaw_fit(degrees: Sequence[int]) -> tuple[float, float]:
    positive = sorted(value for value in degrees if value > 0)
    if len(positive) < 2:
        return 0.0, 1.0
    denominator = sum(math.log(value / 0.5) for value in positive)
    if denominator == 0:
        return 0.0, 1.0
    alpha = 1.0 + len(positive) / denominator
    count = len(positive)
    ks = max(
        abs((index + 1) / count - (1.0 - (value / 0.5) ** (1.0 - alpha)))
        for index, value in enumerate(positive)
    )
    return alpha, ks


@dataclass(frozen=True, slots=True)
class Tie:
    a_id: str
    b_id: str
    type: TieType
    strength: float
    valence: float
    trust: float
    formed_tick: int
    ended_tick: int | None
    last_interaction_tick: int


@dataclass(frozen=True, slots=True)
class Interaction:
    a_id: str
    b_id: str
    kind: str
    weight: float = 1.0


class AgentProfile(Protocol):
    traits: Mapping[str, float]


type AgentState = AgentProfile


class GraphRepository(Protocol):
    def all(self) -> tuple[Tie, ...]: ...

    def put(self, tie: Tie) -> None: ...


class MemoryGraphRepository:
    def __init__(self, ties: Sequence[Tie] = ()) -> None:
        self._ties = {(tie.a_id, tie.b_id, tie.type): tie for tie in ties}

    def all(self) -> tuple[Tie, ...]:
        return tuple(sorted(self._ties.values(), key=lambda row: (row.a_id, row.b_id, row.type)))

    def put(self, tie: Tie) -> None:
        self._ties[(tie.a_id, tie.b_id, tie.type)] = tie


class ContactLedger:
    """Windowed per-tick co-location counts, independent of clock profile."""

    def __init__(self, *, ticks_per_sim_day: int = 24, window_sim_days: int = 30) -> None:
        self.window_ticks = ticks_per_sim_day * window_sim_days
        self._contacts: dict[tuple[str, str], deque[int]] = defaultdict(deque)

    def record(self, place_id: str, occupants: Sequence[str], tick: int) -> None:
        del place_id
        ordered = sorted(set(occupants))
        for index, a_id in enumerate(ordered):
            for b_id in ordered[index + 1 :]:
                self._contacts[(a_id, b_id)].append(tick)
        self._prune(tick)

    def joint_place_ticks(self, a_id: str, b_id: str, tick: int) -> int:
        key = _pair(a_id, b_id)
        rows = self._contacts.get(key)
        if rows is None:
            return 0
        cutoff = tick - self.window_ticks + 1
        while rows and rows[0] < cutoff:
            rows.popleft()
        return len(rows)

    def pairs(self, tick: int) -> tuple[tuple[str, str, int], ...]:
        self._prune(tick)
        return tuple(
            (a_id, b_id, len(rows)) for (a_id, b_id), rows in sorted(self._contacts.items()) if rows
        )

    def _prune(self, tick: int) -> None:
        cutoff = tick - self.window_ticks + 1
        for key in tuple(self._contacts):
            rows = self._contacts[key]
            while rows and rows[0] < cutoff:
                rows.popleft()
            if not rows:
                del self._contacts[key]


_INITIAL: dict[str, tuple[float, float, float]] = {
    "colocation": (0.05, 0.00, 0.50),
    "conversation": (0.10, 0.05, 0.50),
    "dm": (0.08, 0.05, 0.50),
    "employer": (0.15, 0.00, 0.55),
    "school": (0.12, 0.05, 0.55),
    "household": (0.40, 0.30, 0.70),
    "institution": (0.10, 0.05, 0.50),
    "loan": (0.20, 0.00, 0.50),
    "befriend": (0.35, 0.25, 0.60),
    "platform": (0.06, 0.00, 0.45),
}

_DYNAMICS: dict[str, tuple[float, float, float]] = {
    "conversation": (0.030, 0.005, 0.000),
    "conversation_turn": (0.030, 0.005, 0.000),
    "dm": (0.020, 0.005, 0.000),
    "colocation": (0.002, 0.000, 0.000),
    "agreement": (0.010, 0.030, 0.010),
    "disagreement": (0.010, -0.025, -0.005),
    "gift": (0.040, 0.080, 0.030),
    "hired": (0.060, 0.050, 0.020),
    "fired": (0.000, -0.250, -0.120),
    "promise_kept": (0.010, 0.030, 0.060),
    "promise_broken": (0.000, -0.150, -0.220),
    "crime_victim": (0.000, -0.600, -0.500),
    "court_adversary": (0.000, -0.200, -0.100),
    "testimony_against": (0.000, -0.300, -0.200),
}


class SocialGraph:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        repo: GraphRepository,
        cfg: SocietySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.repo = repo
        self.cfg = cfg
        self._interactions: list[Interaction] = []
        self._rival_recovery_since: dict[tuple[str, str, str], int] = {}
        self._last_decay_tick: dict[tuple[str, str, TieType], int] = {}

    @staticmethod
    def _key(a_id: str, b_id: str, type: TieType) -> tuple[str, str, TieType]:
        if type in SYMMETRIC_TYPES and b_id < a_id:
            return b_id, a_id, type
        return a_id, b_id, type

    def _emit(
        self,
        kind: int,
        payload: Mapping[str, object],
        tick: int,
        *,
        subjects: Sequence[str] = (),
    ) -> Event:
        return self.log.stage(
            NewEvent(kind, payload, subject_ids=tuple(subjects)),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )

    def tie(self, a_id: str, b_id: str, type: TieType | None = None) -> Tie | None:
        candidates = [
            row
            for row in self.repo.all()
            if row.ended_tick is None
            and (
                (row.a_id == a_id and row.b_id == b_id)
                or (row.type in SYMMETRIC_TYPES and row.a_id == b_id and row.b_id == a_id)
            )
            and (type is None or row.type == type)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda row: (-row.strength, row.type))[0]

    def strength(self, a_id: str, b_id: str) -> float:
        row = self.tie(a_id, b_id)
        return 0.0 if row is None else row.strength

    def trust(self, a_id: str, b_id: str) -> float:
        row = self.tie(a_id, b_id)
        return 0.0 if row is None else row.trust

    def neighbours(self, agent_id: str, *, min_strength: float = 0.0) -> tuple[Tie, ...]:
        rows = [
            row
            for row in self.repo.all()
            if row.ended_tick is None
            and row.strength >= min_strength
            and (row.a_id == agent_id or row.b_id == agent_id)
        ]
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    row.type,
                    row.b_id if row.a_id == agent_id else row.a_id,
                ),
            )
        )

    def stage_interaction(self, i: Interaction) -> None:
        self._interactions.append(i)

    def form(
        self,
        a_id: str,
        b_id: str,
        type: TieType,
        context: str,
        tick: int,
    ) -> Event | None:
        if a_id == b_id:
            return None
        a_id, b_id, type = self._key(a_id, b_id, type)
        existing = self.tie(a_id, b_id, type)
        initial = _INITIAL.get(context, _INITIAL["institution"])
        if existing is not None:
            strengthened = replace(
                existing,
                strength=min(1.0, max(existing.strength, initial[0])),
                valence=max(-1.0, min(1.0, max(existing.valence, initial[1]))),
                trust=min(1.0, max(existing.trust, initial[2])),
                last_interaction_tick=tick,
            )
            self.repo.put(strengthened)
            return None
        row = Tie(a_id, b_id, type, *initial, tick, None, tick)
        self.repo.put(row)
        return self._emit(
            TIE_FORMED,
            {
                "a_id": a_id,
                "b_id": b_id,
                "type": type,
                "context": context,
                "strength": row.strength,
                "valence": row.valence,
                "trust": row.trust,
            },
            tick,
            subjects=(a_id, b_id),
        )

    def apply_tick(self, tick: int, contacts: ContactLedger) -> Sequence[Event]:
        events: list[Event] = []
        for a_id, b_id, count in contacts.pairs(tick):
            if count >= self.cfg.colocation_threshold and self.tie(a_id, b_id) is None:
                event = self.form(a_id, b_id, "acquaintance", "colocation", tick)
                if event is not None:
                    events.append(event)

        grouped: dict[tuple[str, str], list[Interaction]] = defaultdict(list)
        for item in sorted(
            self._interactions,
            key=lambda row: (min(row.a_id, row.b_id), max(row.a_id, row.b_id), row.kind),
        ):
            context = {
                "conversation": "conversation",
                "conversation_turn": "conversation",
                "dm": "dm",
            }.get(item.kind)
            if context is not None and self.tie(item.a_id, item.b_id) is None:
                formed = self.form(
                    item.a_id,
                    item.b_id,
                    "acquaintance",
                    context,
                    tick,
                )
                if formed is not None:
                    events.append(formed)
            grouped[_pair(item.a_id, item.b_id)].append(item)
        self._interactions.clear()

        for row in self.repo.all():
            if row.ended_tick is not None:
                continue
            drivers = grouped.get(_pair(row.a_id, row.b_id), [])
            ds = dv = dt = 0.0
            for driver in drivers:
                change = _DYNAMICS.get(driver.kind)
                if change is None:
                    continue
                ds += change[0] * driver.weight
                dv += change[1] * driver.weight
                dt += change[2] * driver.weight

            key = self._key(row.a_id, row.b_id, row.type)
            last_decay_tick = self._last_decay_tick.get(
                key,
                max(row.formed_tick, row.last_interaction_tick),
            )
            elapsed = max(0, tick - last_decay_tick)
            halflife = self.cfg.tie_halflife_sim_days.get(row.type)
            decay = (
                0.0
                if halflife is None
                else math.log(2) * elapsed / (halflife * self.clock.profile.ticks_per_sim_day)
            )
            new_strength = min(1.0, max(0.0, row.strength + ds - decay))
            if row.type == "kin":
                new_strength = max(0.25, new_strength)
            updated = replace(
                row,
                strength=new_strength,
                valence=min(1.0, max(-1.0, row.valence + dv)),
                trust=min(1.0, max(0.0, row.trust + dt)),
                last_interaction_tick=tick if drivers else row.last_interaction_tick,
            )
            self.repo.put(updated)
            self._last_decay_tick[key] = tick
            total_change = (
                abs(updated.strength - row.strength)
                + abs(updated.valence - row.valence)
                + abs(updated.trust - row.trust)
            )
            if total_change >= self.cfg.tie_event_threshold:
                events.append(
                    self._emit(
                        TIE_UPDATED,
                        {
                            "a_id": row.a_id,
                            "b_id": row.b_id,
                            "type": row.type,
                            "d_strength": updated.strength - row.strength,
                            "d_valence": updated.valence - row.valence,
                            "d_trust": updated.trust - row.trust,
                            "drivers": [
                                {"kind": item.kind, "weight": item.weight}
                                for item in sorted(drivers, key=lambda item: item.kind)
                            ],
                        },
                        tick,
                        subjects=(row.a_id, row.b_id),
                    )
                )
            transition = self._transition(updated, tick)
            if transition is not None:
                events.append(transition)
        return tuple(events)

    def _transition(self, row: Tie, tick: int) -> Event | None:
        target: TieType | None = None
        trigger = ""
        if row.type == "acquaintance" and row.strength >= 0.40 and row.valence >= 0.20:
            target, trigger = "friend", "strength_valence"
        elif row.type == "friend" and row.valence <= -0.40:
            target, trigger = "rival", "conflict"
        elif row.type == "rival":
            key = (row.a_id, row.b_id, row.type)
            if row.valence >= 0.10:
                since = self._rival_recovery_since.setdefault(key, tick)
                if tick - since >= 30 * self.clock.profile.ticks_per_sim_day:
                    target, trigger = "acquaintance", "sustained_recovery"
            else:
                self._rival_recovery_since.pop(key, None)
        if target is not None:
            self.repo.put(replace(row, ended_tick=tick))
            replacement = replace(row, type=target, formed_tick=tick, ended_tick=None)
            self.repo.put(replacement)
            old_key = self._key(row.a_id, row.b_id, row.type)
            self._last_decay_tick.pop(old_key, None)
            self._last_decay_tick[
                self._key(replacement.a_id, replacement.b_id, replacement.type)
            ] = tick
            return self._emit(
                TIE_TYPE_CHANGED,
                {
                    "a_id": row.a_id,
                    "b_id": row.b_id,
                    "from_type": row.type,
                    "to_type": target,
                    "trigger": trigger,
                },
                tick,
                subjects=(row.a_id, row.b_id),
            )
        if row.strength < 0.02 and row.type not in {"kin", "partner", "creditor"}:
            self.repo.put(replace(row, ended_tick=tick))
            self._last_decay_tick.pop(
                self._key(row.a_id, row.b_id, row.type),
                None,
            )
            return self._emit(
                TIE_ENDED,
                {
                    "a_id": row.a_id,
                    "b_id": row.b_id,
                    "type": row.type,
                    "reason": "decay",
                    "final_strength": row.strength,
                },
                tick,
                subjects=(row.a_id, row.b_id),
            )
        return None

    def snapshot(self, tick: int) -> Event:
        live = [row for row in self.repo.all() if row.ended_tick is None]
        nodes = sorted({value for row in live for value in (row.a_id, row.b_id)})
        adjacent: dict[str, set[str]] = {node: set() for node in nodes}
        for row in live:
            adjacent[row.a_id].add(row.b_id)
            adjacent[row.b_id].add(row.a_id)
        degrees = {node: len(adjacent[node]) for node in nodes}
        values = sorted(degrees.values())
        total = sum(values)
        gini = (
            0.0
            if not values or total == 0
            else sum((2 * index - len(values) - 1) * value for index, value in enumerate(values, 1))
            / (len(values) * total)
        )
        components = self._components(live, nodes)
        closed_triplets = 0
        connected_triplets = 0
        local_clustering: list[float] = []
        for node in nodes:
            neighbours = sorted(adjacent[node])
            possible = len(neighbours) * (len(neighbours) - 1) // 2
            if possible == 0:
                local_clustering.append(0.0)
                continue
            closed = sum(
                neighbour_b in adjacent[neighbour_a]
                for index, neighbour_a in enumerate(neighbours)
                for neighbour_b in neighbours[index + 1 :]
            )
            closed_triplets += closed
            connected_triplets += possible
            local_clustering.append(closed / possible)
        degree_x: list[float] = []
        degree_y: list[float] = []
        for row in live:
            degree_x.extend((degrees[row.a_id], degrees[row.b_id]))
            degree_y.extend((degrees[row.b_id], degrees[row.a_id]))
        powerlaw_alpha, powerlaw_ks = _powerlaw_fit(values)
        component_edges = [
            sum(row.a_id in component and row.b_id in component for row in live)
            for component in components
        ]
        modularity = (
            0.0
            if not live
            else 1.0 - sum((edge_count / len(live)) ** 2 for edge_count in component_edges)
        )
        payload = {
            "n_nodes": len(nodes),
            "n_edges": len(live),
            "mean_degree": 0.0 if not nodes else 2 * len(live) / len(nodes),
            "degree_gini": gini,
            "powerlaw_alpha": powerlaw_alpha,
            "powerlaw_ks": powerlaw_ks,
            "clustering_global": (
                0.0 if connected_triplets == 0 else closed_triplets / connected_triplets
            ),
            "clustering_avg_local": (
                0.0 if not local_clustering else sum(local_clustering) / len(local_clustering)
            ),
            "assortativity_degree": _pearson(degree_x, degree_y),
            "assortativity_wealth": 0.0,
            "assortativity_belief": 0.0,
            "assortativity_district": 0.0,
            "modularity": modularity,
            "n_communities": len(components),
            "largest_component_share": (
                0.0 if not nodes else max(map(len, components), default=0) / len(nodes)
            ),
            "n_components": len(components),
        }
        return self._emit(NETWORK_SNAPSHOT, payload, tick)

    @staticmethod
    def _components(live: Sequence[Tie], nodes: Sequence[str]) -> list[set[str]]:
        adjacent: dict[str, set[str]] = defaultdict(set)
        for row in live:
            adjacent[row.a_id].add(row.b_id)
            adjacent[row.b_id].add(row.a_id)
        pending = set(nodes)
        result: list[set[str]] = []
        while pending:
            root = min(pending)
            component = {root}
            queue = [root]
            pending.remove(root)
            while queue:
                for neighbour in sorted(adjacent[queue.pop()]):
                    if neighbour in pending:
                        pending.remove(neighbour)
                        component.add(neighbour)
                        queue.append(neighbour)
            result.append(component)
        return result

    def end_all_for(self, agent_id: str, reason: str, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for row in self.neighbours(agent_id):
            self.repo.put(replace(row, ended_tick=tick))
            events.append(
                self._emit(
                    TIE_ENDED,
                    {
                        "a_id": row.a_id,
                        "b_id": row.b_id,
                        "type": row.type,
                        "reason": reason,
                        "final_strength": row.strength,
                    },
                    tick,
                    subjects=(row.a_id, row.b_id),
                )
            )
        return tuple(events)


@mechanism(
    "graph_homophily",
    entails=(
        "with homophily_bias = 0 the graph imposes no similarity preference, so measured "
        "assortativity is attributable to space, institutions, and the platform. Setting "
        "homophily_bias > 0 multiplies BEFRIEND acceptance and acquaintance→friend upgrade "
        "probability by exp(β · sim(i,j)) and thereby entails positive belief assortativity; "
        "any B1 result must be reported with the value used."
    ),
)
def formation_multiplier(a: AgentState, b: AgentState, beta: float) -> float:
    if beta == 0.0:
        return 1.0
    a_traits = getattr(a, "traits", {})
    b_traits = getattr(b, "traits", {})
    keys = sorted(set(a_traits) & set(b_traits))
    if not keys:
        return 1.0
    similarity = sum(1.0 - abs(float(a_traits[key]) - float(b_traits[key])) for key in keys)
    similarity /= len(keys)
    return math.exp(beta * similarity)


__all__ = [
    "AgentState",
    "ContactLedger",
    "GraphRepository",
    "Interaction",
    "MemoryGraphRepository",
    "SocialGraph",
    "Tie",
    "TieType",
    "formation_multiplier",
]
