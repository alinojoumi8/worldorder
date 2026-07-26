from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np

from polis.agents.types import AgentState
from polis.config.settings import MemorySettings

MemoryType = Literal["observation", "reflection", "plan", "semantic"]


@dataclass(slots=True)
class MemoryRecord:
    memory_id: str
    agent_id: str
    tick: int
    type: MemoryType
    text: str
    importance: float
    embedding: tuple[float, ...]
    source_event_seq: int | None = None
    parent_memory_ids: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()
    last_accessed_tick: int = 0
    access_count: int = 0
    archived: bool = False


@dataclass(frozen=True, slots=True)
class Retrieval:
    memory_id: str
    type: MemoryType
    text: str
    importance: float
    recency: float
    relevance: float
    score: float
    rank: int
    parent_memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReflectionInsight:
    statement: str
    supported_by: tuple[str, ...]
    importance: float


def embed_text(text: str, dimensions: int = 32) -> tuple[float, ...]:
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{counter}|{text}".encode()).digest()
        values.extend((byte / 127.5) - 1 for byte in digest)
        counter += 1
    vector = np.asarray(values[:dimensions], dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return tuple(float(value) for value in vector)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return max(-1.0, min(1.0, float(np.dot(left, right))))


def _normalise(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


class MemoryStore:
    name = "memories"

    def __init__(self, settings: MemorySettings) -> None:
        self.settings = settings
        self._rows: dict[str, MemoryRecord] = {}
        self._by_agent: defaultdict[str, list[str]] = defaultdict(list)
        self._next_id: defaultdict[str, int] = defaultdict(int)

    def __len__(self) -> int:
        return len(self._rows)

    def for_agent(
        self,
        agent_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[MemoryRecord, ...]:
        return tuple(
            self._rows[memory_id]
            for memory_id in self._by_agent[agent_id]
            if include_archived or not self._rows[memory_id].archived
        )

    def write(
        self,
        *,
        agent_id: str,
        tick: int,
        type: MemoryType,
        text: str,
        importance: float,
        source_event_seq: int | None = None,
        parent_memory_ids: Sequence[str] = (),
        subject_ids: Sequence[str] = (),
    ) -> MemoryRecord:
        owned = set(self._by_agent[agent_id])
        parents = tuple(sorted(set(parent_memory_ids)))
        if not set(parents) <= owned:
            raise ValueError("memory cites a parent not owned by this agent")
        self._next_id[agent_id] += 1
        memory_id = f"mem_{agent_id.removeprefix('ag_')}_{self._next_id[agent_id]:06d}"
        row = MemoryRecord(
            memory_id=memory_id,
            agent_id=agent_id,
            tick=tick,
            type=type,
            text=text,
            importance=min(1.0, max(0.0, importance)),
            embedding=embed_text(text),
            source_event_seq=source_event_seq,
            parent_memory_ids=parents,
            subject_ids=tuple(sorted(set(subject_ids))),
            last_accessed_tick=tick,
        )
        self._rows[memory_id] = row
        self._by_agent[agent_id].append(memory_id)
        self._evict(agent_id, tick)
        return row

    def maybe_write_observation(
        self,
        agent: AgentState,
        *,
        tick: int,
        text: str,
        salience: float,
        source_event_seq: int | None = None,
        life_event: bool = False,
    ) -> MemoryRecord | None:
        importance = min(1.0, 0.15 + salience * 0.85)
        if not life_event and salience <= self.settings.write_threshold:
            return None
        row = self.write(
            agent_id=agent.agent_id,
            tick=tick,
            type="observation",
            text=text,
            importance=importance,
            source_event_seq=source_event_seq,
        )
        agent.importance_since_reflection += row.importance
        return row

    def retrieve(
        self,
        agent_id: str,
        query: str,
        *,
        tick: int,
        k: int | None = None,
    ) -> tuple[Retrieval, ...]:
        candidates = sorted(
            self.for_agent(agent_id),
            key=lambda row: row.memory_id,
        )
        if not candidates:
            return ()
        query_embedding = embed_text(query)
        relevant = sorted(
            candidates,
            key=lambda row: (-_cosine(query_embedding, row.embedding), row.memory_id),
        )[:100]
        raw_recency = [0.995 ** ((tick - row.last_accessed_tick) / 24) for row in relevant]
        raw_importance = [row.importance for row in relevant]
        raw_relevance = [(_cosine(query_embedding, row.embedding) + 1) / 2 for row in relevant]
        recencies = _normalise(raw_recency)
        importances = _normalise(raw_importance)
        relevances = _normalise(raw_relevance)
        weights = self.settings.retrieval_weights
        scored = [
            (
                weights["recency"] * recencies[index]
                + weights["importance"] * importances[index]
                + weights["relevance"] * relevances[index],
                row,
                recencies[index],
                relevances[index],
            )
            for index, row in enumerate(relevant)
        ]
        scored.sort(key=lambda item: (-item[0], item[1].memory_id))
        limit = k if k is not None else self.settings.retrieval_k
        result: list[Retrieval] = []
        for rank, (score, row, recency, relevance) in enumerate(scored[:limit], 1):
            row.last_accessed_tick = tick
            row.access_count += 1
            result.append(
                Retrieval(
                    row.memory_id,
                    row.type,
                    row.text,
                    row.importance,
                    round(recency, 8),
                    round(relevance, 8),
                    round(score, 8),
                    rank,
                    row.parent_memory_ids,
                )
            )
        return tuple(result)

    def reflection_due(
        self,
        agent: AgentState,
        *,
        tick: int,
        life_event: bool = False,
    ) -> bool:
        if tick - agent.last_reflection_tick < self.settings.reflection_min_gap_ticks:
            return False
        return life_event or (
            agent.importance_since_reflection >= self.settings.reflection_threshold
        )

    def apply_reflection(
        self,
        agent: AgentState,
        *,
        tick: int,
        insights: Iterable[ReflectionInsight],
        identity_summary: str,
    ) -> tuple[MemoryRecord, ...]:
        owned = set(self._by_agent[agent.agent_id])
        accepted: list[MemoryRecord] = []
        for insight in insights:
            cited = tuple(sorted(set(insight.supported_by)))
            if not cited or not set(cited) <= owned:
                continue
            accepted.append(
                self.write(
                    agent_id=agent.agent_id,
                    tick=tick,
                    type="reflection",
                    text=insight.statement,
                    importance=insight.importance,
                    parent_memory_ids=cited,
                )
            )
        if accepted:
            agent.identity_summary = identity_summary
            agent.last_reflection_tick = tick
            agent.importance_since_reflection = 0
        return tuple(accepted)

    def archive(self, agent_id: str) -> None:
        for row in self.for_agent(agent_id):
            row.archived = True

    def _evict(self, agent_id: str, tick: int) -> None:
        ids = self._by_agent[agent_id]
        overflow = len(ids) - self.settings.max_per_agent
        if overflow <= 0:
            return
        ranked = sorted(
            (self._rows[memory_id] for memory_id in ids),
            key=lambda row: (
                (0.6 * (0.995 ** ((tick - row.last_accessed_tick) / 24)) + 0.4 * row.importance)
                * (1.5 if row.type == "reflection" else 1.0),
                row.memory_id,
            ),
        )
        removed = {row.memory_id for row in ranked[:overflow]}
        self._by_agent[agent_id] = [memory_id for memory_id in ids if memory_id not in removed]
        for memory_id in removed:
            del self._rows[memory_id]

    def dump(self) -> Mapping[str, Any]:
        return {
            "rows": {memory_id: asdict(row) for memory_id, row in sorted(self._rows.items())},
            "by_agent": {agent_id: list(ids) for agent_id, ids in sorted(self._by_agent.items())},
            "next_id": dict(sorted(self._next_id.items())),
        }

    def load(self, state: Mapping[str, Any]) -> None:
        raw_rows = state["rows"]
        if not isinstance(raw_rows, Mapping):
            raise ValueError("checkpoint memories must be a mapping")
        self._rows = {
            str(memory_id): MemoryRecord(**dict(row))
            for memory_id, row in sorted(raw_rows.items())
            if isinstance(row, Mapping)
        }
        self._by_agent = defaultdict(
            list,
            {str(agent_id): list(ids) for agent_id, ids in dict(state["by_agent"]).items()},
        )
        self._next_id = defaultdict(
            int,
            {str(agent_id): int(value) for agent_id, value in dict(state["next_id"]).items()},
        )
