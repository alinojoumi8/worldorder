from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path as FilePath
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol, cast

from polis.agents.actions import (
    Action,
    ActionParams,
    ActionType,
    GateFailure,
    GateResult,
    InstitutionSlot,
    LegalityVerdict,
    ResolutionContext,
    ValidatedAction,
    ValidationContext,
)
from polis.agents.actions.params.law import (
    CommitCrimeParams,
    FileSuitParams,
    ReportCrimeParams,
    RetainCounselParams,
    RuleParams,
    SettleParams,
    TestifyParams,
)
from polis.config.mechanisms import mechanism
from polis.config.runtime import RuntimeOverlay
from polis.config.settings import LawSettings
from polis.events.kinds import (
    ARREST_MADE,
    CASE_SETTLED,
    COUNSEL_RETAINED,
    CRIME_COMMITTED,
    CRIME_DETECTED,
    CRIME_REPORTED,
    DAMAGES_AWARDED,
    EVIDENCE_ADMITTED,
    FINE_LEVIED,
    GARNISHMENT_COLLECTED,
    INCARCERATION_ENDED,
    INCARCERATION_STARTED,
    INVESTIGATION_CLOSED,
    INVESTIGATION_OPENED,
    JUDGMENT_RENDERED,
    LEGALITY_FLAGGED,
    POLICE_BUDGET_ALLOCATED,
    SUIT_FILED,
    TESTIMONY_GIVEN,
    TRIAL_HELD,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.rng import RngRegistry
from polis.llm.purposes import Purpose
from polis.llm.router import LLMRouter
from polis.society.graph import SocialGraph
from polis.society.media.checker import RESOLVERS, CheckResult, ClaimChecker
from polis.society.polity import OfficeRegister
from polis.society.protocols import BeliefChannel, MemoryLookup
from polis.world.api import Location, World

CrimeType = Literal[
    "theft",
    "fraud",
    "insider_trading",
    "assault",
    "contract_breach",
    "embezzlement",
    "perjury",
]
Path = Literal["explicit", "derived"]
CaseType = Literal["criminal", "civil"]
Verdict = Literal["guilty", "not_guilty", "liable", "not_liable", "dismissed"]

PUBLIC_KINDS: Final[frozenset[int]] = frozenset(
    {
        11010,
        11030,
        ARREST_MADE,
        JUDGMENT_RENDERED,
        INCARCERATION_STARTED,
        INCARCERATION_ENDED,
    }
)


def _stable_id(prefix: str, *parts: object) -> str:
    material = "|".join(str(part) for part in parts).encode()
    return f"{prefix}_{hashlib.sha256(material).hexdigest()[:20]}"


def _get(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _event_source(
    source: Sequence[Event] | Callable[[], Sequence[Event]] | None,
) -> tuple[Event, ...]:
    if source is None:
        return ()
    values = source() if callable(source) else source
    return tuple(sorted(values, key=lambda item: item.seq))


@dataclass(frozen=True, slots=True)
class Crime:
    crime_id: str
    type: CrimeType
    tick: int
    perpetrator_id: str
    victim_id: str | None
    amount_cents: int | None
    place_id: str | None
    district_id: str | None
    source_action_id: str
    concealment: float
    path: Path
    detected: bool = False
    detected_tick: int | None = None
    reported_by: str | None = None
    committed_event_seq: int | None = None


class CrimeRepository(Protocol):
    def add(self, crime: Crime) -> None: ...

    def get(self, crime_id: str) -> Crime | None: ...

    def by_source_action(self, action_id: str) -> Crime | None: ...

    def all(self) -> tuple[Crime, ...]: ...

    def update(self, crime: Crime) -> None: ...


class MemoryCrimeRepository:
    """Deterministic in-memory law state; projections are the durable read model."""

    def __init__(self) -> None:
        self._crimes: dict[str, Crime] = {}
        self._by_action: dict[str, str] = {}
        self._investigated: set[str] = set()

    def add(self, crime: Crime) -> None:
        existing = self._by_action.get(crime.source_action_id)
        if existing is not None and existing != crime.crime_id:
            return
        self._crimes[crime.crime_id] = crime
        self._by_action[crime.source_action_id] = crime.crime_id

    def get(self, crime_id: str) -> Crime | None:
        return self._crimes.get(crime_id)

    def by_source_action(self, action_id: str) -> Crime | None:
        crime_id = self._by_action.get(action_id)
        return None if crime_id is None else self._crimes.get(crime_id)

    def all(self) -> tuple[Crime, ...]:
        return tuple(sorted(self._crimes.values(), key=lambda item: (item.tick, item.crime_id)))

    def update(self, crime: Crime) -> None:
        if crime.crime_id in self._crimes:
            self._crimes[crime.crime_id] = crime

    def mark_investigated(self, crime_id: str) -> None:
        self._investigated.add(crime_id)

    def investigated(self, crime_id: str) -> bool:
        return crime_id in self._investigated

    def backfill_amount(self, crime_id: str, amount_cents: int) -> None:
        crime = self._crimes.get(crime_id)
        if crime is not None:
            self._crimes[crime_id] = replace(crime, amount_cents=max(0, amount_cents))

    def dump(self) -> Mapping[str, Any]:
        return {
            "crimes": [asdict(item) for item in self.all()],
            "investigated": sorted(self._investigated),
        }

    def load(self, state: Mapping[str, Any]) -> None:
        self._crimes.clear()
        self._by_action.clear()
        for row in state.get("crimes", ()):
            crime = Crime(**dict(row))
            self.add(crime)
        self._investigated = set(state.get("investigated", ()))


@dataclass(frozen=True, slots=True)
class Obligation:
    obligation_id: str
    debtor_id: str
    creditor_id: str
    amount_cents: int
    due_tick: int
    performed: bool = False
    source_event_seq: int | None = None


class ObligationIndex:
    """Logged obligations whose funded non-performance is contract breach."""

    def __init__(self, balance: Callable[[str], int] | None = None) -> None:
        self._obligations: dict[str, Obligation] = {}
        self._balance = balance or (lambda _agent_id: 0)

    def add(self, obligation: Obligation) -> None:
        self._obligations[obligation.obligation_id] = obligation

    def perform(self, obligation_id: str) -> None:
        obligation = self._obligations.get(obligation_id)
        if obligation is not None:
            self._obligations[obligation_id] = replace(obligation, performed=True)

    def due(self, agent_id: str, tick: int) -> tuple[Obligation, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._obligations.values()
                    if item.debtor_id == agent_id and item.due_tick <= tick and not item.performed
                ),
                key=lambda item: (item.due_tick, item.obligation_id),
            )
        )

    def missed_with_capacity(self, agent_id: str, tick: int) -> tuple[Obligation, ...]:
        available = max(0, self._balance(agent_id))
        result: list[Obligation] = []
        for item in self.due(agent_id, tick):
            if available < item.amount_cents:
                continue
            result.append(item)
            available -= item.amount_cents
        return tuple(result)


class MnpiIndex:
    MNPI_KINDS: Final[frozenset[int]] = frozenset({5011, 9010, 9030})

    def __init__(
        self,
        *,
        memories: MemoryLookup,
        cfg: LawSettings,
        clock: Clock,
        events: Sequence[Event] | Callable[[], Sequence[Event]] | None = None,
        issuer_for_symbol: Callable[[str], str] | None = None,
        public_kinds: frozenset[int] = PUBLIC_KINDS,
    ) -> None:
        self.memories = memories
        self.cfg = cfg
        self.clock = clock
        self.events = events
        self.issuer_for_symbol = issuer_for_symbol or (lambda symbol: symbol)
        self.public_kinds = public_kinds
        self._event_cache_key: tuple[int, int | None] | None = None
        self._event_cache: tuple[Event, ...] = ()
        self._disclosure_ticks: dict[int, int] = {}

    def _snapshot(self) -> tuple[Event, ...]:
        source = self.events
        if source is None:
            values: tuple[Event, ...] = ()
        else:
            values = tuple(source() if callable(source) else source)
        key = (len(values), values[-1].seq if values else None)
        if key == self._event_cache_key:
            return self._event_cache
        ordered = tuple(sorted(values, key=lambda item: item.seq))
        disclosure_ticks: dict[int, int] = {}
        for event in ordered:
            if event.kind not in {11010, 11030} and event.kind not in self.public_kinds:
                continue
            payload = event.payload
            cited = {
                int(value)
                for field in ("source_event_seqs", "event_seqs", "cited_event_seqs")
                for value in payload.get(field, ())
            }
            single = payload.get("source_event_seq")
            if single is not None:
                cited.add(int(single))
            for event_seq in cited:
                prior = disclosure_ticks.get(event_seq)
                if prior is None or event.tick < prior:
                    disclosure_ticks[event_seq] = event.tick
        self._event_cache_key = key
        self._event_cache = ordered
        self._disclosure_ticks = disclosure_ticks
        return ordered

    def _is_mnpi_kind(self, kind: int) -> bool:
        return kind in self.MNPI_KINDS or 6000 <= kind <= 6999

    def holds(self, agent_id: str, symbol: str, tick: int) -> tuple[bool, int | None]:
        issuer_id = self.issuer_for_symbol(symbol)
        window = self.cfg.mnpi_window_sim_days * self.clock.profile.ticks_per_sim_day
        for event in reversed(self._snapshot()):
            if (
                issuer_id not in event.subject_ids
                or not self._is_mnpi_kind(event.kind)
                or not 0 <= tick - event.tick <= window
            ):
                continue
            if not self.memories.holds_memory_of(agent_id, event.seq):
                continue
            if not self.publicly_disclosed(event.seq, tick):
                return True, event.seq
        return False, None

    def publicly_disclosed(self, event_seq: int, tick: int) -> bool:
        self._snapshot()
        disclosure_tick = self._disclosure_ticks.get(event_seq)
        return disclosure_tick is not None and disclosure_tick <= tick


class DerivedPredicate(Protocol):
    crime_type: CrimeType
    predicate_id: str
    applies_to: frozenset[ActionType]

    def test(
        self,
        action: Action,
        params: ActionParams,
        ctx: ValidationContext,
    ) -> LegalityVerdict | None: ...


