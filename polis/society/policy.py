from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol

from polis.config.mechanisms import mechanism
from polis.config.runtime import Enactment
from polis.config.settings import PolitySettings, Settings
from polis.events.kinds import (
    BUDGET_SET,
    POLICY_BLOCKED,
    POLICY_ENACTED,
    POLICY_PROPOSED,
    POLICY_REJECTED,
    POLICY_REPEALED,
    POLICY_VETOED,
    POLICY_VOTED,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock, SimDuration
from polis.kernel.det import det_uuid

Authority = Literal["council_majority", "council_and_president", "cb_governor_only"]
Predicate = Literal[
    "P-RANGE",
    "P-MONEY",
    "P-SOLVENCY",
    "P-NONNEGATIVE",
    "P-MONOTONE",
    "P-SCOPE",
    "P-SEPARATION",
]


@dataclass(frozen=True, slots=True)
class PolicySpec:
    parameter: str
    py_type: type | str
    lo: Any | None
    hi: Any | None
    authority: Authority
    lag: str
    effect_site: str
    enabled_when: str | None = None


def _spec(
    parameter: str,
    py_type: type | str,
    lo: Any | None,
    hi: Any | None,
    authority: Authority,
    lag: str,
    effect_site: str,
    enabled_when: str | None = None,
) -> PolicySpec:
    return PolicySpec(
        parameter,
        py_type,
        lo,
        hi,
        authority,
        lag,
        effect_site,
        enabled_when,
    )


# Closed by specification. Adding a row is a model change, not an application extension.
POLICY_REGISTRY: Final[Mapping[str, PolicySpec]] = MappingProxyType(
    {
        row.parameter: row
        for row in (
            _spec(
                "tax.income.brackets",
                "brackets",
                0,
                7_500,
                "council_majority",
                "1mo",
                "payroll withholding",
            ),
            _spec(
                "tax.corporate_bp",
                "bp",
                0,
                6_000,
                "council_majority",
                "1q",
                "firm fiscal close",
            ),
            _spec(
                "tax.capital_gains_bp",
                "bp",
                0,
                6_000,
                "council_majority",
                "1mo",
                "exchange settlement",
            ),
            _spec(
                "tax.inheritance_bp",
                "bp",
                0,
                9_000,
                "council_and_president",
                "1mo",
                "estate settlement",
            ),
            _spec(
                "tax.vat_bp",
                "bp",
                0,
                3_500,
                "council_majority",
                "1w",
                "goods purchase",
            ),
            _spec(
                "money.policy_rate_bp",
                "bp",
                -200,
                2_500,
                "cb_governor_only",
                "1w",
                "banking",
            ),
            _spec(
                "welfare.unemployment_benefit_cents",
                "cents",
                0,
                "2x_median_wage",
                "council_majority",
                "1mo",
                "government transfers",
            ),
            _spec(
                "welfare.benefit_duration_ticks",
                int,
                0,
                "2_sim_years",
                "council_majority",
                "1mo",
                "government transfers",
            ),
            _spec(
                "welfare.pension_cents",
                "cents",
                0,
                "2x_median_wage",
                "council_majority",
                "1q",
                "government transfers",
            ),
            _spec(
                "welfare.child_benefit_cents",
                "cents",
                0,
                "median_wage",
                "council_majority",
                "1mo",
                "demography transfers",
            ),
            _spec(
                "education.spend_cents_per_student",
                "cents",
                0,
                None,
                "council_majority",
                "1q",
                "school quality",
            ),
            _spec(
                "education.compulsory_until_age",
                int,
                10,
                22,
                "council_majority",
                "1q",
                "education gating",
            ),
            _spec(
                "police.budget_cents",
                "cents",
                0,
                None,
                "council_majority",
                "1mo",
                "crime detection",
            ),
            _spec(
                "courts.budget_cents",
                "cents",
                0,
                None,
                "council_majority",
                "1mo",
                "court throughput",
            ),
            _spec(
                "courts.loser_pays",
                bool,
                None,
                None,
                "council_majority",
                "1mo",
                "civil judgment",
            ),
            _spec(
                "prison.capacity",
                int,
                0,
                None,
                "council_majority",
                "1q",
                "incarceration capacity",
            ),
            _spec(
                "sentencing.multiplier_bp",
                "bp",
                2_500,
                40_000,
                "council_and_president",
                "next_judgment",
                "statutory ranges",
            ),
            _spec(
                "labour.minimum_wage_cents",
                "cents",
                0,
                "3x_median_wage",
                "council_majority",
                "1mo",
                "labour market",
            ),
            _spec(
                "labour.max_hours_per_sim_week",
                int,
                20,
                80,
                "council_majority",
                "1mo",
                "work validation",
            ),
            _spec(
                "regulation.finance.margin_allowed",
                bool,
                None,
                None,
                "council_majority",
                "1w",
                "exchange validation",
            ),
            _spec(
                "regulation.finance.short_selling_allowed",
                bool,
                None,
                None,
                "council_majority",
                "1w",
                "short validation",
            ),
            _spec(
                "regulation.finance.insider_trading_enforced",
                bool,
                None,
                None,
                "council_majority",
                "immediate",
                "legality gate",
            ),
            _spec(
                "regulation.labour.at_will_dismissal",
                bool,
                None,
                None,
                "council_majority",
                "1mo",
                "dismissal validation",
            ),
            _spec(
                "regulation.media.disclosure_required",
                bool,
                None,
                None,
                "council_majority",
                "1w",
                "media legal gate",
            ),
            _spec(
                "regulation.housing.rent_cap_bp",
                "optional_bp",
                0,
                10_000,
                "council_majority",
                "1q",
                "place rents",
            ),
            _spec(
                "migration.quota_per_sim_year",
                int,
                0,
                "20pct_population",
                "council_majority",
                "1q",
                "migration admission",
            ),
            _spec(
                "polity.campaign_cap_cents",
                "optional_cents",
                0,
                None,
                "council_and_president",
                "next_election",
                "campaign resource gate",
            ),
            _spec(
                "polity.felon_franchise",
                bool,
                None,
                None,
                "council_majority",
                "next_election",
                "election eligibility",
            ),
            _spec(
                "government.debt_ceiling_cents",
                "cents",
                0,
                None,
                "council_and_president",
                "immediate",
                "fiscal admissibility",
            ),
            _spec(
                "society.feed_algorithm",
                "enum",
                ("chronological", "engagement", "social", "diversity"),
                None,
                "council_and_president",
                "1w",
                "feed ranking",
                "polity.can_regulate_feed",
            ),
            _spec(
                "government.public_notices_budget_cents",
                "cents",
                0,
                None,
                "council_majority",
                "1mo",
                "public notice distribution",
            ),
        )
    }
)


def _setting_flag(settings: Any, path: str) -> bool:
    current: Any = settings
    parts = path.split(".")
    if parts[0] == "polity" and not hasattr(current, "polity"):
        parts = parts[1:]
    for part in parts:
        current = getattr(current, part)
    return bool(current)


def registry_for(settings: Settings) -> Mapping[str, PolicySpec]:
    return MappingProxyType(
        {
            key: spec
            for key, spec in POLICY_REGISTRY.items()
            if spec.enabled_when is None or _setting_flag(settings, spec.enabled_when)
        }
    )


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    proposer_id: str
    parameter: str
    old_value: Any
    proposed_value: Any
    rationale: str
    cosigners: tuple[str, ...]
    proposed_tick: int


@dataclass(frozen=True, slots=True)
class Admissibility:
    admissible: bool
    failed: Predicate | None
    detail: str = ""


class Overlay(Protocol):
    def get(self, parameter: str, tick: int) -> Any: ...

    def bp(self, key: str, tick: int) -> int: ...

    def cents(self, key: str, tick: int) -> int: ...

    def flag(self, key: str, tick: int) -> bool: ...

    def brackets(self, key: str, tick: int) -> tuple[tuple[int, int], ...]: ...

    def as_of(self, tick: int) -> Mapping[str, Any]: ...

    def enact(
        self,
        parameter: str,
        value: Any,
        effective_tick: int,
        policy_id: str,
        event_seq: int,
        *,
        enacted_tick: int = 0,
    ) -> None: ...

    def history(self, parameter: str) -> tuple[Enactment, ...]: ...


class OfficeLookup(Protocol):
    def holds_office(self, agent_id: str, tick: int) -> str | None: ...

    def holder(self, office: str, tick: int) -> str | tuple[str, ...] | None: ...


@dataclass(frozen=True, slots=True)
class PolicyRecord:
    policy_id: str
    parameter: str
    old_value: Any
    new_value: Any
    enacted_tick: int
    effective_tick: int
    enacted_by: str
    vote_margin: float
    proposal_seq: int
    repealed_tick: int | None = None


class PolicyRepository(Protocol):
    def add_proposal(self, proposal: Proposal, event_seq: int) -> None: ...

    def pending(self) -> tuple[Proposal, ...]: ...

    def proposal_seq(self, proposal_id: str) -> int: ...

    def close_proposal(self, proposal_id: str) -> None: ...

    def enact(self, record: PolicyRecord) -> PolicyRecord | None: ...

    def live(self, parameter: str) -> PolicyRecord | None: ...

    def history(self, parameter: str) -> tuple[PolicyRecord, ...]: ...


class MemoryPolicyRepository:
    def __init__(self) -> None:
        self._proposals: dict[str, Proposal] = {}
        self._proposal_seqs: dict[str, int] = {}
        self._closed: set[str] = set()
        self._records: dict[str, list[PolicyRecord]] = {}

    def add_proposal(self, proposal: Proposal, event_seq: int) -> None:
        self._proposals[proposal.proposal_id] = proposal
        self._proposal_seqs[proposal.proposal_id] = event_seq

    def pending(self) -> tuple[Proposal, ...]:
        return tuple(
            self._proposals[key] for key in sorted(self._proposals) if key not in self._closed
        )

    def proposal_seq(self, proposal_id: str) -> int:
        return self._proposal_seqs[proposal_id]

    def close_proposal(self, proposal_id: str) -> None:
        self._closed.add(proposal_id)

    def enact(self, record: PolicyRecord) -> PolicyRecord | None:
        rows = self._records.setdefault(record.parameter, [])
        prior = rows[-1] if rows else None
        if prior is not None:
            rows[-1] = PolicyRecord(
                prior.policy_id,
                prior.parameter,
                prior.old_value,
                prior.new_value,
                prior.enacted_tick,
                prior.effective_tick,
                prior.enacted_by,
                prior.vote_margin,
                prior.proposal_seq,
                record.enacted_tick,
            )
        rows.append(record)
        return prior

    def live(self, parameter: str) -> PolicyRecord | None:
        rows = self._records.get(parameter, ())
        return None if not rows else rows[-1]

    def history(self, parameter: str) -> tuple[PolicyRecord, ...]:
        return tuple(self._records.get(parameter, ()))


@dataclass(frozen=True, slots=True)
class FiscalSnapshot:
    current_balance_cents: int = 0
    projected_revenue_cents: int = 0
    projected_outlay_cents: int = 0
    median_wage_cents: int = 0
    population: int = 0
    money_delta_cents: int = 0


class FiscalProjector:
    def __init__(
        self,
        snapshot: Callable[[Mapping[str, Any], int, int], FiscalSnapshot] | None = None,
    ) -> None:
        self._snapshot = snapshot or (lambda _overlay, _horizon, _tick: FiscalSnapshot())

    def snapshot(
        self,
        overlay: Mapping[str, Any],
        horizon_ticks: int,
        tick: int,
    ) -> FiscalSnapshot:
        return self._snapshot(overlay, horizon_ticks, tick)

    @mechanism(
        "fiscal_scoring",
        entails=(
            "proposals are scored against a frozen population, so behavioural responses "
            "to tax and benefit changes are never anticipated by the legislature. This "
            "makes fiscal policy systematically mis-calibrated in the direction of "
            "ignoring Laffer and labour-supply effects. Any A4 result about policy "
            "transmission must note that the enacting body did not anticipate the response "
            "it caused."
        ),
    )
    def projected_balance(
        self,
        overlay: Mapping[str, Any],
        horizon_ticks: int,
        tick: int,
    ) -> int:
        row = self.snapshot(overlay, horizon_ticks, tick)
        return row.current_balance_cents + row.projected_revenue_cents - row.projected_outlay_cents

    def money_delta(
        self,
        overlay: Mapping[str, Any],
        horizon_ticks: int,
        tick: int,
    ) -> int:
        return self.snapshot(overlay, horizon_ticks, tick).money_delta_cents


VoteProvider = Callable[[str, Proposal], Literal["aye", "nay", "abstain"]]


class PolicyEngine:
    def __init__(
        self,
        *,
        runtime: Overlay,
        log: EventLog,
        clock: Clock,
        offices: OfficeLookup,
        fiscal: FiscalProjector,
        repo: PolicyRepository,
        cfg: PolitySettings,
        registry: Mapping[str, PolicySpec] | None = None,
        vote_provider: VoteProvider | None = None,
    ) -> None:
        self.runtime = runtime
        self.log = log
        self.clock = clock
        self.offices = offices
        self.fiscal = fiscal
        self.repo = repo
        self.cfg = cfg
        self.registry = (
            MappingProxyType(
                {
                    key: spec
                    for key, spec in POLICY_REGISTRY.items()
                    if spec.enabled_when is None or _setting_flag(cfg, spec.enabled_when)
                }
            )
            if registry is None
            else registry
        )
        self.vote_provider = vote_provider or (lambda _agent_id, _proposal: "aye")

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

    def propose(self, p: Proposal) -> Event:
        event = self._emit(
            POLICY_PROPOSED,
            {
                "proposal_id": p.proposal_id,
                "proposer_id": p.proposer_id,
                "parameter": p.parameter,
                "old_value": p.old_value,
                "proposed_value": p.proposed_value,
                "rationale": p.rationale,
                "cosigners": list(p.cosigners),
            },
            p.proposed_tick,
            actor_id=p.proposer_id,
            subject_ids=(p.proposer_id, *p.cosigners),
        )
        self.repo.add_proposal(p, event.seq)
        return event

    def _dynamic_hi(self, marker: Any, p: Proposal, tick: int) -> int | None:
        if not isinstance(marker, str):
            return marker if isinstance(marker, int) and not isinstance(marker, bool) else None
        horizon = self.clock.profile.ticks_per_sim_day * self.clock.profile.days_per_sim_year
        row = self.fiscal.snapshot({p.parameter: p.proposed_value}, horizon, tick)
        if marker == "median_wage":
            return row.median_wage_cents
        if marker == "2x_median_wage":
            return 2 * row.median_wage_cents
        if marker == "3x_median_wage":
            return 3 * row.median_wage_cents
        if marker == "20pct_population":
            return row.population // 5
        if marker == "2_sim_years":
            return 2 * horizon
        return None

    def _range_failure(self, p: Proposal, tick: int) -> str | None:
        spec = self.registry.get(p.parameter)
        if spec is None:
            return "parameter is outside the closed policy registry"
        value = p.proposed_value
        if spec.py_type is bool:
            return None if isinstance(value, bool) else "value must be boolean"
        if spec.py_type == "enum":
            choices = spec.lo
            if not isinstance(choices, (tuple, list, set, frozenset)):
                return "policy registry contains an invalid enum"
            return None if value in choices else "value is outside the enum"
        if spec.py_type == "brackets":
            if (
                isinstance(spec.lo, bool)
                or not isinstance(spec.lo, int)
                or isinstance(spec.hi, bool)
                or not isinstance(spec.hi, int)
            ):
                return "policy registry contains invalid bracket bounds"
            if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 5:
                return "brackets must contain between one and five rows"
            for row in value:
                if (
                    not isinstance(row, (tuple, list))
                    or len(row) != 2
                    or isinstance(row[0], bool)
                    or isinstance(row[1], bool)
                    or not isinstance(row[0], int)
                    or not isinstance(row[1], int)
                    or not spec.lo <= row[1] <= spec.hi
                ):
                    return "brackets contain an invalid threshold or rate"
            return None
        nullable = spec.py_type in {"optional_bp", "optional_cents"}
        if nullable and value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return "value must be an integer in canonical units"
        hi = self._dynamic_hi(spec.hi, p, tick)
        nonnegative_type = spec.py_type in {int, "cents", "optional_cents"}
        if (
            spec.lo is not None
            and value < int(spec.lo)
            and not (nonnegative_type and spec.lo == 0 and value < 0)
        ):
            return f"value is below {spec.lo}"
        if hi is not None and value > hi:
            return f"value is above {hi}"
        return None

    def admissible(self, p: Proposal, tick: int) -> Admissibility:
        forbidden = (
            "run.",
            "llm.",
            "clock.",
            "mechanisms.",
            "ablations.",
            "population.",
            "world.",
        )
        if p.parameter.startswith(forbidden):
            return Admissibility(False, "P-SCOPE", "simulation machinery is not legislable")

        range_failure = self._range_failure(p, tick)
        if range_failure is not None:
            return Admissibility(False, "P-RANGE", range_failure)

        value = p.proposed_value
        spec = self.registry[p.parameter]
        if spec.py_type in {int, "cents", "optional_cents"} and value is not None and value < 0:
            return Admissibility(False, "P-NONNEGATIVE", "count or price cannot be negative")
        if spec.py_type == "brackets":
            thresholds = [row[0] for row in value]
            if thresholds != sorted(set(thresholds)):
                return Admissibility(False, "P-MONOTONE", "bracket thresholds must increase")

        if (
            p.parameter == "money.policy_rate_bp"
            and self.offices.holds_office(p.proposer_id, tick) != "cb_governor"
        ):
            return Admissibility(
                False,
                "P-SEPARATION",
                "only the central bank governor may set the policy rate",
            )

        horizon = self.clock.profile.ticks_per_sim_day * self.clock.profile.days_per_sim_year
        proposed = {
            parameter: self.runtime.get(parameter, tick) for parameter in sorted(self.registry)
        }
        proposed.update(self.runtime.as_of(tick))
        proposed[p.parameter] = value
        if self.fiscal.money_delta(proposed, horizon, tick) != 0:
            return Admissibility(False, "P-MONEY", "projected ledger legs are not balanced")

        ceiling = int(
            proposed.get(
                "government.debt_ceiling_cents",
                self.runtime.get("government.debt_ceiling_cents", tick),
            )
        )
        if self.fiscal.projected_balance(proposed, horizon, tick) < -ceiling:
            return Admissibility(False, "P-SOLVENCY", "projection breaches the debt ceiling")
        return Admissibility(True, None)

    async def council_session(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for proposal in self.repo.pending():
            admissibility = self.admissible(proposal, tick)
            if not admissibility.admissible:
                events.append(
                    self._emit(
                        POLICY_BLOCKED,
                        {
                            "proposal_id": proposal.proposal_id,
                            "predicate": admissibility.failed,
                            "detail": admissibility.detail,
                        },
                        tick,
                        subject_ids=(proposal.proposal_id,),
                    )
                )
                self.repo.close_proposal(proposal.proposal_id)
                continue

            spec = self.registry[proposal.parameter]
            if spec.authority == "cb_governor_only":
                events.extend(self.enact(proposal, 1.0, proposal.proposer_id, tick))
                continue

            holder = self.offices.holder("council", tick)
            members = () if holder is None else ((holder,) if isinstance(holder, str) else holder)
            votes = [
                {
                    "agent_id": member,
                    "choice": self.vote_provider(member, proposal),
                    "origin": "reflex",
                }
                for member in sorted(members)
            ]
            yeas = sum(row["choice"] == "aye" for row in votes)
            nays = sum(row["choice"] == "nay" for row in votes)
            abstentions = len(votes) - yeas - nays
            passed = yeas > nays and yeas > 0
            margin = 0.0 if not votes else round((yeas - nays) / len(votes), 6)
            events.append(
                self._emit(
                    POLICY_VOTED,
                    {
                        "proposal_id": proposal.proposal_id,
                        "chamber": "council",
                        "votes": votes,
                        "yeas": yeas,
                        "nays": nays,
                        "abstentions": abstentions,
                        "passed": passed,
                        "margin": margin,
                    },
                    tick,
                    subject_ids=(proposal.proposal_id,),
                )
            )
            if not passed:
                events.append(
                    self._emit(
                        POLICY_REJECTED,
                        {
                            "proposal_id": proposal.proposal_id,
                            "yeas": yeas,
                            "nays": nays,
                            "abstentions": abstentions,
                        },
                        tick,
                    )
                )
                self.repo.close_proposal(proposal.proposal_id)
                continue

            if spec.authority == "council_and_president":
                president = self.offices.holder("president", tick)
                president_id = president if isinstance(president, str) else None
                vetoed = (
                    president_id is not None and self.vote_provider(president_id, proposal) == "nay"
                )
                if vetoed:
                    seats = self.cfg.offices["council"].seats
                    overridden = len(members) >= seats and yeas * 3 >= seats * 2
                    events.append(
                        self._emit(
                            POLICY_VETOED,
                            {
                                "proposal_id": proposal.proposal_id,
                                "president_id": president_id,
                                "overridden": overridden,
                                "override_margin": margin,
                            },
                            tick,
                        )
                    )
                    if not overridden:
                        self.repo.close_proposal(proposal.proposal_id)
                        continue
            events.extend(self.enact(proposal, margin, "council", tick))
        return tuple(events)

    def effective_tick_for(self, parameter: str, enacted_tick: int) -> int:
        lag = self.registry[parameter].lag
        if lag in {"immediate", "next_judgment"}:
            delta = 1
        elif lag == "next_election":
            delta = self.clock.ticks_for(
                SimDuration(days=max(1, self.cfg.campaign_length_sim_days))
            )
        else:
            delta = self.clock.ticks_for(SimDuration.parse(lag))
        return enacted_tick + max(1, delta)

    def enact(
        self,
        p: Proposal,
        margin: float,
        enacted_by: str,
        tick: int,
    ) -> Sequence[Event]:
        effective_tick = self.effective_tick_for(p.parameter, tick)
        policy_id = str(det_uuid("polis.policy", p.proposal_id, tick))
        proposal_seq = self.repo.proposal_seq(p.proposal_id)
        event = self._emit(
            POLICY_ENACTED,
            {
                "policy_id": policy_id,
                "parameter": p.parameter,
                "old_value": p.old_value,
                "new_value": p.proposed_value,
                "effective_tick": effective_tick,
                "enacted_by": enacted_by,
                "vote_margin": margin,
                "proposal_seq": proposal_seq,
            },
            tick,
            actor_id=enacted_by,
            subject_ids=(p.proposal_id, policy_id),
        )
        try:
            self.runtime.enact(
                p.parameter,
                p.proposed_value,
                effective_tick,
                policy_id,
                event.seq,
                enacted_tick=tick,
            )
        except Exception:
            self.log.rollback()
            raise
        prior = self.repo.enact(
            PolicyRecord(
                policy_id,
                p.parameter,
                p.old_value,
                p.proposed_value,
                tick,
                effective_tick,
                enacted_by,
                margin,
                proposal_seq,
            )
        )
        self.repo.close_proposal(p.proposal_id)
        events = [event]
        history = self.repo.history(p.parameter)
        if len(history) >= 2 and p.proposed_value == history[-2].old_value:
            events.append(
                self._emit(
                    POLICY_REPEALED,
                    {
                        "policy_id": policy_id,
                        "parameter": p.parameter,
                        "restored_value": p.proposed_value,
                        "repealed_policy_id": None if prior is None else prior.policy_id,
                    },
                    tick,
                )
            )
        return tuple(events)

    def set_budget(self, tick: int) -> Event:
        horizon = self.clock.profile.ticks_per_sim_day * self.clock.profile.days_per_sim_year
        overlay = self.runtime.as_of(tick)
        fiscal = self.fiscal.snapshot(overlay, horizon, tick)
        allocations = {
            "police": self.runtime.cents("police.budget_cents", tick),
            "courts": self.runtime.cents("courts.budget_cents", tick),
            "education": self.runtime.cents("education.spend_cents_per_student", tick),
            "welfare": sum(
                (
                    self.runtime.cents("welfare.unemployment_benefit_cents", tick),
                    self.runtime.cents("welfare.pension_cents", tick),
                    self.runtime.cents("welfare.child_benefit_cents", tick),
                )
            ),
            "prisons": int(self.runtime.get("prison.capacity", tick)),
            "public_notices": self.runtime.cents(
                "government.public_notices_budget_cents",
                tick,
            ),
        }
        return self._emit(
            BUDGET_SET,
            {
                "period_start_tick": tick,
                "revenue_projection_cents": fiscal.projected_revenue_cents,
                "outlay_projection_cents": fiscal.projected_outlay_cents,
                "allocations": allocations,
                "debt_cents": max(0, -fiscal.current_balance_cents),
            },
            tick,
        )


def project_enactment(runtime: Overlay, event: Event) -> None:
    if event.kind != POLICY_ENACTED:
        return
    runtime.enact(
        str(event.payload["parameter"]),
        event.payload["new_value"],
        int(event.payload["effective_tick"]),
        str(event.payload["policy_id"]),
        event.seq,
        enacted_tick=event.tick,
    )


__all__ = [
    "POLICY_REGISTRY",
    "Admissibility",
    "Authority",
    "FiscalProjector",
    "FiscalSnapshot",
    "MemoryPolicyRepository",
    "OfficeLookup",
    "Overlay",
    "PolicyEngine",
    "PolicyRecord",
    "PolicyRepository",
    "PolicySpec",
    "Predicate",
    "Proposal",
    "project_enactment",
    "registry_for",
]
