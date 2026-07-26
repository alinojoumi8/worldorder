# C07 — Agent state, traits, needs, skills, reflex policy

**M1** · `polis/agents/` (`state.py`, `traits.py`, `needs.py`, `skills.py`, `reflex.py`, `perception.py`, `lifecycle.py`, `population.py`) · **Depends on** C01 C02 C03 C04 C06 · **Blocks** C08 C09 C10 C11 C16 C20 C21 C22 · **Size M**

---

## 1. Context

This chunk builds the citizen: the persistent identity that traits condition, needs pressure, skills gate, and the reflex policy drives when no LLM is available. It also builds `Observation` — the single data structure every downstream cognition chunk consumes — and the PHASE 1 perception builder that produces one for every agent, every tick, in under 80 µs each. Getting `Observation` right matters more than anything else here: C08, C09, C10, and the external-agent gateway all read it, and its hard caps are the project's primary defence against runaway token cost. The reflex action set is deliberately narrow (`04 §8`); if it ever widens to include negotiated or committed actions, the "LLM society" claim collapses, so the narrowness is enforced by assertion, not by convention.

## 2. Required reading

| Document | Sections | Why |
|---|---|---|
| `docs/02-ARCHITECTURE.md` | all | Binding. §4 determinism, §5 tick phases, §6 actions, §7.1 imports, §8.1 MECHANISM, §11 perf budget |
| `docs/03-DATA-MODEL.md` | §0, §2.1, §2.2, §12 | Binding. `agents`, `agent_skills`, projection rebuild |
| `docs/04-AGENT-SPEC.md` | **all, in full** | This chunk implements §2, §3, §4, §5, §8, §12.1, §12.2 |
| `docs/05-WORLD-SPEC.md` | §4.4, §4.5, §5.5, §6.2, §8.2 | Affordances, need restoration, transit perception, `world.reflex_destination` |
| Chunk interfaces consumed | C01 config, C02 events, C03 repos, C04 rng/clock/det, **C06 `World`, `PlaceView`, `ColocationContext`** | |

## 3. Scope — in

