from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from polis.config.settings import SocietySettings
from polis.events.kinds import CLAIM_CHECKED
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.society.media.platform import Post

Verdict = Literal["supported", "imprecise", "contradicted", "unverifiable"]
ResolverKind = Literal["categorical", "boolean", "numeric", "existential"]


class ClaimLike(Protocol):
    @property
    def claim_id(self) -> str: ...

    @property
    def predicate(self) -> str: ...

    @property
    def entity_id(self) -> str: ...

    @property
    def value(self) -> Any: ...

    @property
    def as_of_tick(self) -> int: ...

    @property
    def sourced_to_event_seqs(self) -> tuple[int, ...]: ...


@dataclass(frozen=True, slots=True)
class CheckResult:
    claim_id: str
    predicate: str
    entity_id: str
    claimed_value: Any
    truth_value: Any
    verdict: Verdict
    score: float | None
    matched_event_seqs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FactObservation:
    tick: int
    value: Any
    event_seqs: tuple[int, ...] = ()


class CheckContext(Protocol):
    def lookup(
        self,
        predicate: str,
        entity_id: str,
        as_of_tick: int,
    ) -> tuple[Any, tuple[int, ...]] | None: ...


class MemoryCheckContext:
    """Append-only fact history used by resolvers without hindsight."""

    def __init__(
        self,
        facts: Mapping[tuple[str, str], Sequence[FactObservation]] | None = None,
    ) -> None:
        self._facts: dict[tuple[str, str], list[FactObservation]] = {
            key: sorted(rows, key=lambda row: row.tick) for key, rows in (facts or {}).items()
        }

    def record(
        self,
        predicate: str,
        entity_id: str,
        tick: int,
        value: Any,
        event_seqs: Sequence[int] = (),
    ) -> None:
        rows = self._facts.setdefault((predicate, entity_id), [])
        rows.append(FactObservation(tick, value, tuple(sorted(set(event_seqs)))))
        rows.sort(key=lambda row: row.tick)

    def lookup(
        self,
        predicate: str,
        entity_id: str,
        as_of_tick: int,
    ) -> tuple[Any, tuple[int, ...]] | None:
        rows = self._facts.get((predicate, entity_id), ())
        index = bisect_right(rows, as_of_tick, key=lambda row: row.tick)
        if index == 0:
            return None
        row = rows[index - 1]
        return row.value, row.event_seqs


class Resolver(Protocol):
    @property
    def kind(self) -> ResolverKind: ...

    def __call__(self, entity_id: str, as_of_tick: int, ctx: CheckContext) -> Any: ...


@dataclass(frozen=True, slots=True)
class ContextResolver:
    predicate: str
    kind: ResolverKind

    def __call__(
        self,
        entity_id: str,
        as_of_tick: int,
        ctx: CheckContext,
    ) -> Any:
        found = ctx.lookup(self.predicate, entity_id, as_of_tick)
        return None if found is None else found[0]


_RESOLVER_KINDS: Final[Mapping[str, ResolverKind]] = {
    "firm.solvent": "boolean",
    "firm.headcount": "numeric",
    "firm.profitable": "boolean",
    "agent.employed": "boolean",
    "agent.convicted": "boolean",
    "agent.wealth_percentile": "numeric",
    "agent.holds_office": "existential",
    "crime.occurred": "existential",
    "price.close": "numeric",
    "policy.value": "numeric",
    "election.winner": "categorical",
    "election.turnout": "numeric",
    "macro.unemployment": "numeric",
    "macro.cpi": "numeric",
    "outlet.retracted": "boolean",
}
RESOLVERS: Final[Mapping[str, Resolver]] = {
    predicate: ContextResolver(predicate, kind) for predicate, kind in _RESOLVER_KINDS.items()
}
"""Closed resolver registry.

Adding a predicate changes the misinformation measurement and therefore requires a
society-spec revision, a new as-of fixture, and a purity regression test.
"""


