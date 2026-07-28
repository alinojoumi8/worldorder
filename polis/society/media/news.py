from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Literal, Protocol, cast

from polis.agents.actions import (
    Action,
    ActionType,
    GateFailure,
    GateResult,
    InstitutionSlot,
    ResolutionContext,
    ValidatedAction,
    ValidationContext,
)
from polis.config.canon import sha256_hex
from polis.config.settings import SocietySettings
from polis.events.kinds import (
    ARTICLE_DISTRIBUTED,
    ARTICLE_PUBLISHED,
    ARTICLE_RETRACTED,
    ARTICLE_SPIKED,
    OUTLET_CLOSED,
    OUTLET_FOUNDED,
    OUTLET_REVENUE_BOOKED,
    SOURCE_CULTIVATED,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.det import det_id
from polis.kernel.rng import RngRegistry
from polis.kernel.scheduler import Cadence
from polis.llm.purposes import Purpose
from polis.llm.router import CallRequest, LLMRouter
from polis.society.beliefs import BeliefEngine
from polis.society.graph import SocialGraph
from polis.society.media.checker import RESOLVERS, CheckResult, ClaimChecker
from polis.society.media.platform import Platform
from polis.society.protocols import ArticleBrief, BeliefUpdate, MemoryLookup


@dataclass(frozen=True, slots=True)
class Outlet:
    outlet_id: str
    name: str
    firm_id: str | None
    slant: float
    rigour: float
    reach: int
    closed_tick: int | None


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    text: str
    entity_id: str
    predicate: str
    value: Any
    as_of_tick: int
    sourced_to_event_seqs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Article:
    article_id: str
    outlet_id: str
    reporter_id: str | None
    tick: int
    headline: str
    body: str
    source_event_seqs: tuple[int, ...]
    claims: tuple[Claim, ...]
    accuracy: float | None
    slant_applied: float | None
    reach: int
    retracted_tick: int | None
    stance_proposition: str | None = None
    stance_value: float | None = None


@dataclass(frozen=True, slots=True)
class Draft:
    outlet_id: str
    reporter_id: str | None
    headline: str
    body: str
    source_event_seqs: tuple[int, ...]
    claims: tuple[Claim, ...]
    llm_call_id: str | None = None
    legal_risk: bool = False
    over_budget: bool = False
    stance_proposition: str | None = None
    stance_value: float | None = None


@dataclass(frozen=True, slots=True)
class StaffMember:
    agent_id: str
    role: Literal["reporter", "editor"]
    writing_skill: float


@dataclass(frozen=True, slots=True)
class RevenueBooking:
    ad_revenue_cents: int = 0
    subscription_cents: int = 0
    campaign_cents: int = 0
    advertisers: tuple[str, ...] = ()
    txn_ids: tuple[str, ...] = ()
    event: Event | None = None


class NewsLedgerApi(Protocol):
    def book_outlet_revenue(
        self,
        *,
        outlet: Outlet,
        period_start_tick: int,
        tick: int,
        impressions: int,
        cpm_cents: int,
        subscribers: Sequence[str],
    ) -> RevenueBooking: ...


class NullNewsLedger:
    def book_outlet_revenue(
        self,
        *,
        outlet: Outlet,
        period_start_tick: int,
        tick: int,
        impressions: int,
        cpm_cents: int,
        subscribers: Sequence[str],
    ) -> RevenueBooking:
        del outlet, period_start_tick, tick, impressions, cpm_cents, subscribers
        return RevenueBooking()


class NewsRepository(Protocol):
    def put_outlet(self, outlet: Outlet) -> None: ...

    def outlet(self, outlet_id: str) -> Outlet | None: ...

    def outlets(self) -> tuple[Outlet, ...]: ...

    def set_staff(self, outlet_id: str, staff: Sequence[StaffMember]) -> None: ...

    def staff(self, outlet_id: str) -> tuple[StaffMember, ...]: ...

    def put_article(self, article: Article) -> None: ...

    def article(self, article_id: str) -> Article | None: ...

    def articles(self) -> tuple[Article, ...]: ...

    def note_exposure(self, agent_id: str, article_id: str, tick: int) -> bool: ...

    def exposures(self) -> tuple[tuple[str, str, int], ...]: ...

    def audience_ids(self) -> tuple[str, ...]: ...

    def subscribers(self, outlet_id: str) -> tuple[str, ...]: ...


class MemoryNewsRepository:
    def __init__(self, audience_ids: Sequence[str] = ()) -> None:
        self._outlets: dict[str, Outlet] = {}
        self._articles: dict[str, Article] = {}
        self._staff: dict[str, tuple[StaffMember, ...]] = {}
        self._exposures: set[tuple[str, str, int]] = set()
        self._audience = set(audience_ids)
        self._subscriptions: set[tuple[str, str]] = set()

    def put_outlet(self, outlet: Outlet) -> None:
        self._outlets[outlet.outlet_id] = outlet

    def outlet(self, outlet_id: str) -> Outlet | None:
        return self._outlets.get(outlet_id)

    def outlets(self) -> tuple[Outlet, ...]:
        return tuple(sorted(self._outlets.values(), key=lambda row: row.outlet_id))

    def set_staff(self, outlet_id: str, staff: Sequence[StaffMember]) -> None:
        self._staff[outlet_id] = tuple(sorted(staff, key=lambda row: row.agent_id))
        self._audience.update(row.agent_id for row in staff)

    def staff(self, outlet_id: str) -> tuple[StaffMember, ...]:
        return self._staff.get(outlet_id, ())

    def put_article(self, article: Article) -> None:
        self._articles[article.article_id] = article

    def article(self, article_id: str) -> Article | None:
        return self._articles.get(article_id)

    def articles(self) -> tuple[Article, ...]:
        return tuple(sorted(self._articles.values(), key=lambda row: row.article_id))

    def note_exposure(self, agent_id: str, article_id: str, tick: int) -> bool:
        row = (agent_id, article_id, tick)
        if row in self._exposures:
            return False
        self._exposures.add(row)
        self._audience.add(agent_id)
        return True

    def exposures(self) -> tuple[tuple[str, str, int], ...]:
        return tuple(sorted(self._exposures))

    def audience_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._audience))

    def subscribe(self, agent_id: str, outlet_id: str) -> None:
        self._subscriptions.add((agent_id, outlet_id))
        self._audience.add(agent_id)

    def subscribers(self, outlet_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(agent_id for agent_id, target in self._subscriptions if target == outlet_id)
        )