1. **`AgentState`** — the in-memory record and its projection to/from the `agents` and `agent_skills` rows.
2. **Traits** — the ten dimensions of `04 §2`, generated from a correlated multivariate normal at genesis, and the birth inheritance formula of `04 §2.1`.
3. **Needs** — the six drives of `04 §4`, per-tick decay derived from per-sim-day rates, restoration hooks consumed by C06's `SLEEP`/`EAT`/`IDLE` outcomes, and the zero-consequence path into health.
4. **Skills** — the closed 14-skill vector, growth (school and work), decay, learning rate from conscientiousness, and the age curve.
5. **`ReflexProfile`** — a pure deterministic function of traits, derived once at birth.
6. **Reflex policy** — legal-action enumeration over the narrow set, the utility function, softmax sampling; and the implementation of `world.reflex_destination`.
7. **`Observation`** and every sub-view except `PlaceView` (C06's), plus the PHASE 1 perception builder and kind `4001 PERCEPTION_BUILT` under the `02 §3.3` sampling policy.
8. **Lifecycle stages** — infant / child / adolescent / adult / elder, their constraints, ageing under `demographic_acceleration`.
9. **Population initialisation** — sampling an age pyramid, assigning households, homes, education levels, and starting skills.
10. **A minimal M1 death path** — `2002 AGENT_DIED` with vitals-driven cause, position vacated, archival hook fired. C20 supersedes it at M5 with full estate settlement.
11. Event kinds **2000–2999**, plus **4001** only.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| Memory stream, retrieval, embeddings, reflection | C08 |
| Salience scoring, cognition routing, deliberate and reflect modes | C09 |
| **`digest_features()` / `digest_hash()`** — the feature vocabulary itself. C09 owns the single implementation in `polis/agents/cognition/digest.py`; C07 calls it through an injected `digest_fn` | C09 |
| Mortality hazard, fertility, **full** death settlement, household formation | C20 (C07's M1 death path is a stub, §9.11) |
| Action schema registry, the five validation gates, action-slot budget, resolution dispatch | C10 |
| Beliefs table and belief updates (kinds 10060–10069) | C08/C17 |
| Movement resolution, co-location computation, affordances, `PlaceView` | C06 |
| Wage, employment, occupation intensity vectors, the *definition of a skill being "used"* | C11 |
| Ledger accounts and opening balances | C11/C14 |
| Feed, inbox, news, market, employer, offer slices — you define their **types** and consume precomputed maps; you do not produce them | C11–C17 |

You define `Observation`. You do not populate the slices that belong to unbuilt chunks; in M1 they arrive empty and every consumer must tolerate that.

## 5. Interfaces you provide

```python
# polis/agents/state.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Final, Literal, Mapping, Sequence

Trait = Literal["openness","conscientiousness","extraversion","agreeableness","neuroticism",
                "risk_tolerance","time_preference","altruism","ambition","honesty"]
Skill = Literal["manual","operations","sales","finance","engineering","research","law",
                "medicine","teaching","writing","design","management","negotiation","persuasion"]
Need  = Literal["energy","hunger","security","social","esteem","purpose"]
LifeStage = Literal["infant","child","adolescent","adult","elder"]
EducationLevel = Literal["none","primary","secondary","tertiary","graduate"]
EmploymentStatus = Literal["child","student","employed","unemployed","self_employed",
                           "retired","dead"]

TRAITS: Final[tuple[Trait, ...]]      # canonical order — never re-order, it is hashed
SKILLS: Final[tuple[Skill, ...]]
NEEDS:  Final[tuple[Need, ...]]

@dataclass(frozen=True, slots=True)
class TraitVector:
    values: tuple[float, ...]                        # len 10, [0,1], aligned to TRAITS
    def __getitem__(self, t: Trait) -> float: ...
    def as_mapping(self) -> Mapping[Trait, float]: ...

@dataclass(slots=True)
class AgentState:
    agent_id: str
    display_name: str
    kind: Literal["native","external"]
    born_at_tick: int
    died_at_tick: int | None
    death_cause: str | None
    age_years: float
    household_id: str | None
    mother_id: str | None
    father_id: str | None
    generation: int
    traits: TraitVector
    needs: dict[Need, float]                         # mutable, [0,1]
    health: float
    skills: dict[Skill, float]                       # [0,1]
    skill_last_used_tick: dict[Skill, int]
    education_level: EducationLevel
    employment_status: EmploymentStatus
    employer_id: str | None
    occupation: str | None
    ledger_account_id: str
    wealth_cents: int
    reputation: float
    criminal_record: int
    reflex_profile: ReflexProfile
    goals: tuple[str, ...]
    identity_summary: str                            # written by C09's REFLECT; read-only here

    @property
    def alive(self) -> bool: ...
    @property
    def stage(self) -> LifeStage: ...
    def skill_bp(self, s: Skill) -> int: ...          # round(level * 10_000); economy reads this

def to_row(a: AgentState) -> Mapping[str, object]: ...        # -> agents row, floats at 6 dp
def from_row(row: Mapping[str, object],
             skills: Sequence[Mapping[str, object]]) -> AgentState: ...
```

```python
# polis/agents/traits.py
def generate_traits(agent_id: str, cfg: TraitConfig, rng: RngRegistry) -> TraitVector:
    """MVN via per-agent Cholesky. rng.get('agent.traits', agent_id) -> numpy default_rng."""
def inherit_traits(mother: TraitVector, father: TraitVector, pop_mean: TraitVector,
                   child_id: str, cfg: TraitConfig, rng: RngRegistry) -> TraitVector:
    """04 §2.1: clip(0.5*(m+f)*h + (1-h)*pop_mean + N(0, sigma_trait), 0, 1)."""
def validate_correlation_matrix(m: Sequence[Sequence[float]]) -> None:
    """Symmetric, unit diagonal, positive-definite. Raises ConfigError at load time."""
def trait_narrative(t: TraitVector) -> str:
    """Prose rendering for prompts. NEVER emits a number. 04 §9.1."""
```

```python
# polis/agents/needs.py
def decay_needs(a: AgentState, ticks_per_sim_day: int, cfg: NeedConfig) -> None: ...
def restore(a: AgentState, need: Need, amount: float) -> float:
    """Clamps to [0,1]; returns the applied delta."""
def health_penalty(a: AgentState, cfg: NeedConfig) -> float:
    """Aggregate per-tick health delta implied by needs at or near zero. <= 0."""
def critical_needs(a: AgentState, cfg: NeedConfig) -> tuple[Need, ...]: ...

# polis/agents/skills.py
def learning_rate(a: AgentState, cfg: SkillConfig) -> float:
    """(0.5 + 0.5*conscientiousness) * age_curve(age_years). 04 §3."""
def age_curve(age_years: float, cfg: SkillConfig) -> float: ...
def apply_skill_growth(a: AgentState, weights: Mapping[Skill, float], scale: float,
                       quality: float, tick: int) -> dict[Skill, float]:
    """Δ_s = quality * weights[s] * scale * learning_rate * (1 - level_s). Returns deltas.
    Mutates `a`. Emits NOTHING — the caller emits in its own kind range."""
def apply_skill_decay(a: AgentState, used: frozenset[Skill], tick: int,
                      cfg: SkillConfig) -> dict[Skill, float]:
    """Δ = -decay_rate * level per sim-month for skills not in `used`. Returns deltas."""
```

```python
# polis/agents/reflex.py
REFLEX_ACTION_TYPES: Final[frozenset[ActionType]]   # exactly the 10 of 04 §8

@dataclass(frozen=True, slots=True)
class ReflexProfile:
    temperature: float
    need_weights: Mapping[Need, float]
    obligation_weight: float
    move_cost_weight: float
    thrift: float                       # resistance to BUY_GOOD, from time_preference
    sociality: float                    # SAY propensity, from extraversion
    diligence: float                    # WORK/STUDY propensity, from conscientiousness

def derive_reflex_profile(t: TraitVector, cfg: ReflexConfig) -> ReflexProfile: ...
def legal_reflex_actions(obs: Observation, world: World) -> tuple[Action, ...]:
    """Candidate set, already filtered by affords(), stage, and REFLEX_ACTION_TYPES."""
def utility(candidate: Action, obs: Observation, p: ReflexProfile) -> float: ...
def reflex_decide(obs: Observation, p: ReflexProfile, world: World,
                  rng: RngRegistry) -> Action:
    """Softmax over `utility`, temperature=p.temperature,
    rng.get('agent.reflex', obs.self_state.agent_id, obs.tick)."""

@mechanism("world.reflex_destination", entails="...")   # entails string from 05 §8.2
def reflex_destination(obs: Observation, p: ReflexProfile, world: World,
                       rng: RngRegistry) -> str | None: ...
```

```python
# polis/agents/perception.py — PHASE 1
@dataclass(frozen=True, slots=True)
class SelfView:
    agent_id: str; display_name: str; age_years: float; stage: LifeStage
    needs: Mapping[Need, float]; health: float
    wealth_cents: int; employment_status: EmploymentStatus
    employer_id: str | None; occupation: str | None; education_level: EducationLevel
    skills: Mapping[Skill, float]; goals: tuple[str, ...]
    identity_summary: str; household_id: str | None; home_place_id: str | None
    reputation: float; criminal_record: int
    last_action_rejected: tuple[str, str] | None      # (action_type, reason), 04 §11

@dataclass(frozen=True, slots=True)
class AgentBrief:
    agent_id: str; display_name: str; age_years: int
    relationship: str | None; tie_strength: float
    occupation: str | None; is_novel: bool

@dataclass(frozen=True, slots=True)
class MessageBrief:
    message_id: str; from_id: str; tick: int; text: str

@dataclass(frozen=True, slots=True)
class PostBrief:
    post_id: str; author_id: str; tick: int; text: str
    topic: str | None; likes: int; is_repost: bool

@dataclass(frozen=True, slots=True)
class ArticleBrief:
    article_id: str; outlet_id: str; headline: str; tick: int

@dataclass(frozen=True, slots=True)
class MarketView:
    quotes: tuple[tuple[str, int, int], ...]          # (symbol, bid_cents, ask_cents)
    holdings: tuple[tuple[str, int], ...]

@dataclass(frozen=True, slots=True)
class EmployerView:
    firm_id: str; name: str; place_id: str
    my_wage_cents: int; headcount: int; status: str
    colleagues_present: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class OfferBrief:
    offer_id: str; type: str; from_id: str; summary: str
    expires_tick: int | None; amount_cents: int | None

@dataclass(frozen=True, slots=True)
class Obligation:
    obligation_id: str; type: str
    due_tick: int; place_id: str | None
    amount_cents: int | None
    mandatory: bool                                   # force-routes to DELIBERATE in C09

@dataclass(frozen=True, slots=True)
class Observation:
    tick: int
    sim_time: datetime
    self_state: SelfView
    place: PlaceView                                  # from polis.world.types
    co_located: tuple[AgentBrief, ...]                # cap 12, ranked by C06
    inbox: tuple[MessageBrief, ...]                   # cap 10
    feed: tuple[PostBrief, ...]                       # cap 15
    news: tuple[ArticleBrief, ...]                    # cap 3
    market: MarketView | None
    employer: EmployerView | None
    offers: tuple[OfferBrief, ...]                    # cap 8
    obligations: tuple[Obligation, ...]               # cap 8
    digest_hash: str

@dataclass(frozen=True, slots=True)
class PerceptionSources:
    """Precomputed ONCE per tick for the whole population by the composition root.
    Every field is agent_id -> already-capped, already-ranked slice."""
    inbox: Mapping[str, tuple[MessageBrief, ...]]
    feed: Mapping[str, tuple[PostBrief, ...]]
    news: tuple[ArticleBrief, ...]                    # shared; sliced per agent by reach
    market: Mapping[str, MarketView]
    employer: Mapping[str, EmployerView]
    offers: Mapping[str, tuple[OfferBrief, ...]]
    obligations: Mapping[str, tuple[Obligation, ...]]
    colocation_ctx: Mapping[str, ColocationContext]
    rejections: Mapping[str, tuple[str, str]]

DigestFn = Callable[["Observation"], str]      # C09's digest_hash(digest_features(obs))

def build_observation(a: AgentState, world: World, src: PerceptionSources, clock: Clock,
                      cfg: PerceptionConfig, digest_fn: DigestFn) -> Observation:
    """Pure. Reads only last tick's committed state. Target < 80 µs.
    `digest_fn` is C09's; C07 ships `fallback_digest` only so M1 can run before C09 lands."""
def build_all(agents: Sequence[AgentState], world: World, src: PerceptionSources,
              clock: Clock, cfg: PerceptionConfig,
              digest_fn: DigestFn) -> dict[str, Observation]: ...
def fallback_digest(obs: Observation) -> str:
    """Placeholder ONLY. C09 replaces it; two live implementations is a bug (C09 §9.1)."""
```

```python
# polis/agents/lifecycle.py
def stage_for_age(age_years: float) -> LifeStage: ...
def advance_age(a: AgentState, clock: Clock, demographic_acceleration: float) -> bool:
    """Returns True if the stage changed (caller emits 2004)."""
def allowed_action_types(stage: LifeStage) -> frozenset[ActionType]: ...
def mark_dead(a: AgentState, cause: str, tick: int, world: World) -> list[Event]:
    """M1 stub of 04 §12.3: sets died_at_tick/death_cause/employment_status='dead',
    vacates the place, emits 2002 AGENT_DIED. Performs NO estate settlement.
    C20 replaces this wholesale at M5; C08 archives memories on 2002."""

# polis/agents/population.py
def initialise_population(cfg: PopulationConfig, world: World, clock: Clock,
                          rng: RngRegistry) -> tuple[list[AgentState], list[Event]]: ...
def sample_age(index: int, pyramid: AgePyramid, rng: RngRegistry) -> float: ...
def population_mean_traits(agents: Sequence[AgentState]) -> TraitVector: ...
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C01 | `PopulationConfig`, `TraitConfig`, `NeedConfig`, `SkillConfig`, `ReflexConfig`, `PerceptionConfig` | §10 |
| C02 | `Event`, `register_kind` | your kinds, 2000–2999 |
| C03 | `AgentRepository`, `AgentSkillRepository` | batched writes in PHASE 6 only |
| C04 | `RngRegistry.get`, `Clock` (`tick`, `sim_time`, `sim_day`, `ticks_per_sim_day`, `profile`), `stable`, `@mechanism` | |
| C06 | `World`, `PlaceView`, `ColocationContext`, `Location`, `Place` | `place_view`, `co_located`, `travel_ticks`, `affords`, `places_of_type`, `find_home` |

`agents → kernel, events, world, llm, store, config`. You may **not** import `polis.economy` or `polis.society`.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `agents` | write every column **except** the movement five (C06) and `died_at_tick`/`death_cause` (C20) | `traits`, `needs`, `reflex_profile`, `goals` are JSONB; floats at 6 dp |
| `agent_skills` | read/write | one row per `(agent_id, skill)`, 14 per agent; `level` is `NUMERIC(6,4)` in [0,1] |
| `households` | read `member_ids`, `home_place_id` | C20 writes; C06 writes tenure/rent |
| `ledger_accounts` | **not written** | `ledger_account_id = f"acc_{agent_id}"` is minted here; the row is created by C11/C14 at M2. `wealth_cents = 0` in M1 |

**Two spec notes to flag on handback.**
(a) `03 §2.2` stores skill `level` as a float in `[0,1]`; `06 §3.5` reads `agent.skill_bp` in basis points. Both are satisfied by storing the float and exposing `skill_bp()` as `round(level * 10_000)`. Round at the boundary, once.
(b) `AgentState.identity_summary` has **no column** in `03 §2.1` and none is added. It lives on the in-memory state; its durable record is C09's event `4007 IDENTITY_SUMMARY_SET`, and rebuild restores it by replaying 4007. `to_row` must not emit it.

## 8. Event kinds owned

Range **2000–2999**, plus **4001** only (C09 owns 4002–4009, C08 owns 4010–4029, C05 owns 4100–4199). Register in `polis/events/kinds.py`.

| Kind | Name | Payload |
|---|---|---|
| 2001 | `AGENT_BORN` | `agent_id, household_id, mother_id, father_id, generation, traits, inherited_belief_priors, home_place_id, heritability` |
| 2002 | `AGENT_DIED` | `agent_id, cause, age_years, estate_value_cents` — **minimal M1 emission** (§9.11); C20 rewrites the settlement at M5. C08 archives memories on this kind |
| 2003 | `POPULATION_INITIALISED` | `count, pyramid_id, trait_model, world_hash, seed, stage_counts, mean_age_years` |
| 2004 | `AGENT_STAGE_CHANGED` | `agent_id, from_stage, to_stage, age_years` |
| 2010 | `AGENT_HEALTH_CHANGED` | `agent_id, delta, new_health, cause` |
| 2011 | `NEED_CRITICAL` | `agent_id, need, value, consecutive_ticks` |
| 2020 | `SKILL_LEVEL_CHANGED` | `agent_id, skill, from_level, to_level, source ∈ init·birth·school·work·decay` — **emitted here only for `init`/`birth`**; C21 and C11 emit their own kinds for school/work/decay |
| 2030 | `REFLEX_PROFILE_DERIVED` | `agent_id, temperature, need_weights, thrift, sociality, diligence` |
| 2040 | `REFLEX_ACTION_CHOSEN` | `agent_id, action_type, utility, n_candidates, temperature` — sampled at `cognition_sample_rate` |
| 2050 | `EDUCATION_LEVEL_CHANGED` | reserved for C21 — do not implement |
| 2900 | `AGENT_INVARIANT_WARNING` | `invariant_id, agent_id, expected, actual` |
| **4001** | `PERCEPTION_BUILT` | `agent_id, digest_hash, co_located_count, inbox_count, feed_count, obligations_count, in_transit` — written under the `02 §3.3` sampling policy: always when the agent was routed to DELIBERATE or REFLECT (C09 tells you next tick), otherwise at `cognition_sample_rate` |

## 9. Implementation notes

**9.1 Trait generation.** One `numpy.random.default_rng` **per agent**, seeded from `rng.get("agent.traits", agent_id)`, drawing exactly 10 standard normals. Never one shared generator for the whole population: a per-agent stream means agent *k*'s traits do not depend on how many agents were generated before it, which is what makes immigration, birth, and partial regeneration replayable. Cholesky-factorise the configured correlation matrix **once at config load** (and validate positive-definiteness there, loudly). Then `x = mean + (L @ z) * sd`, `clip(x, 0, 1)`.

**9.2 Clipping, not rejection.** `04 §2.1` says *clipped*. Clip. Do **not** resample until the draw lands in range: a rejection sampler consumes a seed-dependent number of draws and destroys stream alignment between runs that differ in any unrelated parameter. Clipping compresses the tails and slightly deflates the realised correlations — measure that once and report it as `population.trait_corr_realised`; do not "fix" it.

**9.3 Needs.** `04 §4` gives decay per **sim-day**. Per-tick decay is `rate / clock.ticks_per_sim_day`. Never write `0.0417` or any constant that assumes 24 ticks/day — `chronicle` runs at 1. Restoration amounts come from `05 §4.5` and are supplied by C06's resolution of `SLEEP`/`EAT`/`IDLE`; C07 provides `restore()` and clamps. Needs at zero produce a health delta (2010) and, for `hunger` and `energy`, an action-failure probability that C10 consumes; death is C20's.

**9.4 Skills.** Growth is applied **per sim-day**, not per tick, by whichever chunk owns the activity (C21 school, C11 work). C07 owns only the arithmetic. `learning_rate = (0.5 + 0.5 * conscientiousness) * age_curve(age)`. The spec does not define the age curve, so this chunk defines it and tags it:

```python
@mechanism("agent.skill_age_curve", entails=(
    "Learning rate rises to a peak at 18 and declines linearly after 35 to a floor of 0.25. "
    "Therefore later-life retraining is mechanically less effective than early schooling, and "
    "any finding that early education dominates lifetime skill attainment is partly implied "
    "by this curve. Ablation --mechanism-off agent.skill_age_curve pins it at 1.0."))
def age_curve(age_years: float, cfg: SkillConfig) -> float:
    if age_years <= 18.0:  return 0.60 + 0.40 * (age_years / 18.0)
    if age_years <= 35.0:  return 1.00
    return max(0.25, 1.00 - 0.02 * (age_years - 35.0))
```

**9.5 `ReflexProfile` derivation.** Deterministic, no learning in v1, no RNG:

| Field | From |
|---|---|
| `temperature` | `0.20 + 0.50 * (1 - conscientiousness)` |
| `need_weights[energy/hunger]` | `1.0` (fixed; survival is not a personality trait) |
| `need_weights[social]` | `0.4 + 0.8 * extraversion` |
| `need_weights[esteem]` | `0.3 + 0.9 * ambition` |
| `need_weights[security]` | `0.3 + 0.9 * neuroticism` |
| `need_weights[purpose]` | `0.2 + 0.8 * openness` |
| `obligation_weight` | `0.5 + 1.5 * conscientiousness` |
| `move_cost_weight` | `0.3 + 0.7 * (1 - openness)` |
| `thrift` | `1 - time_preference` |
| `sociality` | `extraversion` |
| `diligence` | `conscientiousness` |

**9.6 Reflex decision.** Enumerate candidates, score, then: round every utility to 6 dp; sort by `(-utility, action_type.value, canonical_param_json)`; subtract the max; exponentiate at `temperature`; take one `rng.get("agent.reflex", agent_id, tick).random()` draw against the cumulative. One draw, one sorted list, no dict iteration. Assert `action.type in REFLEX_ACTION_TYPES` before returning — this assertion is the enforcement of `04 §8` and it must never be relaxed "for the reflex-only baseline".

**9.7 Perception cost.** The 80 ms PHASE 1 budget over 1,000 agents is 80 µs each, which is roughly one dict lookup per slice and one sort of ≤ 12 items. Therefore: **no database access inside `build_observation`**, no per-agent query, no re-ranking of anything C06 already ranked. `PerceptionSources` is built once per tick for the whole population by the composition root; `build_all` is a loop over `stable(agents, key=agent_id)` that can go into a process pool if profiling demands it (pure, returns values). Cap **before** you sort where the source is unbounded; C06's `co_located` already returns ≤ cap.

**9.8 In-transit perception.** `05 §5.5`: `place` is C06's transit `PlaceView`, `co_located` is **empty**, every other slice is unchanged (phones exist). The legal-action set excludes `SAY`. Infants get a full `Observation` — C09 floors their salience, but a missing observation crashes it.

**9.9 Digest features — C09's function, C07's constraint.** C09 owns the one implementation (`polis/agents/cognition/digest.py`); C07 injects it as `digest_fn` and ships `fallback_digest` only so M1 can run before C09 exists. Whichever implementation is live, the contract C07 depends on is: ordinal tokens only, so the jaccard is stable — `place:office`, `district:d3`, `hour:morning`, `emp:employed`, `need:hunger:low`, `wealth:q3`, `colo:ag_000123`, `obl:rent_due`, `rej:resources` — every continuous value bucketed (needs into 4 bins, wealth into quintiles), ≤ 64 tokens. Never a raw float: the surprise term would then be 1.0 every tick, every agent would be force-routed to DELIBERATE, and the LLM budget would be gone on tick 1. Delete `fallback_digest` the moment C09 lands; two live implementations is precisely the divergence C09 §9.1 warns about.

**9.10 Coordination items with C08/C09 (raise these on handback).**

| Item | C07 position | Peer brief |
|---|---|---|
| `reflex_decide` arity | `(obs, profile, world, rng)` — `world` is required by `world.reflex_destination` | C09 §6 lists the 3-arg form of `04 §8`'s pseudocode; C09 must adapt |
| `digest_features` owner | C09, injected here | C09 §9.1 agrees |
| `4001 PERCEPTION_BUILT` | emitted here | C08 §8 agrees ("C07 owns 4001") |
| `2002 AGENT_DIED` at M1 | emitted here as a stub | C08 §9 assumes it ("called by C07 at M1 and C20 from M5") |
| `identity_summary` storage | in-memory field, durable via C09's 4007, no DDL | C09 §7 agrees |

**9.11 The M1 death path.** M1 has no C20 and no ledger, but C08 archives memories on `2002` and a city where nobody dies is not a city. `mark_dead` therefore does exactly four things and no more: set `died_at_tick`, `death_cause`, `employment_status = 'dead'`; vacate the current place via C06; emit `2002 AGENT_DIED` with `estate_value_cents = wealth_cents`; and nothing else. **No estate settlement, no debt write-off, no heir distribution, no household restructuring** — those are C20's and they are where `INV-MONEY` breaks (`04 §12.3`). The M1 trigger is vitals only: `health <= 0` after the needs penalty. Do not implement the Gompertz–Makeham hazard here; it is C20's `MECHANISM` and implementing it twice guarantees two different curves.

**9.12 Population initialisation.** Inverse-CDF sample from the configured pyramid using `rng.get("population.init", f"{index:06d}")`. Then, in this order and all seeded per agent: traits → `ReflexProfile` → education level from age (`< 6` none, `6–11` primary-in-progress, `12–17` secondary-in-progress, `18+` drawn from a configured attainment distribution) → skills seeded from `(education_level, age)` with noise → household assignment → `World.find_home` → `current_place_id = home_place_id`. Emit one 2001 per agent plus one 2003 summary. The genesis population is emitted as `AGENT_BORN` with `mother_id = father_id = None, generation = 0`.

## 10. Configuration keys

```yaml
population:
  initial_agents: 1000
  age_distribution: pyramid_ca_2020          # configs/pyramids/*.yaml
  trait_model: big_five_plus_econ
  attainment_distribution: {none: 0.04, primary: 0.10, secondary: 0.52,
                            tertiary: 0.28, graduate: 0.06}

agents:
  traits:
    means: {openness: 0.50, ..., honesty: 0.55}      # 10 keys
    sd: 0.15
    correlation: big_five_plus_econ                   # named matrix; must be PSD
    heritability: 0.40                                # MECHANISM trait_heritability
    sigma_birth: 0.08
  needs:
    decay_per_sim_day: {energy: 1.0, hunger: 1.0, security: 0.05,
                        social: 0.30, esteem: 0.10, purpose: 0.05}
    health_penalty_per_tick_at_zero: 0.004
    critical_threshold: 0.10
  skills:
    decay_rate: 0.004                                 # per sim-month unused
    age_curve_floor: 0.25
  reflex:
    temperature_base: 0.20
    temperature_span: 0.50
  perception:
    caps: {co_located: 12, inbox: 10, feed: 15, news: 3, offers: 8, obligations: 8}
    digest_max_features: 64

mechanisms:
  trait_heritability: midparent_blend                 # 04 §2.1
  need_decay: constant_per_sim_day                    # 04 §4; ablation --needs-off
  agent.skill_age_curve: peak_18_decay_35             # §9.4
  world.reflex_destination: obligation_then_need      # implemented here, declared in 05 §15
```

`agents.perception.caps.co_located` must equal `world.colocation.cap`; validate at config load and fail loudly if they diverge.

## 11. Acceptance criteria

- [ ] Traits for a given `(seed, agent_id)` are identical regardless of how many agents were generated before that agent, and regardless of population size.
- [ ] The realised trait correlation matrix over 10,000 generated agents is within 0.05 of the configured matrix on every off-diagonal entry, after clipping.
- [ ] A non-positive-definite correlation matrix raises at config load, not at generation time.
- [ ] `inherit_traits` with `heritability = 1.0` reproduces the midparent mean up to `sigma_birth`; with `heritability = 0.0` it reproduces the population mean.
- [ ] Need decay over one sim-day is identical in `microscope` and `chronicle` for the same starting state.
- [ ] `apply_skill_growth` never pushes a level above 1.0 or below 0.0; `apply_skill_decay` never touches a skill in `used`.
- [ ] `derive_reflex_profile` is a pure function: same traits → identical profile, no RNG consumed.
- [ ] `reflex_decide` returns only members of `REFLEX_ACTION_TYPES`, for 10,000 random states across all five life stages.
- [ ] `reflex_decide` is deterministic given `(state, tick, seed)` and consumes exactly one RNG draw.
- [ ] `build_observation` performs zero database queries and zero `World` calls that are not O(1) lookups.
- [ ] `build_all` over 1,000 agents completes in < 80 ms p50 on the reference machine.
- [ ] Every capped list is at or under its cap for every agent over a 500-tick run.
- [ ] An in-transit agent's `co_located` is empty and `SAY` is absent from its legal set.
- [ ] `build_observation` calls the injected `digest_fn` exactly once and stores its result verbatim; swapping `fallback_digest` for C09's implementation changes no other behaviour.
- [ ] `4001 PERCEPTION_BUILT` is written for every DELIBERATE/REFLECT agent and for `cognition_sample_rate` of the rest, and never carries prompt text.
- [ ] `mark_dead` sets the three death fields, vacates the place, emits exactly one `2002`, and posts no ledger legs and no estate transfer.
- [ ] `initialise_population` with `initial_agents: 1000` produces 1,000 agents whose age histogram matches the pyramid within 3 percentage points per decade band, all homed or explicitly recorded as unhomed.
- [ ] `to_row(from_row(r)) == r` for 1,000 randomly generated states (round-trip, floats at 6 dp).
- [ ] `polis rebuild` reproduces `agents` and `agent_skills` byte-identically from the log over 500 ticks.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/agents/test_traits.py` | per-agent stream independence; correlation recovery; clipping (not resampling); PSD validation raises at load; `trait_narrative` contains no digit |
| `tests/unit/agents/test_inheritance.py` | `h=0`, `h=1`, `h=0.4` cases; clipping at the bounds; determinism on `child_id` |
| `tests/unit/agents/test_needs.py` | per-sim-day equivalence across clock profiles; clamping; `health_penalty` sign; `NEED_CRITICAL` hysteresis |
| `tests/unit/agents/test_skills.py` | growth formula against hand arithmetic; decay skips `used`; `age_curve` shape and its ablation; `skill_bp` rounding at 0.00005 boundaries |
| `tests/unit/agents/test_reflex_profile.py` | purity, no RNG consumed, monotonicity in the driving trait for each field |
| `tests/unit/agents/test_reflex_policy.py` | closed action set assertion; one RNG draw; permutation-invariance of the candidate list; hungry agent eats, tired agent sleeps, employed agent in work hours commutes; `--reflex-only` produces a non-degenerate action-type histogram (V4 floor) |
| `tests/unit/agents/test_observation.py` | every cap; in-transit shape; infant shape; empty M1 slices tolerated; frozen/slots enforced; no `PlaceView` construction outside C06 |
| `tests/unit/agents/test_digest_injection.py` | `digest_fn` called once per observation, result stored verbatim; `fallback_digest` satisfies the §9.9 contract (≤ 64 tokens, no float, jaccard ≈ 1.0 for two identical days, < 0.6 after a job loss) so C09 can drop in against the same assertions |
| `tests/unit/agents/test_death_stub.py` | `mark_dead` field writes, place vacated, one `2002`, no ledger call (assert via a forbidden-import spy), `health <= 0` is the only M1 trigger |
| `tests/unit/agents/test_lifecycle.py` | stage boundaries at 6/12/18/65; `demographic_acceleration` scaling; `AGENT_STAGE_CHANGED` fires exactly once per boundary |
| `tests/unit/agents/test_population_init.py` | pyramid match; household and home assignment; education/skill seeding by age; 2003 payload |
| `tests/unit/agents/test_state_roundtrip.py` | `to_row`/`from_row` round-trip incl. JSONB fields and 6 dp rounding |
| `tests/determinism/test_agent_core_determinism.py` | same seed, 200 ticks, StubProvider → identical event-log hash chain, twice |
| `tests/integration/test_perception_budget.py` | 1,000 agents, p50 < 80 ms, zero DB queries inside the loop (assert via a counting cursor wrapper) |

## 13. Definition of done

All of `chunks/README.md §5`. Specifically: acceptance criteria met; `pytest` green including §12; `mypy --strict polis/agents` clean (excluding `memory/`, `cognition/`, `actions/`, which are C08–C10); `ruff` clean; `import-linter` shows no `polis.economy` or `polis.society` import; determinism test passes twice; config keys in the pydantic schema; kinds 2001, 2002, 2003, 2004, 2010, 2011, 2020, 2030, 2040, 2900 and 4001 registered with payload schemas, and 2050 reserved with a comment naming its owner. Write down: the `skill_bp` float/bp boundary decision, the `identity_summary` no-column decision, the `agent.skill_age_curve` mechanism you had to invent, the `PerceptionSources` precomputation contract, the five coordination items in §9.10, and any `04` text you could not implement as written.

## 14. Traps

1. **One shared numpy generator for the whole population.** It works, it is deterministic, and it is wrong: agent 500's traits then depend on the fact that 499 agents were drawn first. Change `initial_agents` and every agent's identity shifts; add an immigrant mid-run and you cannot reproduce it. Per-agent streams, always.
2. **Resampling instead of clipping.** The obvious "fix" for out-of-range MVN draws is a rejection loop. It consumes a variable number of draws, so a run that differs only in an unrelated parameter now has a different trait realisation for every agent, and the sweep comparison you were building is worthless.
3. **A correlation matrix that is not positive-definite.** Hand-authored Big-Five matrices frequently are not. `numpy.linalg.cholesky` will raise — at tick 0 of a long run, or worse, in a sweep cell at 3 a.m. Validate at config load, with the offending eigenvalue in the error.
4. **Hard-coded 24 ticks/day.** Needs decay, skill decay, and age advance are all per-sim-day quantities. A literal `/ 24` makes `chronicle` agents starve 24× too slowly and silently invalidates every cross-profile comparison. Divide by `clock.ticks_per_sim_day`.
5. **Ageing by tick count.** `age_years += 1/8640` ignores `demographic_acceleration`, which is the whole point of the parameter. Go through `Clock` and the config.
6. **A database query inside `build_observation`.** One `SELECT` per agent is 1,000 round-trips per tick — roughly 100× the entire PHASE 1 budget. It will not show up in unit tests and will make the engine run at 0.1 ticks/s. The counting-cursor test in §12 exists precisely to catch it.
7. **Building `Observation` from current-tick state.** Perception must read the snapshot C06 froze last tick. Reading live state lets agents see each other's same-tick moves, which produces what looks like emergent coordination and is a bug.
8. **Sorting before capping.** Sorting 400 co-located agents for each of 1,000 agents is 400k comparisons per tick. C06 already returns ≤ 12 ranked; do not re-rank. For your own slices, cap at the source.
9. **Raw floats in `digest_features`.** `wealth:41823.7` differs every tick, jaccard collapses to ~0, surprise saturates at 1.0, every agent looks maximally surprised, and the LLM budget is exhausted on the first tick. Bucket everything.
10. **Softmax over an unsorted candidate list.** Two runs enumerate candidates in different dict order, the cumulative sum is built differently, floating-point association differs, and one run in fifty picks a different action. This is the classic "determinism test passes 49 times" bug. Sort, round to 6 dp, then exponentiate.
11. **Widening the reflex action set.** Someone will want `APPLY_FOR_JOB` in reflex so the `--reflex-only` baseline "does something". That baseline is supposed to be impoverished — it is the control that shows what the LLM contributes. The assertion in `reflex_decide` is load-bearing.
12. **Mutable defaults and `frozen=True, slots=True`.** A `Mapping` field defaulting to `{}` gives every instance the same dict. With `slots=True` you also lose `__dict__`, so any code that does `obs.__dict__` or `dataclasses.replace` on a non-init field breaks late.
13. **Skipping perception for infants.** `04 §12.2` says perception builds for infants and salience is floored. If you skip them, C09's routing loop hits a `KeyError` at the first birth, ~2,000 ticks into a run.
14. **Creating `ledger_accounts` rows.** `agents → economy` is forbidden and ledger writes are confined to `ledger.py`. Mint the id string, set `wealth_cents = 0`, and leave the row to M2.
15. **Emitting skill events for school and work.** C21 and C11 own those kinds. If C07 also emits 2020 for them, the log double-counts skill growth and every attainment metric is wrong by a factor of two. `apply_skill_growth` returns deltas and emits nothing.
16. **`identity_summary` writes.** C09's REFLECT owns that field. C07 reads it into `SelfView`, never writes it, and never adds a column for it — the durable record is C09's event 4007.
17. **Two live digest implementations.** If `fallback_digest` survives into M1 alongside C09's `digest_features`, the perception digest and the surprise EWMA drift apart, salience quietly decorrelates from what agents actually saw, and the routing statistics in every paper are wrong. Delete it the day C09 lands; the shared contract test in §12 exists so the swap is a one-line change.
18. **Letting the M1 death stub grow.** The moment someone adds estate distribution, debt write-off, or heir logic to `mark_dead`, C20 has a second implementation to reconcile at M5 and `INV-MONEY` — which is not even armed in M1 — starts failing the first time the economy exists. Four operations, no more.