def _matched(
    ctx: CheckContext,
    claim: ClaimLike,
) -> tuple[int, ...]:
    found = ctx.lookup(claim.predicate, claim.entity_id, claim.as_of_tick)
    if found is None:
        return ()
    return tuple(sorted(set(found[1]) | set(claim.sourced_to_event_seqs)))


class ClaimChecker:
    def __init__(
        self,
        *,
        ctx: CheckContext,
        log: EventLog,
        cfg: SocietySettings,
        clock: Clock | None = None,
        extractor: Callable[[Post, int], bool] | None = None,
    ) -> None:
        self.ctx = ctx
        self.log = log
        self.cfg = cfg
        self.clock = clock
        self.extractor = extractor

    def _compare(
        self,
        claimed: Any,
        truth: Any,
        kind: ResolverKind,
    ) -> tuple[Verdict, float]:
        if kind == "numeric":
            try:
                claimed_number = float(claimed)
                truth_number = float(truth)
            except (TypeError, ValueError):
                return "contradicted", 0.0
            scale = max(abs(truth_number), 1.0)
            error = abs(claimed_number - truth_number) / scale
            if error <= self.cfg.claim_tolerance:
                return "supported", 1.0
            if error <= 3.0 * self.cfg.claim_tolerance:
                return "imprecise", 0.5
            return "contradicted", 0.0
        if kind in {"boolean", "existential"}:
            return (
                ("supported", 1.0)
                if bool(claimed) is bool(truth)
                else (
                    "contradicted",
                    0.0,
                )
            )
        return ("supported", 1.0) if claimed == truth else ("contradicted", 0.0)

    def check(
        self,
        claim: ClaimLike,
        subject_kind: Literal["article", "post", "speech"],
        subject_id: str,
        tick: int,
    ) -> tuple[CheckResult, Event]:
        resolver = RESOLVERS.get(claim.predicate)
        if resolver is None:
            truth = None
            verdict: Verdict = "unverifiable"
            score = None
            matched: tuple[int, ...] = ()
        else:
            truth = resolver(claim.entity_id, claim.as_of_tick, self.ctx)
            matched = _matched(self.ctx, claim)
            if truth is None:
                verdict, score = "unverifiable", None
            else:
                verdict, score = self._compare(claim.value, truth, resolver.kind)
        result = CheckResult(
            claim.claim_id,
            claim.predicate,
            claim.entity_id,
            claim.value,
            truth,
            verdict,
            score,
            matched,
        )
        sim_time = (
            self.clock.sim_time_at(tick)
            if self.clock is not None
            else self.log.staged()[-1].sim_time
            if self.log.staged()
            else _epoch()
        )
        event = self.log.stage(
            NewEvent(
                CLAIM_CHECKED,
                {
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                    "claim_id": result.claim_id,
                    "predicate": result.predicate,
                    "entity_id": result.entity_id,
                    "claimed_value": result.claimed_value,
                    "truth_value": result.truth_value,
                    "verdict": result.verdict,
                    "matched_event_seqs": list(result.matched_event_seqs),
                    "score": result.score,
                },
                subject_ids=(subject_id,),
            ),
            tick=tick,
            sim_time=sim_time,
        )
        return result, event

    def aggregate(self, results: Sequence[CheckResult]) -> float | None:
        scores = [result.score for result in results if result.score is not None]
        return None if not scores else math.fsum(scores) / len(scores)

    def audit_unannotated(self, posts: Sequence[Post], tick: int) -> float:
        if not posts:
            return 1.0
        annotated = sum(bool(post.claims) for post in posts)
        for post in posts:
            if post.claims:
                continue
            material = f"{self.log.run_id}|{tick}|{post.post_id}".encode()
            draw = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") / (2**64 - 1)
            if draw >= self.cfg.misinfo_audit_rate:
                continue
            if self.extractor is not None and self.extractor(post, tick):
                annotated += 1
        return annotated / len(posts)


def _epoch() -> Any:
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)


__all__ = [
    "RESOLVERS",
    "CheckContext",
    "CheckResult",
    "ClaimChecker",
    "ContextResolver",
    "FactObservation",
    "MemoryCheckContext",
    "Resolver",
    "Verdict",
]