class ArticleStore:
    def __init__(self, repo: NewsRepository) -> None:
        self.repo = repo

    def put(self, article: Article) -> None:
        self.repo.put_article(article)

    def get(self, article_id: str) -> Article | None:
        return self.repo.article(article_id)

    def all(self) -> tuple[Article, ...]:
        return self.repo.articles()

    def retract(self, article_id: str, tick: int) -> Article | None:
        article = self.get(article_id)
        if article is None or article.retracted_tick is not None:
            return None
        article = replace(article, retracted_tick=tick)
        self.put(article)
        return article

    def set_accuracy(
        self,
        article_id: str,
        accuracy: float | None,
        slant_applied: float | None,
    ) -> Article | None:
        article = self.get(article_id)
        if article is None:
            return None
        article = replace(article, accuracy=accuracy, slant_applied=slant_applied)
        self.put(article)
        return article

    def set_reach(self, article_id: str, reach: int) -> Article | None:
        article = self.get(article_id)
        if article is None:
            return None
        article = replace(article, reach=max(article.reach, reach))
        self.put(article)
        return article


class OutletRegistry:
    def __init__(
        self,
        *,
        log: EventLog,
        repo: NewsRepository,
        rng: RngRegistry,
        cfg: SocietySettings,
        clock: Clock | None = None,
    ) -> None:
        self.log = log
        self.repo = repo
        self.rng = rng
        self.cfg = cfg
        self.clock = clock
        self._ordinal = len(repo.outlets())

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
            sim_time=self.clock.sim_time_at(tick) if self.clock else _epoch(),
        )

    def _new_outlet(
        self,
        *,
        name: str,
        firm_id: str | None,
        founder_id: str,
        place_id: str,
        tick: int,
    ) -> tuple[Outlet, Event]:
        ordinal = self._ordinal
        self._ordinal += 1
        outlet_id = det_id("ol", "society.outlet", firm_id or "genesis", ordinal)
        stream = self.rng.numpy("news.outlet", outlet_id)
        slant = float(max(-1.0, min(1.0, stream.normal(0.0, self.cfg.outlet_slant_dispersion))))
        rigour = float(0.2 + 0.75 * stream.random())
        outlet = Outlet(outlet_id, name, firm_id, slant, rigour, 0, None)
        self.repo.put_outlet(outlet)
        event = self._emit(
            OUTLET_FOUNDED,
            {
                "outlet_id": outlet_id,
                "name": name,
                "firm_id": firm_id,
                "founder_id": founder_id,
                "slant": slant,
                "rigour": rigour,
                "place_id": place_id,
            },
            tick,
            subjects=(outlet_id, founder_id),
        )
        return outlet, event

    def seed_at_genesis(self, n: int, tick: int) -> Sequence[Event]:
        events = []
        for ordinal in range(max(0, n)):
            _, event = self._new_outlet(
                name=f"Public Ledger {ordinal + 1}",
                firm_id=None,
                founder_id=f"genesis_{ordinal:02d}",
                place_id="public",
                tick=tick,
            )
            events.append(event)
        return tuple(events)

    def register_from_firm(
        self,
        firm_id: str,
        founder_id: str,
        place_id: str,
        tick: int,
    ) -> tuple[Outlet, Event]:
        return self._new_outlet(
            name=f"{firm_id} News",
            firm_id=firm_id,
            founder_id=founder_id,
            place_id=place_id,
            tick=tick,
        )

    def get(self, outlet_id: str) -> Outlet | None:
        return self.repo.outlet(outlet_id)

    def live(self) -> tuple[Outlet, ...]:
        return tuple(row for row in self.repo.outlets() if row.closed_tick is None)

    def newsroom(self, outlet_id: str) -> tuple[str, tuple[str, ...]]:
        staff = self.repo.staff(outlet_id)
        if not staff:
            return "", ()
        editors = [row for row in staff if row.role == "editor"]
        candidates = editors or list(staff)
        editor = sorted(candidates, key=lambda row: (-row.writing_skill, row.agent_id))[0]
        reporters = tuple(
            sorted(row.agent_id for row in staff if row.role == "reporter" and row != editor)
        )
        return editor.agent_id, reporters

    def close(self, outlet_id: str, reason: str, tick: int) -> Event:
        outlet = self.get(outlet_id)
        if outlet is None:
            raise KeyError(outlet_id)
        if outlet.closed_tick is not None:
            raise ValueError(f"outlet {outlet_id} is already closed")
        closed = replace(outlet, closed_tick=tick)
        self.repo.put_outlet(closed)
        staff_ids = [row.agent_id for row in self.repo.staff(outlet_id)]
        return self._emit(
            OUTLET_CLOSED,
            {
                "outlet_id": outlet_id,
                "firm_id": outlet.firm_id,
                "reason": reason,
                "final_reach": outlet.reach,
                "staff_ids": staff_ids,
            },
            tick,
            subjects=(outlet_id, *staff_ids),
        )


