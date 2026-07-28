from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import asdict
from typing import Any

from polis.agents.types import AgentState, Needs, ReflexProfile, Traits


class AgentPopulation:
    name = "agents"

    def __init__(self, agents: Mapping[str, AgentState]) -> None:
        self.tick = 0
        self.agents = dict(sorted(agents.items()))
        self._initial_population = len(self.agents)
        self._action_counts: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self.agents)

    def __iter__(self) -> Iterator[AgentState]:
        return iter(self.agents.values())

    def __getitem__(self, agent_id: str) -> AgentState:
        return self.agents[agent_id]

    def alive(self) -> tuple[AgentState, ...]:
        return tuple(agent for agent in self.agents.values() if agent.alive)

    def add(self, agent: AgentState) -> None:
        if agent.agent_id in self.agents:
            raise ValueError(f"duplicate agent id: {agent.agent_id}")
        self.agents[agent.agent_id] = agent
        self.agents = dict(sorted(self.agents.items()))

    def money_supply_cents(self) -> int:
        return sum(agent.wealth_cents for agent in self.alive())

    def total_balances_cents(self) -> int:
        return self.money_supply_cents()

    def ledger_imbalance_cents(self) -> int:
        return 0

    def price_inflation_yoy_bp(self) -> int | None:
        return None

    def interest_imbalance_cents(self) -> int:
        return 0

    def population(self) -> int:
        return len(self.alive())

    def initial_population(self) -> int:
        return self._initial_population

    def action_type_counts(self) -> Mapping[str, int]:
        return dict(self._action_counts)

    def order_invariant_failures(self) -> Mapping[str, object]:
        return {}

    def share_invariant_failures(self) -> Mapping[str, object]:
        return {}

    def cap_table_invariant_failures(self) -> Mapping[str, object]:
        return {}

    def record_action(self, action_type: str) -> None:
        self._action_counts[action_type] += 1

    def reset_action_counts(self) -> None:
        self._action_counts.clear()

    def chain_ok(self) -> bool:
        return True

    def dump(self) -> Mapping[str, Any]:
        return {
            "tick": self.tick,
            "initial_population": self._initial_population,
            "agents": {
                agent.agent_id: {
                    **asdict(agent),
                    "expectation_features": sorted(agent.expectation_features),
                    "seen_situations": sorted(agent.seen_situations),
                }
                for agent in self
            },
            "action_counts": dict(sorted(self._action_counts.items())),
        }

    def load(self, state: Mapping[str, Any]) -> None:
        rows = state["agents"]
        if not isinstance(rows, Mapping):
            raise ValueError("checkpoint agents must be a mapping")
        restored: dict[str, AgentState] = {}
        for agent_id, raw in sorted(rows.items()):
            if not isinstance(raw, Mapping):
                raise ValueError("checkpoint agent must be a mapping")
            row = dict(raw)
            row["traits"] = Traits(**row["traits"])
            row["needs"] = Needs(**row["needs"])
            row["reflex_profile"] = ReflexProfile(**row["reflex_profile"])
            row["expectation_features"] = frozenset(row["expectation_features"])
            row["seen_situations"] = set(row["seen_situations"])
            restored[str(agent_id)] = AgentState(**row)
        self.agents = restored
        self.tick = int(state["tick"])
        self._initial_population = int(state["initial_population"])
        self._action_counts = Counter(state["action_counts"])
