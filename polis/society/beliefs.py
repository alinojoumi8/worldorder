from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Literal, Protocol

from polis.config.mechanisms import mechanism
from polis.config.settings import BeliefSettings, SocietySettings
from polis.events.kinds import (
    BANKRUPTCY_FILED,
    BELIEF_DRIFT_APPLIED,
    BELIEF_PRIORS_SET,
    BELIEF_UPDATE_REJECTED,
    BELIEF_UPDATED,
    FIRED,
    PAYROLL_SHORTFALL,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.rng import RngRegistry
from polis.society.graph import SocialGraph
from polis.society.protocols import BeliefUpdate

PropClass = Literal["policy", "factual", "trust"]
Channel = Literal["inherited", "experience", "social", "media", "reflection"]


@dataclass(frozen=True, slots=True)
class PropositionSpec:
    name: str
    cls: PropClass
    lo: float
    hi: float
    default_value: float
    default_confidence: float
    templated: bool = False


@dataclass(frozen=True, slots=True)
class Belief:
    agent_id: str
    proposition: str
    value: float
    confidence: float
    source: Channel
    source_ref: str | None
    updated_tick: int


class EntityContext(Protocol):
    def entity_exists(self, entity_kind: str, entity_id: str) -> bool: ...


class BeliefRepository(Protocol):
    def get(self, agent_id: str, proposition: str) -> Belief | None: ...

    def put(self, belief: Belief) -> None: ...

    def for_proposition(self, proposition: str) -> tuple[Belief, ...]: ...

    def entity_exists(self, entity_kind: str, entity_id: str) -> bool: ...


class MemoryBeliefRepository:
    def __init__(self, *, entities: Mapping[str, frozenset[str]] | None = None) -> None:
        self._beliefs: dict[tuple[str, str], Belief] = {}
        self._entities = dict(entities or {})

    def get(self, agent_id: str, proposition: str) -> Belief | None:
        return self._beliefs.get((agent_id, proposition))

    def put(self, belief: Belief) -> None:
        self._beliefs[(belief.agent_id, belief.proposition)] = belief

    def for_proposition(self, proposition: str) -> tuple[Belief, ...]:
        return tuple(
            sorted(
                (row for row in self._beliefs.values() if row.proposition == proposition),
                key=lambda row: row.agent_id,
            )
        )

    def entity_exists(self, entity_kind: str, entity_id: str) -> bool:
        known = self._entities.get(entity_kind)
        return bool(entity_id) if known is None else entity_id in known


POLICY_PROPOSITIONS: Final[tuple[str, ...]] = (
    "policy.tax.progressivity",
    "policy.tax.rate_should_rise",
    "policy.tax.corporate_should_rise",
    "policy.tax.inheritance_should_rise",
    "policy.welfare.generosity",
    "policy.welfare.conditionality",
    "policy.money.should_tighten",
    "policy.money.independence",
    "policy.education.spend",
    "policy.education.compulsory_longer",
    "policy.police.budget",
    "policy.sentencing.severity",
    "policy.labour.min_wage_should_rise",
    "policy.labour.protection",
    "policy.regulation.finance",
    "policy.regulation.media",
    "policy.housing.rent_control",
    "policy.migration.openness",
    "policy.market.free_vs_managed",
    "policy.debt.austerity",
)

_FACTUAL_TEMPLATES: Final[tuple[str, ...]] = (
    "fact.firm.<fm_id>.solvent",
    "fact.firm.<fm_id>.fraudulent",
    "fact.agent.<ag_id>.corrupt",
    "fact.agent.<ag_id>.competent",
    "fact.market.<symbol>.overvalued",
    "fact.election.<el_id>.rigged",
    "fact.outlet.<ol_id>.fabricates",
    "fact.policy.<param>.caused_harm",
    "fact.party.<pt_id>.corrupt",
)
_FACTUAL_CLOSED: Final[tuple[str, ...]] = (
    "fact.economy.recession_now",
    "fact.economy.prices_rising",
    "fact.economy.jobs_scarce",
    "fact.crime.rising",
)
_TRUST_CLOSED: Final[tuple[str, ...]] = (
    "trust.generalised",
    "trust.institution.court",
    "trust.institution.police",
    "trust.institution.government",
    "trust.institution.bank",
    "trust.institution.market",
    "trust.institution.press",
)
_TRUST_TEMPLATES: Final[tuple[str, ...]] = (
    "trust.outlet.<ol_id>",
    "trust.party.<pt_id>",
    "trust.agent.<ag_id>",
)


def _registry() -> dict[str, PropositionSpec]:
    rows = {
        name: PropositionSpec(name, "policy", -1.0, 1.0, 0.0, 0.35) for name in POLICY_PROPOSITIONS
    }
    rows.update(
        {name: PropositionSpec(name, "factual", 0.0, 1.0, 0.5, 0.25) for name in _FACTUAL_CLOSED}
    )
    rows.update(
        {
            name: PropositionSpec(name, "factual", 0.0, 1.0, 0.5, 0.25, True)
            for name in _FACTUAL_TEMPLATES
        }
    )
    rows.update(
        {name: PropositionSpec(name, "trust", 0.0, 1.0, 0.5, 0.35) for name in _TRUST_CLOSED}
    )
    rows.update(
        {
            name: PropositionSpec(name, "trust", 0.0, 1.0, 0.5, 0.35, True)
            for name in _TRUST_TEMPLATES
        }
    )
    return rows


PROPOSITION_REGISTRY: Final[Mapping[str, PropositionSpec]] = _registry()

_TEMPLATE_TOKEN = re.compile(r"<([^>]+)>")


def _match_template(raw: str, template: str) -> tuple[str, str] | None:
    token = _TEMPLATE_TOKEN.search(template)
    if token is None:
        return None
    before, after = template[: token.start()], template[token.end() :]
    if not raw.startswith(before) or not raw.endswith(after):
        return None
    end = len(raw) - len(after) if after else len(raw)
    entity_id = raw[len(before) : end]
    if not entity_id or "." in entity_id:
        return None
    entity_kind = token.group(1).removesuffix("_id")
    return entity_kind, entity_id


def resolve_proposition(raw: str, ctx: EntityContext) -> str | None:
    direct = PROPOSITION_REGISTRY.get(raw)
    if direct is not None and not direct.templated:
        return raw
    for template, spec in PROPOSITION_REGISTRY.items():
        if not spec.templated:
            continue
        match = _match_template(raw, template)
        if match is None:
            continue
        kind, entity_id = match
        return raw if ctx.entity_exists(kind, entity_id) else None
    return None


def _spec_for(proposition: str) -> PropositionSpec | None:
    direct = PROPOSITION_REGISTRY.get(proposition)
    if direct is not None and not direct.templated:
        return direct
    for template, spec in PROPOSITION_REGISTRY.items():
        if spec.templated and _match_template(proposition, template) is not None:
            return replace(spec, name=proposition, templated=False)
    return None


_SOCIAL_ENTAILS = (
    "exposure to an annotated stance moves the receiver toward it in proportion to source "
    "trust and inversely to own confidence. Consensus formation within trusting clusters "
    "and separation between distrusting ones follow analytically. Therefore no B1 claim "
    "may rest on this rule alone: every headline B1 effect must be reproduced under "
    "--social-influence-off, where belief change is LLM-authored only, and the effect size "
    "under that ablation is the reported result."
)
_BACKFIRE_ENTAILS = (
    "cross-cutting exposure from a distrusted source to a confidently-held opposing belief "
    "moves the receiver AWAY from the source. Any finding that cross-cutting exposure "
    "increases polarisation is therefore partly entailed. Ablate with --backfire-off; the "
    "sign and magnitude of the cross-cutting effect must be reported under both settings."
)


@mechanism(
    "belief_social_influence",
    entails=_SOCIAL_ENTAILS,
    config_key="mechanisms.belief_social_influence",
)
@mechanism(
    "belief_backfire",
    entails=_BACKFIRE_ENTAILS,
    config_key="mechanisms.belief_backfire",
)
def update_kernel(
    b: float,
    c: float,
    target: float,
    tau: float,
    alpha: float,
    cfg: BeliefSettings,
) -> tuple[float, float, bool]:
    d = abs(target - b)
    entrenched = (
        not cfg.backfire_off
        and d > cfg.theta_backfire
        and c > cfg.theta_entrench
        and tau < cfg.theta_trust
    )
    if entrenched:
        direction = 0.0 if target == b else math.copysign(1.0, target - b)
        delta_value = -cfg.beta_backfire * (1.0 - tau) * direction * min(d, 1.0)
        delta_confidence = cfg.delta_entrench
    else:
        delta_value = alpha * tau * (1.0 - c) * (target - b)
        delta_confidence = cfg.gamma_c * tau * (1.0 - d)
    return (
        max(-1.0, min(1.0, b + delta_value)),
        max(0.0, min(1.0, c + delta_confidence)),
        entrenched,
    )


class BeliefEngine:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        rng: RngRegistry,
        repo: BeliefRepository,
        graph: SocialGraph,
        cfg: SocietySettings | BeliefSettings,
        belief_cfg: BeliefSettings | None = None,
    ) -> None:
        self.log = log
        self.clock = clock
        self.rng = rng
        self.repo = repo
        self.graph = graph
        self.society_cfg = cfg if isinstance(cfg, SocietySettings) else SocietySettings()
        self.cfg = belief_cfg or (cfg if isinstance(cfg, BeliefSettings) else BeliefSettings())

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

    def _current(self, agent_id: str, proposition: str) -> tuple[PropositionSpec, Belief]:
        spec = _spec_for(proposition)
        if spec is None:
            raise ValueError(f"unknown proposition: {proposition}")
        row = self.repo.get(agent_id, proposition)
        if row is None:
            row = Belief(
                agent_id,
                proposition,
                spec.default_value,
                spec.default_confidence,
                "inherited",
                None,
                0,
            )
        return spec, row

    def value(self, agent_id: str, proposition: str) -> float:
        return self._current(agent_id, proposition)[1].value

    def confidence(self, agent_id: str, proposition: str) -> float:
        return self._current(agent_id, proposition)[1].confidence

    def population_mean(self, proposition: str) -> float:
        rows = self.repo.for_proposition(proposition)
        spec = _spec_for(proposition)
        if spec is None:
            raise ValueError(f"unknown proposition: {proposition}")
        return spec.default_value if not rows else sum(row.value for row in rows) / len(rows)

    def trust_in(self, agent_id: str, source_id: str, channel: Channel) -> float:
        if channel in {"experience", "inherited", "reflection"}:
            return 1.0
        if channel == "social":
            return (
                0.35
                if self.graph.tie(agent_id, source_id) is None
                else self.graph.trust(agent_id, source_id)
            )
        trust = self.value(agent_id, f"trust.outlet.{source_id}")
        fabricates = self.value(agent_id, f"fact.outlet.{source_id}.fabricates")
        return trust * (1.0 - fabricates)

    def _project(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        channel: Channel,
    ) -> tuple[float, float, bool]:
        spec, current = self._current(agent_id, proposition)
        if channel in {"social", "media"} and self.cfg.social_influence_off:
            return current.value, current.confidence, False
        alpha = self.cfg.alpha.get(channel, 1.0)
        new_value, new_confidence, entrenched = update_kernel(
            current.value,
            current.confidence,
            max(spec.lo, min(spec.hi, target)),
            self.trust_in(agent_id, source_id, channel),
            alpha,
            self.cfg,
        )
        return max(spec.lo, min(spec.hi, new_value)), new_confidence, entrenched

    def predict_delta(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        channel: Literal["social", "media"],
    ) -> float:
        old = self.value(agent_id, proposition)
        return self._project(agent_id, proposition, target, source_id, channel)[0] - old

    def apply(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        channel: Channel,
        source_id: str,
        tick: int,
        llm_call_id: str | None = None,
    ) -> Event | None:
        spec, current = self._current(agent_id, proposition)
        if channel == "experience" and spec.cls == "policy":
            raise AssertionError("direct experience may never update policy propositions")
        new_value, new_confidence, entrenched = self._project(
            agent_id, proposition, target, source_id, channel
        )
        if new_value == current.value and new_confidence == current.confidence:
            return None
        source_ref = (
            f"llm_call:{llm_call_id}" if channel == "reflection" and llm_call_id else source_id
        )
        self.repo.put(
            Belief(
                agent_id,
                proposition,
                new_value,
                new_confidence,
                channel,
                source_ref,
                tick,
            )
        )
        if channel in {"social", "media"}:
            return self._emit(
                BELIEF_DRIFT_APPLIED,
                {
                    "agent_id": agent_id,
                    "channel": channel,
                    "updates": [
                        {
                            "proposition": proposition,
                            "d_value": new_value - current.value,
                            "d_confidence": new_confidence - current.confidence,
                        }
                    ],
                    "n_sources": 1,
                },
                tick,
                subjects=(agent_id,),
            )
        return self._emit(
            BELIEF_UPDATED,
            {
                "agent_id": agent_id,
                "proposition": proposition,
                "old_value": current.value,
                "new_value": new_value,
                "old_confidence": current.confidence,
                "new_confidence": new_confidence,
                "channel": channel,
                "source_id": source_id,
                "source_ref": source_ref,
                "entrenched": entrenched,
                "llm_call_id": llm_call_id,
            },
            tick,
            actor_id=agent_id,
            subjects=(agent_id,),
        )

    def apply_social(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        source_id: str,
        tick: int,
    ) -> Event | None:
        return self.apply(agent_id, proposition, target, "social", source_id, tick)

    def apply_media(
        self,
        agent_id: str,
        proposition: str,
        target: float,
        outlet_id: str,
        tick: int,
    ) -> Event | None:
        return self.apply(agent_id, proposition, target, "media", outlet_id, tick)

    def apply_experience(
        self,
        agent_id: str,
        trigger_kind: int,
        payload: Mapping[str, Any],
        tick: int,
    ) -> Sequence[Event]:
        explicit = payload.get("proposition")
        if isinstance(explicit, str) and explicit.startswith("policy."):
            raise AssertionError("direct experience may never update policy propositions")
        updates: list[tuple[str, float, str]] = []
        if trigger_kind in {FIRED, BANKRUPTCY_FILED, PAYROLL_SHORTFALL}:
            firm_id = str(payload.get("firm_id") or payload.get("employer_id") or "")
            if firm_id:
                target = 0.0 if trigger_kind != PAYROLL_SHORTFALL else 0.15
                updates.append((f"fact.firm.{firm_id}.solvent", target, firm_id))
        if explicit is not None:
            proposition = str(explicit)
            spec = _spec_for(proposition)
            if spec is None:
                return ()
            if spec.cls == "policy":
                raise AssertionError("direct experience may never update policy propositions")
            updates.append(
                (proposition, float(payload.get("target", payload.get("value", 0.5))), "self")
            )
        events = [
            event
            for proposition, target, source in updates
            if (event := self.apply(agent_id, proposition, target, "experience", source, tick))
            is not None
        ]
        return tuple(events)

    def _reject(
        self,
        agent_id: str,
        update: BeliefUpdate,
        gate: str,
        tick: int,
        llm_call_id: str | None,
    ) -> Event:
        return self._emit(
            BELIEF_UPDATE_REJECTED,
            {
                "agent_id": agent_id,
                "proposition": update.proposition,
                "raw_value": update.value,
                "gate": gate,
                "llm_call_id": llm_call_id,
            },
            tick,
            actor_id=agent_id,
            subjects=(agent_id,),
        )

    def apply_llm_belief_updates(
        self,
        agent_id: str,
        tick: int,
        updates: Sequence[BeliefUpdate],
        llm_call_id: str | None,
    ) -> int:
        prepared: list[tuple[str, BeliefUpdate, PropositionSpec]] = []
        for update in updates:
            resolved = resolve_proposition(update.proposition, self.repo)
            if resolved is None:
                self._reject(agent_id, update, "unknown", tick, llm_call_id)
                continue
            spec = _spec_for(resolved)
            assert spec is not None
            value = update.value
            confidence = update.confidence
            if value < spec.lo or value > spec.hi:
                self._reject(agent_id, update, "range", tick, llm_call_id)
                value = max(spec.lo, min(spec.hi, value))
            if confidence < 0.0 or confidence > 1.0:
                self._reject(agent_id, update, "range", tick, llm_call_id)
                confidence = max(0.0, min(1.0, confidence))
            prepared.append(
                (
                    resolved,
                    replace(update, value=value, confidence=confidence),
                    spec,
                )
            )
        limited = prepared[: self.cfg.max_belief_updates_per_call]
        for _, update, _ in prepared[self.cfg.max_belief_updates_per_call :]:
            self._reject(agent_id, update, "count", tick, llm_call_id)
        applied = 0
        for proposition, update, _spec in sorted(limited, key=lambda row: row[0]):
            target = update.value
            confidence = update.confidence
            current = self._current(agent_id, proposition)[1]
            delta = target - current.value
            if abs(delta) > self.cfg.max_step:
                self._reject(agent_id, update, "step", tick, llm_call_id)
                target = current.value + math.copysign(self.cfg.max_step, delta)
            self_serving = (
                proposition.startswith("trust.outlet.")
                and update.source_ref is not None
                and update.source_ref.startswith("article:")
                and update.source_ref.split(":", 2)[1] == proposition.removeprefix("trust.outlet.")
            )
            if self_serving:
                self._reject(agent_id, update, "self_serving", tick, llm_call_id)
                target = current.value + 0.5 * (target - current.value)
                confidence = current.confidence + 0.5 * (confidence - current.confidence)
            source_ref = (
                f"llm_call:{llm_call_id}"
                if llm_call_id is not None
                else update.source_ref or "reflection"
            )
            self.repo.put(
                Belief(
                    agent_id,
                    proposition,
                    target,
                    confidence,
                    "reflection",
                    source_ref,
                    tick,
                )
            )
            event = self._emit(
                BELIEF_UPDATED,
                {
                    "agent_id": agent_id,
                    "proposition": proposition,
                    "old_value": current.value,
                    "new_value": target,
                    "old_confidence": current.confidence,
                    "new_confidence": confidence,
                    "channel": "reflection",
                    "source_id": update.source_ref or "reflection",
                    "source_ref": source_ref,
                    "entrenched": False,
                    "llm_call_id": llm_call_id,
                },
                tick,
                actor_id=agent_id,
                subjects=(agent_id,),
            )
            if event is not None:
                applied += 1
        return applied

    def _set_priors(
        self,
        agent_id: str,
        source: Literal["genesis", "birth", "migration"],
        rows: Sequence[tuple[str, float, float]],
        tick: int,
    ) -> Event:
        for proposition, value, confidence in rows:
            self.repo.put(
                Belief(
                    agent_id,
                    proposition,
                    value,
                    confidence,
                    "inherited",
                    source,
                    tick,
                )
            )
        return self._emit(
            BELIEF_PRIORS_SET,
            {
                "agent_id": agent_id,
                "source": source,
                "propositions": [
                    {"proposition": prop, "value": value, "confidence": confidence}
                    for prop, value, confidence in rows
                ],
            },
            tick,
            subjects=(agent_id,),
        )

    def initialise_population(self, agent_ids: Sequence[str], tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for agent_id in sorted(set(agent_ids)):
            rng = self.rng.numpy("beliefs.genesis", agent_id)
            rows = []
            for proposition in POLICY_PROPOSITIONS:
                side = -1.0 if rng.random() < 0.5 else 1.0
                mean = side * self.cfg.genesis.mixture_separation / 2.0
                value = float(max(-1.0, min(1.0, rng.normal(mean, self.cfg.genesis.sd))))
                rows.append((proposition, value, 0.35))
            rows.append(("trust.generalised", 0.5, 0.35))
            events.append(self._set_priors(agent_id, "genesis", rows, tick))
        return tuple(events)

    def priors_at_birth(
        self,
        child_id: str,
        mother_id: str,
        father_id: str,
    ) -> tuple[tuple[str, float, float], ...]:
        rng = self.rng.numpy("beliefs.noise", child_id)
        rows: list[tuple[str, float, float]] = []
        for proposition in (*POLICY_PROPOSITIONS, "trust.generalised"):
            spec = _spec_for(proposition)
            assert spec is not None
            midparent = (
                self.value(mother_id, proposition) + self.value(father_id, proposition)
            ) / 2.0
            population = self.population_mean(proposition)
            centre = (
                self.cfg.heritability_beliefs * midparent
                + (1.0 - self.cfg.heritability_beliefs) * population
            )
            value = max(
                spec.lo,
                min(spec.hi, centre + float(rng.normal(0.0, self.cfg.sigma_belief))),
            )
            parent_confidence = (
                self.confidence(mother_id, proposition) + self.confidence(father_id, proposition)
            ) / 2.0
            confidence = parent_confidence * self.cfg.confidence_dilution
            rows.append((proposition, value, confidence))
        return tuple(rows)

    def priors_for_migrant(
        self,
        agent_id: str,
        offsets: Mapping[str, float],
    ) -> tuple[tuple[str, float, float], ...]:
        del agent_id
        return (
            *(
                (
                    proposition,
                    max(
                        -1.0,
                        min(
                            1.0,
                            self.population_mean(proposition) + offsets.get(proposition, 0),
                        ),
                    ),
                    0.25,
                )
                for proposition in POLICY_PROPOSITIONS
            ),
            (
                "trust.generalised",
                max(0.0, min(1.0, 0.5 + offsets.get("trust.generalised", 0))),
                0.25,
            ),
        )


__all__ = [
    "POLICY_PROPOSITIONS",
    "PROPOSITION_REGISTRY",
    "Belief",
    "BeliefEngine",
    "BeliefRepository",
    "Channel",
    "EntityContext",
    "MemoryBeliefRepository",
    "PropClass",
    "PropositionSpec",
    "resolve_proposition",
    "update_kernel",
]