class AvailabilityIndex:
    PUBLIC_KINDS: Final[frozenset[int]] = frozenset(
        {
            ARTICLE_PUBLISHED,
            ARTICLE_RETRACTED,
            OUTLET_FOUNDED,
            OUTLET_CLOSED,
        }
    )

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        memories: MemoryLookup,
        graph: SocialGraph,
        reporter_outlet: Callable[[str], str | None] | None = None,
        message_exchange: Callable[[str, str, str, int], bool] | None = None,
        document_access: Callable[[str, Event, int], bool] | None = None,
        public_kinds: frozenset[int] | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.memories = memories
        self.graph = graph
        self.reporter_outlet = reporter_outlet or (lambda _reporter: None)
        self.message_exchange = message_exchange or (
            lambda _reporter, _source, _message, _tick: False
        )
        self.document_access = document_access or (lambda _reporter, _event, _tick: False)
        self.public_kinds = self.PUBLIC_KINDS if public_kinds is None else public_kinds
        self._sources: dict[tuple[str, str], tuple[int, str]] = {}

    def channel(
        self,
        reporter_id: str,
        event: Event,
        tick: int,
    ) -> Literal["public", "witness", "source", "document"] | None:
        if event.kind in self.public_kinds or bool(event.payload.get("public", False)):
            return "public"
        if self.memories.holds_memory_of(reporter_id, event.seq):
            return "witness"
        for (reporter, source), (cultivated_tick, _message_id) in sorted(self._sources.items()):
            if reporter != reporter_id or cultivated_tick > tick:
                continue
            if (
                tick - cultivated_tick
                > self.graph.cfg.source_window_sim_days * self.clock.profile.ticks_per_sim_day
            ):
                continue
            if self.memories.holds_memory_of(source, event.seq):
                return "source"
        if self.document_access(reporter_id, event, tick):
            return "document"
        return None

    def available_to(self, reporter_id: str, event: Event, tick: int) -> bool:
        return self.channel(reporter_id, event, tick) is not None

    def cultivate(
        self,
        reporter_id: str,
        source_id: str,
        message_id: str,
        tick: int,
    ) -> Event | None:
        tie = self.graph.tie(reporter_id, source_id)
        if (
            tie is None
            or tie.type not in {"friend", "colleague"}
            or tie.trust < self.graph.cfg.line_threshold
            or not self.message_exchange(reporter_id, source_id, message_id, tick)
        ):
            return None
        self._sources[(reporter_id, source_id)] = (tick, message_id)
        subject_seqs = sorted(
            seq
            for seq in range(1, self.log.last_seq + 1)
            if self.memories.holds_memory_of(source_id, seq)
        )
        return self.log.stage(
            NewEvent(
                SOURCE_CULTIVATED,
                {
                    "reporter_id": reporter_id,
                    "source_id": source_id,
                    "outlet_id": self.reporter_outlet(reporter_id),
                    "message_id": message_id,
                    "subject_event_seqs": subject_seqs,
                },
                actor_id=reporter_id,
                subject_ids=(reporter_id, source_id),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )


class Newsworthiness:
    def __init__(
        self,
        *,
        events: Callable[[int, int], Sequence[Event]],
        outlets: OutletRegistry,
        availability: AvailabilityIndex,
        cfg: SocietySettings,
    ) -> None:
        self.events = events
        self.outlets = outlets
        self.availability = availability
        self.cfg = cfg

    def score(self, event: Event, outlet: Outlet, tick: int) -> float:
        age = max(0, tick - event.tick)
        recency = math.exp(-age / 24.0)
        payload = event.payload
        magnitude = float(payload.get("magnitude", payload.get("stakes", 0.5)))
        prominence = float(payload.get("prominence", 0.5))
        novelty = float(payload.get("novelty", recency))
        conflict = float(payload.get("conflict", 0.0))
        proximity = float(payload.get("proximity", 0.5))
        event_stance = float(payload.get("stance_value", 0.0) or 0.0)
        slant_fit = 1.0 - min(1.0, abs(outlet.slant - event_stance) / 2.0)
        terms = {
            "mag": magnitude,
            "prom": prominence,
            "nov": novelty,
            "conf": conflict,
            "prox": proximity,
            "slant": slant_fit,
        }
        return sum(self.cfg.newsworthiness_weights.get(name, 0.0) * terms[name] for name in terms)

    def story_list(
        self,
        outlet: Outlet,
        since_seq: int,
        tick: int,
        n: int,
    ) -> tuple[Event, ...]:
        _, reporters = self.outlets.newsroom(outlet.outlet_id)
        candidates = [
            event
            for event in self.events(since_seq, tick)
            if event.seq > since_seq
            and event.tick <= tick
            and any(
                self.availability.available_to(reporter_id, event, tick)
                for reporter_id in reporters
            )
        ]
        return tuple(
            sorted(candidates, key=lambda event: (-self.score(event, outlet, tick), event.seq))[
                : max(0, n)
            ]
        )


class EditorGate:
    def __init__(self) -> None:
        self._rewrite_attempts: dict[tuple[str, str | None, tuple[int, ...]], int] = {}

    def spike_reason(
        self,
        draft: Draft,
        outlet: Outlet,
    ) -> Literal["thin_sourcing", "slant_mismatch", "legal_risk", "budget"] | None:
        resolvable = [claim for claim in draft.claims if claim.predicate in RESOLVERS]
        if not resolvable:
            return "thin_sourcing"
        empty_share = sum(not claim.sourced_to_event_seqs for claim in draft.claims) / len(
            draft.claims
        )
        if empty_share > 1.0 - outlet.rigour:
            return "thin_sourcing"
        if draft.stance_value is not None:
            alignment = 1.0 - abs(draft.stance_value - outlet.slant) / 2.0
            if alignment < 0.25:
                return "slant_mismatch"
        if outlet.rigour >= 0.3 and draft.legal_risk:
            return "legal_risk"
        if draft.over_budget:
            return "budget"
        return None

    def review(
        self,
        draft: Draft,
        outlet: Outlet,
        tick: int,
    ) -> Literal["publish", "rewrite", "spike"]:
        del tick
        reason = self.spike_reason(draft, outlet)
        if reason is None:
            return "publish"
        if reason not in {"thin_sourcing", "slant_mismatch"}:
            return "spike"
        key = (draft.outlet_id, draft.reporter_id, draft.source_event_seqs)
        attempts = self._rewrite_attempts.get(key, 0)
        self._rewrite_attempts[key] = attempts + 1
        return "rewrite" if attempts == 0 else "spike"


def measured_slant(
    article: Article,
    checks: Sequence[CheckResult],
    outlet: Outlet,
) -> float:
    del article
    deviations: list[float] = []
    direction = -1.0 if outlet.slant < 0 else 1.0
    for result in checks:
        if not isinstance(result.claimed_value, (int, float)) or not isinstance(
            result.truth_value, (int, float)
        ):
            continue
        deviations.append(direction * (float(result.claimed_value) - float(result.truth_value)))
    return 0.0 if not deviations else math.fsum(deviations) / len(deviations)


class NewsResolver:
    slot: Final[InstitutionSlot] = InstitutionSlot.COMMUNICATION
    handles: Final[frozenset[ActionType]] = frozenset(
        {ActionType.PUBLISH_ARTICLE, ActionType.RETRACT}
    )

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        outlets: OutletRegistry,
        articles: ArticleStore,
        checker: ClaimChecker,
        ledger: NewsLedgerApi,
        cfg: SocietySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.outlets = outlets
        self.articles = articles
        self.checker = checker
        self.ledger = ledger
        self.cfg = cfg
        self._ordinal: dict[tuple[int, str], int] = defaultdict(int)

    def _employment(self, actor_id: str) -> tuple[Outlet, StaffMember] | None:
        for outlet in self.outlets.live():
            for staff in self.outlets.repo.staff(outlet.outlet_id):
                if staff.agent_id == actor_id:
                    return outlet, staff
        return None

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        employment = self._employment(action.actor_id)
        if action.type == ActionType.PUBLISH_ARTICLE:
            outlet_id = str(action.params.get("outlet_id", ""))
            if employment is None or employment[0].outlet_id != outlet_id:
                return GateFailure("capability", "publisher must be outlet newsroom staff")
            return None
        article_id = action.params.get("article_id")
        if article_id is not None:
            article = self.articles.get(str(article_id))
            if article is None or employment is None:
                return GateFailure("capability", "retraction requires the article editor")
            editor_id, _ = self.outlets.newsroom(article.outlet_id)
            if action.actor_id != editor_id:
                return GateFailure("capability", "only the outlet editor may retract an article")
        else:
            post_id = str(action.params.get("post_id", ""))
            posts = ctx.repositories.get("posts")
            post = posts.post(post_id) if hasattr(posts, "post") else None
            if post is None or getattr(post, "author_id", None) != action.actor_id:
                return GateFailure("capability", "only the post author may retract a post")
        return None

    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        del action, ctx
        return None

    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        del ctx
        if action.type == ActionType.PUBLISH_ARTICLE:
            outlet = self.outlets.get(str(action.params.get("outlet_id", "")))
            if outlet is None or outlet.closed_tick is not None:
                return GateFailure("resources", "outlet is missing or closed")
            return None
        article_id = action.params.get("article_id")
        if article_id is not None:
            article = self.articles.get(str(article_id))
            if article is None or article.retracted_tick is not None:
                return GateFailure("resources", "article is missing or already retracted")
        elif not action.params.get("post_id"):
            return GateFailure("resources", "retraction subject is missing")
        return None

    @staticmethod
    def _param(params: object, name: str, default: Any = None) -> Any:
        return (
            params.get(name, default)
            if isinstance(params, Mapping)
            else getattr(params, name, default)
        )

    def _claims(self, rows: Sequence[object]) -> tuple[Claim, ...]:
        claims = []
        for row in rows:
            refers = self._param(row, "refers_to", {})
            claims.append(
                Claim(
                    str(self._param(row, "claim_id")),
                    str(self._param(row, "text", "")),
                    str(self._param(refers, "entity_id", "")),
                    str(self._param(refers, "predicate", "")),
                    self._param(refers, "value"),
                    int(self._param(refers, "as_of_tick", 0)),
                    tuple(self._param(row, "sourced_to_event_seqs", ())),
                )
            )
        return tuple(claims)

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        events: list[Event] = []
        for row in sorted(
            actions, key=lambda item: (item.action.actor_id, str(item.action.action_id))
        ):
            action = row.action
            params = row.validated_params
            if action.type == ActionType.PUBLISH_ARTICLE:
                outlet_id = str(self._param(params, "outlet_id"))
                key = (tick, outlet_id)
                ordinal = self._ordinal[key]
                self._ordinal[key] += 1
                article_id = det_id(
                    "ar", "society.article", outlet_id, action.actor_id, tick, ordinal
                )
                claims = self._claims(self._param(params, "claims", ()))
                article = Article(
                    article_id,
                    outlet_id,
                    action.actor_id,
                    tick,
                    str(self._param(params, "headline", "")),
                    str(self._param(params, "body", "")),
                    tuple(self._param(params, "source_event_seqs", ())),
                    claims,
                    None,
                    None,
                    0,
                    None,
                )
                self.articles.put(article)
                events.append(
                    ctx.emit(
                        NewEvent(
                            ARTICLE_PUBLISHED,
                            _article_payload(
                                article,
                                self.outlets.get(outlet_id),
                                None,
                            ),
                            actor_id=action.actor_id,
                            subject_ids=(article_id, outlet_id, action.actor_id),
                        )
                    )
                )
                continue
            article_id = self._param(params, "article_id")
            post_id = self._param(params, "post_id")
            original_reach = 0
            retract_outlet_id: str | None = None
            if article_id is not None:
                retracted = self.articles.retract(str(article_id), tick)
                if retracted is None:
                    continue
                original_reach = retracted.reach
                retract_outlet_id = retracted.outlet_id
            correction_reach = round(original_reach * self.cfg.correction_reach_multiplier)
            events.append(
                ctx.emit(
                    NewEvent(
                        ARTICLE_RETRACTED,
                        {
                            "article_id": article_id,
                            "post_id": post_id,
                            "outlet_id": retract_outlet_id,
                            "author_id": action.actor_id,
                            "reason": self._param(params, "reason"),
                            "correction_text": self._param(params, "correction_text"),
                            "original_reach": original_reach,
                            "correction_reach": correction_reach,
                        },
                        actor_id=action.actor_id,
                        subject_ids=tuple(
                            str(value)
                            for value in (article_id, post_id, retract_outlet_id)
                            if value is not None
                        ),
                    )
                )
            )
        return tuple(events)

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]:
        actor_id = str(getattr(ctx.state, "agent_id", ""))
        employment = self._employment(actor_id)
        if action_type == ActionType.PUBLISH_ARTICLE and employment is not None:
            return ({"outlet_id": employment[0].outlet_id},)
        if action_type == ActionType.RETRACT and employment is not None:
            editor, _ = self.outlets.newsroom(employment[0].outlet_id)
            if editor == actor_id:
                return tuple(
                    {"article_id": article.article_id}
                    for article in self.articles.all()
                    if article.outlet_id == employment[0].outlet_id
                    and article.retracted_tick is None
                )
        return ()