def _pure_check(checker: ClaimChecker, claim: object) -> CheckResult:
    reference = _get(claim, "refers_to", claim)
    predicate = str(_get(reference, "predicate", ""))
    entity_id = str(_get(reference, "entity_id", ""))
    claimed_value = _get(reference, "value")
    as_of_tick = int(_get(reference, "as_of_tick", 0))
    claim_id = str(_get(claim, "claim_id", _stable_id("cl", predicate, entity_id, as_of_tick)))
    resolver = RESOLVERS.get(predicate)
    if resolver is None:
        return CheckResult(
            claim_id, predicate, entity_id, claimed_value, None, "unverifiable", None, ()
        )
    found = checker.ctx.lookup(predicate, entity_id, as_of_tick)
    if found is None:
        return CheckResult(
            claim_id, predicate, entity_id, claimed_value, None, "unverifiable", None, ()
        )
    truth, matched = found
    verdict, score = checker._compare(claimed_value, truth, resolver.kind)
    sourced = tuple(int(value) for value in _get(claim, "sourced_to_event_seqs", ()))
    return CheckResult(
        claim_id,
        predicate,
        entity_id,
        claimed_value,
        truth,
        verdict,
        score,
        tuple(sorted(set(matched) | set(sourced))),
    )


class FraudPredicate:
    crime_type: Final[CrimeType] = "fraud"
    predicate_id: Final = "derived.fraud.reliance"
    applies_to: Final = frozenset(
        {ActionType.PITCH, ActionType.APPLY_FOR_LOAN, ActionType.SET_PRICE}
    )

    def test(
        self, action: Action, params: ActionParams, ctx: ValidationContext
    ) -> LegalityVerdict | None:
        checker = cast(ClaimChecker | None, ctx.repositories.get("checker"))
        claim_rows = cast(
            Mapping[str, Sequence[object]], ctx.repositories.get("claims_by_action", {})
        )
        reliance_rows = cast(
            Mapping[str, Mapping[str, Any]], ctx.repositories.get("reliance_by_action", {})
        )
        reliance = reliance_rows.get(str(action.action_id))
        if checker is None or reliance is None:
            return None
        if not any(
            _pure_check(checker, claim).verdict == "contradicted"
            for claim in claim_rows.get(str(action.action_id), ())
        ):
            return None
        return LegalityVerdict(
            True,
            self.crime_type,
            str(reliance["counterparty_id"]),
            int(reliance.get("amount_cents", 0)),
        )


class InsiderTradingPredicate:
    crime_type: Final[CrimeType] = "insider_trading"
    predicate_id: Final = "derived.insider_trading.mnpi"
    applies_to: Final = frozenset({ActionType.SUBMIT_ORDER, ActionType.SHORT})

    def test(
        self, action: Action, params: ActionParams, ctx: ValidationContext
    ) -> LegalityVerdict | None:
        runtime = cast(RuntimeOverlay | None, ctx.repositories.get("runtime"))
        mnpi = cast(MnpiIndex | None, ctx.repositories.get("mnpi"))
        symbol = _get(params, "symbol")
        if runtime is None or mnpi is None or symbol is None:
            return None
        if not runtime.flag("regulation.finance.insider_trading_enforced", ctx.tick):
            return None
        holds, _source_seq = mnpi.holds(action.actor_id, str(symbol), ctx.tick)
        return LegalityVerdict(True, self.crime_type, None, None) if holds else None


class EmbezzlementPredicate:
    crime_type: Final[CrimeType] = "embezzlement"
    predicate_id: Final = "derived.embezzlement.firm_to_self"
    applies_to: Final = frozenset(ActionType)

    def test(
        self, action: Action, params: ActionParams, ctx: ValidationContext
    ) -> LegalityVerdict | None:
        del params
        transfers = cast(
            Mapping[str, Mapping[str, Any]], ctx.repositories.get("transfers_by_action", {})
        )
        row = transfers.get(str(action.action_id))
        if row is None:
            return None
        reason = str(row.get("reason", ""))
        if (
            not bool(row.get("has_firm_authority"))
            or str(row.get("to_owner_id")) != action.actor_id
            or reason in {"payroll", "dividend"}
        ):
            return None
        return LegalityVerdict(
            True,
            self.crime_type,
            str(row["from_owner_id"]),
            int(row["amount_cents"]),
        )


class ContractBreachPredicate:
    crime_type: Final[CrimeType] = "contract_breach"
    predicate_id: Final = "derived.contract_breach.funded_default"
    applies_to: Final = frozenset({ActionType.DEFAULT})

    def test(
        self, action: Action, params: ActionParams, ctx: ValidationContext
    ) -> LegalityVerdict | None:
        del params
        obligations = cast(ObligationIndex | None, ctx.repositories.get("obligations"))
        if obligations is None:
            return None
        missed = obligations.missed_with_capacity(action.actor_id, ctx.tick)
        if not missed:
            return None
        item = missed[0]
        return LegalityVerdict(
            True,
            self.crime_type,
            item.creditor_id,
            item.amount_cents,
        )


class PerjuryPredicate:
    crime_type: Final[CrimeType] = "perjury"
    predicate_id: Final = "derived.perjury.first_hand_contradiction"
    applies_to: Final = frozenset({ActionType.TESTIFY})

    def test(
        self, action: Action, params: ActionParams, ctx: ValidationContext
    ) -> LegalityVerdict | None:
        checker = cast(ClaimChecker | None, ctx.repositories.get("checker"))
        memories = cast(MemoryLookup | None, ctx.repositories.get("memories"))
        if checker is None or memories is None or not isinstance(params, TestifyParams):
            return None
        for claim in params.claims:
            result = _pure_check(checker, claim)
            if result.verdict != "contradicted":
                continue
            first_hand = any(
                memories.holds_memory_of(action.actor_id, seq)
                for seq in claim.sourced_to_event_seqs
            )
            if first_hand:
                return LegalityVerdict(True, self.crime_type, None, 0)
        return None


DERIVED_PREDICATES: Final[tuple[DerivedPredicate, ...]] = cast(
    tuple[DerivedPredicate, ...],
    (
        FraudPredicate(),
        InsiderTradingPredicate(),
        EmbezzlementPredicate(),
        ContractBreachPredicate(),
        PerjuryPredicate(),
    ),
)


class LawLegalityOracle:
    """C10 legality oracle: flags offences, persists them, and never rejects."""

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        runtime: RuntimeOverlay,
        mnpi: MnpiIndex,
        obligations: ObligationIndex,
        checker: ClaimChecker,
        memories: MemoryLookup,
        repo: CrimeRepository,
        cfg: LawSettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.runtime = runtime
        self.mnpi = mnpi
        self.obligations = obligations
        self.checker = checker
        self.memories = memories
        self.repo = repo
        self.cfg = cfg

    def _predicate_context(self, ctx: ValidationContext) -> ValidationContext:
        repositories = {
            **ctx.repositories,
            "runtime": self.runtime,
            "mnpi": self.mnpi,
            "obligations": self.obligations,
            "checker": self.checker,
            "memories": self.memories,
        }
        return replace(ctx, repositories=MappingProxyType(repositories))

    def _concealment(self, state: object) -> float:
        skills = _get(state, "skills", {})
        law = float(_get(skills, "law", 0.0))
        finance = float(_get(skills, "finance", 0.0))
        traits = _get(state, "traits", {})
        honesty = float(_get(traits, "honesty", _get(state, "honesty", 0.5)))
        return max(0.0, min(0.85, 0.10 + 0.45 * max(law, finance) + 0.15 * (1 - honesty)))

    def _record(
        self,
        verdict: LegalityVerdict,
        action: Action,
        ctx: ValidationContext,
        *,
        path: Path,
        predicate_id: str,
    ) -> LegalityVerdict:
        existing = self.repo.by_source_action(str(action.action_id))
        if existing is not None:
            return LegalityVerdict(
                True,
                existing.type,
                existing.victim_id,
                existing.amount_cents,
                existing.crime_id,
            )
        crime_type = cast(CrimeType, verdict.crime_type)
        crime_id = _stable_id("cr", self.log.run_id, action.action_id)
        location = _get(ctx.observation, "location", ctx.observation)
        place_id = _get(location, "place_id")
        district_id = _get(location, "district_id")
        flagged = self.log.stage(
            NewEvent(
                LEGALITY_FLAGGED,
                {
                    "action_id": str(action.action_id),
                    "actor_id": action.actor_id,
                    "action_type": action.type.value,
                    "offence_type": crime_type,
                    "path": path,
                    "predicate_id": predicate_id,
                    "crime_id": crime_id,
                },
                actor_id=action.actor_id,
                subject_ids=(verdict.victim_id,) if verdict.victim_id else (),
            ),
            tick=ctx.tick,
            sim_time=self.clock.sim_time_at(ctx.tick),
        )
        committed = self.log.stage(
            NewEvent(
                CRIME_COMMITTED,
                {
                    "crime_id": crime_id,
                    "type": crime_type,
                    "perpetrator_id": action.actor_id,
                    "victim_id": verdict.victim_id,
                    "amount_cents": verdict.amount_cents,
                    "place_id": place_id,
                    "district_id": district_id,
                    "source_action_id": str(action.action_id),
                    "concealment": self._concealment(ctx.state),
                    "detected": False,
                    "path": path,
                },
                actor_id=action.actor_id,
                subject_ids=tuple(value for value in (action.actor_id, verdict.victim_id) if value),
                cause_seq=flagged.seq,
            ),
            tick=ctx.tick,
            sim_time=self.clock.sim_time_at(ctx.tick),
        )
        crime = Crime(
            crime_id,
            crime_type,
            ctx.tick,
            action.actor_id,
            verdict.victim_id,
            verdict.amount_cents,
            place_id,
            district_id,
            str(action.action_id),
            self._concealment(ctx.state),
            path,
            committed_event_seq=committed.seq,
        )
        self.repo.add(crime)
        return LegalityVerdict(
            True,
            crime_type,
            verdict.victim_id,
            verdict.amount_cents,
            crime_id,
        )

    def assess(
        self,
        action: Action,
        params: ActionParams,
        ctx: ValidationContext,
    ) -> LegalityVerdict:
        predicate_ctx = self._predicate_context(ctx)
        for predicate in DERIVED_PREDICATES:
            if action.type not in predicate.applies_to:
                continue
            verdict = predicate.test(action, params, predicate_ctx)
            if verdict is not None:
                return self._record(
                    verdict,
                    action,
                    ctx,
                    path="derived",
                    predicate_id=predicate.predicate_id,
                )
        if action.type is ActionType.COMMIT_CRIME and isinstance(params, CommitCrimeParams):
            verdict = LegalityVerdict(
                True,
                params.crime_type,
                params.victim_id,
                params.amount_cents,
            )
            return self._record(
                verdict,
                action,
                ctx,
                path="explicit",
                predicate_id="explicit.commit_crime",
            )
        return LegalityVerdict(is_crime=False)


