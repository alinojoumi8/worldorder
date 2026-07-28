from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol
from uuid import UUID

from polis.events.types import Event


class EstatePort(Protocol):
    def settle_death(
        self,
        agent_id: str,
        tick: int,
        *,
        heirs: Sequence[tuple[str, int]] | None,
        ctx: Any,
    ) -> Sequence[Event]: ...

    def case_for(self, agent_id: str, tick: int) -> Literal["A", "B", "C", "D"]: ...

    def estate_account_id(self, agent_id: str, tick: int) -> str: ...

    def gross_cents(self, agent_id: str) -> int: ...

    def open_order_count(self, agent_id: str) -> int: ...

    def open_loan_count(self, agent_id: str) -> int: ...


class LedgerPort(Protocol):
    def balance(self, account_id: str) -> int: ...

    def liquid(self, owner_id: str) -> int: ...

    def accounts_of(self, owner_id: str) -> tuple[str, ...]: ...

    def transfer(self, src: str, dst: str, amount_cents: int, reason: str) -> list[Any]: ...

    def government_transfer(self, dst: str, amount_cents: int) -> list[Any]: ...

    def post_transaction(
        self,
        legs: Sequence[Any],
        *,
        tick: int,
        cause: Any,
        allow_negative: frozenset[str] = frozenset(),
    ) -> UUID: ...

    def next_txn_id(self, tick: int) -> UUID: ...

    def record_government_spending(self, amount_cents: int, tick: int) -> None: ...

    def allocate(self, pool_cents: int, weights: Sequence[tuple[str, int]]) -> dict[str, int]: ...

    def ensure_agent_account(self, agent_id: str, tick: int) -> str: ...

    def dump(self) -> Mapping[str, object]: ...

    def load(self, state: Mapping[str, object]) -> None: ...


class HousingPort(Protocol):
    def vacate(self, agent_id: str, tick: int) -> None: ...

    def find_affordable_home(self, income_cents: int, tick: int) -> str | None: ...


class SocialGraphPort(Protocol):
    def end_all_for(self, agent_id: str, reason: str, tick: int) -> Sequence[Event]: ...

    def end_pair(
        self,
        a_id: str,
        b_id: str,
        tie_type: str,
        reason: str,
        tick: int,
    ) -> Event | None: ...

    def strong_ties(self, agent_id: str, threshold: float) -> tuple[str, ...]: ...

    def strength(self, a_id: str, b_id: str) -> float: ...

    def form(
        self,
        a_id: str,
        b_id: str,
        tie_type: Literal[
            "kin",
            "partner",
            "friend",
            "colleague",
            "rival",
            "creditor",
            "acquaintance",
        ],
        origin: str,
        tick: int,
    ) -> Event | None: ...

    def live_partner(self, agent_id: str) -> str | None: ...


class BeliefPriorPort(Protocol):
    def priors_at_birth(
        self, child_id: str, mother_id: str, father_id: str
    ) -> tuple[tuple[str, float, float], ...]: ...

    def priors_for_migrant(
        self, agent_id: str, offsets: Mapping[str, float]
    ) -> tuple[tuple[str, float, float], ...]: ...

    def apply_priors(
        self,
        agent_id: str,
        priors: Sequence[tuple[str, float, float]],
        *,
        tick: int,
        source_ref: str,
    ) -> None: ...


class MemoryArchivePort(Protocol):
    def archive_agent(self, agent_id: str, tick: int) -> int: ...


class IncarcerationPort(Protocol):
    def is_incarcerated(self, agent_id: str) -> bool: ...


class EmploymentPort(Protocol):
    def income_cents(self, agent_id: str, tick: int) -> int: ...