NEWS_WRITE_SCHEMA: Final[Mapping[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["headline", "body", "claims"],
    "properties": {
        "headline": {"type": "string", "minLength": 1},
        "body": {"type": "string", "minLength": 1},
        "claims": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "claim_id",
                    "text",
                    "entity_id",
                    "predicate",
                    "value",
                    "as_of_tick",
                    "sourced_to_event_seqs",
                ],
                "properties": {
                    "claim_id": {"type": "string"},
                    "text": {"type": "string"},
                    "entity_id": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {},
                    "as_of_tick": {"type": "integer", "minimum": 0},
                    "sourced_to_event_seqs": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "additionalProperties": False,
            },
        },
        "stance_proposition": {"type": ["string", "null"]},
        "stance_value": {"type": ["number", "null"], "minimum": -1, "maximum": 1},
    },
    "additionalProperties": False,
}
NEWS_CADENCES: Final[tuple[Cadence, ...]] = (
    Cadence("news_cycle", "1d", 7, "polis.society.media.news", align="day"),
    Cadence("claim_check", "1d", 7, "polis.society.media.checker", align="day"),
    Cadence("outlet_close", "7d", 7, "polis.society.media.news", align="week"),
    Cadence("trust_accuracy", "7d", 7, "polis.society.beliefs", align="week"),
)


