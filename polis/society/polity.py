from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from polis.agents.actions.params.polity import (
    AnnounceCandidacyParams,
    CampaignParams,
    FoundPartyParams,
    JoinPartyParams,
    ProposePolicyParams,
    VoteParams,
)
from polis.config.mechanisms import mechanism
from polis.config.settings import PolitySettings
from polis.events.kinds import (
    ABSTAINED,
    APPOINTMENT_MADE,
    CAMPAIGN_SPEND,
    CANDIDACY_ANNOUNCED,
    CANDIDACY_DEPOSITS_REFUNDED,
    ELECTION_CALLED,
    ELECTION_RESOLVED,
    OFFICE_ASSUMED,
    OFFICE_VACATED,
    OFFICER_REMOVED,
    PARTY_DISSOLVED,
    PARTY_FOUNDED,
    PARTY_JOINED,
    PARTY_LEFT,
    PARTY_PLATFORM_CHANGED,
    VOTE_CAST,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock, SimDuration
from polis.kernel.det import det_uuid
from polis.kernel.rng import RngRegistry
from polis.society.policy import Overlay, PolicyEngine, Proposal

POLITY_ACTIONS: Final[frozenset[ActionType]] = frozenset(
    {
        ActionType.FOUND_PARTY,
        ActionType.JOIN_PARTY,
        ActionType.ANNOUNCE_CANDIDACY,
        ActionType.CAMPAIGN,
        ActionType.VOTE,
        ActionType.PROPOSE_POLICY,
        ActionType.LOBBY,
    }
)


class BeliefLookup(Protocol):
    def value(self, agent_id: str, proposition: str) -> float: ...

    def confidence(self, agent_id: str, proposition: str) -> float: ...


class GraphLookup(Protocol):
    def neighbours(self, agent_id: str, *, min_strength: float = 0.0) -> Sequence[Any]: ...

    def strength(self, a_id: str, b_id: str, tie_type: str) -> float: ...

    def trust(self, a_id: str, b_id: str, tie_type: str) -> float: ...


class OutletLookup(Protocol):
    def get(self, outlet_id: str) -> Any | None: ...

    def live(self) -> Sequence[Any]: ...


class PlatformLookup(Protocol):
    def posts_in_window(self, tick: int, window_ticks: int) -> Sequence[Any]: ...


class WorldLookup(Protocol):
    locations: Mapping[str, Any]

    def occupancy(self, place_id: str) -> Sequence[str]: ...

    def place(self, place_id: str) -> Any: ...


class PolityLedger(Protocol):
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

    def post_transfers(
        self,
        transfers: Sequence[tuple[str, str, int]],
        *,
        reason: str,
        tick: int,
        cause: Event,
    ) -> str: ...


class NullPolityLedger:
    def __init__(self) -> None:
        self._ordinal_by_tick: dict[int, int] = defaultdict(int)

    def can_pay(self, payer_id: str, cents: int, payee_id: str | None = None) -> bool:
        del payer_id, payee_id
        return cents >= 0

    def next_transfer_id(self, tick: int) -> str:
        return str(
            det_uuid(
                "polis.polity.null_txn",
                tick,
                self._ordinal_by_tick[tick],
            )
        )

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
        del payer_id, payee_id, cents, reason, cause
        transfer_id = self.next_transfer_id(tick)
        self._ordinal_by_tick[tick] += 1
        return transfer_id

    def post_transfers(
        self,
        transfers: Sequence[tuple[str, str, int]],
        *,
        reason: str,
        tick: int,
        cause: Event,
    ) -> str:
        del transfers, reason, cause
        transfer_id = self.next_transfer_id(tick)
        self._ordinal_by_tick[tick] += 1
        return transfer_id


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _other(agent_id: str, tie: Any) -> str:
    a_id = str(_get(tie, "a_id", ""))
    b_id = str(_get(tie, "b_id", ""))
    return b_id if a_id == agent_id else a_id


@dataclass(frozen=True, slots=True)
class Party:
    party_id: str
    name: str
    platform: Mapping[str, float]
    founded_tick: int
    dissolved_tick: int | None
    member_ids: tuple[str, ...]
    leader_id: str | None


class PartyRegistry:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        beliefs: BeliefLookup,
        cfg: PolitySettings,
        ledger: PolityLedger | None = None,
        treasury_id: str = "gv_treasury",
        drift_mode: Literal["member_mean", "fixed"] = "member_mean",
    ) -> None:
        self.log = log
        self.clock = clock
        self.beliefs = beliefs
        self.cfg = cfg
        self.ledger = ledger or NullPolityLedger()
        self.treasury_id = treasury_id
        self.drift_mode = drift_mode
        self._parties: dict[str, Party] = {}
        self._membership: dict[str, str] = {}
        self._founding_members: dict[str, frozenset[str]] = {}
        self._ratified: set[str] = set()
        self._below_min_since: dict[str, int] = {}
        self._candidate_cycles: dict[str, int] = defaultdict(int)
        self._votes_received: dict[str, int] = defaultdict(int)
        self.founding_attempts = 0

    def _emit(
        self,
        kind: int,
        payload: Mapping[str, Any],
        tick: int,
        *,
        actor_id: str | None = None,
        subject_ids: Sequence[str] = (),
    ) -> Event:
        return self.log.stage(
            NewEvent(
                kind,
                dict(payload),
                actor_id=actor_id,
                subject_ids=tuple(subject_ids),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )

    def get(self, party_id: str) -> Party | None:
        return self._parties.get(party_id)

    def found(
        self,
        founder_id: str,
        params: FoundPartyParams,
        tick: int,
    ) -> tuple[Party, Sequence[Event]]:
        self.founding_attempts += 1
        founding_ids = tuple(sorted(set(params.founding_member_ids)))
        if len(founding_ids) < 3:
            raise ValueError("a party requires at least three founding members")
        if founder_id not in founding_ids:
            founding_ids = tuple(sorted((*founding_ids, founder_id)))
        if len(params.platform) > self.cfg.max_platform_planks:
            raise ValueError("party platform exceeds max_platform_planks")
        party_id = str(det_uuid("polis.party", founder_id, params.name, tick))
        if party_id in self._parties:
            raise ValueError("party already exists")
        fee = self.cfg.party_founding_fee_cents
        predicted = self.ledger.next_transfer_id(tick) if fee else ""
        party = Party(
            party_id,
            params.name,
            dict(sorted(params.platform.items())),
            tick,
            None,
            (founder_id,),
            founder_id,
        )
        event = self._emit(
            PARTY_FOUNDED,
            {
                "party_id": party_id,
                "founder_id": founder_id,
                "name": params.name,
                "platform": dict(party.platform),
                "founding_member_ids": list(founding_ids),
                "fee_cents": fee,
                "txn_id": predicted,
            },
            tick,
            actor_id=founder_id,
            subject_ids=(party_id, *founding_ids),
        )
        if fee:
            try:
                actual = self.ledger.post_transfer(
                    founder_id,
                    self.treasury_id,
                    fee,
                    reason="transfer",
                    tick=tick,
                    cause=event,
                )
                if actual != predicted:
                    raise RuntimeError("party founding transaction ordinal diverged")
            except Exception:
                self.log.rollback()
                raise
        self._parties[party_id] = party
        self._founding_members[party_id] = frozenset(founding_ids)
        self._membership[founder_id] = party_id
        return party, (event,)

    def _alignment(self, agent_id: str, party: Party) -> float:
        if not party.platform:
            return 0.0
        distances = [
            abs(self.beliefs.value(agent_id, proposition) - stance)
            for proposition, stance in sorted(party.platform.items())
        ]
        return round(1.0 - sum(distances) / (2 * len(distances)), 6)

    def join(self, agent_id: str, party_id: str, tick: int) -> Sequence[Event]:
        party = self._parties.get(party_id)
        if party is None or party.dissolved_tick is not None:
            raise ValueError("party is not live")
        prior_id = self._membership.get(agent_id)
        events: list[Event] = []
        if prior_id == party_id:
            return ()
        if prior_id is not None:
            left = self.leave(agent_id, "switched", tick)
            if left is not None:
                events.append(left)
        members = tuple(sorted((*party.member_ids, agent_id)))
        updated = replace(
            party,
            member_ids=members,
            leader_id=self._leader(members),
        )
        self._parties[party_id] = updated
        self._membership[agent_id] = party_id
        if self._founding_members[party_id].issubset(updated.member_ids):
            self._ratified.add(party_id)
        self._below_min_since.pop(party_id, None)
        events.append(
            self._emit(
                PARTY_JOINED,
                {
                    "agent_id": agent_id,
                    "party_id": party_id,
                    "alignment_score": self._alignment(agent_id, updated),
                    "prior_party_id": prior_id,
                },
                tick,
                actor_id=agent_id,
                subject_ids=(agent_id, party_id),
            )
        )
        return tuple(events)

    def leave(self, agent_id: str, reason: str, tick: int) -> Event | None:
        party_id = self._membership.pop(agent_id, None)
        if party_id is None:
            return None
        party = self._parties[party_id]
        members = tuple(member for member in party.member_ids if member != agent_id)
        self._parties[party_id] = replace(
            party,
            member_ids=members,
            leader_id=self._leader(members),
        )
        if len(members) < 3:
            self._below_min_since.setdefault(party_id, tick)
        return self._emit(
            PARTY_LEFT,
            {"agent_id": agent_id, "party_id": party_id, "reason": reason},
            tick,
            actor_id=agent_id,
            subject_ids=(agent_id, party_id),
        )

    def membership(self, agent_id: str) -> str | None:
        return self._membership.get(agent_id)

    def live(self) -> tuple[Party, ...]:
        return tuple(
            self._parties[key]
            for key in sorted(self._parties)
            if self._parties[key].dissolved_tick is None
        )

    def note_votes(self, agent_id: str, votes: int) -> None:
        self._votes_received[agent_id] += max(0, votes)

    def close_election_cycle(self, party_ids: Sequence[str | None]) -> None:
        fielded = {party_id for party_id in party_ids if party_id is not None}
        for party in self.live():
            if party.party_id in fielded:
                self._candidate_cycles[party.party_id] = 0
            else:
                self._candidate_cycles[party.party_id] += 1

    def _leader(self, members: Sequence[str]) -> str | None:
        if not members:
            return None
        return min(
            members,
            key=lambda agent_id: (-self._votes_received[agent_id], agent_id),
        )

    @mechanism(
        "party_platform_drift",
        entails=(
            "party platforms converge on their members' trimmed mean stance, so inter-party "
            "platform distance tracks inter-cluster belief distance by construction. Party "
            "polarisation is therefore NOT independent evidence of mass polarisation. Ablate "
            "with party_platform_drift: fixed (platform frozen at founding), under which "
            "platform-voter divergence becomes observable."
        ),
    )
    def drift_platforms(self, tick: int) -> Sequence[Event]:
        if self.drift_mode == "fixed":
            return ()
        events: list[Event] = []
        for party in self.live():
            changes: list[dict[str, float | str]] = []
            platform = dict(party.platform)
            for proposition, old in sorted(party.platform.items()):
                values = sorted(
                    self.beliefs.value(member_id, proposition) for member_id in party.member_ids
                )
                if not values:
                    continue
                trim = int(len(values) * 0.1)
                trimmed = (
                    values[trim : len(values) - trim] if trim and 2 * trim < len(values) else values
                )
                mean = sum(trimmed) / len(trimmed)
                new = round(0.75 * old + 0.25 * mean, 6)
                if new != old:
                    platform[proposition] = new
                    changes.append({"proposition": proposition, "old": old, "new": new})
            if changes:
                self._parties[party.party_id] = replace(party, platform=platform)
                events.append(
                    self._emit(
                        PARTY_PLATFORM_CHANGED,
                        {
                            "party_id": party.party_id,
                            "changes": changes,
                            "driver": "member_drift",
                        },
                        tick,
                        subject_ids=(party.party_id,),
                    )
                )
        return tuple(events)

    def dissolve_stale(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        thirty_days = 30 * self.clock.profile.ticks_per_sim_day
        for party in self.live():
            founding_expired = (
                tick > party.founded_tick + 1
                and len(party.member_ids) < 3
                and party.party_id not in self._ratified
                and not self._founding_members[party.party_id].issubset(party.member_ids)
            )
            membership_stale = (
                len(party.member_ids) < 3
                and tick - self._below_min_since.get(party.party_id, tick) >= thirty_days
            )
            no_candidates = self._candidate_cycles[party.party_id] >= 2
            if not (founding_expired or membership_stale or no_candidates):
                continue
            reason = (
                "founding_unratified"
                if founding_expired
                else ("membership_below_three" if membership_stale else "no_candidates")
            )
            self._parties[party.party_id] = replace(party, dissolved_tick=tick)
            for member in party.member_ids:
                self._membership.pop(member, None)
            events.append(
                self._emit(
                    PARTY_DISSOLVED,
                    {
                        "party_id": party.party_id,
                        "reason": reason,
                        "final_membership": len(party.member_ids),
                        "merged_into": None,
                    },
                    tick,
                    subject_ids=(party.party_id, *party.member_ids),
                )
            )
        return tuple(events)


@dataclass(frozen=True, slots=True)
class Candidacy:
    candidacy_id: str
    election_id: str
    agent_id: str
    party_id: str | None
    platform: Mapping[str, float]
    spend_cents: int
    votes: int


@dataclass(frozen=True, slots=True)
class Ballot:
    voter_id: str
    choice: str | None
    ranking: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()
    origin: Literal["deliberate", "reflex"] = "reflex"
    utility: Mapping[str, float] = field(default_factory=dict)
    max_utility: float = 0.0


@dataclass(frozen=True, slots=True)
class FitResult:
    omega: Mapping[str, float]
    log_likelihood: float
    holdout_accuracy: float
    n_deliberate: int
    usable: bool


@dataclass(frozen=True, slots=True)
class Election:
    election_id: str
    office: str
    seats: int
    method: str
    called_tick: int
    voting_tick: int
    campaign_ends_tick: int
    electorate_ids: tuple[str, ...]
    resolved: bool = False


@dataclass(frozen=True, slots=True)
class Tally:
    counts: Mapping[str, int]
    winner_ids: tuple[str, ...]
    margin: float
    rounds: tuple[Mapping[str, int], ...] = ()


class MemoryElectionRepository:
    def __init__(self) -> None:
        self.elections: dict[str, Election] = {}
        self.candidacies: dict[str, Candidacy] = {}
        self.ballots: dict[tuple[str, str], Ballot] = {}
        self.deposits: dict[str, tuple[str, int, bool]] = {}
        self.eligible_counts: dict[str, int] = {}

    def open_candidacies(self, election_id: str | None = None) -> tuple[Candidacy, ...]:
        return tuple(
            row
            for row in sorted(self.candidacies.values(), key=lambda item: item.candidacy_id)
            if election_id is None or row.election_id == election_id
        )


class OfficeRegister:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        cfg: PolitySettings,
        confirmation: Callable[[str, str], float] | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.cfg = cfg
        self.confirmation = confirmation or (lambda _office, _agent_id: 1.0)
        self._holders: dict[str, dict[str, tuple[int, int | None, int]]] = defaultdict(dict)
        self._last_votes: dict[str, int] = defaultdict(int)

    def _emit(
        self,
        kind: int,
        payload: Mapping[str, Any],
        tick: int,
        *,
        actor_id: str | None = None,
        subject_ids: Sequence[str] = (),
    ) -> Event:
        return self.log.stage(
            NewEvent(
                kind,
                dict(payload),
                actor_id=actor_id,
                subject_ids=tuple(subject_ids),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )

    def note_votes(self, agent_id: str, votes: int) -> None:
        self._last_votes[agent_id] = votes

    def holds_office(self, agent_id: str, tick: int) -> str | None:
        for office in sorted(self._holders):
            row = self._holders[office].get(agent_id)
            if row is not None and row[0] <= tick and (row[1] is None or tick < row[1]):
                return office
        return None

    def holder(self, office: str, tick: int) -> str | tuple[str, ...] | None:
        rows = tuple(
            sorted(
                agent_id
                for agent_id, (start, end, _salary) in self._holders.get(office, {}).items()
                if start <= tick and (end is None or tick < end)
            )
        )
        if not rows:
            return None
        return rows[0] if self.cfg.offices[office].seats == 1 else rows

    def assume(
        self,
        office: str,
        agent_id: str,
        tick: int,
        *,
        via: str,
        salary_cents: int,
    ) -> Sequence[Event]:
        term_years = self.cfg.offices[office].term_sim_years
        term_end = (
            None
            if term_years is None
            else tick + self.clock.ticks_for(SimDuration(years=term_years))
        )
        self._holders[office][agent_id] = (tick, term_end, salary_cents)
        return (
            self._emit(
                OFFICE_ASSUMED,
                {
                    "office": office,
                    "agent_id": agent_id,
                    "via": via,
                    "term_start_tick": tick,
                    "term_end_tick": term_end,
                    "salary_cents": salary_cents,
                },
                tick,
                actor_id=agent_id,
                subject_ids=(agent_id,),
            ),
        )

    def vacate(
        self,
        office: str,
        agent_id: str,
        reason: str,
        tick: int,
    ) -> Sequence[Event]:
        if agent_id not in self._holders.get(office, {}):
            return ()
        start, _end, salary = self._holders[office][agent_id]
        self._holders[office][agent_id] = (start, tick, salary)
        successor: str | None = None
        if office == "president" and reason in {"death", "incarceration", "emigration"}:
            council = self.holder("council", tick)
            members = (
                () if council is None else ((council,) if isinstance(council, str) else council)
            )
            if members:
                successor = min(
                    members,
                    key=lambda member: (-self._last_votes[member], member),
                )
        events: list[Event] = [
            self._emit(
                OFFICE_VACATED,
                {
                    "office": office,
                    "agent_id": agent_id,
                    "reason": reason,
                    "successor_id": successor,
                },
                tick,
                subject_ids=(agent_id, *((successor,) if successor else ())),
            )
        ]
        if successor is not None:
            events.extend(
                self.assume(
                    "president",
                    successor,
                    tick,
                    via="succession",
                    salary_cents=self.cfg.offices["president"].salary_cents,
                )
            )
        return tuple(events)

    def appoint(
        self,
        office: str,
        appointee_id: str,
        by_id: str,
        tick: int,
    ) -> Sequence[Event]:
        margin = self.confirmation(office, appointee_id)
        confirmed = office == "police_chief" or margin > 0
        events: list[Event] = [
            self._emit(
                APPOINTMENT_MADE,
                {
                    "office": office,
                    "appointee_id": appointee_id,
                    "appointed_by": by_id,
                    "confirmed": confirmed,
                    "confirm_margin": margin,
                },
                tick,
                actor_id=by_id,
                subject_ids=(appointee_id,),
            )
        ]
        if confirmed:
            events.extend(
                self.assume(
                    office,
                    appointee_id,
                    tick,
                    via="appointment",
                    salary_cents=self.cfg.offices[office].salary_cents,
                )
            )
        return tuple(events)

    def remove(
        self,
        office: str,
        agent_id: str,
        by: str,
        margin: float,
        tick: int,
    ) -> Sequence[Event]:
        if office == "cb_governor" and margin < 5 / 7:
            return ()
        event = self._emit(
            OFFICER_REMOVED,
            {
                "office": office,
                "agent_id": agent_id,
                "removed_by": by,
                "margin": margin,
                "reason": "council_removal",
            },
            tick,
            actor_id=by,
            subject_ids=(agent_id,),
        )
        return (event, *self.vacate(office, agent_id, "removal", tick))


class ExposureLedger:
    def __init__(self, *, half_life_ticks: int) -> None:
        self.half_life_ticks = max(1, half_life_ticks)
        self._rows: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)

    def record(
        self,
        candidacy_id: str,
        agent_ids: Sequence[str],
        channel: str,
        tick: int,
    ) -> None:
        for agent_id in sorted(set(agent_ids)):
            self._rows[(agent_id, candidacy_id)].append((tick, channel))

    def exposure(self, agent_id: str, candidacy_id: str, tick: int) -> float:
        return round(
            sum(
                2 ** (-(tick - seen_tick) / self.half_life_ticks)
                for seen_tick, _channel in self._rows.get((agent_id, candidacy_id), ())
                if seen_tick <= tick
            ),
            6,
        )


class VoteModel:
    FEATURES: Final[tuple[str, ...]] = (
        "congruence",
        "self_interest",
        "social",
        "media",
        "party_id",
        "incumbency",
    )

    def __init__(
        self,
        *,
        rng: RngRegistry,
        beliefs: BeliefLookup,
        graph: GraphLookup,
        parties: PartyRegistry,
        offices: OfficeRegister,
        exposure: ExposureLedger,
        cfg: PolitySettings,
        clock: Clock | None = None,
        platform: PlatformLookup | None = None,
        income_statement: Callable[[str, int], Mapping[str, int]] | None = None,
        traits: Callable[[str], Mapping[str, float]] | None = None,
    ) -> None:
        self.rng = rng
        self.beliefs = beliefs
        self.graph = graph
        self.parties = parties
        self.offices = offices
        self.exposure_ledger = exposure
        self.cfg = cfg
        self.clock = clock
        self.platform = platform
        self.income_statement = income_statement or (lambda _agent_id, _tick: {})
        self.traits = traits or (lambda _agent_id: {})

    def _congruence(self, voter_id: str, platform: Mapping[str, float]) -> float:
        weighted = 0.0
        confidence = 0.0
        for proposition, stance in sorted(platform.items()):
            conf = max(0.0, self.beliefs.confidence(voter_id, proposition))
            weighted += conf * abs(self.beliefs.value(voter_id, proposition) - stance)
            confidence += conf
        if confidence == 0:
            return 0.5
        return round(1.0 - weighted / (2.0 * confidence), 6)

    def self_interest(
        self,
        voter_id: str,
        platform: Mapping[str, float],
        tick: int,
    ) -> float:
        statement = self.income_statement(voter_id, tick)
        income = int(statement.get("annual_income_cents", statement.get("wage_cents", 0)))
        taxable = int(statement.get("taxable_income_cents", income))
        delta = 0.0
        for parameter, proposed in sorted(platform.items()):
            if parameter == "tax.vat_bp":
                consumption = int(statement.get("consumption_cents", 0))
                current = int(statement.get("vat_bp", 0))
                delta -= consumption * (float(proposed) - current) / 10_000
            elif parameter == "labour.minimum_wage_cents":
                hours = int(statement.get("annual_hours", 0))
                current = int(statement.get("hourly_wage_cents", 0))
                delta += max(0.0, float(proposed) - current) * hours
            elif parameter == "welfare.unemployment_benefit_cents":
                if bool(statement.get("unemployed", 0)):
                    current = int(statement.get("unemployment_benefit_cents", 0))
                    delta += float(proposed) - current
            elif parameter == "tax.income.brackets":
                brackets = cast(Sequence[Sequence[int]], proposed)
                proposed_tax = 0
                for index, row in enumerate(brackets):
                    threshold, rate = int(row[0]), int(row[1])
                    next_threshold = (
                        taxable
                        if index + 1 == len(brackets)
                        else min(taxable, int(brackets[index + 1][0]))
                    )
                    proposed_tax += max(0, next_threshold - threshold) * rate // 10_000
                delta -= proposed_tax - int(statement.get("income_tax_cents", 0))
        scale = max(1, abs(income))
        return round(max(-1.0, min(1.0, delta / scale)), 6)

    def _social(self, voter_id: str, candidate: Candidacy, tick: int) -> float:
        if self.platform is None:
            return 0.0
        ticks_per_day = 1 if self.clock is None else self.clock.profile.ticks_per_sim_day
        window = self.cfg.campaign_length_sim_days * ticks_per_day
        posts = self.platform.posts_in_window(tick, max(1, window))
        stances: dict[str, list[float]] = defaultdict(list)
        for post in posts:
            author_id = str(_get(post, "author_id", ""))
            proposition = _get(post, "stance_proposition")
            value = _get(post, "stance_value")
            if proposition in candidate.platform and value is not None:
                alignment = 1.0 - abs(float(value) - candidate.platform[str(proposition)]) / 2.0
                stances[author_id].append(alignment)
        numerator = 0.0
        denominator = 0.0
        for tie in self.graph.neighbours(voter_id):
            neighbour = _other(voter_id, tie)
            values = stances.get(neighbour)
            if not values:
                continue
            strength = float(_get(tie, "strength", 0.0))
            valence = float(_get(tie, "valence", _get(tie, "trust", 0.0)))
            numerator += strength * valence * (sum(values) / len(values))
            denominator += abs(strength)
        return 0.0 if denominator == 0 else round(numerator / denominator, 6)

    def features(
        self,
        voter_id: str,
        c: Candidacy,
        election_id: str,
        tick: int,
    ) -> Mapping[str, float]:
        del election_id
        membership = self.parties.membership(voter_id)
        party_term = 1.0 if membership is not None and membership == c.party_id else 0.0
        incumbency = 1.0 if self.offices.holds_office(c.agent_id, tick) is not None else 0.0
        return {
            "congruence": self._congruence(voter_id, c.platform),
            "self_interest": self.self_interest(voter_id, c.platform, tick),
            "social": self._social(voter_id, c, tick),
            "media": self.exposure_ledger.exposure(voter_id, c.candidacy_id, tick),
            "party_id": party_term,
            "incumbency": incumbency,
        }

    @mechanism(
        "vote_model",
        entails=(
            "reflex voters are an extrapolation of the deliberate voters in the same "
            "election, fitted by multinomial logit on the six utility terms with the "
            "deliberate choices as labels. This makes the reflex electorate a projection "
            "of LLM behaviour rather than an independent hard-coded theory of voting, but "
            "it also means reflex voters cannot exhibit a preference structure absent from "
            "the deliberate sample. Reported per election: n_deliberate, n_reflex, fitted "
            "ω vector, log-likelihood, and holdout accuracy on a 20% split of the deliberate "
            "voters. If holdout accuracy is below 0.5 above chance, the reflex vote is not "
            "usable and the election must be re-run with a larger LLM share. For the first "
            "election of a run, ω comes from the config prior, and that election is excluded "
            "from B-track analysis."
        ),
    )
    def fit(
        self,
        deliberate: Sequence[
            tuple[
                str,
                str,
                Mapping[str, float] | Mapping[str, Mapping[str, float]],
            ]
        ],
        election_id: str,
    ) -> FitResult:
        del election_id
        rows = tuple(sorted(deliberate, key=lambda row: (row[0], row[1])))
        if len(rows) < 2:
            return FitResult(dict(self.cfg.omega_prior), 0.0, 0.0, len(rows), False)

        normalized: list[tuple[str, str, Mapping[str, Mapping[str, float]]]] = []
        for voter_id, choice, raw_values in rows:
            first = next(iter(raw_values.values()), None)
            if isinstance(first, Mapping):
                alternative_rows = {
                    str(candidate_id): {
                        feature: float(values.get(feature, 0.0)) for feature in self.FEATURES
                    }
                    for candidate_id, values in cast(
                        Mapping[str, Mapping[str, float]],
                        raw_values,
                    ).items()
                }
            else:
                chosen_values = cast(Mapping[str, float], raw_values)
                alternative_rows = {
                    choice: {
                        feature: float(chosen_values.get(feature, 0.0)) for feature in self.FEATURES
                    }
                }
            if choice in alternative_rows and alternative_rows:
                normalized.append((voter_id, choice, alternative_rows))

        if len(normalized) < 2:
            return FitResult(
                dict(self.cfg.omega_prior),
                0.0,
                0.0,
                len(normalized),
                False,
            )

        def probabilities(
            alternatives: Mapping[str, Mapping[str, float]],
            omega: Mapping[str, float],
        ) -> Mapping[str, float]:
            scores = {
                candidate_id: sum(omega[feature] * values[feature] for feature in self.FEATURES)
                for candidate_id, values in alternatives.items()
            }
            largest = max(scores.values())
            weights = {
                candidate_id: math.exp(max(-30.0, min(30.0, score - largest)))
                for candidate_id, score in scores.items()
            }
            denominator = sum(weights.values())
            return {candidate_id: weight / denominator for candidate_id, weight in weights.items()}

        holdout_n = max(1, round(len(normalized) * self.cfg.vote_holdout_share))
        train = normalized[:-holdout_n]
        holdout = normalized[-holdout_n:]
        if not train:
            train = normalized
        omega = {feature: 0.0 for feature in self.FEATURES}
        learning_rate = 0.08
        for _pass in range(64):
            for _voter_id, choice, choice_set in train:
                probability = probabilities(choice_set, omega)
                for feature in self.FEATURES:
                    expected = sum(
                        probability[candidate_id] * values[feature]
                        for candidate_id, values in choice_set.items()
                    )
                    gradient = choice_set[choice][feature] - expected
                    omega[feature] = round(
                        omega[feature] + learning_rate * gradient,
                        6,
                    )
        log_likelihood = 0.0
        for _voter_id, choice, choice_set in train:
            probability = probabilities(choice_set, omega)
            log_likelihood += math.log(max(1e-12, probability[choice]))
        correct = 0
        chance = 0.0
        for _voter_id, choice, choice_set in holdout:
            probability = probabilities(choice_set, omega)
            predicted = min(probability, key=lambda key: (-probability[key], key))
            correct += predicted == choice
            chance += 1 / len(choice_set)
        accuracy = round(correct / len(holdout), 6)
        chance /= len(holdout)
        required = min(1.0, chance + self.cfg.vote_min_holdout_lift)
        return FitResult(
            dict(omega),
            round(log_likelihood, 6),
            accuracy,
            len(normalized),
            accuracy >= required,
        )

    def choose(
        self,
        voter_id: str,
        candidacies: Sequence[Candidacy],
        omega: Mapping[str, float],
        election_id: str,
        tick: int,
    ) -> Ballot:
        rng = self.rng.get("polity.vote", voter_id, tick)
        utilities: list[tuple[float, Candidacy, Mapping[str, float], float]] = []
        for candidacy in sorted(candidacies, key=lambda row: row.candidacy_id):
            features = self.features(voter_id, candidacy, election_id, tick)
            uniform = max(1e-12, min(1 - 1e-12, rng.random()))
            epsilon = -math.log(-math.log(uniform))
            utility = (
                sum(float(omega.get(key, 0.0)) * float(features[key]) for key in self.FEATURES)
                + epsilon
            )
            utilities.append((utility, candidacy, features, epsilon))
        if not utilities:
            return Ballot(voter_id, None, utility={"epsilon": 0.0}, max_utility=0.0)
        best = max(utilities, key=lambda row: (row[0], row[1].candidacy_id))
        traits = self.traits(voter_id)
        threshold = (
            self.cfg.abstain.theta_0
            - self.cfg.abstain.theta_conscientiousness * float(traits.get("conscientiousness", 0.0))
            - self.cfg.abstain.theta_civic * float(traits.get("civic", 0.0))
        )
        components = {key: round(float(best[2][key]), 6) for key in self.FEATURES}
        components["epsilon"] = round(best[3], 6)
        return Ballot(
            voter_id,
            best[1].candidacy_id if best[0] >= threshold else None,
            origin="reflex",
            utility=components,
            max_utility=round(best[0], 6),
        )


class ElectionOffice:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        cfg: PolitySettings,
        parties: PartyRegistry,
        offices: OfficeRegister,
        vote_model: VoteModel,
        exposure: ExposureLedger,
        runtime: Overlay,
        ledger: PolityLedger | None = None,
        agents: Mapping[str, object] | None = None,
        world: WorldLookup | None = None,
        outlets: OutletLookup | None = None,
        deliberate_ballots: Callable[[str, Sequence[Candidacy], int], Ballot | None] | None = None,
        repo: MemoryElectionRepository | None = None,
        treasury_id: str = "gv_treasury",
        cpm_cents: int = 40,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.cfg = cfg
        self.parties = parties
        self.offices = offices
        self.vote_model = vote_model
        self.exposure = exposure
        self.runtime = runtime
        self.ledger = ledger or NullPolityLedger()
        self.agents = {} if agents is None else agents
        self.world = world
        self.outlets = outlets
        self.deliberate_ballots = deliberate_ballots
        self.repo = repo or MemoryElectionRepository()
        self.treasury_id = treasury_id
        self.cpm_cents = cpm_cents
        self._resolved_elections = 0

    def _emit(
        self,
        kind: int,
        payload: Mapping[str, Any],
        tick: int,
        *,
        actor_id: str | None = None,
        subject_ids: Sequence[str] = (),
    ) -> Event:
        return self.log.stage(
            NewEvent(
                kind,
                dict(payload),
                actor_id=actor_id,
                subject_ids=tuple(subject_ids),
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )

    def call(self, office: str, tick: int) -> tuple[str, Event]:
        voting_tick = tick + self.clock.ticks_for(
            SimDuration(days=self.cfg.campaign_length_sim_days)
        )
        campaign_ends = voting_tick - self.clock.ticks_for(
            SimDuration(days=self.cfg.candidacy_close_sim_days)
        )
        election_id = str(det_uuid("polis.election", office, voting_tick))
        electorate = tuple(
            agent_id
            for agent_id in sorted(self.agents)
            if self._eligible_state(self.agents[agent_id], voting_tick)
        )
        office_cfg = self.cfg.offices[office]
        method = office_cfg.method or self.cfg.election_method
        election = Election(
            election_id,
            office,
            office_cfg.seats,
            method,
            tick,
            voting_tick,
            campaign_ends,
            electorate,
        )
        self.repo.elections[election_id] = election
        self.repo.eligible_counts[election_id] = len(electorate)
        event = self._emit(
            ELECTION_CALLED,
            {
                "election_id": election_id,
                "office": office,
                "seats": office_cfg.seats,
                "method": method,
                "called_tick": tick,
                "voting_tick": voting_tick,
                "campaign_ends_tick": campaign_ends,
                "electorate_size": len(electorate),
            },
            tick,
            subject_ids=(election_id,),
        )
        return election_id, event

    def _eligible_state(self, state: object, tick: int) -> bool:
        age = int(_get(state, "age_years", _get(state, "age", 0)))
        alive = bool(_get(state, "alive", True))
        incarcerated = bool(_get(state, "incarcerated", False))
        resident_since = int(_get(state, "resident_since_tick", 0))
        resident_ticks = 90 * self.clock.profile.ticks_per_sim_day
        franchise = self.runtime.flag("polity.felon_franchise", tick)
        return (
            alive
            and age >= 18
            and (not incarcerated or franchise)
            and tick - resident_since >= resident_ticks
        )

    def eligible(self, agent_id: str, election_id: str, tick: int) -> bool:
        election = self.repo.elections.get(election_id)
        state = self.agents.get(agent_id)
        return (
            election is not None
            and state is not None
            and agent_id in election.electorate_ids
            and self._eligible_state(state, tick)
        )

    def has_voted(self, agent_id: str, election_id: str) -> bool:
        return (election_id, agent_id) in self.repo.ballots

    def announce(
        self,
        agent_id: str,
        params: AnnounceCandidacyParams,
        tick: int,
    ) -> tuple[Candidacy, Sequence[Event]]:
        election_id = params.election_id or params.office_id
        if election_id is None or election_id not in self.repo.elections:
            raise ValueError("unknown election")
        election = self.repo.elections[election_id]
        if tick > election.campaign_ends_tick:
            raise ValueError("candidacy window is closed")
        if params.party_id is not None and self.parties.get(params.party_id) is None:
            raise ValueError("unknown party")
        platform = dict(sorted(params.platform.items()))
        if not platform and params.party_id is not None:
            party = self.parties.get(params.party_id)
            platform = {} if party is None else dict(party.platform)
        candidacy_id = str(det_uuid("polis.candidacy", election_id, agent_id))
        deposit = self.cfg.candidacy_deposit_cents
        predicted = self.ledger.next_transfer_id(tick) if deposit else ""
        candidacy = Candidacy(
            candidacy_id,
            election_id,
            agent_id,
            params.party_id,
            platform,
            0,
            0,
        )
        event = self._emit(
            CANDIDACY_ANNOUNCED,
            {
                "candidacy_id": candidacy_id,
                "agent_id": agent_id,
                "election_id": election_id,
                "party_id": params.party_id,
                "platform": platform,
                "deposit_cents": deposit,
                "txn_id": predicted,
            },
            tick,
            actor_id=agent_id,
            subject_ids=(agent_id, candidacy_id, election_id),
        )
        if deposit:
            try:
                actual = self.ledger.post_transfer(
                    agent_id,
                    self.treasury_id,
                    deposit,
                    reason="transfer",
                    tick=tick,
                    cause=event,
                )
                if actual != predicted:
                    raise RuntimeError("candidacy deposit transaction ordinal diverged")
            except Exception:
                self.log.rollback()
                raise
        self.repo.candidacies[candidacy_id] = candidacy
        self.repo.deposits[candidacy_id] = (agent_id, deposit, False)
        return candidacy, (event,)

    def cast(self, election_id: str, ballot: Ballot, tick: int) -> Event:
        key = (election_id, ballot.voter_id)
        if key in self.repo.ballots:
            raise ValueError("voter has already voted")
        valid = {row.candidacy_id for row in self.repo.open_candidacies(election_id)}
        referenced = {
            *((ballot.choice,) if ballot.choice is not None else ()),
            *ballot.ranking,
            *ballot.approvals,
        }
        if not referenced <= valid:
            raise ValueError("ballot references a candidacy outside this election")
        self.repo.ballots[key] = ballot
        if ballot.choice is None and not ballot.ranking and not ballot.approvals:
            return self._emit(
                ABSTAINED,
                {
                    "election_id": election_id,
                    "agent_id": ballot.voter_id,
                    "reason": "below_threshold",
                    "max_utility": ballot.max_utility,
                    "origin": ballot.origin,
                    "utility": {
                        component: float(ballot.utility.get(component, 0.0))
                        for component in (*VoteModel.FEATURES, "epsilon")
                    },
                },
                tick,
                actor_id=ballot.voter_id,
            )
        return self._emit(
            VOTE_CAST,
            {
                "election_id": election_id,
                "voter_id": ballot.voter_id,
                "candidacy_id": ballot.choice,
                "ranking": list(ballot.ranking),
                "approvals": list(ballot.approvals),
                "origin": ballot.origin,
                "utility": {
                    component: float(ballot.utility.get(component, 0.0))
                    for component in (*VoteModel.FEATURES, "epsilon")
                },
            },
            tick,
            actor_id=ballot.voter_id,
            subject_ids=(ballot.voter_id, election_id),
        )

    def campaign(
        self,
        agent_id: str,
        params: CampaignParams,
        tick: int,
    ) -> Sequence[Event]:
        candidacy = self.repo.candidacies[params.candidacy_id]
        amount = max(params.amount_cents, params.spend_cents)
        reached: tuple[str, ...] = ()
        payee: str | None = None
        reason = "purchase"
        if params.channel == "ads":
            outlet = (
                None
                if self.outlets is None or params.target_id is None
                else self.outlets.get(params.target_id)
            )
            if outlet is None:
                raise ValueError("ads require a live target outlet")
            reach = min(
                int(_get(outlet, "reach", 0)),
                round(amount / max(1, self.cpm_cents) * 1_000 * self.cfg.outlet_efficiency),
            )
            candidates = [] if self.world is None else sorted(self.world.locations)
            self.rng.get("polity.campaign.reach", candidacy.candidacy_id, tick).shuffle(candidates)
            reached = tuple(sorted(candidates[:reach]))
            payee = _get(outlet, "firm_id")
        elif params.channel == "rally":
            if self.world is None or params.place_id is None:
                raise ValueError("rally requires a place")
            reached = tuple(sorted(self.world.occupancy(params.place_id)))
            payee = _get(self.world.place(params.place_id), "owner_id")
            reason = "rent"
        else:
            party = None if candidacy.party_id is None else self.parties.get(candidacy.party_id)
            reached = () if party is None else tuple(sorted(set(party.member_ids) - {agent_id}))
            amount = 0
        predicted = self.ledger.next_transfer_id(tick) if amount and payee is not None else ""
        event = self._emit(
            CAMPAIGN_SPEND,
            {
                "candidacy_id": candidacy.candidacy_id,
                "agent_id": agent_id,
                "amount_cents": amount,
                "channel": params.channel,
                "target_id": params.target_id or params.place_id,
                "reached_agent_ids": list(reached[:10_000]),
                "reach": len(reached),
                "txn_id": predicted,
            },
            tick,
            actor_id=agent_id,
            subject_ids=(agent_id, candidacy.candidacy_id, *reached[:250]),
        )
        if amount and payee is not None:
            try:
                actual = self.ledger.post_transfer(
                    agent_id,
                    str(payee),
                    amount,
                    reason=reason,
                    tick=tick,
                    cause=event,
                )
                if actual != predicted:
                    raise RuntimeError("campaign transaction ordinal diverged")
            except Exception:
                self.log.rollback()
                raise
        self.exposure.record(candidacy.candidacy_id, reached, params.channel, tick)
        self.repo.candidacies[candidacy.candidacy_id] = replace(
            candidacy,
            spend_cents=candidacy.spend_cents + amount,
        )
        return (event,)

    def _plurality(self, ballots: Sequence[Ballot], election_id: str, tick: int) -> Tally:
        counts: dict[str, int] = defaultdict(int)
        for ballot in ballots:
            if ballot.choice is not None:
                counts[ballot.choice] += 1
        if not counts:
            return Tally({}, (), 0.0)
        top = max(counts.values())
        tied = tuple(sorted(key for key, value in counts.items() if value == top))
        winner = (
            tied[0]
            if len(tied) == 1
            else self.rng.get("polity.vote", election_id, tick).choice(tied)
        )
        second = max((value for key, value in counts.items() if key != winner), default=0)
        return Tally(
            dict(sorted(counts.items())),
            (winner,),
            round((top - second) / max(1, sum(counts.values())), 6),
            ({"tie_break_candidates": len(tied)},) if len(tied) > 1 else (),
        )

    def _approval(self, ballots: Sequence[Ballot]) -> Tally:
        counts: dict[str, int] = defaultdict(int)
        for ballot in ballots:
            for candidate_id in sorted(set(ballot.approvals)):
                counts[candidate_id] += 1
        if not counts:
            return Tally({}, (), 0.0)
        ranking = sorted(counts, key=lambda key: (-counts[key], key))
        total = max(1, len(ballots))
        margin = (
            (counts[ranking[0]] - counts[ranking[1]]) / total
            if len(ranking) > 1
            else counts[ranking[0]] / total
        )
        return Tally(dict(sorted(counts.items())), (ranking[0],), round(margin, 6))

    def _irv(self, ballots: Sequence[Ballot]) -> Tally:
        active = set(candidate_id for ballot in ballots for candidate_id in ballot.ranking)
        rounds: list[Mapping[str, int]] = []
        last_counts: dict[str, int] = {}
        while active:
            counts = {candidate_id: 0 for candidate_id in sorted(active)}
            for ballot in ballots:
                choice = next(
                    (candidate_id for candidate_id in ballot.ranking if candidate_id in active),
                    None,
                )
                if choice is not None:
                    counts[choice] += 1
            rounds.append(dict(counts))
            last_counts = counts
            total = sum(counts.values())
            if total == 0:
                return Tally(counts, (), 0.0, tuple(rounds))
            leader = min(counts, key=lambda key: (-counts[key], key))
            if counts[leader] * 2 > total or len(active) == 1:
                second = max((value for key, value in counts.items() if key != leader), default=0)
                return Tally(
                    counts,
                    (leader,),
                    round((counts[leader] - second) / total, 6),
                    tuple(rounds),
                )
            loser = min(counts, key=lambda key: (counts[key], key))
            active.remove(loser)
        return Tally(last_counts, (), 0.0, tuple(rounds))

    def _proportional(
        self,
        election_id: str,
        ballots: Sequence[Ballot],
        seats: int,
    ) -> Tally:
        candidates = {row.candidacy_id: row for row in self.repo.open_candidacies(election_id)}
        party_votes: dict[str, int] = defaultdict(int)
        candidate_votes: dict[str, int] = defaultdict(int)
        for ballot in ballots:
            if ballot.choice is None or ballot.choice not in candidates:
                continue
            candidacy = candidates[ballot.choice]
            party_id = candidacy.party_id or f"independent:{candidacy.candidacy_id}"
            party_votes[party_id] += 1
            candidate_votes[candidacy.candidacy_id] += 1
        seat_counts: dict[str, int] = defaultdict(int)
        for _seat in range(seats):
            if not party_votes:
                break
            party_id = min(
                party_votes,
                key=lambda key: (-party_votes[key] / (seat_counts[key] + 1), key),
            )
            seat_counts[party_id] += 1
        winners: list[str] = []
        for party_id, count in sorted(seat_counts.items()):
            rows = [
                row
                for row in candidates.values()
                if (row.party_id or f"independent:{row.candidacy_id}") == party_id
            ]
            rows.sort(key=lambda row: (-candidate_votes[row.candidacy_id], row.candidacy_id))
            winners.extend(row.candidacy_id for row in rows[:count])
        return Tally(
            dict(sorted(candidate_votes.items())),
            tuple(winners),
            0.0,
            (dict(sorted(seat_counts.items())),),
        )

    def tally(
        self,
        election_id: str,
        ballots: Sequence[Ballot],
        method: str,
    ) -> Tally:
        election = self.repo.elections[election_id]
        if method == "plurality":
            return self._plurality(ballots, election_id, election.voting_tick)
        if method == "approval":
            return self._approval(ballots)
        if method == "irv":
            return self._irv(ballots)
        if method == "proportional":
            return self._proportional(election_id, ballots, election.seats)
        raise ValueError(f"unknown election method: {method}")

    def turnout(self, election_id: str) -> float:
        eligible = self.repo.eligible_counts.get(election_id, 0)
        if eligible == 0:
            return 0.0
        cast_count = sum(
            1
            for (stored_election_id, _voter_id), ballot in self.repo.ballots.items()
            if stored_election_id == election_id
            and (ballot.choice is not None or ballot.ranking or ballot.approvals)
        )
        return round(cast_count / eligible, 6)

    async def hold(self, election_id: str, tick: int) -> Sequence[Event]:
        election = self.repo.elections[election_id]
        candidacies = self.repo.open_candidacies(election_id)
        prior_ballots = dict(self.repo.ballots)
        events: list[Event] = []
        deliberate: list[Ballot] = []
        reflex_voters: list[str] = []
        pending_voters = tuple(
            agent_id
            for agent_id in election.electorate_ids
            if not self.has_voted(agent_id, election_id)
        )
        if self.deliberate_ballots is None:
            reflex_voters.extend(pending_voters)
        else:
            budget = max(1, round(len(pending_voters) / self.cfg.llm_election_multiplier))
            for agent_id in pending_voters[:budget]:
                ballot = self.deliberate_ballots(agent_id, candidacies, tick)
                if ballot is not None:
                    deliberate.append(replace(ballot, origin="deliberate"))
            deliberate_ids = {ballot.voter_id for ballot in deliberate}
            reflex_voters.extend(
                agent_id for agent_id in pending_voters if agent_id not in deliberate_ids
            )

        first_election = self._resolved_elections == 0
        if first_election:
            fit = FitResult(dict(self.cfg.omega_prior), 0.0, 0.0, len(deliberate), True)
        else:
            fit_rows: list[tuple[str, str, Mapping[str, Mapping[str, float]]]] = []
            for ballot in deliberate:
                if ballot.choice is not None:
                    fit_rows.append(
                        (
                            ballot.voter_id,
                            ballot.choice,
                            {
                                candidacy.candidacy_id: self.vote_model.features(
                                    ballot.voter_id,
                                    candidacy,
                                    election_id,
                                    tick,
                                )
                                for candidacy in candidacies
                            },
                        )
                    )
            fit = self.vote_model.fit(fit_rows, election_id)
            if not fit.usable:
                rerun = replace(
                    election,
                    voting_tick=tick + self.clock.ticks_for(SimDuration(weeks=1)),
                    campaign_ends_tick=tick,
                    resolved=False,
                )
                self.repo.elections[election_id] = rerun
                events.append(
                    self._emit(
                        ELECTION_CALLED,
                        {
                            "election_id": election_id,
                            "office": election.office,
                            "seats": election.seats,
                            "method": election.method,
                            "called_tick": tick,
                            "voting_tick": rerun.voting_tick,
                            "campaign_ends_tick": rerun.campaign_ends_tick,
                            "electorate_size": len(election.electorate_ids),
                            "rerun_reason": "unusable_reflex_fit",
                        },
                        tick,
                    )
                )
                return tuple(events)

        for ballot in deliberate:
            events.append(self.cast(election_id, ballot, tick))
        for voter_id in reflex_voters:
            ballot = self.vote_model.choose(
                voter_id,
                candidacies,
                fit.omega,
                election_id,
                tick,
            )
            events.append(self.cast(election_id, ballot, tick))
        ballots = tuple(
            ballot
            for (stored_election_id, _voter_id), ballot in sorted(self.repo.ballots.items())
            if stored_election_id == election_id
        )
        tally = self.tally(election_id, ballots, election.method)
        turnout = self.turnout(election_id)
        events.append(
            self._emit(
                ELECTION_RESOLVED,
                {
                    "election_id": election_id,
                    "method": election.method,
                    "tallies": dict(tally.counts),
                    "winner_ids": list(tally.winner_ids),
                    "turnout": turnout,
                    "margin": tally.margin,
                    "rounds": [dict(row) for row in tally.rounds],
                    "n_deliberate": len(deliberate),
                    "n_reflex": len(reflex_voters),
                    "fitted_omega": dict(fit.omega),
                    "holdout_accuracy": fit.holdout_accuracy,
                    "first_election_prior": first_election,
                },
                tick,
                subject_ids=(election_id, *tally.winner_ids),
            )
        )
        total_votes = sum(tally.counts.values())
        refundable: list[tuple[str, str, int]] = []
        for candidacy_id, (agent_id, deposit, refunded) in sorted(self.repo.deposits.items()):
            candidacy = self.repo.candidacies[candidacy_id]
            if candidacy.election_id != election_id or refunded or total_votes == 0:
                continue
            votes = tally.counts.get(candidacy_id, 0)
            if votes / total_votes < self.cfg.deposit_refund_share:
                continue
            refundable.append((candidacy_id, agent_id, deposit))
        positive_refunds = [
            (self.treasury_id, agent_id, deposit)
            for _candidacy_id, agent_id, deposit in refundable
            if deposit > 0
        ]
        if positive_refunds:
            predicted = self.ledger.next_transfer_id(tick)
            refund_event = self._emit(
                CANDIDACY_DEPOSITS_REFUNDED,
                {
                    "election_id": election_id,
                    "refunds": [
                        {
                            "candidacy_id": candidacy_id,
                            "agent_id": agent_id,
                            "amount_cents": deposit,
                        }
                        for candidacy_id, agent_id, deposit in refundable
                        if deposit > 0
                    ],
                    "txn_id": predicted,
                },
                tick,
                subject_ids=(
                    election_id,
                    *(agent_id for _candidacy_id, agent_id, _deposit in refundable),
                ),
            )
            try:
                actual = self.ledger.post_transfers(
                    positive_refunds,
                    reason="transfer",
                    tick=tick,
                    cause=refund_event,
                )
                if actual != predicted:
                    raise RuntimeError("deposit refund transaction ordinal diverged")
            except Exception:
                self.repo.ballots.clear()
                self.repo.ballots.update(prior_ballots)
                self.log.rollback()
                raise
            events.append(refund_event)
        for candidacy_id, agent_id, deposit in refundable:
            self.repo.deposits[candidacy_id] = (agent_id, deposit, True)
        for candidacy_id, votes in tally.counts.items():
            candidacy = self.repo.candidacies[candidacy_id]
            self.repo.candidacies[candidacy_id] = replace(candidacy, votes=votes)
            self.parties.note_votes(candidacy.agent_id, votes)
            self.offices.note_votes(candidacy.agent_id, votes)
        for candidacy_id in tally.winner_ids:
            candidacy = self.repo.candidacies[candidacy_id]
            events.extend(
                self.offices.assume(
                    election.office,
                    candidacy.agent_id,
                    tick,
                    via=election_id,
                    salary_cents=self.cfg.offices[election.office].salary_cents,
                )
            )
        self.parties.close_election_cycle(tuple(candidacy.party_id for candidacy in candidacies))
        self.repo.elections[election_id] = replace(election, resolved=True)
        self._resolved_elections += 1
        return tuple(events)


class PolityResolver:
    slot: Final[InstitutionSlot] = InstitutionSlot.POLITY
    handles: Final[frozenset[ActionType]] = POLITY_ACTIONS

    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        parties: PartyRegistry,
        elections: ElectionOffice,
        offices: OfficeRegister,
        policy: PolicyEngine,
        exposure: ExposureLedger,
        graph: GraphLookup,
        beliefs: BeliefLookup,
        outlets: OutletLookup,
        world: WorldLookup,
        ledger: PolityLedger,
        runtime: Overlay,
        cfg: PolitySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.parties = parties
        self.elections = elections
        self.offices = offices
        self.policy = policy
        self.exposure = exposure
        self.graph = graph
        self.beliefs = beliefs
        self.outlets = outlets
        self.world = world
        self.ledger = ledger
        self.runtime = runtime
        self.cfg = cfg

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        state = ctx.state
        age = int(_get(state, "age_years", _get(state, "age", 0)))
        alive = bool(_get(state, "alive", True))
        incarcerated = bool(_get(state, "incarcerated", False))
        if action.type == ActionType.FOUND_PARTY:
            founding = tuple(action.params.get("founding_member_ids", ()))
            if not alive or age < 18 or incarcerated or len(set(founding)) < 3:
                return GateFailure(
                    "capability", "founding requires an eligible adult and 3 members"
                )
        elif action.type == ActionType.JOIN_PARTY:
            party_id = str(action.params.get("party_id", ""))
            if self.parties.get(party_id) is None:
                return GateFailure("capability", "party is not live")
        elif action.type == ActionType.ANNOUNCE_CANDIDACY:
            election_id = str(
                action.params.get("election_id") or action.params.get("office_id") or ""
            )
            records = set(_get(state, "criminal_record", ()))
            if (
                not alive
                or age < 18
                or incarcerated
                or records.intersection(self.cfg.candidacy_record_bar)
                or election_id not in self.elections.repo.elections
            ):
                return GateFailure("capability", "candidate fails age, record, or election gate")
        elif action.type == ActionType.CAMPAIGN:
            candidacy_id = str(action.params.get("candidacy_id", ""))
            candidacy = self.elections.repo.candidacies.get(candidacy_id)
            campaign_party_id = self.parties.membership(action.actor_id)
            if candidacy is None or (
                candidacy.agent_id != action.actor_id
                and (candidacy.party_id is None or candidacy.party_id != campaign_party_id)
            ):
                return GateFailure("capability", "actor does not hold or support the candidacy")
        elif action.type == ActionType.VOTE:
            election_id = str(action.params.get("election_id", ""))
            if not self.elections.eligible(action.actor_id, election_id, ctx.tick):
                return GateFailure("capability", "voter is not eligible")
            if self.elections.has_voted(action.actor_id, election_id):
                return GateFailure("capability", "voter has already voted")
            references = {
                str(value)
                for value in (
                    action.params.get("candidacy_id"),
                    action.params.get("candidate_id"),
                    *action.params.get("ranking", ()),
                    *action.params.get("approvals", ()),
                )
                if value is not None
            }
            valid = {row.candidacy_id for row in self.elections.repo.open_candidacies(election_id)}
            if not references <= valid:
                return GateFailure(
                    "capability",
                    "ballot references a candidacy outside this election",
                )
        elif action.type == ActionType.PROPOSE_POLICY:
            parameter = str(action.params.get("parameter", ""))
            if parameter not in self.policy.registry:
                return GateFailure("capability", "parameter is outside the policy registry")
            office = self.offices.holds_office(action.actor_id, ctx.tick)
            cosigners = set(action.params.get("cosigners", ()))
            if (
                office not in {"council", "president", "cb_governor"}
                and len(cosigners) < self.cfg.initiative_signatures
            ):
                return GateFailure("capability", "proposal lacks office authority or signatures")
        return None

    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        requires_place = action.type == ActionType.VOTE or (
            action.type == ActionType.CAMPAIGN and action.params.get("channel") == "rally"
        )
        if not requires_place:
            return None
        observed = _get(_get(ctx.observation, "place"), "place_id")
        place_id = action.params.get("place_id") or observed
        if not place_id:
            return GateFailure("locality", "vote or rally requires a committed place")
        return None

    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        cents = 0
        payee_id: str | None = None
        if action.type == ActionType.FOUND_PARTY:
            cents = self.cfg.party_founding_fee_cents
            payee_id = self.parties.treasury_id
        elif action.type == ActionType.ANNOUNCE_CANDIDACY:
            cents = self.cfg.candidacy_deposit_cents
            payee_id = self.elections.treasury_id
        elif action.type == ActionType.CAMPAIGN:
            channel = action.params.get("channel", "canvass")
            cents = max(
                int(action.params.get("amount_cents", 0)),
                int(action.params.get("spend_cents", 0)),
            )
            if channel == "ads":
                outlet = self.outlets.get(str(action.params.get("target_id", "")))
                payee_id = None if outlet is None else _get(outlet, "firm_id")
            elif channel == "rally":
                observed = _get(_get(ctx.observation, "place"), "place_id")
                place_id = action.params.get("place_id") or observed
                if place_id:
                    payee_id = _get(self.world.place(str(place_id)), "owner_id")
            cap = self.runtime.get("polity.campaign_cap_cents", ctx.tick)
            if cap is not None and cents > int(cap):
                return GateFailure("resources", "campaign spend exceeds the live policy cap")
            if channel == "canvass":
                cents = 0
        elif action.type == ActionType.LOBBY:
            cents = int(action.params.get("spend_cents", 0))
        if cents and not self.ledger.can_pay(action.actor_id, cents, payee_id):
            return GateFailure("resources", "actor cannot fund the polity action")
        return None

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        del ctx
        events: list[Event] = []
        for row in sorted(
            actions,
            key=lambda item: (item.action.actor_id, str(item.action.action_id)),
        ):
            action = row.action
            params = row.validated_params
            if action.type == ActionType.FOUND_PARTY:
                _party, emitted = self.parties.found(
                    action.actor_id,
                    cast(FoundPartyParams, params),
                    tick,
                )
                events.extend(emitted)
            elif action.type == ActionType.JOIN_PARTY:
                join = cast(JoinPartyParams, params)
                events.extend(self.parties.join(action.actor_id, join.party_id, tick))
            elif action.type == ActionType.ANNOUNCE_CANDIDACY:
                _candidacy, emitted = self.elections.announce(
                    action.actor_id,
                    cast(AnnounceCandidacyParams, params),
                    tick,
                )
                events.extend(emitted)
            elif action.type == ActionType.CAMPAIGN:
                events.extend(
                    self.elections.campaign(
                        action.actor_id,
                        cast(CampaignParams, params),
                        tick,
                    )
                )
            elif action.type == ActionType.VOTE:
                vote = cast(VoteParams, params)
                choice = vote.candidacy_id or vote.candidate_id
                ballot = Ballot(
                    action.actor_id,
                    choice,
                    vote.ranking,
                    vote.approvals,
                    "deliberate" if action.origin == "deliberate" else "reflex",
                    {key: 0.0 for key in (*VoteModel.FEATURES, "epsilon")},
                )
                events.append(self.elections.cast(vote.election_id, ballot, tick))
            elif action.type == ActionType.PROPOSE_POLICY:
                policy = cast(ProposePolicyParams, params)
                if policy.parameter is None:
                    continue
                proposal = Proposal(
                    str(det_uuid("polis.proposal", action.actor_id, action.action_id)),
                    action.actor_id,
                    policy.parameter,
                    self.runtime.get(policy.parameter, tick),
                    policy.proposed_value,
                    policy.rationale or policy.description,
                    tuple(sorted(set(policy.cosigners))),
                    tick,
                )
                events.append(self.policy.propose(proposal))
        return tuple(events)

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]:
        del ctx
        if action_type == ActionType.JOIN_PARTY:
            return tuple(
                {
                    "party_id": party.party_id,
                    "name": party.name,
                    "platform": dict(party.platform),
                }
                for party in self.parties.live()
            )
        if action_type == ActionType.VOTE:
            return tuple(
                {
                    "candidacy_id": row.candidacy_id,
                    "election_id": row.election_id,
                    "agent_id": row.agent_id,
                    "party_id": row.party_id,
                }
                for row in self.elections.repo.open_candidacies()
            )
        if action_type == ActionType.PROPOSE_POLICY:
            return tuple(
                {
                    "parameter": key,
                    "type": str(spec.py_type),
                    "lo": spec.lo,
                    "hi": spec.hi,
                    "authority": spec.authority,
                }
                for key, spec in sorted(self.policy.registry.items())
            )
        return ()


__all__ = [
    "POLITY_ACTIONS",
    "Ballot",
    "Candidacy",
    "Election",
    "ElectionOffice",
    "ExposureLedger",
    "FitResult",
    "MemoryElectionRepository",
    "NullPolityLedger",
    "OfficeRegister",
    "Party",
    "PartyRegistry",
    "PolityLedger",
    "PolityResolver",
    "Tally",
    "VoteModel",
]