_CRIME_DETECTION_ENTAILS = (
    "detection probability rises monotonically with police.budget_cents. Therefore the "
    "observation that 'more police means more crimes detected' is definitional and is NOT "
    "a finding. The studiable quantity for B5 is the elasticity of the COMMITTED crime "
    "rate — the count of 13010 events, detected or not — with respect to p_detect, which "
    "operates only through agents' own decisions. Every B5 result must be stated over "
    "committed crimes; any result stated over detected crimes is rejected by the reviewer "
    "checklist."
)


class DetectionEngine:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        runtime: RuntimeOverlay,
        repo: CrimeRepository,
        world: World,
        cfg: LawSettings,
        district_shares: Mapping[str, float] | None = None,
        profiles: Mapping[str, object] | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.runtime = runtime
        self.repo = repo
        self.world = world
        self.cfg = cfg
        self.district_shares = district_shares
        self.profiles = profiles or {}

    def concealment(self, agent_id: str) -> float:
        profile = self.profiles.get(agent_id, {})
        skills = _get(profile, "skills", {})
        traits = _get(profile, "traits", {})
        law = float(_get(skills, "law", 0.0))
        finance = float(_get(skills, "finance", 0.0))
        honesty = float(_get(traits, "honesty", _get(profile, "honesty", 0.5)))
        return max(0.0, min(0.85, 0.10 + 0.45 * max(law, finance) + 0.15 * (1 - honesty)))

    def _district_share(self, district_id: str | None) -> float:
        district_ids = tuple(sorted(item.district_id for item in self.world.districts))
        if not district_ids:
            return 1.0
        if self.district_shares is None:
            return 1.0 / len(district_ids)
        return max(0.0, float(self.district_shares.get(str(district_id), 0.0)))

    def _population(self, district_id: str | None) -> int:
        count = 0
        for location in self.world.locations.values():
            if location.district_id != district_id or location.place_id is None:
                continue
            if self.world.place(location.place_id).type != "prison":
                count += 1
        return max(1, count)

    @mechanism("crime_detection", entails=_CRIME_DETECTION_ENTAILS)
    def p_detect(self, crime: Crime, tick: int) -> float:
        budget = self.runtime.cents("police.budget_cents", tick)
        capacity = (
            budget
            * self._district_share(crime.district_id)
            / (self._population(crime.district_id) * self.cfg.cost_per_patrol_cents)
        )
        witnesses = 0
        if crime.place_id is not None:
            witnesses = len(
                set(self.world.occupancy(crime.place_id)) - {crime.perpetrator_id, crime.victim_id}
            )
        witness_bonus = min(
            self.cfg.witness_bonus_cap,
            witnesses * self.cfg.witness_bonus_per_witness,
        )
        probability = (
            self.cfg.base_detect[crime.type]
            * capacity**self.cfg.capacity_exponent
            * (1.0 + witness_bonus)
            * self.cfg.victim_awareness[crime.type]
            * (1.0 - crime.concealment)
        )
        return float(max(0.0, min(0.98, probability)))

    def run_hazard(self, tick: int) -> Sequence[Event]:
        window_ticks = self.cfg.detection_window_sim_days * self.clock.profile.ticks_per_sim_day
        events: list[Event] = []
        for crime in self.repo.all():
            age = tick - crime.tick
            if crime.detected or age < 0 or age > window_ticks:
                continue
            probability = self.p_detect(crime, tick)
            draw = self.rng.get("law.detect", crime.crime_id, tick).random()
            if draw >= probability / window_ticks:
                continue
            event = self.log.stage(
                NewEvent(
                    CRIME_DETECTED,
                    {
                        "crime_id": crime.crime_id,
                        "detector": "audit",
                        "p_detect": probability,
                        "ticks_since_commission": age,
                    },
                    subject_ids=(crime.crime_id, crime.perpetrator_id),
                    cause_seq=crime.committed_event_seq,
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            self.repo.update(replace(crime, detected=True, detected_tick=tick))
            events.append(event)
        return tuple(events)


@dataclass(frozen=True, slots=True)
class Investigation:
    case_file_id: str
    crime_id: str
    opened_tick: int
    evidence_event_seqs: tuple[int, ...]
    evidence_strength: float
    outcome: Literal["charged", "unsolved", "no_crime"]


class PoliceService:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        runtime: RuntimeOverlay,
        repo: CrimeRepository,
        world: World,
        cfg: LawSettings,
        events: Sequence[Event] | Callable[[], Sequence[Event]] | None = None,
        chief_allocator: Callable[[str, tuple[str, ...], int], Mapping[str, float]] | None = None,
        officer_id: str = "ag_police",
        criminal_filer: Callable[[Crime, tuple[int, ...], float, int], Sequence[Event]]
        | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.runtime = runtime
        self.repo = repo
        self.world = world
        self.cfg = cfg
        self.events = events
        self.chief_allocator = chief_allocator
        self.officer_id = officer_id
        self.criminal_filer = criminal_filer
        self.district_shares: dict[str, float] = {}
        self.investigations: dict[str, Investigation] = {}

    def allocate_budget(self, chief_id: str | None, tick: int) -> Event:
        districts = tuple(sorted(item.district_id for item in self.world.districts))
        if chief_id is not None and self.chief_allocator is not None:
            proposed = self.chief_allocator(chief_id, districts, tick)
            shares = {
                district: max(0.0, float(proposed.get(district, 0.0))) for district in districts
            }
            total = math.fsum(shares.values())
            shares = (
                {key: value / total for key, value in shares.items()}
                if total > 0
                else {key: 1 / len(districts) for key in districts}
            )
        else:
            shares = {key: 1 / len(districts) for key in districts} if districts else {}
        self.district_shares = shares
        total_cents = self.runtime.cents("police.budget_cents", tick)
        patrol_units = total_cents // self.cfg.cost_per_patrol_cents
        event = self.log.stage(
            NewEvent(
                POLICE_BUDGET_ALLOCATED,
                {
                    "total_cents": total_cents,
                    "chief_id": chief_id,
                    "district_shares": shares,
                    "patrol_units": patrol_units,
                    "audit_units": max(0, patrol_units // 5),
                    "investigation_slots": self.investigation_slots(tick),
                },
                actor_id=chief_id,
                subject_ids=districts,
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        return event

    def investigation_slots(self, tick: int) -> int:
        return max(
            0,
            self.runtime.cents("police.budget_cents", tick)
            // self.cfg.cost_per_investigation_cents,
        )

    def _severity(self, crime: Crime, tick: int) -> float:
        type_weight = {
            "assault": 5.0,
            "embezzlement": 4.0,
            "fraud": 3.5,
            "insider_trading": 3.0,
            "theft": 2.5,
            "perjury": 2.0,
            "contract_breach": 1.0,
        }[crime.type]
        return (
            math.log1p(max(0, crime.amount_cents or 0)) + type_weight + 0.01 * (tick - crime.tick)
        )

    def evidence(self, crime: Crime, tick: int) -> tuple[tuple[int, ...], float]:
        window = self.cfg.evidence_window_sim_days * self.clock.profile.ticks_per_sim_day
        relevant: list[tuple[Event, float]] = []
        subjects = {crime.perpetrator_id, crime.victim_id}
        for event in _event_source(self.events):
            if not crime.tick - window <= event.tick <= crime.tick + window:
                continue
            if not subjects.intersection(event.subject_ids):
                continue
            if 5000 <= event.kind <= 9999:
                directness = 1.0
            elif event.kind == TESTIMONY_GIVEN:
                directness = 0.6
            elif 3000 <= event.kind <= 3999:
                directness = 0.3
            else:
                directness = 0.1
            relevant.append((event, directness))
        corroboration = 1.0 + 0.2 * max(0, len(relevant) - 1)
        strength = min(
            1.0,
            math.fsum(weight for _, weight in relevant) * corroboration / self.cfg.strength_norm,
        )
        return tuple(event.seq for event, _ in relevant), strength

    def match_report(self, params: ReportCrimeParams) -> Crime | None:
        if params.crime_id is not None:
            return self.repo.get(params.crime_id)
        candidates = (
            crime
            for crime in self.repo.all()
            if (params.suspect_id is None or crime.perpetrator_id == params.suspect_id)
            and (params.crime_type is None or crime.type == params.crime_type)
            and (
                not params.evidence_event_seqs
                or crime.committed_event_seq in params.evidence_event_seqs
            )
        )
        return max(candidates, key=lambda item: (item.tick, item.crime_id), default=None)

    def report(
        self,
        reporter_id: str,
        params: ReportCrimeParams,
        tick: int,
    ) -> Sequence[Event]:
        crime = self.match_report(params)
        if crime is None:
            return ()
        event = self.log.stage(
            NewEvent(
                CRIME_REPORTED,
                {
                    "crime_id": crime.crime_id,
                    "reporter_id": reporter_id,
                    "latency_ticks": tick - crime.tick,
                    "evidence_event_seqs": list(params.evidence_event_seqs),
                },
                actor_id=reporter_id,
                subject_ids=(crime.crime_id, crime.perpetrator_id),
                cause_seq=crime.committed_event_seq,
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        self.repo.update(replace(crime, reported_by=reporter_id))
        return (event,)

    def process_queue(self, tick: int) -> Sequence[Event]:
        candidates = [
            crime
            for crime in self.repo.all()
            if (crime.detected or crime.reported_by is not None)
            and not bool(
                getattr(self.repo, "investigated", lambda _crime_id: False)(crime.crime_id)
            )
        ]
        candidates.sort(key=lambda item: (-self._severity(item, tick), item.crime_id))
        events: list[Event] = []
        for position, crime in enumerate(candidates[: self.investigation_slots(tick)], start=1):
            case_file_id = _stable_id("inv", crime.crime_id)
            opened = self.log.stage(
                NewEvent(
                    INVESTIGATION_OPENED,
                    {
                        "case_file_id": case_file_id,
                        "crime_id": crime.crime_id,
                        "officer_id": self.officer_id,
                        "severity": self._severity(crime, tick),
                        "queue_position": position,
                    },
                    actor_id=self.officer_id,
                    subject_ids=(crime.crime_id, crime.perpetrator_id),
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            seqs, strength = self.evidence(crime, tick)
            charged = strength >= self.cfg.charge_threshold
            outcome: Literal["charged", "unsolved", "no_crime"] = (
                "charged" if charged else "unsolved"
            )
            closed = self.log.stage(
                NewEvent(
                    INVESTIGATION_CLOSED,
                    {
                        "case_file_id": case_file_id,
                        "outcome": outcome,
                        "evidence_strength": strength,
                        "evidence_event_seqs": list(seqs),
                    },
                    actor_id=self.officer_id,
                    subject_ids=(crime.crime_id, crime.perpetrator_id),
                    cause_seq=opened.seq,
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            events.extend((opened, closed))
            if charged:
                place_id = self.world.locations.get(
                    crime.perpetrator_id,
                    Location(None, crime.district_id or "", 0, 0),
                ).place_id
                arrest = self.log.stage(
                    NewEvent(
                        ARREST_MADE,
                        {
                            "crime_id": crime.crime_id,
                            "suspect_id": crime.perpetrator_id,
                            "officer_id": self.officer_id,
                            "place_id": place_id,
                            "evidence_strength": strength,
                        },
                        actor_id=self.officer_id,
                        subject_ids=(crime.perpetrator_id,),
                        cause_seq=closed.seq,
                    ),
                    tick=tick,
                    sim_time=self.clock.sim_time_at(tick),
                )
                events.append(arrest)
                if self.criminal_filer is not None:
                    events.extend(self.criminal_filer(crime, seqs, strength, tick))
            marker = getattr(self.repo, "mark_investigated", None)
            if marker is not None:
                marker(crime.crime_id)
            self.investigations[case_file_id] = Investigation(
                case_file_id, crime.crime_id, tick, seqs, strength, outcome
            )
        return tuple(events)


@dataclass(frozen=True, slots=True)
class Range:
    fine_lo: int
    fine_hi: int
    sentence_lo_ticks: int
    sentence_hi_ticks: int


STATUTORY: Final[Mapping[CrimeType, Range]] = MappingProxyType(
    {
        "theft": Range(0, 0, 0, 90),
        "assault": Range(5_000, 50_000, 30, 365),
        "fraud": Range(0, 0, 90, 1080),
        "insider_trading": Range(0, 0, 0, 720),
        "embezzlement": Range(0, 0, 180, 1440),
        "contract_breach": Range(0, 0, 0, 0),
        "perjury": Range(10_000, 100_000, 30, 540),
    }
)


def statutory_range(
    crime_type: CrimeType,
    amount_cents: int | None,
    tick: int,
    runtime: RuntimeOverlay,
    ticks_per_sim_day: int,
) -> Range:
    base = STATUTORY[crime_type]
    amount = max(0, amount_cents or 0)
    if crime_type in {"theft", "contract_breach"}:
        fine_lo, fine_hi = amount, amount * (3 if crime_type == "theft" else 2)
    elif crime_type in {"fraud", "embezzlement"}:
        fine_lo, fine_hi = amount * 2, amount * 5
    elif crime_type == "insider_trading":
        fine_lo, fine_hi = amount * 3, amount * 10
    else:
        fine_lo, fine_hi = base.fine_lo, base.fine_hi
    multiplier_bp = runtime.bp("sentencing.multiplier_bp", tick)
    ticks_per_day = max(1, ticks_per_sim_day)
    return Range(
        fine_lo * multiplier_bp // 10_000,
        fine_hi * multiplier_bp // 10_000,
        base.sentence_lo_ticks * ticks_per_day * multiplier_bp // 10_000,
        base.sentence_hi_ticks * ticks_per_day * multiplier_bp // 10_000,
    )


@dataclass(frozen=True, slots=True)
class Judgment:
    verdict: Verdict
    findings: tuple[str, ...]
    fine_cents: int
    sentence_ticks: int
    damages_cents: int
    restitution_cents: int
    disqualification_ticks: int
    clamped: tuple[str, ...]
    origin: Literal["llm", "bench"]
    llm_call_id: str | None


@dataclass(frozen=True, slots=True)
class CourtCase:
    case_id: str
    type: CaseType
    plaintiff_id: str | None
    defendant_id: str
    crime_id: str | None
    cause_of_action: str
    claim_cents: int
    filed_tick: int
    evidence_event_seqs: tuple[int, ...] = ()
    plaintiff_counsel_id: str | None = None
    defence_counsel_id: str | None = None
    witness_ids: tuple[str, ...] = ()
    admitted_event_seqs: tuple[int, ...] = ()
    evidence_strength: float = 0.0
    judge_id: str | None = None
    resolved_tick: int | None = None
    judgment: Judgment | None = None
    status: Literal["open", "settled", "resolved"] = "open"


class CourtRepository(Protocol):
    def add(self, case: CourtCase) -> None: ...

    def get(self, case_id: str) -> CourtCase | None: ...

    def update(self, case: CourtCase) -> None: ...

    def open_cases(self) -> tuple[CourtCase, ...]: ...


class MemoryCourtRepository:
    def __init__(self) -> None:
        self._cases: dict[str, CourtCase] = {}

    def add(self, case: CourtCase) -> None:
        self._cases.setdefault(case.case_id, case)

    def get(self, case_id: str) -> CourtCase | None:
        return self._cases.get(case_id)

    def update(self, case: CourtCase) -> None:
        if case.case_id in self._cases:
            self._cases[case.case_id] = case

    def open_cases(self) -> tuple[CourtCase, ...]:
        return tuple(
            sorted(
                (case for case in self._cases.values() if case.status == "open"),
                key=lambda item: (item.filed_tick, item.case_id),
            )
        )

    def all(self) -> tuple[CourtCase, ...]:
        return tuple(sorted(self._cases.values(), key=lambda item: item.case_id))

    def dump(self) -> Mapping[str, Any]:
        return {"cases": [asdict(item) for item in self.all()]}

    def load(self, state: Mapping[str, Any]) -> None:
        self._cases.clear()
        for row in state.get("cases", ()):
            values = dict(row)
            judgment = values.get("judgment")
            if isinstance(judgment, Mapping):
                values["judgment"] = Judgment(**dict(judgment))
            case = CourtCase(**values)
            self._cases[case.case_id] = case


class LawLedger(Protocol):
    def compatible_balance(self, payer_id: str, payee_id: str) -> int: ...

    def can_pay(self, payer_id: str, cents: int, payee_id: str | None = None) -> bool: ...

    def next_transfer_id(self, tick: int) -> str: ...

    def post_transfer(
        self,
        payer_id: str,
        payee_id: str,
        cents: int,
        *,
        reason: str,
        tick: int,
        cause: Event,
    ) -> str: ...


class NullLawLedger:
    def compatible_balance(self, payer_id: str, payee_id: str) -> int:
        del payer_id, payee_id
        return 0

    def can_pay(self, payer_id: str, cents: int, payee_id: str | None = None) -> bool:
        del payer_id, payee_id
        return cents == 0

    def next_transfer_id(self, tick: int) -> str:
        return _stable_id("tx", tick, "null")

    def post_transfer(
        self,
        payer_id: str,
        payee_id: str,
        cents: int,
        *,
        reason: str,
        tick: int,
        cause: Event,
    ) -> str:
        del payer_id, payee_id, reason, tick, cause
        if cents:
            raise RuntimeError("null law ledger cannot move money")
        return _stable_id("tx", "zero")


_BENCH_RULE_ENTAILS = (
    "when the JUDGE call fails, conviction is a monotone function of evidence_strength "
    "alone, with no consideration of the defendant's identity. The bench-rule share of "
    "judgments is reported per run; any finding about judicial bias must be computed over "
    "LLM-decided judgments only, and the bench-rule share is the ceiling on how much of "
    "the docket that excludes."
)


@mechanism("bench_rule", entails=_BENCH_RULE_ENTAILS)
def bench_verdict(
    evidence_strength: float,
    threshold: float,
    lo: int,
    hi: int,
) -> tuple[bool, int]:
    convicted = evidence_strength >= threshold
    if not convicted:
        return False, 0
    if threshold >= 1.0:
        return True, lo
    fraction = min(1.0, max(0.0, (evidence_strength - threshold) / (1 - threshold)))
    return True, round(lo + (hi - lo) * fraction)


def _judge_schema() -> Mapping[str, Any]:
    path = FilePath(__file__).resolve().parents[2] / "prompts" / "schemas" / "judge.schema.json"
    return cast(Mapping[str, Any], json.loads(path.read_text(encoding="utf-8")))


class CourtService:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        runtime: RuntimeOverlay,
        repo: CourtRepository,
        crimes: CrimeRepository,
        ledger: LawLedger,
        offices: OfficeRegister,
        memories: MemoryLookup,
        checker: ClaimChecker,
        cfg: LawSettings,
        router: LLMRouter | None = None,
        events: Sequence[Event] | Callable[[], Sequence[Event]] | None = None,
        skill_law: Callable[[str], float] | None = None,
        wealth_percentile: Callable[[str], float] | None = None,
        available_lawyers: Callable[[int], Sequence[tuple[str, int, float]]] | None = None,
        penalties: PenaltyService | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.runtime = runtime
        self.repo = repo
        self.crimes = crimes
        self.ledger = ledger
        self.offices = offices
        self.memories = memories
        self.checker = checker
        self.cfg = cfg
        self.router = router
        self.events = events
        self.skill_law = skill_law or (lambda _agent_id: 0.0)
        self.wealth_percentile = wealth_percentile or (lambda _agent_id: 1.0)
        self.available_lawyers = available_lawyers or (lambda _tick: ())
        self.penalties = penalties

    def cases_per_session(self, tick: int) -> int:
        return max(
            0,
            self.runtime.cents("courts.budget_cents", tick) // self.cfg.cost_per_case_cents,
        )

    def _stage_transfer_event(
        self,
        *,
        kind: int,
        payload: Mapping[str, Any],
        payer_id: str,
        payee_id: str,
        amount_cents: int,
        reason: str,
        tick: int,
        actor_id: str | None,
        subjects: tuple[str, ...],
    ) -> Event:
        predicted = self.ledger.next_transfer_id(tick) if amount_cents > 0 else None
        event_payload = {**payload, "txn_id": predicted}
        event = self.log.stage(
            NewEvent(
                kind,
                event_payload,
                actor_id=actor_id,
                subject_ids=subjects,
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        if amount_cents > 0:
            try:
                actual = self.ledger.post_transfer(
                    payer_id,
                    payee_id,
                    amount_cents,
                    reason=reason,
                    tick=tick,
                    cause=event,
                )
                if actual != predicted:
                    raise RuntimeError("law ledger transaction ordinal diverged")
            except Exception:
                self.log.rollback()
                raise
        return event

    def file(
        self,
        filer_id: str,
        params: FileSuitParams,
        tick: int,
    ) -> Sequence[Event]:
        cause = params.cause_of_action or params.claim or ""
        claim_cents = int(params.claim_cents or params.amount_cents or 0)
        waived = self.wealth_percentile(filer_id) <= self.cfg.filing_fee_waiver_pct
        fee = 0 if waived or params.case_type == "criminal" else self.cfg.filing_fee_cents
        case_id = _stable_id(
            "case",
            self.log.run_id,
            filer_id,
            params.defendant_id,
            tick,
            len(self.repo.open_cases()),
        )
        event = self._stage_transfer_event(
            kind=SUIT_FILED,
            payload={
                "case_id": case_id,
                "type": params.case_type,
                "plaintiff_id": filer_id,
                "defendant_id": params.defendant_id,
                "crime_id": params.crime_id,
                "cause_of_action": cause,
                "claim_cents": claim_cents,
                "filing_fee_cents": fee,
                "evidence_event_seqs": list(tuple(sorted(set(params.evidence_event_seqs)))),
            },
            payer_id=filer_id,
            payee_id="government",
            amount_cents=fee,
            reason="transfer",
            tick=tick,
            actor_id=filer_id,
            subjects=(filer_id, params.defendant_id),
        )
        self.repo.add(
            CourtCase(
                case_id,
                params.case_type,
                filer_id,
                params.defendant_id,
                params.crime_id,
                cause,
                claim_cents,
                tick,
                tuple(sorted(set(params.evidence_event_seqs))),
            )
        )
        return (event,)

    def file_criminal(
        self,
        crime: Crime,
        evidence_event_seqs: tuple[int, ...],
        evidence_strength: float,
        tick: int,
    ) -> Sequence[Event]:
        filer = cast(str | None, self.offices.holder("police_chief", tick)) or "government"
        params = FileSuitParams(
            case_type="criminal",
            defendant_id=crime.perpetrator_id,
            cause_of_action=crime.type,
            claim_cents=crime.amount_cents or 0,
            crime_id=crime.crime_id,
            evidence_event_seqs=evidence_event_seqs,
        )
        events = self.file(filer, params, tick)
        case_id = str(events[0].payload["case_id"])
        case = self.repo.get(case_id)
        if case is not None:
            self.repo.update(replace(case, evidence_strength=evidence_strength))
        return events

    def retain(
        self,
        client_id: str,
        params: RetainCounselParams,
        tick: int,
        *,
        public_defender: bool = False,
    ) -> Sequence[Event]:
        case = self.repo.get(params.case_id)
        counsel_id = params.counsel_id or params.lawyer_id or ""
        if case is None:
            return ()
        fee = int(params.fee_cents or 0)
        payer = "government" if public_defender else client_id
        event = self._stage_transfer_event(
            kind=COUNSEL_RETAINED,
            payload={
                "case_id": case.case_id,
                "party_id": client_id,
                "counsel_id": counsel_id,
                "side": ("defence" if client_id == case.defendant_id else "plaintiff"),
                "fee_cents": fee,
                "counsel_skill_law": self.skill_law(counsel_id),
                "public_defender": public_defender,
            },
            payer_id=payer,
            payee_id=counsel_id,
            amount_cents=fee,
            reason="purchase",
            tick=tick,
            actor_id=client_id,
            subjects=(client_id, counsel_id, case.case_id),
        )
        if client_id == case.defendant_id:
            case = replace(case, defence_counsel_id=counsel_id)
        else:
            case = replace(case, plaintiff_counsel_id=counsel_id)
        self.repo.update(case)
        return (event,)

    def assign_public_defender(
        self,
        case_id: str,
        defendant_id: str,
        tick: int,
    ) -> Sequence[Event] | None:
        if self.wealth_percentile(defendant_id) > self.cfg.legal_aid_wealth_pct:
            return None
        lawyers = sorted(
            (
                row
                for row in self.available_lawyers(tick)
                if row[2] >= self.cfg.min_counsel_skill_law
            ),
            key=lambda row: (row[1], row[0]),
        )
        if not lawyers:
            return None
        counsel_id, fee, _skill = lawyers[0]
        return self.retain(
            defendant_id,
            RetainCounselParams(
                case_id=case_id,
                counsel_id=counsel_id,
                fee_cents=fee,
            ),
            tick,
            public_defender=True,
        )

    def admit_evidence(self, case_id: str, tick: int) -> tuple[tuple[int, ...], Event]:
        case = self.repo.get(case_id)
        if case is None:
            raise ValueError(f"unknown court case: {case_id}")
        by_seq = {event.seq: event for event in _event_source(self.events)}
        parties = {case.plaintiff_id, case.defendant_id}
        admitted: list[int] = []
        excluded: list[int] = []
        reasons: list[str] = []
        for seq in sorted(set(case.evidence_event_seqs)):
            event = by_seq.get(seq)
            if event is None:
                excluded.append(seq)
                reasons.append("missing")
                continue
            if not parties.intersection(event.subject_ids):
                excluded.append(seq)
                reasons.append("no_party_subject")
                continue
            ledger_backed = 5000 <= event.kind <= 9999
            remembered = bool(self.memories.holders_of(seq).intersection(case.witness_ids))
            published = 11000 <= event.kind <= 11069
            public = event.kind in PUBLIC_KINDS
            if ledger_backed or remembered or published or public:
                admitted.append(seq)
            else:
                excluded.append(seq)
                reasons.append("inadmissible_source")
        counsel = case.plaintiff_counsel_id or case.defence_counsel_id
        skill = self.skill_law(counsel) if counsel is not None else 0.0
        surfaced_count = round(
            self.cfg.counsel_base_evidence + self.cfg.counsel_skill_factor * skill
        )
        surfaced = tuple(admitted[:surfaced_count])
        strength = min(1.0, len(surfaced) / max(1, self.cfg.counsel_base_evidence))
        event = self.log.stage(
            NewEvent(
                EVIDENCE_ADMITTED,
                {
                    "case_id": case_id,
                    "admitted_seqs": list(surfaced),
                    "excluded_seqs": excluded,
                    "excluded_reasons": reasons,
                    "evidence_strength": strength,
                    "surfaced_by_counsel": surfaced_count,
                },
                subject_ids=tuple(value for value in parties if value),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        self.repo.update(
            replace(
                case,
                admitted_event_seqs=surfaced,
                evidence_strength=max(case.evidence_strength, strength),
            )
        )
        return surfaced, event

    def testify(
        self,
        witness_id: str,
        params: TestifyParams,
        tick: int,
    ) -> Sequence[Event]:
        case = self.repo.get(params.case_id)
        if case is None:
            return ()
        checks = [_pure_check(self.checker, claim) for claim in params.claims]
        scored = [result.score for result in checks if result.score is not None]
        consistency = math.fsum(scored) / len(scored) if scored else 1.0
        perjury = any(
            result.verdict == "contradicted"
            and any(
                self.memories.holds_memory_of(witness_id, seq)
                for seq in params.claims[index].sourced_to_event_seqs
            )
            for index, result in enumerate(checks)
        )
        event = self.log.stage(
            NewEvent(
                TESTIMONY_GIVEN,
                {
                    "case_id": params.case_id,
                    "witness_id": witness_id,
                    "statement": params.statement,
                    "claims": [claim.model_dump(mode="json") for claim in params.claims],
                    "consistency_score": consistency,
                    "perjury_flagged": perjury,
                },
                actor_id=witness_id,
                subject_ids=(params.case_id, case.defendant_id),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        if witness_id not in case.witness_ids:
            self.repo.update(
                replace(case, witness_ids=tuple(sorted((*case.witness_ids, witness_id))))
            )
        return (event,)

    def bench_rule(
        self,
        evidence_strength: float,
        case_type: str,
        statutory: Range,
    ) -> Judgment:
        convicted, fine = bench_verdict(
            evidence_strength,
            self.cfg.conviction_threshold,
            statutory.fine_lo,
            statutory.fine_hi,
        )
        _convicted_sentence, sentence = bench_verdict(
            evidence_strength,
            self.cfg.conviction_threshold,
            statutory.sentence_lo_ticks,
            statutory.sentence_hi_ticks,
        )
        verdict: Verdict
        if case_type == "criminal":
            verdict = "guilty" if convicted else "not_guilty"
        else:
            verdict = "liable" if convicted else "not_liable"
        return Judgment(
            verdict,
            (),
            fine,
            sentence,
            0,
            0,
            0,
            (),
            "bench",
            None,
        )

    def _prompt(
        self,
        case: CourtCase,
        statutory: Range,
        judge_id: str,
        tick: int,
    ) -> str:
        admitted = [
            {
                "seq": event.seq,
                "kind": event.kind,
                "tick": event.tick,
                "subjects": list(event.subject_ids),
                "payload": dict(event.payload),
            }
            for event in _event_source(self.events)
            if event.seq in case.admitted_event_seqs
        ]
        memories = self.memories.retrieve_recent_texts(judge_id, tick, 6)
        return (
            "Decide this simulated case from admitted evidence only. "
            "Return one JSON object matching the supplied schema.\n"
            f"CASE={json.dumps(asdict(case), sort_keys=True, default=str)}\n"
            "STATUTORY RANGE IN FORCE: "
            f"fine {statutory.fine_lo}..{statutory.fine_hi} cents; "
            f"sentence {statutory.sentence_lo_ticks}..{statutory.sentence_hi_ticks} ticks.\n"
            f"ADMITTED={json.dumps(admitted, sort_keys=True, default=str)}\n"
            f"JUDGE_MEMORIES={json.dumps(memories)}"
        )

    def _clamp_judgment(
        self,
        case: CourtCase,
        statutory: Range,
        parsed: Mapping[str, Any],
        call_id: str,
    ) -> Judgment | None:
        verdict = str(parsed.get("verdict", ""))
        allowed = (
            {"guilty", "not_guilty", "dismissed"}
            if case.type == "criminal"
            else {"liable", "not_liable", "dismissed"}
        )
        if verdict not in allowed:
            return None
        penalty = cast(Mapping[str, Any], parsed.get("penalty", {}))
        clamped: list[str] = []

        def bounded(name: str, lo: int, hi: int) -> int:
            raw = max(0, int(penalty.get(name, 0)))
            value = min(hi, max(lo, raw))
            if value != raw:
                clamped.append(name)
            return value

        punitive = verdict in {"guilty", "liable"}
        fine = bounded("fine_cents", statutory.fine_lo, statutory.fine_hi) if punitive else 0
        sentence = (
            bounded(
                "sentence_ticks",
                statutory.sentence_lo_ticks,
                statutory.sentence_hi_ticks,
            )
            if verdict == "guilty"
            else 0
        )
        damages_raw = max(0, int(penalty.get("damages_cents", 0)))
        damages = min(case.claim_cents, damages_raw)
        if damages != damages_raw:
            clamped.append("damages_cents")
        crime = self.crimes.get(case.crime_id or "")
        restitution_limit = max(0, crime.amount_cents or 0) if crime is not None else 0
        restitution_raw = max(0, int(penalty.get("restitution_cents", 0)))
        restitution = min(restitution_limit, restitution_raw)
        if restitution != restitution_raw:
            clamped.append("restitution_cents")
        admitted = set(case.admitted_event_seqs)
        findings: list[str] = []
        for finding in parsed.get("findings", ()):
            text = str(finding)
            cited = {int(value) for value in re.findall(r"#(\d+)\b", text)}
            if cited and not cited.issubset(admitted):
                clamped.append("finding_non_admitted")
                continue
            findings.append(text)
        return Judgment(
            cast(Verdict, verdict),
            tuple(findings),
            fine,
            sentence,
            damages,
            restitution,
            max(0, int(penalty.get("disqualification_ticks", 0))),
            tuple(clamped),
            "llm",
            call_id,
        )

    async def _decide(
        self,
        case: CourtCase,
        statutory: Range,
        judge_id: str,
        tick: int,
    ) -> Judgment:
        if self.router is None:
            return self.bench_rule(case.evidence_strength, case.type, statutory)
        prompt = self._prompt(case, statutory, judge_id, tick)
        for attempt in range(3):
            result = await self.router.call(
                Purpose.JUDGE,
                judge_id,
                tick,
                {
                    "prompt": prompt,
                    "attempt": attempt,
                    "statutory": asdict(statutory),
                },
                _judge_schema(),
            )
            if result.parsed_ok and result.parsed is not None:
                judgment = self._clamp_judgment(case, statutory, result.parsed, str(result.call_id))
                if judgment is not None:
                    return judgment
            prompt = f"{prompt}\nThe prior verdict did not match the case type. Repair it."
        return self.bench_rule(case.evidence_strength, case.type, statutory)

    async def hold_session(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for case in self.repo.open_cases()[: self.cases_per_session(tick)]:
            if case.type == "criminal" and case.defence_counsel_id is None:
                assigned = self.assign_public_defender(case.case_id, case.defendant_id, tick)
                if assigned:
                    events.extend(assigned)
                    case = self.repo.get(case.case_id) or case
            if not case.admitted_event_seqs:
                _admitted, admitted_event = self.admit_evidence(case.case_id, tick)
                events.append(admitted_event)
                case = self.repo.get(case.case_id) or case
            judge_id = cast(str | None, self.offices.holder("judge", tick)) or "ag_bench"
            trial = self.log.stage(
                NewEvent(
                    TRIAL_HELD,
                    {
                        "case_id": case.case_id,
                        "judge_id": judge_id,
                        "session_tick": tick,
                        "plaintiff_counsel_id": case.plaintiff_counsel_id,
                        "defence_counsel_id": case.defence_counsel_id,
                        "evidence_strength": case.evidence_strength,
                    },
                    actor_id=judge_id,
                    subject_ids=(case.case_id, case.defendant_id),
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            crime = self.crimes.get(case.crime_id or "")
            crime_type = cast(
                CrimeType,
                crime.type if crime is not None else case.cause_of_action,
            )
            statutory = (
                statutory_range(
                    crime_type,
                    crime.amount_cents if crime else case.claim_cents,
                    tick,
                    self.runtime,
                    self.clock.profile.ticks_per_sim_day,
                )
                if crime_type in STATUTORY
                else Range(0, case.claim_cents, 0, 0)
            )
            judgment = await self._decide(case, statutory, judge_id, tick)
            rendered = self.log.stage(
                NewEvent(
                    JUDGMENT_RENDERED,
                    {
                        "case_id": case.case_id,
                        "judge_id": judge_id,
                        "verdict": judgment.verdict,
                        "findings": list(judgment.findings),
                        "fine_cents": judgment.fine_cents,
                        "sentence_ticks": judgment.sentence_ticks,
                        "damages_cents": judgment.damages_cents,
                        "restitution_cents": judgment.restitution_cents,
                        "disqualification_ticks": judgment.disqualification_ticks,
                        "clamped": list(judgment.clamped),
                        "origin": judgment.origin,
                        "llm_call_id": judgment.llm_call_id,
                        "nominal": (
                            judgment.verdict in {"guilty", "liable"}
                            and not any(
                                (
                                    judgment.fine_cents,
                                    judgment.sentence_ticks,
                                    judgment.damages_cents,
                                    judgment.restitution_cents,
                                )
                            )
                        ),
                    },
                    actor_id=judge_id,
                    subject_ids=(case.case_id, case.defendant_id),
                    cause_seq=trial.seq,
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            self.repo.update(
                replace(
                    case,
                    judge_id=judge_id,
                    resolved_tick=tick,
                    judgment=judgment,
                    status="resolved",
                )
            )
            events.extend((trial, rendered))
            if self.penalties is not None:
                events.extend(self.penalties.apply(case.case_id, judgment, tick))
        return tuple(events)

    def settle(
        self,
        case_id: str,
        params: SettleParams,
        tick: int,
        *,
        offered_by: str | None = None,
    ) -> Sequence[Event]:
        case = self.repo.get(case_id)
        if case is None or case.status != "open" or case.plaintiff_id is None:
            return ()
        payer = (
            case.defendant_id
            if offered_by is None or offered_by == case.defendant_id
            else offered_by
        )
        payee = case.plaintiff_id
        amount = min(int(params.amount_cents), case.claim_cents)
        event = self._stage_transfer_event(
            kind=CASE_SETTLED,
            payload={
                "case_id": case_id,
                "amount_cents": amount,
                "offered_by": payer,
            },
            payer_id=payer,
            payee_id=payee,
            amount_cents=amount,
            reason="transfer",
            tick=tick,
            actor_id=payer,
            subjects=(case_id, payer, payee),
        )
        self.repo.update(replace(case, status="settled", resolved_tick=tick))
        return (event,)


@dataclass(frozen=True, slots=True)
class Receivable:
    receivable_id: str
    case_id: str
    debtor_id: str
    creditor_id: str
    original_cents: int
    outstanding_cents: int
    reason: str


class PenaltyService:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        runtime: RuntimeOverlay,
        ledger: LawLedger,
        cases: CourtRepository,
        crimes: CrimeRepository,
        cfg: LawSettings,
        incarceration: Incarceration | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.runtime = runtime
        self.ledger = ledger
        self.cases = cases
        self.crimes = crimes
        self.cfg = cfg
        self.incarceration = incarceration
        self._receivables: dict[str, Receivable] = {}
        self._receivable_causes: dict[str, Event] = {}

    def _award(
        self,
        *,
        case_id: str,
        payer_id: str,
        payee_id: str,
        amount_cents: int,
        kind: int,
        reason: str,
        tick: int,
    ) -> Event | None:
        if amount_cents <= 0:
            return None
        paid = min(
            amount_cents,
            max(0, self.ledger.compatible_balance(payer_id, payee_id)),
        )
        shortfall = amount_cents - paid
        predicted = self.ledger.next_transfer_id(tick) if paid else None
        if kind == FINE_LEVIED:
            payload = {
                "case_id": case_id,
                "payer_id": payer_id,
                "amount_cents": amount_cents,
                "txn_id": predicted,
                "garnished": shortfall > 0,
                "shortfall_cents": shortfall,
            }
        else:
            payload = {
                "case_id": case_id,
                "from_id": payer_id,
                "to_id": payee_id,
                "amount_cents": amount_cents,
                "txn_id": predicted,
                "shortfall_cents": shortfall,
            }
        event = self.log.stage(
            NewEvent(
                kind,
                payload,
                actor_id=payer_id,
                subject_ids=(case_id, payer_id, payee_id),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        if paid:
            try:
                actual = self.ledger.post_transfer(
                    payer_id,
                    payee_id,
                    paid,
                    reason=reason,
                    tick=tick,
                    cause=event,
                )
                if actual != predicted:
                    raise RuntimeError("penalty ledger transaction ordinal diverged")
            except Exception:
                self.log.rollback()
                raise
        if shortfall:
            receivable_id = _stable_id(
                "rec", case_id, payer_id, payee_id, reason, len(self._receivables)
            )
            self._receivables[receivable_id] = Receivable(
                receivable_id,
                case_id,
                payer_id,
                payee_id,
                amount_cents,
                shortfall,
                reason,
            )
            self._receivable_causes[receivable_id] = event
        return event

    def apply(self, case_id: str, judgment: Judgment, tick: int) -> Sequence[Event]:
        case = self.cases.get(case_id)
        if case is None:
            return ()
        events: list[Event] = []
        if judgment.verdict == "guilty" and self.incarceration is not None:
            self.incarceration.record_conviction(case.defendant_id)
        if judgment.fine_cents:
            event = self._award(
                case_id=case_id,
                payer_id=case.defendant_id,
                payee_id="government",
                amount_cents=judgment.fine_cents,
                kind=FINE_LEVIED,
                reason="fine",
                tick=tick,
            )
            if event is not None:
                events.append(event)
        plaintiff = case.plaintiff_id
        if plaintiff is not None and judgment.damages_cents:
            event = self._award(
                case_id=case_id,
                payer_id=case.defendant_id,
                payee_id=plaintiff,
                amount_cents=judgment.damages_cents,
                kind=DAMAGES_AWARDED,
                reason="transfer",
                tick=tick,
            )
            if event is not None:
                events.append(event)
        crime = self.crimes.get(case.crime_id or "")
        if crime is not None and crime.victim_id is not None and judgment.restitution_cents:
            event = self._award(
                case_id=case_id,
                payer_id=case.defendant_id,
                payee_id=crime.victim_id,
                amount_cents=judgment.restitution_cents,
                kind=DAMAGES_AWARDED,
                reason="transfer",
                tick=tick,
            )
            if event is not None:
                events.append(event)
        if (
            self.runtime.flag("courts.loser_pays", tick)
            and plaintiff is not None
            and judgment.verdict in {"guilty", "liable"}
        ):
            event = self._award(
                case_id=case_id,
                payer_id=case.defendant_id,
                payee_id=plaintiff,
                amount_cents=self.cfg.filing_fee_cents,
                kind=DAMAGES_AWARDED,
                reason="transfer",
                tick=tick,
            )
            if event is not None:
                events.append(event)
        if judgment.sentence_ticks and self.incarceration is not None:
            events.extend(
                self.incarceration.commit(
                    case.defendant_id,
                    case_id,
                    judgment.sentence_ticks,
                    tick,
                )
            )
        return tuple(events)

    def garnish(self, agent_id: str, income_cents: int, tick: int) -> int:
        cap = max(0, round(income_cents * self.cfg.garnishment_rate))
        diverted = 0
        for receivable_id in sorted(self._receivables):
            row = self._receivables[receivable_id]
            if row.debtor_id != agent_id or row.outstanding_cents <= 0:
                continue
            available = max(
                0,
                self.ledger.compatible_balance(agent_id, row.creditor_id),
            )
            amount = min(row.outstanding_cents, cap - diverted, available)
            if amount <= 0:
                continue
            predicted = self.ledger.next_transfer_id(tick)
            remaining = row.outstanding_cents - amount
            event = self.log.stage(
                NewEvent(
                    GARNISHMENT_COLLECTED,
                    {
                        "receivable_id": receivable_id,
                        "case_id": row.case_id,
                        "debtor_id": agent_id,
                        "creditor_id": row.creditor_id,
                        "amount_cents": amount,
                        "txn_id": predicted,
                        "reason": row.reason,
                        "remaining_cents": remaining,
                    },
                    actor_id=agent_id,
                    subject_ids=(row.case_id, agent_id, row.creditor_id),
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            prior_cause = self._receivable_causes[receivable_id]
            updated = replace(row, outstanding_cents=remaining)
            self._receivables[receivable_id] = updated
            self._receivable_causes[receivable_id] = event
            try:
                actual = self.ledger.post_transfer(
                    agent_id,
                    row.creditor_id,
                    amount,
                    reason=row.reason,
                    tick=tick,
                    cause=event,
                )
            except Exception:
                self._receivables[receivable_id] = row
                self._receivable_causes[receivable_id] = prior_cause
                self.log.rollback()
                raise
            diverted += amount
            if updated.outstanding_cents == 0:
                self._receivable_causes.pop(receivable_id, None)
            if actual != predicted:
                raise RuntimeError("garnishment ledger transaction ordinal diverged")
        return diverted

    def outstanding(self, agent_id: str) -> int:
        return sum(
            row.outstanding_cents for row in self._receivables.values() if row.debtor_id == agent_id
        )

    def receivables(self) -> tuple[Receivable, ...]:
        return tuple(sorted(self._receivables.values(), key=lambda item: item.receivable_id))


_EX_OFFENDER_ENTAILS = (
    "released agents receive wage offers multiplied by (1 - penalty · criminal_record), "
    "so lower post-release earnings and any resulting recidivism follow partly from this "
    "rule rather than from agent choice. Ablate with --no-record-penalty; recidivism must "
    "be reported under both."
)


@dataclass(frozen=True, slots=True)
class Sentence:
    agent_id: str
    case_id: str
    started_tick: int
    release_tick: int
    prison_place_id: str
    prior_location: Location | None


class Incarceration:
    ALLOWED_ACTIONS: Final[frozenset[ActionType]] = frozenset(
        {
            ActionType.IDLE,
            ActionType.SLEEP,
            ActionType.EAT,
            ActionType.SAY,
            ActionType.STUDY,
            ActionType.NULL_ACTION,
        }
    )

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        world: World,
        runtime: RuntimeOverlay,
        cfg: LawSettings,
        terminate_employment: Callable[[str, int], Sequence[Event]] | None = None,
        household_return: Callable[[str], tuple[str | None, str | None]] | None = None,
        conversion_fine: Callable[[str, str, int, int], Sequence[Event]] | None = None,
        no_record_penalty: bool = False,
    ) -> None:
        self.log = log
        self.clock = clock
        self.world = world
        self.runtime = runtime
        self.cfg = cfg
        self.terminate_employment = terminate_employment or (lambda _agent_id, _tick: ())
        self.household_return = household_return or (lambda _agent_id: (None, None))
        self.conversion_fine = conversion_fine
        self.no_record_penalty = no_record_penalty
        self._sentences: dict[str, Sentence] = {}
        self._records: dict[str, int] = defaultdict(int)

    def commit(
        self,
        agent_id: str,
        case_id: str,
        sentence_ticks: int,
        tick: int,
    ) -> Sequence[Event]:
        prisons = sorted(self.world.places_of_type("prison"), key=lambda item: item.place_id)
        capacity = self.runtime.get("prison.capacity", tick)
        occupied = len(self._sentences)
        converted = not prisons or occupied >= int(capacity)
        place_id = None if not prisons else prisons[occupied % len(prisons)].place_id
        event = self.log.stage(
            NewEvent(
                INCARCERATION_STARTED,
                {
                    "agent_id": agent_id,
                    "case_id": case_id,
                    "ticks": sentence_ticks,
                    "place_id": place_id,
                    "converted_to_fine": converted,
                    "capacity_at_sentencing": int(capacity),
                },
                actor_id=agent_id,
                subject_ids=(agent_id, case_id),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )
        events: list[Event] = [event]
        if converted:
            if self.conversion_fine is not None:
                events.extend(
                    self.conversion_fine(
                        agent_id,
                        case_id,
                        sentence_ticks * self.cfg.fine_per_tick_cents,
                        tick,
                    )
                )
            return tuple(events)
        prior = self.world.locations.get(agent_id)
        prison = self.world.place(cast(str, place_id))
        self.world.locations[agent_id] = Location(
            prison.place_id,
            prison.district_id,
            prison.x,
            prison.y,
        )
        self.world.freeze_occupancy()
        self._sentences[agent_id] = Sentence(
            agent_id,
            case_id,
            tick,
            tick + sentence_ticks,
            prison.place_id,
            prior,
        )
        events.extend(self.terminate_employment(agent_id, tick))
        return tuple(events)

    def record_conviction(self, agent_id: str) -> None:
        self._records[agent_id] += 1

    def is_incarcerated(self, agent_id: str, tick: int) -> bool:
        sentence = self._sentences.get(agent_id)
        return (
            sentence is not None and sentence.started_tick <= tick and tick < sentence.release_tick
        )

    def release_due(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for agent_id, sentence in sorted(tuple(self._sentences.items())):
            if sentence.release_tick > tick:
                continue
            household_id, home_place_id = self.household_return(agent_id)
            prior = sentence.prior_location
            if home_place_id is not None and self.world.has_place(home_place_id):
                home = self.world.place(home_place_id)
                self.world.locations[agent_id] = Location(
                    home.place_id, home.district_id, home.x, home.y
                )
            elif prior is not None:
                self.world.locations[agent_id] = prior
            event = self.log.stage(
                NewEvent(
                    INCARCERATION_ENDED,
                    {
                        "agent_id": agent_id,
                        "ticks_served": tick - sentence.started_tick,
                        "skill_delta": 0.0,
                        "ties_lost": 0,
                        "returns_to_household_id": household_id,
                    },
                    actor_id=agent_id,
                    subject_ids=(agent_id, sentence.case_id),
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            events.append(event)
            del self._sentences[agent_id]
        if events:
            self.world.freeze_occupancy()
        return tuple(events)

    def decay_multiplier(self, agent_id: str, tick: int) -> float:
        return (
            self.cfg.incarceration_decay_multiplier if self.is_incarcerated(agent_id, tick) else 1.0
        )

    def criminal_record(self, agent_id: str) -> int:
        return self._records.get(agent_id, 0)

    @mechanism("ex_offender_wage_penalty", entails=_EX_OFFENDER_ENTAILS)
    def wage_multiplier(self, agent_id: str) -> float:
        if self.no_record_penalty:
            return 1.0
        return max(
            self.cfg.ex_offender_penalty_floor,
            1.0 - self.cfg.ex_offender_wage_penalty * self.criminal_record(agent_id),
        )


class LawResolver:
    slot: Final[InstitutionSlot] = InstitutionSlot.LAW
    handles: Final[frozenset[ActionType]] = frozenset(
        {
            ActionType.COMMIT_CRIME,
            ActionType.REPORT_CRIME,
            ActionType.FILE_SUIT,
            ActionType.RETAIN_COUNSEL,
            ActionType.TESTIFY,
            ActionType.SETTLE,
            ActionType.RULE,
        }
    )

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        world: World,
        police: PoliceService,
        courts: CourtService,
        penalties: PenaltyService,
        graph: SocialGraph,
        beliefs: BeliefChannel,
        offices: OfficeRegister,
        runtime: RuntimeOverlay,
        ledger: LawLedger,
        cfg: LawSettings,
        incarceration: Incarceration | None = None,
        memories: MemoryLookup | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.world = world
        self.police = police
        self.courts = courts
        self.penalties = penalties
        self.graph = graph
        self.beliefs = beliefs
        self.offices = offices
        self.runtime = runtime
        self.ledger = ledger
        self.cfg = cfg
        self.incarceration = incarceration
        self.memories = memories

    def _case(self, action: Action) -> CourtCase | None:
        case_id = action.params.get("case_id")
        return self.courts.repo.get(str(case_id)) if case_id is not None else None

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        if (
            self.incarceration is not None
            and self.incarceration.is_incarcerated(action.actor_id, ctx.tick)
            and action.type not in Incarceration.ALLOWED_ACTIONS
        ):
            return GateFailure("capability", "incarcerated action restriction")
        if action.type is ActionType.RETAIN_COUNSEL:
            counsel_id = action.params.get("counsel_id") or action.params.get("lawyer_id")
            skills = cast(Mapping[str, object], ctx.repositories.get("agent_skills", {}))
            law_skill = float(_get(skills.get(str(counsel_id), {}), "law", 0.0))
            if law_skill < self.cfg.min_counsel_skill_law:
                return GateFailure("capability", "counsel lacks required law skill")
            case = self._case(action)
            if case is None or counsel_id in {case.plaintiff_id, case.defendant_id}:
                return GateFailure("capability", "counsel is unavailable or a party")
            alive = cast(Mapping[str, bool], ctx.repositories.get("alive", {}))
            if counsel_id in alive and not alive[str(counsel_id)]:
                return GateFailure("capability", "counsel is not alive")
            if self.incarceration is not None and self.incarceration.is_incarcerated(
                str(counsel_id), ctx.tick
            ):
                return GateFailure("capability", "counsel is incarcerated")
        if (
            action.type is ActionType.FILE_SUIT
            and action.params.get("case_type") == "criminal"
            and self.offices.holds_office(action.actor_id, ctx.tick) != "police_chief"
        ):
            return GateFailure("capability", "criminal filing requires the prosecutor")
        if (
            action.type is ActionType.RULE
            and self.offices.holds_office(action.actor_id, ctx.tick) != "judge"
        ):
            return GateFailure("capability", "RULE requires the judge office")
        if action.type is ActionType.TESTIFY:
            case = self._case(action)
            if case is None or (case.witness_ids and action.actor_id not in case.witness_ids):
                return GateFailure("capability", "actor is not a listed witness")
        if action.type is ActionType.REPORT_CRIME and self.memories is not None:
            crime = self.police.match_report(ReportCrimeParams.model_validate(action.params))
            if (
                crime is None
                or crime.committed_event_seq is None
                or not self.memories.holds_memory_of(action.actor_id, crime.committed_event_seq)
            ):
                return GateFailure("capability", "reporter has no memory of the crime")
        return None

    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        observed = _get(ctx.observation, "location", ctx.observation)
        place_id = _get(observed, "place_id")
        if action.type is ActionType.COMMIT_CRIME:
            crime_type = action.params.get("crime_type")
            victim_id = action.params.get("victim_id")
            if crime_type in {"theft", "assault"} and victim_id is not None:
                victim_location = _get(
                    cast(Mapping[str, object], ctx.repositories.get("observed_locations", {})).get(
                        str(victim_id)
                    ),
                    "place_id",
                )
                if place_id is None or victim_location != place_id:
                    return GateFailure("locality", "target was not co-located in observation")
        if action.type in {ActionType.TESTIFY, ActionType.RULE} and (
            place_id is None
            or not self.world.has_place(str(place_id))
            or self.world.place(str(place_id)).type != "courthouse"
        ):
            return GateFailure("locality", "court action requires the courthouse")
        return None

    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        if action.type is ActionType.FILE_SUIT:
            case_type = action.params.get("case_type", "civil")
            waived = (
                self.courts.wealth_percentile(action.actor_id) <= self.cfg.filing_fee_waiver_pct
            )
            fee = 0 if case_type == "criminal" or waived else self.cfg.filing_fee_cents
            if fee and not self.ledger.can_pay(action.actor_id, fee, "government"):
                return GateFailure("resources", "filing fee is unaffordable")
        elif action.type is ActionType.RETAIN_COUNSEL:
            fee = int(action.params.get("fee_cents") or 0)
            counsel_id = str(
                action.params.get("counsel_id") or action.params.get("lawyer_id") or ""
            )
            if fee and not self.ledger.can_pay(action.actor_id, fee, counsel_id):
                return GateFailure("resources", "counsel fee is unaffordable")
        elif action.type is ActionType.SETTLE:
            case = self._case(action)
            amount = int(action.params.get("amount_cents") or 0)
            if (
                case is None
                or case.plaintiff_id is None
                or not self.ledger.can_pay(action.actor_id, amount, case.plaintiff_id)
            ):
                return GateFailure("resources", "settlement is unaffordable")
        return None

    def _crime_cause(self, crime_id: str) -> Event | None:
        for event in reversed(self.log.staged()):
            if event.kind == CRIME_COMMITTED and event.payload.get("crime_id") == crime_id:
                return event
        return None

    def _commit_crime(
        self,
        action: ValidatedAction,
        tick: int,
    ) -> Sequence[Event]:
        crime_id = action.legality.crime_id
        if crime_id is None:
            return ()
        crime = self.police.repo.get(crime_id)
        if (
            crime is None
            or crime.type != "theft"
            or crime.victim_id is None
            or not crime.amount_cents
        ):
            return ()
        cause = self._crime_cause(crime_id)
        if cause is None:
            return ()
        amount = min(
            crime.amount_cents,
            self.ledger.compatible_balance(crime.victim_id, crime.perpetrator_id),
        )
        if amount <= 0:
            return ()
        self.ledger.post_transfer(
            crime.victim_id,
            crime.perpetrator_id,
            amount,
            reason="transfer",
            tick=tick,
            cause=cause,
        )
        if amount != crime.amount_cents:
            self.police.repo.update(replace(crime, amount_cents=amount))
        return ()

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        del ctx
        events: list[Event] = []
        for validated in actions:
            action = validated.action
            params = validated.validated_params
            if action.type is ActionType.COMMIT_CRIME:
                events.extend(self._commit_crime(validated, tick))
            elif action.type is ActionType.REPORT_CRIME and isinstance(params, ReportCrimeParams):
                events.extend(self.police.report(action.actor_id, params, tick))
            elif action.type is ActionType.FILE_SUIT and isinstance(params, FileSuitParams):
                events.extend(self.courts.file(action.actor_id, params, tick))
            elif action.type is ActionType.RETAIN_COUNSEL and isinstance(
                params, RetainCounselParams
            ):
                events.extend(self.courts.retain(action.actor_id, params, tick))
            elif action.type is ActionType.TESTIFY and isinstance(params, TestifyParams):
                events.extend(self.courts.testify(action.actor_id, params, tick))
            elif action.type is ActionType.SETTLE and isinstance(params, SettleParams):
                events.extend(
                    self.courts.settle(
                        params.case_id,
                        params,
                        tick,
                        offered_by=action.actor_id,
                    )
                )
            elif action.type is ActionType.RULE and isinstance(params, RuleParams):
                continue
        return tuple(events)

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]:
        actor_id = str(_get(ctx.state, "agent_id", ""))
        if action_type in {ActionType.TESTIFY, ActionType.SETTLE}:
            return tuple(
                {
                    "case_id": case.case_id,
                    "type": case.type,
                    "defendant_id": case.defendant_id,
                    "claim_cents": case.claim_cents,
                }
                for case in self.courts.repo.open_cases()
                if actor_id in {case.plaintiff_id, case.defendant_id, *case.witness_ids}
            )
        if action_type is ActionType.RETAIN_COUNSEL:
            return tuple(
                {
                    "counsel_id": counsel_id,
                    "fee_cents": fee,
                    "skill_law": skill,
                }
                for counsel_id, fee, skill in sorted(self.courts.available_lawyers(ctx.tick))
                if skill >= self.cfg.min_counsel_skill_law
            )
        if action_type is ActionType.REPORT_CRIME and self.memories is not None:
            return tuple(
                {
                    "crime_id": crime.crime_id,
                    "crime_type": crime.type,
                    "suspect_id": crime.perpetrator_id,
                }
                for crime in self.police.repo.all()
                if crime.committed_event_seq is not None
                and self.memories.holds_memory_of(actor_id, crime.committed_event_seq)
            )
        return ()


class GarnishmentProtocol(Protocol):
    def garnish(self, agent_id: str, income_cents: int, tick: int) -> int: ...


class WagePenaltyProtocol(Protocol):
    def wage_multiplier(self, agent_id: str) -> float: ...


def backfill_insider_profit(
    repo: CrimeRepository,
    source_action_id: str,
    realised_profit_cents: int,
) -> Crime | None:
    crime = repo.by_source_action(source_action_id)
    if crime is None or crime.type != "insider_trading":
        return None
    updated = replace(crime, amount_cents=max(0, realised_profit_cents))
    repo.update(updated)
    return updated


__all__ = [
    "DERIVED_PREDICATES",
    "STATUTORY",
    "CourtCase",
    "CourtRepository",
    "CourtService",
    "Crime",
    "CrimeRepository",
    "CrimeType",
    "DerivedPredicate",
    "DetectionEngine",
    "GarnishmentProtocol",
    "Incarceration",
    "Judgment",
    "LawLedger",
    "LawLegalityOracle",
    "LawResolver",
    "MemoryCourtRepository",
    "MemoryCrimeRepository",
    "MnpiIndex",
    "NullLawLedger",
    "Obligation",
    "ObligationIndex",
    "Path",
    "PenaltyService",
    "PoliceService",
    "Range",
    "WagePenaltyProtocol",
    "backfill_insider_profit",
    "bench_verdict",
    "statutory_range",
]