class NewsCycle:
    def __init__(
        self,
        *,
        router: LLMRouter,
        outlets: OutletRegistry,
        newsworthiness: Newsworthiness,
        availability: AvailabilityIndex,
        checker: ClaimChecker,
        beliefs: BeliefEngine,
        memories: MemoryLookup,
        platform: Platform,
        ledger: NewsLedgerApi,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        cfg: SocietySettings,
    ) -> None:
        self.router = router
        self.outlets = outlets
        self.newsworthiness = newsworthiness
        self.availability = availability
        self.checker = checker
        self.beliefs = beliefs
        self.memories = memories
        self.platform = platform
        self.ledger = ledger
        self.log = log
        self.clock = clock
        self.rng = rng
        self.cfg = cfg
        self.articles = ArticleStore(outlets.repo)
        self.editor = EditorGate()
        self._since_seq: dict[str, int] = defaultdict(int)
        self._checked: set[str] = set()

    def _emit(
        self,
        kind: int,
        payload: Mapping[str, object],
        tick: int,
        *,
        actor_id: str | None = None,
        subjects: Sequence[str] = (),
    ) -> Event:
        return self.log.stage(
            NewEvent(kind, payload, actor_id=actor_id, subject_ids=tuple(subjects)),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )

    def _request(
        self,
        outlet: Outlet,
        reporter_id: str,
        story: Event,
        tick: int,
    ) -> CallRequest:
        editorial_direction = (
            "left" if outlet.slant < -0.2 else "right" if outlet.slant > 0.2 else "centre"
        )
        verification_style = (
            "cautious" if outlet.rigour >= 0.7 else "rapid" if outlet.rigour < 0.3 else "standard"
        )
        variables = {
            "outlet_name": outlet.name,
            "editorial_direction": editorial_direction,
            "verification_style": verification_style,
            "reporter_id": reporter_id,
            "source_event": {
                "seq": story.seq,
                "tick": story.tick,
                "kind": story.kind,
                "payload": _safe_prompt_payload(story.payload),
            },
            "reporter_memories": list(self.memories.retrieve_recent_texts(reporter_id, tick, 8)),
        }
        return CallRequest(Purpose.NEWS_WRITE, reporter_id, tick, variables, NEWS_WRITE_SCHEMA)

    @staticmethod
    def _draft(
        outlet: Outlet,
        reporter_id: str,
        story: Event,
        parsed: Mapping[str, Any],
        call_id: str,
    ) -> Draft:
        claims = tuple(
            Claim(
                str(row["claim_id"]),
                str(row["text"]),
                str(row["entity_id"]),
                str(row["predicate"]),
                row["value"],
                int(row["as_of_tick"]),
                tuple(int(seq) for seq in row["sourced_to_event_seqs"]),
            )
            for row in cast(Sequence[Mapping[str, Any]], parsed.get("claims", ()))
        )
        return Draft(
            outlet.outlet_id,
            reporter_id,
            str(parsed.get("headline", "")),
            str(parsed.get("body", "")),
            (story.seq,),
            claims,
            call_id,
            stance_proposition=cast(str | None, parsed.get("stance_proposition")),
            stance_value=(
                None if parsed.get("stance_value") is None else float(parsed["stance_value"])
            ),
        )

    async def run_cycle(self, tick: int) -> Sequence[Event]:
        work: list[tuple[Outlet, str, Event, CallRequest]] = []
        for outlet in self.outlets.live():
            _, reporters = self.outlets.newsroom(outlet.outlet_id)
            stories = self.newsworthiness.story_list(
                outlet,
                self._since_seq[outlet.outlet_id],
                tick,
                len(reporters) * self.cfg.stories_per_reporter_per_cycle,
            )
            for reporter_id, story in zip(
                (
                    reporter
                    for reporter in reporters
                    for _ in range(self.cfg.stories_per_reporter_per_cycle)
                ),
                stories,
                strict=False,
            ):
                work.append(
                    (outlet, reporter_id, story, self._request(outlet, reporter_id, story, tick))
                )
            if stories:
                self._since_seq[outlet.outlet_id] = max(story.seq for story in stories)
        if not work:
            return ()
        results = await self.router.gather([item[3] for item in work])
        events: list[Event] = []
        published: list[Article] = []
        for (outlet, reporter_id, story, _), result in zip(work, results, strict=True):
            editor_id, _ = self.outlets.newsroom(outlet.outlet_id)
            draft_id = det_id(
                "dr",
                "society.article_draft",
                outlet.outlet_id,
                reporter_id,
                story.seq,
            )
            if not result.parsed_ok or result.parsed is None:
                events.append(
                    self._emit(
                        ARTICLE_SPIKED,
                        {
                            "draft_id": draft_id,
                            "outlet_id": outlet.outlet_id,
                            "reporter_id": reporter_id,
                            "editor_id": editor_id,
                            "reason": "thin_sourcing",
                            "rewrite_attempts": 0,
                        },
                        tick,
                        actor_id=reporter_id,
                        subjects=(outlet.outlet_id, reporter_id),
                    )
                )
                continue
            draft = self._draft(outlet, reporter_id, story, result.parsed, str(result.call_id))
            decision = self.editor.review(draft, outlet, tick)
            reason = self.editor.spike_reason(draft, outlet)
            rewrite_attempts = 0
            if decision == "rewrite":
                rewrite_attempts = 1
                request = self._request(outlet, reporter_id, story, tick)
                rewrite_variables = {
                    **request.variables,
                    "rewrite_reason": reason,
                    "prior_headline": draft.headline,
                    "prior_body": draft.body,
                }
                rewritten = (
                    await self.router.gather(
                        [
                            CallRequest(
                                request.purpose,
                                request.agent_id,
                                request.tick,
                                rewrite_variables,
                                request.schema,
                            )
                        ]
                    )
                )[0]
                if rewritten.parsed_ok and rewritten.parsed is not None:
                    draft = self._draft(
                        outlet,
                        reporter_id,
                        story,
                        rewritten.parsed,
                        str(rewritten.call_id),
                    )
                    decision = self.editor.review(draft, outlet, tick)
                    reason = self.editor.spike_reason(draft, outlet)
                else:
                    decision = "spike"
                    reason = "thin_sourcing"
            if decision != "publish":
                events.append(
                    self._emit(
                        ARTICLE_SPIKED,
                        {
                            "draft_id": draft_id,
                            "outlet_id": outlet.outlet_id,
                            "reporter_id": reporter_id,
                            "editor_id": editor_id,
                            "reason": reason or "thin_sourcing",
                            "rewrite_attempts": rewrite_attempts,
                        },
                        tick,
                        actor_id=reporter_id,
                        subjects=(outlet.outlet_id, reporter_id),
                    )
                )
                continue
            article_id = det_id(
                "ar",
                "society.article",
                outlet.outlet_id,
                reporter_id,
                tick,
                story.seq,
            )
            article = Article(
                article_id,
                outlet.outlet_id,
                reporter_id,
                tick,
                draft.headline,
                draft.body,
                draft.source_event_seqs,
                draft.claims,
                None,
                None,
                0,
                None,
                draft.stance_proposition,
                draft.stance_value,
            )
            self.articles.put(article)
            events.append(
                self._emit(
                    ARTICLE_PUBLISHED,
                    _article_payload(article, outlet, draft.llm_call_id),
                    tick,
                    actor_id=reporter_id,
                    subjects=(article_id, outlet.outlet_id, reporter_id),
                )
            )
            published.append(article)
        _, distribution_events = self.distribute(published, tick)
        events.extend(distribution_events)
        return tuple(events)

    async def check_pending(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for article in self.articles.all():
            if article.article_id in self._checked:
                continue
            checks: list[CheckResult] = []
            for claim in article.claims:
                result, event = self.checker.check(claim, "article", article.article_id, tick)
                checks.append(result)
                events.append(event)
            outlet = self.outlets.get(article.outlet_id)
            accuracy = self.checker.aggregate(checks)
            slant = None if outlet is None else measured_slant(article, checks, outlet)
            self.articles.set_accuracy(article.article_id, accuracy, slant)
            self._checked.add(article.article_id)
        return tuple(events)

    def distribute(
        self,
        articles: Sequence[Article],
        tick: int,
    ) -> tuple[Mapping[str, tuple[ArticleBrief, ...]], Sequence[Event]]:
        by_agent: dict[str, list[tuple[float, Article]]] = defaultdict(list)
        for agent_id in self.outlets.repo.audience_ids():
            for article in articles:
                outlet = self.outlets.get(article.outlet_id)
                if outlet is None:
                    continue
                trust = self.beliefs.value(agent_id, f"trust.outlet.{outlet.outlet_id}")
                topic_fit = 1.0
                proximity = 0.5
                subscribed = float(agent_id in self.outlets.repo.subscribers(outlet.outlet_id))
                reach = min(1.0, outlet.reach / self.cfg.reach_norm)
                weights = self.cfg.distribution_weights
                score = (
                    weights.get("trust", 0.0) * trust
                    + weights.get("topic", 0.0) * topic_fit
                    + weights.get("prox", 0.0) * proximity
                    + weights.get("sub", 0.0) * subscribed
                    + weights.get("reach", 0.0) * reach
                )
                by_agent[agent_id].append((score, article))
        news: dict[str, tuple[ArticleBrief, ...]] = {}
        reached: dict[str, list[str]] = defaultdict(list)
        for agent_id, rows in sorted(by_agent.items()):
            selected = [
                article
                for _, article in sorted(rows, key=lambda row: (-row[0], row[1].article_id))[:3]
            ]
            news[agent_id] = tuple(
                ArticleBrief(
                    article.article_id,
                    article.outlet_id,
                    article.headline,
                    article.tick,
                    article.stance_proposition,
                    article.stance_value,
                )
                for article in selected
            )
            for article in selected:
                if self.outlets.repo.note_exposure(agent_id, article.article_id, tick):
                    reached[article.article_id].append(agent_id)
                if article.stance_proposition is not None and article.stance_value is not None:
                    self.beliefs.apply_media(
                        agent_id,
                        article.stance_proposition,
                        article.stance_value,
                        article.outlet_id,
                        tick,
                    )
        events = []
        for article in sorted(articles, key=lambda row: row.article_id):
            audience = sorted(reached.get(article.article_id, ()))
            self.articles.set_reach(article.article_id, len(audience))
            events.append(
                self._emit(
                    ARTICLE_DISTRIBUTED,
                    {
                        "article_id": article.article_id,
                        "outlet_id": article.outlet_id,
                        "agent_ids": audience,
                        "reach": len(audience),
                        "impressions": len(audience),
                        "district_shares": {},
                        "subscriber_share": (
                            0.0
                            if not audience
                            else sum(
                                agent_id in self.outlets.repo.subscribers(article.outlet_id)
                                for agent_id in audience
                            )
                            / len(audience)
                        ),
                    },
                    tick,
                    subjects=(article.article_id, article.outlet_id, *audience),
                )
            )
        return news, tuple(events)

    def close_books(self, tick: int) -> Sequence[Event]:
        week_ticks = self.clock.profile.ticks_per_sim_day * 7
        period_start = max(0, tick - week_ticks)
        events = []
        exposures = self.outlets.repo.exposures()
        for outlet in self.outlets.live():
            article_ids = {
                article.article_id
                for article in self.articles.all()
                if article.outlet_id == outlet.outlet_id
            }
            impressions = sum(
                1
                for _, article_id, exposure_tick in exposures
                if article_id in article_ids and period_start <= exposure_tick <= tick
            )
            booking = self.ledger.book_outlet_revenue(
                outlet=outlet,
                period_start_tick=period_start,
                tick=tick,
                impressions=impressions,
                cpm_cents=self.cfg.cpm_cents,
                subscribers=self.outlets.repo.subscribers(outlet.outlet_id),
            )
            event = booking.event or self._emit(
                OUTLET_REVENUE_BOOKED,
                {
                    "outlet_id": outlet.outlet_id,
                    "period_start_tick": period_start,
                    "impressions": impressions,
                    "cpm_cents": self.cfg.cpm_cents,
                    "ad_revenue_cents": booking.ad_revenue_cents,
                    "subscription_cents": booking.subscription_cents,
                    "campaign_cents": booking.campaign_cents,
                    "advertisers": list(booking.advertisers),
                    "txn_ids": list(booking.txn_ids),
                },
                tick,
                subjects=(outlet.outlet_id, *booking.advertisers),
            )
            events.append(event)
        return tuple(events)

    def update_outlet_trust(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for agent_id, article_id, _ in self.outlets.repo.exposures():
            article = self.articles.get(article_id)
            if article is None or article.accuracy is None:
                continue
            proposition = f"trust.outlet.{article.outlet_id}"
            current = self.beliefs.value(agent_id, proposition)
            target = current + 0.05 * (article.accuracy - current)
            before = len(self.log.staged())
            self.beliefs.apply_llm_belief_updates(
                agent_id,
                tick,
                (
                    BeliefUpdate(
                        proposition,
                        target,
                        self.beliefs.confidence(agent_id, proposition),
                        f"accuracy:{article.article_id}",
                    ),
                ),
                None,
            )
            events.extend(self.log.staged()[before:])
        return tuple(events)


def _article_payload(
    article: Article,
    outlet: Outlet | None,
    llm_call_id: str | None,
) -> dict[str, object]:
    return {
        "article_id": article.article_id,
        "outlet_id": article.outlet_id,
        "reporter_id": article.reporter_id,
        "headline": article.headline,
        "body": article.body,
        "body_hash": sha256_hex(article.body.encode()),
        "source_event_seqs": list(article.source_event_seqs),
        "claims": [
            {
                "claim_id": claim.claim_id,
                "text": claim.text,
                "entity_id": claim.entity_id,
                "predicate": claim.predicate,
                "value": claim.value,
                "as_of_tick": claim.as_of_tick,
                "sourced_to_event_seqs": list(claim.sourced_to_event_seqs),
            }
            for claim in article.claims
        ],
        "slant_applied": article.slant_applied,
        "slant_at_write": None if outlet is None else outlet.slant,
        "rigour_at_write": None if outlet is None else outlet.rigour,
        "llm_call_id": llm_call_id,
        "stance_proposition": article.stance_proposition,
        "stance_value": article.stance_value,
    }


def _safe_prompt_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_prompt_payload(item)
            for key, item in value.items()
            if str(key) not in {"accuracy", "truthfulness"}
        }
    if isinstance(value, (list, tuple)):
        return [_safe_prompt_payload(item) for item in value]
    return value


def _epoch() -> Any:
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)


__all__ = [
    "NEWS_CADENCES",
    "NEWS_WRITE_SCHEMA",
    "Article",
    "ArticleStore",
    "AvailabilityIndex",
    "Claim",
    "Draft",
    "EditorGate",
    "MemoryNewsRepository",
    "NewsCycle",
    "NewsLedgerApi",
    "NewsRepository",
    "NewsResolver",
    "Newsworthiness",
    "NullNewsLedger",
    "Outlet",
    "OutletRegistry",
    "RevenueBooking",
    "StaffMember",
    "measured_slant",
]
