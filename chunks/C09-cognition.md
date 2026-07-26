# C09 — Salience scoring, cognition routing, deliberate, reflect

**M1** · `polis/agents/cognition/` · **Depends on:** C04 (kernel), C05 (llm), C07 (agent core, reflex), C08 (memory), C10 (actions) · **Blocks:** C21, C23a, C16–C19 · **Size:** L

---

## 1. Context

This chunk decides **which agents get to think** and **what they say when they do**. It is
the cost-control mechanism for the entire platform — at 1,000 agents and a 90-call budget,
~93% of agent-ticks are resolved by a deterministic reflex policy and ~7% by an LLM. That
allocation is a *systematic treatment assignment*: agents selected for cognition are not a
random sample, they are the agents with high stakes and high surprise, which is precisely
the population whose outcomes we then measure. This is threat **T8**, and the only defence
is that every input to the assignment is computed transparently, logged per tick, and
reproducible against a `random` control arm. Everything in §9.2 exists for that reason.

---

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/02-ARCHITECTURE.md` | **all** — §3.3 cognition sampling, §4.3 concurrency, §5 PHASE 2/3, §6.1 `Action`, §8 config |
| `../docs/03-DATA-MODEL.md` | §1.3 `llm_calls`, §2.1 `agents`, §10 `metrics` |
| `../docs/04-AGENT-SPEC.md` | §5 perception, **§7 salience & routing (primary source)**, §8 reflex, §9 deliberate, §10 reflect, §13 prompt discipline, §14 |
| `../docs/09-MODEL-ROUTING.md` | §3 purposes, §4 routing/budget/concurrency, §6 structured output & repair, §7.6 prefix caching, §8 prompt management |
| `../docs/10-RESEARCH-AND-OBSERVABILITY.md` | §1.8 `sys.cognition.*` metrics |
| Chunks | C05 (`Router`, `Purpose`, `SchemaRepairExhausted`), C07 (`Observation`, `AgentState`, `reflex_decide`), C08 (`Retriever`, `ReflectionEngine`, `GoalStack`, `BeliefWriter`), C10 (`Action`, `legal_actions`) |

---

## 3. Scope — in

1. `SalienceScorer` — the five components, the exploration term, the digest EWMA.
2. `digest_features()` / `digest_hash()` — the feature vocabulary behind `Observation.digest_hash`.
3. `CognitionRouter` — PHASE 2: force-routes, budget-aware top-K, `weighted | random | always`
   policies, per-tick logging of the cutoff and the full component breakdown.
4. `DeliberatePath` — prompt assembly from `prompts/deliberate/`, trait narrative, legal
   actions with schemas, the token cap and shrink ladder, output parsing, fallback to reflex.
5. `ReflectPath` — the `1 + Q` REFLECT calls, `identity_summary` write-back.
6. `CognitionPhase` — PHASE 3 orchestration: `asyncio.gather`, deterministic reordering by
   `actor_id`, reflex for everyone else, external-agent hand-off point.
7. The `prompts/deliberate/` and `prompts/reflect/` templates, schemas, and paraphrase siblings.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `Observation` construction, `ReflexProfile`, `reflex_decide()`, needs decay | **C07** |
| Model selection, temperature, lane semaphores, retries, the repair loop, `llm_calls` rows, kinds 4100–4199 | **C05** |
| Memory writes, retrieval scoring, citation validation, goal-stack semantics | **C08** |
| `ActionType`, params models, the five validation gates, `legal_actions()` | **C10** |
| Draining external agents from Redis, deadlines, signatures | **C22** (you expose the seam) |
| `beliefs` writes and the `07 §5.5` gates | **C16/C17** via C08's `BeliefWriter` |

---

## 5. Interfaces you provide

```python
# polis/agents/cognition/types.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

Mode = Literal["reflex", "deliberate", "reflect"]

@dataclass(frozen=True, slots=True)
class SalienceComponents:
    surprise:  float          # [0,1]
    stakes:    float          # [0,1] after the neuroticism scale and clip
    novelty:   float          # [0,1]
    social:    float          # [0,1]
    scheduled: float          # [0,1]
    epsilon:   float          # [0, exploration_epsilon)

    def total(self, w: Mapping[str, float]) -> float: ...

@dataclass(frozen=True, slots=True)
class SalienceResult:
    agent_id:   str
    score:      float
    components: SalienceComponents
    eligible:   bool          # False for infants, sleepers, incarcerated, dead
    forced:     Literal["none", "mandatory", "reflection"]

@dataclass(frozen=True, slots=True)
class RoutingPlan:
    tick:                int
    deliberate:          tuple[str, ...]     # sorted by agent_id
    reflect:             tuple[str, ...]     # sorted by agent_id
    reflex:              tuple[str, ...]     # sorted by agent_id
    external:            tuple[str, ...]
    cutoff:              float               # lowest salience admitted to DELIBERATE
    policy:              Literal["weighted", "random", "always"]
    calls_budgeted:      int
    tokens_budgeted:     int
    binding_constraint:  Literal["calls", "tokens", "population", "none"]
    force_routed:        int
    scores:              Mapping[str, SalienceResult]

@dataclass(frozen=True, slots=True)
class Decision:
    actor_id:      str
    action:        Action                     # from C10
    mode:          Mode
    origin:        Literal["reflex", "deliberate", "reflect", "external", "scripted"]
    salience:      float
    llm_call_id:   str | None
    fell_back:     bool
    fallback_reason: str | None               # parse|illegal_type|budget|timeout|no_action
```

```python
# polis/agents/cognition/digest.py
def digest_features(obs: Observation) -> frozenset[str]:
    """Closed, versioned feature vocabulary. See §9.1. Pure."""

def digest_hash(features: frozenset[str]) -> str:
    """sha256 over '\\x1f'.join(sorted(features)). C07's perception calls this to fill
    Observation.digest_hash — one implementation, not two."""

# polis/agents/cognition/salience.py
class SalienceScorer:
    def __init__(self, cfg: SalienceConfig, rng: RngRegistry) -> None: ...

    def score(self, obs: Observation, state: AgentState, tick: int) -> SalienceResult:
        """Pure function of `obs` (last tick's committed state) + `state` + the agent's
        stored expectation map. Never reads current-tick state. Target < 20 us/agent."""

    def update_expectation(self, agent_id: str, features: frozenset[str]) -> None:
        """PHASE 6 only. EWMA over the digest feature map. See §9.1."""

    def reset_agent(self, agent_id: str) -> None:
        """On death/naturalisation. Drops the expectation map and novelty counters."""

# polis/agents/cognition/routing.py
class CognitionRouter:
    def plan(
        self, tick: int,
        observations: Mapping[str, Observation],
        states: Mapping[str, AgentState],
        budget: BudgetGuard,                # from C05
        reflection: ReflectionEngine,       # from C08
        external_ids: Sequence[str] = (),
    ) -> RoutingPlan:
        """PHASE 2. Emits 4002 (sampled) and 4003 (once). Mutates nothing but its own
        counters. Deterministic given (tick, seed, observations, states, budget)."""

# polis/agents/cognition/deliberate.py
class DeliberatePath:
    async def decide(self, agent_id: str, obs: Observation, state: AgentState,
                     tick: int, salience: float) -> Decision: ...

    def build_prompt(self, agent_id: str, obs: Observation, state: AgentState,
                     tick: int, memories: Sequence[ScoredMemory],
                     legal: Sequence[LegalAction]) -> RenderedPrompt:
        """Synchronous, pure given its inputs, and separately testable. Applies the
        §9.4 shrink ladder until est_tokens <= cfg.max_prompt_tokens."""

# polis/agents/cognition/reflect.py
class ReflectPath:
    async def reflect(self, agent_id: str, tick: int) -> ReflectApplied: ...

# polis/agents/cognition/narrative.py
def render_traits(traits: Mapping[str, float]) -> str:
    """Trait vector -> prose. Deterministic, fixed trait order, banded phrasing.
    Post-condition: the output contains no digit and no trait key name."""

def render_needs(needs: Mapping[str, float]) -> str: ...

# polis/agents/cognition/phase.py
class CognitionPhase:
    async def run(self, tick: int, plan: RoutingPlan,
                  observations: Mapping[str, Observation],
                  states: Mapping[str, AgentState]) -> tuple[Decision, ...]:
        """PHASE 3. Returns decisions sorted by actor_id. The DELIBERATE wave goes through
        LLMRouter.gather (request-order results and request-order budget charging);
        NO local semaphore — the router owns lane concurrency (09 §4.4). See §9.5."""
```

---

## 6. Interfaces you consume

| From | Symbol | Notes |
|---|---|---|
| C05 | `LLMRouter.call(purpose, agent_id, tick, variables, schema_name=None, *, deferred=False) -> CallResult` | single calls (REFLECT) |
| C05 | `LLMRouter.gather(requests: Sequence[CallRequest]) -> list[CallResult]` | **the DELIBERATE wave**; results in request order, budget charged under one lock in request order |
| C05 | `CallRequest(purpose, agent_id, tick, variables, schema_name)` | request construction |
| C05 | `CallResult.parsed`, `.parsed_ok`, `.call_id`, `.degraded`, `.tokens_in/out` | result handling |
| C05 | `SchemaRepairExhausted`, `ProviderError` subclasses, `Purpose` | exception handling |
| C05 | `BudgetGuard.remaining(line)`, `.binding_constraint` | PHASE 2 allocation |
| C05 | `PromptLibrary`, `RenderedPrompt(system, user, rendered_hash, template_hash, est_tokens)` | Jinja2, `StrictUndefined`, token estimate |
| C07 | `Observation`, `AgentState`, `reflex_decide(obs, profile, world, rng) -> Action` | reflex fallback |
| C07 | `AgentState.identity_summary: str`, `.stage`, `.goals` | write-back target |
| C08 | `Retriever.retrieve(...)`, `ReflectionEngine.*`, `GoalStack.apply/replace`, `BeliefWriter` | memory side |
| C10 | `Action`, `ActionType`, `LegalAction`, `ValidationContext`, `ResolverRegistry` | envelope + prompt |
| C10 | `legal_actions(obs, state, registry, ctx) -> tuple[LegalAction, ...]` | the `## What you can do` block; also the legal-type membership check |
| C04 | `RngRegistry`, `polis.kernel.det.stable` | ordering |

> **Coordination item for C05 (shared with C08).** `LLMRouter.call` and `CallRequest` need an
> optional `template: str | None = None`, defaulting to the `RouteSpec`'s template. `REFLECT`
> addresses two templates (`questions`, `insights`) and there is no other legal way to select
> between them — inlining a prompt in Python violates `04 §13`, and overloading `schema_name`
> couples two independent things. **Raise it jointly with C08.**
> `AgentState.identity_summary` already exists in C07 (`C07 §5`), so no action there.

---

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `agents` | R + W(`goals`, `identity_summary`) | goals written through C08's `GoalStack`; `identity_summary` written by `ReflectPath` |
| `memories` | R (via C08) | never touched directly |
| `metrics` | W | `sys.cognition.*`, via C24a's sink |
| `llm_calls` | **never** | C05 writes every row, including cache hits |
| `events` | W | 4002–4009 only |

**`agents.identity_summary` has no column in `03 §2.1`.** Do **not** add DDL. The durable
record is event `4007 IDENTITY_SUMMARY_SET`; the value lives on the in-memory `AgentState`
and in the checkpoint, and `polis rebuild` restores it by replaying `4007`. This is
consistent with `03 §0` ("every table except `events` … is a projection"). C23a's agent
inspector reads the latest `4007` for the agent. If a column is later wanted for query
convenience, it is additive and nullable — flag it, do not ship it here.

---

## 8. Event kinds owned

**Range: 4002–4009.** (C07 owns 4001; C08 owns 4010–4029; 4100–4199 is `polis.llm` and you
never emit into it — `LLM_CALL_FAILED` and `BUDGET_EXHAUSTED` are the router's.)

| Kind | Name | Payload | Persistence |
|---|---|---|---|
| 4002 | `SALIENCE_SCORED` | `agent_id, score, components{surprise,stakes,novelty,social,scheduled,epsilon}, weights, routed_mode, forced, eligible` | `02 §3.3` sampling: **always** if routed deliberate/reflect, else `cognition_sample_rate` |
| 4003 | `COGNITION_ROUTED` | `tick, policy, cutoff, n_deliberate, n_reflect, n_reflex, n_external, n_eligible, force_routed_mandatory, force_routed_reflection, calls_budgeted, tokens_budgeted, binding_constraint, salience_p50, salience_p90, exploration_epsilon` | **always, once per tick** |
| 4004 | `DELIBERATION_COMPLETED` | `agent_id, action_type, llm_call_id, prompt_tokens, retrieval_k_used, feed_slice_used, shrink_steps, belief_updates_n, goal_updates_n` | always |
| 4005 | `DELIBERATION_FAILED` | `agent_id, reason ∈ {parse, illegal_type, budget, timeout, no_action}, llm_call_id, fell_back_to ∈ {reflex}` | always |
| 4006 | `REFLECT_COMPLETED` | `agent_id, questions[], n_insights_kept, n_insights_dropped, llm_call_ids[], calls_used` | always |
| 4007 | `IDENTITY_SUMMARY_SET` | `agent_id, summary, previous_summary, llm_call_id` | always |
| 4008 | `PROMPT_TRUNCATED` | `agent_id, purpose, requested_tokens, cap, ladder_step_reached, blocks_dropped[]` | always |
| 4009 | `COGNITION_DEGRADED` | `agent_id, from_mode, to_mode, cause ∈ {budget, breaker, repair_exhausted, no_resolver}` | always |

`4003` is the T8 audit record. It must be emitted on **every** tick, including ticks where
nothing was routed, or the treatment-assignment series has gaps and the control comparison
cannot be made.

---

## 9. Implementation notes

### 9.1 Salience

```
salience = w_surprise·surprise + w_stakes·stakes + w_novelty·novelty
         + w_social·social + w_scheduled·scheduled + ε
ε ~ U(0, exploration_epsilon)   via rng.get("salience.epsilon", agent_id, tick)
```

Each component is computed into `[0,1]` **before** weighting. Weights come from config and
need not sum to 1; the realised sum is reported in `4003.weights`.

**`surprise` — the digest EWMA.** A set cannot be exponentially averaged, so the agent keeps
a feature-weight map `w: dict[str, float]`:

```python
# PHASE 6, once per agent per tick
for f in w: w[f] *= (1 - alpha)                    # alpha = digest_ewma_alpha, 0.2
for f in features: w[f] = w.get(f, 0.0) * (1 - alpha) + alpha
w = {f: v for f, v in w.items() if v >= prune_floor}   # 0.01, keeps the map small

# PHASE 2
expectation = frozenset(f for f, v in w.items() if v >= expectation_floor)   # 0.15
surprise    = 1.0 - jaccard(features, expectation)     # empty ∪ empty -> surprise 0.0
```

Feature vocabulary (closed, versioned as `DIGEST_V = 1`; changing it changes every prompt
variable and therefore every cache key — treat it as a schema):

```
place:{place_type}          hour:{tick_hour // 6}       colo:{bucket(n_colocated)}
emp:{employment_status}     inbox:{bucket(len)}         offer:{bool}
oblig:{class}               feed:{top_topic}            news:{bool}
wealth:{decile}             health:{band}               need_crit:{need_name}   (per need < 0.2)
```

`digest_features()` lives here and C07's perception calls it to fill
`Observation.digest_hash`. Two implementations of this function is how the surprise term and
the perception digest silently diverge.

**`stakes`.**

```
raw = max(
  min(abs(d_wealth_cents) / (wealth_cents + stakes_floor_cents), 1.0),
  min(abs(d_health) / 0.10, 1.0),
  1.0 if employment_status changed else 0.0,
  1.0 if a relationship formed/ended/changed valence sign else 0.0,
  1.0 if legal jeopardy (accused, charged, court date, detected crime) else 0.0,
)
stakes = min(1.0, raw * (1.0 + traits["neuroticism"]))
```

The clip after the neuroticism scale is mandatory — without it `stakes` reaches 2.0 and one
component silently dominates the sum.

**`novelty`.** `pair = (place_type, situation_type)`, where `situation_type` is a closed
vocabulary (`routine, addressed, offer_pending, obligation_due, market_open, conflict,
transition, crisis`). `novelty = traits["openness"] * (1.0 / (1.0 + seen[pair]))`; `seen` is
incremented in PHASE 6.

**`social`.** `1.0` if directly addressed this tick (DM, offer, accusation, proposal,
mention); `0.4` if a strong tie (`relationships.strength >= social_tie_floor`, 0.6) is
co-located or a subject of an event in the observation; else `0.0`.

**`scheduled`.** `1.0` if `obs.obligations` contains anything with `due_tick == tick`. C07's
`Obligation` carries `mandatory: bool`; `mandatory=True` additionally force-routes in §9.2.
`scheduled` fires for both kinds — a routine rent payment is salient, it is just not
guaranteed cognition.

**Ineligibility.** `eligible = False` and `score = 0.0` for: infants (`stage == "infant"`,
`04 §12.2`), sleeping agents, the dead, and the incarcerated. Ineligible agents are excluded
from the ranking **and from the denominator** of `sys.cognition.deliberate_share`.

`@mechanism("cognition.salience_routing", entails="LLM cognition is allocated to the agents with the highest surprise, stakes, novelty, social pressure, and scheduled obligation. Therefore agents experiencing large events think more, and any finding that outcomes concentrate among agents who deliberate is partly a selection effect of this rule. Every headline behavioural result must be reported alongside the salience.policy: random arm.")`

### 9.2 Routing (PHASE 2)

```
1.  scores  = {a: score(obs[a], state[a], tick) for a in awake}          # all agents, always
2.  forced_mandatory  = [a for a in eligible if has_mandatory_obligation(a)]
3.  forced_reflection = [a for a in eligible if reflection.is_triggered(a, tick)[0]]
                        minus forced_mandatory        # mandatory wins; reflection re-arms
4.  calls, tokens = budget.remaining("cognition")
    reserve  = len(forced_mandatory)*1 + len(forced_reflection)*(1 + max_questions)
    k_calls  = max(0, calls  - reserve)
    k_tokens = max(0, tokens - reserve*est_tokens_per_call) // est_tokens_per_call
    k        = min(k_calls, k_tokens, len(remainder))
    binding  = "calls" if k_calls <= k_tokens else "tokens"; "population" if k == len(remainder)
5.  ranked   = sorted(remainder, key=lambda a: (-scores[a].score, a))
    deliberate = forced_mandatory + ranked[:k]
    cutoff     = scores[ranked[k-1]].score if k else +inf
6.  everyone else -> reflex.  external -> deliberate, from the `external` budget line.
```

`est_tokens_per_call = cfg.max_prompt_tokens + routing.DELIBERATE.max_tokens` (3,000 + 700 =
3,700). Being pessimistic here is correct: the router enforces the real budget and a
disagreement between PHASE 2's intent and the router's reality shows up as `4009`.

**Force-routes are not optional and are not part of the policy under test.** They apply
identically under `weighted` and `random`, which is what keeps the control arm comparable.
If `reserve > calls`, the forced set still runs (they are mandatory), `k = 0`, and
`4003.binding_constraint = "calls"` with `force_routed > calls_budgeted` — surface this as
the metric `cog.force_route_overflow`; a run where it is chronically positive has no
discretionary cognition left and its T8 story is different.

**`salience.policy: random` (the control).** Compute salience exactly as under `weighted` and
log it — the comparison needs both arms' scores. Then select the DELIBERATE set uniformly at
random from `remainder` with the **same cardinality `k`**, via
`rng.get("salience.control", "", tick)` over the `stable()`-sorted remainder. `cutoff` is
recorded as the minimum realised score, flagged `policy: random`. Randomising the force-routes
too would make it a different experiment.

**`salience.policy: always` (debug).** Every eligible agent routes DELIBERATE; the router's
budget guard degrades the overflow and emits `4009`. Useful for prompt iteration on 50 agents,
never for a run.

### 9.3 The DELIBERATE prompt

Templates: `prompts/deliberate/system.v1.jinja`, `user.v1.jinja`, plus **two paraphrase
siblings each** (`09 §8.5` fails CI without them). Schema:
`prompts/schemas/deliberate.schema.json`, exactly the five keys of `04 §9.2`, with
`additionalProperties: false` and explicit `required` (`09 §6.4`).

Block order is normative for prefix caching (`09 §7.6`) and must not be rearranged:

| Block | Content | Tokens | Stability |
|---|---|---|---|
| 1 | City description, rules of conduct, **the action schemas** | ~800 | byte-identical across all agents and ticks |
| 2 | Name, age, `render_traits()`, `identity_summary`, goals | ~400 | per agent |
| 3 | Place, needs, money, employment, co-located, offers, obligations, inbox, feed, news, retrieved memories, **legal actions last** | ~1,800 | per tick |

Block 1 is emitted first and identically or the ~14% prefix-cache saving evaporates silently.
The legal-action list goes **last in the user message, immediately before the instruction to
respond** (`09 §6.4` — recency dominates schema adherence on the `repair` tier, and MiniMax
M2.x is `repair`, not `constrain`).

**Trait narrative.** `render_traits` maps each of the ten traits to one of three banded
phrases (`< 0.33`, `< 0.67`, else) and joins them in a fixed trait order. Never numbers,
never the trait key name (`04 §9.1`). A post-condition assert on "contains no digit" is
cheap and catches the "just for debugging" regression.

**Legal actions with schemas.** From C10: `tuple[LegalAction, ...]` where each carries
`type`, `param_schema` (JSON Schema dict) and `options` (concrete targets where the set is
small and knowable). Render as a stable-ordered list. This is what lets the model
*parameterise a real option* rather than invent one, and it is also what C05's `StubProvider`
parses out of the prompt (`09 §2.6.3`) — changing the block's format breaks the entire
integration suite.

**Token cap and shrink ladder.** Cap is `cfg.max_prompt_tokens` (3,000). Estimate with the
same pessimistic `ceil(chars / 3.5)` rule C08 uses. Shrink in exactly this order, re-measuring
after each step, and emit `4008` if any step fires:

| Step | Action |
|---|---|
| 1 | retrieval `k`: 12 → 8 → 5 → 3 → 0 |
| 2 | feed slice: 15 → 10 → 5 → 0 |
| 3 | news 3 → 1 → 0, then inbox 10 → 5 → 0 |
| 4 | hard-truncate block 3's free text, oldest first, and set `4008.ladder_step_reached = 4` |

**Blocks 1 and the legal-action list are never shrunk.** Shrinking the action space is a
change of treatment that looks exactly like a change of behaviour — it is the most dangerous
possible "optimisation" in this chunk.

**Output handling.**

```python
try:
    res = await router.call(Purpose.DELIBERATE, agent_id, tick, vars_, "deliberate")
except (SchemaRepairExhausted, ProviderError):        # incl. timeout, permanent, budget deny
    return self._fall_back(agent_id, obs, state, reason=...)   # 4005 + reflex
```

The router owns retries, the repair loop, `LLM_CALL_FAILED` (4101) and the `llm_calls` row.
C09 does **not** re-emit those. After parsing, C09 performs the checks the JSON Schema cannot:

1. `action.type` is in the legal set for this agent-tick. If not → **no repair**, fall back to
   reflex, `4005{reason: "illegal_type"}`. Repairs are the router's; a second repair channel
   here doubles the token spend invisibly.
2. `reasoning` is stored verbatim on the `Action` and **never inspected** (`02 §6.1`).
3. `speech` is passed through to the action envelope untouched.
4. `belief_updates` → `BeliefWriter` via C08. `goal_updates` → `GoalStack.apply()`.
5. `origin = "deliberate"` **only if a model produced the action**; a fallback is
   `origin = "reflex"` (`09 §6.2`), so downstream analysis never misattributes reflex
   behaviour to deliberation.

Emit `4004` on success, `4005` on any fallback.

### 9.4 The REFLECT path

```
ctx        = await memory.build_context(agent_id, tick)                   # C08: 40 recent
qs         = await router.call(REFLECT, agent_id, tick, vars(ctx), "reflect_questions",
                               template="questions")                      # <= max_questions
for i, q in enumerate(qs):
    shown  = await memory.memories_for_question(agent_id, q, tick)        # C08: top 12
    ins    = await router.call(REFLECT, agent_id, tick, vars(q, shown),
                               "reflect_insights", template="insights")
applied    = memory.apply_output(agent_id, tick, ReflectOutput(...), shown_ids, call_id)
state.identity_summary = out.identity_summary                             # + emit 4007
```

`shown_ids` passed to `apply_output` is the **union over questions** of the ids actually
rendered into a prompt. Passing the retrieval candidate set instead of the rendered set
defeats the `not_shown` citation check in C08 §9.5.

Total cost per reflection: `1 + Q` calls. Charge all of them to the `cognition` line in PHASE
2's reserve or REFLECT quietly crowds out DELIBERATE and the deliberate rate drops without
explanation.

**A REFLECT agent does not produce an action from the REFLECT call.** `02 §5` PHASE 3 says it
*may*; v1 says it does not. The agent's action for that tick comes from `reflex_decide()`.
This is the boring option, it keeps the REFLECT schema at four keys, and it is recorded as a
decision in the handback.

On failure (`SchemaRepairExhausted` or budget): skip the reflection, do **not** reset C08's
accumulator, emit `4009{cause}`; the trigger re-arms next tick (`09 §4.2`).

### 9.5 PHASE 3 orchestration

Four stages, in order. Stages A and C are concurrent I/O; B and D are synchronous and are
where every decision is made.

```python
# A. retrieval — pure I/O, no state mutation, no budget
mems = dict(zip(plan.deliberate,
                await asyncio.gather(*(retriever.retrieve(a, query(a), tick)
                                       for a in plan.deliberate))))

# B. prompt assembly — synchronous, in actor_id order, deterministic
reqs = [CallRequest(Purpose.DELIBERATE, a, tick,
                    self.build_prompt(a, obs[a], states[a], tick, mems[a], legal[a]).variables,
                    "deliberate")
        for a in plan.deliberate]                       # plan.deliberate is already sorted

# C. one wave — the router returns results in REQUEST order and charges budget in that
#    order under a single lock (09 §4.5). Do NOT hand-roll this with asyncio.gather.
results = await router.gather(reqs)

# D. parse + apply, zipped in request order; mutation happens only here
for agent_id, res in zip(plan.deliberate, results, strict=True):
    ...
```

REFLECT agents run separately (`1 + Q` sequential calls each, since question *n* determines
prompt *n+1*), concurrently across agents via `asyncio.gather`, and are applied afterwards in
`actor_id` order.

- **No semaphore in this chunk.** Lane concurrency is the router's (`09 §4.4`). A second
  semaphore here does not deadlock — it silently halves throughput and makes the router's
  `max_calls_per_tick(lane)` sizing arithmetic wrong.
- Use `router.gather` rather than `asyncio.gather` over `router.call` for the DELIBERATE
  wave. Its request-order budget charging is what makes "the same run exhausts the budget at
  the same call" true; per-call charging makes budget exhaustion depend on network jitter.
- `ProviderError` subclasses, `SchemaRepairExhausted`, and budget denial → fall back to
  reflex. **Anything else re-raises** — an unhandled exception in cognition is a bug and
  `02 §10` says HALT. Never `except Exception: continue`.
- Reflex decisions for `plan.reflex` are computed synchronously, after the gather, in sorted
  order, so reflex RNG draws are not interleaved with I/O completion order.
- The returned tuple is sorted by `actor_id` before leaving the phase (`02 §5` PHASE 3).
- External agents: `CognitionPhase` exposes `external_decisions: Sequence[Decision]` as an
  injected input. C09 does not import `polis.gateway`, does not touch Redis, and does not
  know about deadlines. A missed deadline arrives as an already-built reflex `Decision`.

### 9.6 Metrics (per tick, `10 §1.8`)

`sys.cognition.deliberate_share`, `.reflect_share`, `.reflex_share` (denominator = eligible
agents), `.salience_cutoff`, `.salience_p50`, `.salience_p90`, `.force_routed`,
`.budget_exhausted`, plus this chunk's own: `cog.force_route_overflow`,
`cog.prompt_tokens_p50/p95`, `cog.shrink_ladder_step_mean`, `cog.illegal_type_rate`,
`cog.fallback_share`, `cog.reflect_calls_per_reflection`, `cog.surprise_mean`.

---

## 10. Configuration keys

```yaml
salience:
  policy: weighted                  # weighted | random (control) | always (debug)
  weights: {surprise: 0.30, stakes: 0.35, novelty: 0.10, social: 0.15, scheduled: 0.10}
  exploration_epsilon: 0.02
  digest_ewma_alpha: 0.20
  expectation_floor: 0.15
  prune_floor: 0.01
  stakes_floor_cents: 100_000
  social_tie_floor: 0.60
  infant_floor: 0.0                 # infants never deliberate (04 §12.2)

cognition:
  max_prompt_tokens: 3000
  retrieval_k_ladder: [12, 8, 5, 3, 0]
  feed_slice_ladder:  [15, 10, 5, 0]
  reflect_max_questions: 3          # cost lever: 1 => 2 calls per reflection
  sample_rate: 0.02                 # 4002 for reflex agents; == cognition_sample_rate

llm:
  budget:
    lines:
      cognition: {calls_per_tick: 90, tokens_per_tick: 300_000}   # 90 x ~3,300 tok/call
      external:  {calls_per_tick: 32, tokens_per_tick: 100_000}
```

> `tokens_per_tick: 300_000` is the ratified value: `calls_per_tick: 90` at ~3,300 tokens per
> call. `09 §0.3 R2`'s 240,000 and `02 §8`'s 120,000 are the older, internally inconsistent
> figures — 120,000 binds at 36 calls/tick and is retained only as the low-cost `chronicle`
> profile that the $12/sim-year target assumes. Do not mix them.

---

## 11. Acceptance criteria

1. `SalienceScorer.score` is a pure function of `(Observation, AgentState, expectation map,
   tick)` and never reads current-tick state; a mutation test that changes live state
   mid-PHASE-2 leaves scores unchanged.
2. Every component of `SalienceComponents` is in `[0,1]`, including `stakes` at
   `neuroticism = 1.0` with a raw stake of 1.0.
3. `ε` is drawn exactly once per agent-tick from `rng.get("salience.epsilon", agent_id, tick)`;
   two runs at the same seed produce identical `ε` per agent.
4. A day identical to the agent's expectation scores `surprise ≈ 0`; a first-ever
   `(place_type, situation_type)` scores `novelty = openness`.
5. Ranking ties are broken by `agent_id` ascending; the same seed produces the same
   DELIBERATE set twice.
6. Budget allocation respects **both** caps: with `calls_per_tick: 90` and
   `tokens_per_tick: 3_700`, exactly 1 agent is routed and `binding_constraint == "tokens"`.
7. Force-routed MANDATORY agents are admitted before budget allocation and are present in
   `plan.deliberate` even when `k == 0`.
8. Reflection force-routes reserve `1 + reflect_max_questions` calls each.
9. Under `policy: random`, `len(plan.deliberate)` equals the `weighted` value for the same
   inputs, the sets differ, salience is still computed and logged, and force-routes are
   identical across both arms.
10. `4003` is emitted on **every** tick, including ticks with zero deliberations.
11. `4002` is emitted for every deliberate/reflect agent and for a seeded `sample_rate` of
    reflex agents — never for all of them.
12. `render_traits` output contains no digit and no trait key name, and is byte-stable for a
    given trait vector.
13. A rendered prompt never exceeds `max_prompt_tokens`; forcing an over-long context walks
    the ladder in order and emits `4008` with the step reached.
14. The legal-action block and block 1 are byte-identical before and after any shrink step.
15. Block 1 is the first content in the system message and is byte-identical across two
    different agents at the same tick.
16. `SchemaRepairExhausted` produces a reflex action with `origin == "reflex"`, `4005` is
    emitted, and `4101` is **not** emitted by this chunk.
17. An `action.type` outside the legal set falls back to reflex with
    `4005{reason: "illegal_type"}` and issues no additional LLM call.
18. `identity_summary` from REFLECT lands on `AgentState`, emits `4007`, and appears in the
    next tick's deliberate prompt for that agent.
19. `shown_ids` handed to C08 is the union of ids actually rendered, verified by asserting a
    candidate that was retrieved but shrunk out of the prompt is rejected as `not_shown`.
20. PHASE 3 returns decisions sorted by `actor_id`; a stub with per-key latency that shuffles
    completion order leaves the output byte-identical.
21. The DELIBERATE wave is issued through `LLMRouter.gather` with requests in `actor_id`
    order, and results are zipped `strict=True` against that order.
22. No `asyncio.Semaphore` exists anywhere in `polis/agents/cognition/`.
23. `prompts/deliberate/` and `prompts/reflect/` each have ≥2 paraphrase siblings and pass
    `scripts/lint_prompts.py` (no provider names, no simulation words).
24. `mypy --strict polis/agents/cognition` and `import-linter` pass.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/cognition/test_digest.py` | Feature vocabulary closed; `digest_hash` stable and sorted; C07's hash matches this function's |
| `tests/unit/cognition/test_salience_components.py` | Each component's bounds; neuroticism clip; openness scaling; social 1.0/0.4/0.0; scheduled on obligation ticks |
| `tests/unit/cognition/test_salience_surprise.py` | EWMA convergence; identical-day → ~0; regime change → spike; prune floor keeps the map bounded; `reset_agent` clears it |
| `tests/unit/cognition/test_routing_budget.py` | Calls-bound, tokens-bound, population-bound cases; `binding_constraint` correct in each; reserve arithmetic for REFLECT |
| `tests/unit/cognition/test_routing_force.py` | MANDATORY admitted with `k == 0`; reflection force-route; mandatory beats reflection; overflow metric |
| `tests/unit/cognition/test_routing_control_arm.py` | `random` matches `weighted` in cardinality, differs in membership, keeps force-routes, still logs salience |
| `tests/unit/cognition/test_routing_determinism.py` | Same seed → same plan twice; tie-break by `agent_id`; shuffled input dict order → same plan |
| `tests/unit/cognition/test_narrative.py` | No digits, no trait keys, banded, byte-stable, all ten traits present |
| `tests/unit/cognition/test_prompt_assembly.py` | Block order; block 1 byte-identical across agents; legal actions last; `StrictUndefined` satisfied with an empty observation |
| `tests/unit/cognition/test_prompt_shrink.py` | Ladder order; `4008` payload; legal-action block and block 1 unchanged; cap never exceeded |
| `tests/unit/cognition/test_deliberate_output.py` | Schema-valid parse; `reasoning` stored verbatim; illegal type → reflex + `4005`; `origin` bookkeeping; belief/goal forwarding called once each |
| `tests/unit/cognition/test_reflect_path.py` | `1 + Q` calls; `shown_ids` is the rendered union; `identity_summary` + `4007`; failure re-arms without resetting the accumulator |
| `tests/integration/test_cognition_phase_order.py` | Stub with per-agent latency: completion order varies, decision order does not |
| `tests/integration/test_cognition_budget_pressure.py` | Under `--llm-chaos`, the run completes, `4009` fires, `deliberate_share` falls, nothing raises |
| `tests/integration/test_salience_treatment_log.py` | Over 200 ticks: every tick has exactly one `4003`; `4002` count == deliberate + reflect + ~2% of reflex; component sums reproduce the logged score |
| `tests/determinism/test_cognition_determinism.py` | 200 ticks, same seed, `StubProvider` → identical event hash chain |
| `tests/unit/cognition/test_no_semaphore.py` | AST scan of `polis/agents/cognition/` finds no `asyncio.Semaphore` |

---

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis/agents/cognition/` exports the §5 symbols with those exact signatures.
2. Kinds 4002–4009 registered in `polis/events/kinds.py` with payload schemas and the `4002`
   sampling rule wired to `cognition_sample_rate`.
3. `prompts/deliberate/{system,user}.v1.jinja`, `prompts/reflect/{questions,insights}.v1.jinja`,
   two paraphrase siblings each, `prompts/schemas/{deliberate,reflect_questions,reflect_insights}.schema.json`,
   all with `09 §8.2` version headers and all in `runs.prompt_manifest`.
4. `@mechanism("cognition.salience_routing", ...)` declared.
5. Handback records: the two coordination items in §6; the `identity_summary` storage
   decision in §7; the "REFLECT emits no action" decision in §9.4; and a measured
   `deliberate_share` from a 2,000-tick calibration run against `04 §14` open question 1
   (what cutoff yields ~7%). **Do not guess the cutoff — measure it.**

---

## 14. Traps

1. **Scoring from live state instead of the `Observation`.** PHASE 2 runs after PHASE 1 and
   the temptation to read "the current wealth" is enormous. It breaks the simultaneity
   guarantee (`04 §5` rule 1) and makes salience depend on how far through PHASE 1 you are.
2. **Weighting before clipping.** `stakes` scaled by `(1 + neuroticism)` reaches 2.0. With
   `w_stakes = 0.35` that is 0.70 of a score whose other four components sum to at most 0.65.
   Every neurotic agent deliberates every tick and the budget is gone by agent 90.
3. **Drawing `ε` per component, or per call, or from an unseeded source.** The ranking becomes
   irreproducible, the control arm becomes incomparable, and the determinism test fails in a
   way that looks like an LLM problem.
4. **Tie-breaking by dict order.** At `exploration_epsilon: 0`, ties are common (many agents
   score exactly 0). `sorted(remainder, key=lambda a: -score[a])` is not deterministic in the
   presence of ties across Python versions and insertion orders. Use `(-score, agent_id)`.
5. **Randomising force-routes in the `random` control.** Then the arms differ in two ways and
   the control identifies nothing. Force-routes are held fixed by construction.
6. **Allocating on calls only.** `calls_per_tick: 90` with `tokens_per_tick: 120_000` binds at
   36. Allocating 90 means the router degrades 54 agents mid-tick, `4009` floods, and the
   realised deliberate rate is neither the configured one nor a reported one.
7. **Not reserving REFLECT's `1 + Q` calls.** REFLECT is charged to the same line. Reserve one
   call per reflection and the other `Q` arrive as budget denials, silently converting
   reflections into skipped triggers that re-arm forever.
8. **Shrinking the legal-action block to fit.** This changes the agent's action space as a
   function of how long its memories were. It is a confound that is indistinguishable from
   behaviour in every downstream analysis. Never do it; truncate free text instead.
9. **Reordering prompt blocks "for readability".** Block 1 must be first and byte-identical
   or provider prefix caching stops hitting. The cost increase is ~14% and there is no error.
10. **Rendering traits as numbers.** "risk_tolerance: 0.2" invites the model to optimise the
    number instead of inhabiting the disposition, and it is a direct `04 §9.1` violation. The
    "just for this debug run" version always survives into main.
11. **A second repair loop in C09.** The router already retries twice. Adding a third channel
    here triples the worst-case wire calls per decision and the budget guard cannot see it.
12. **Adding a semaphore.** `04 §9.3`'s `llm.max_concurrency` was replaced by per-lane limits
    (`09 §0.2`). A local semaphore of 32 in front of a 90-wide lane turns a 3 s phase into a
    9 s phase and the only symptom is that the run is three times slower than the projection
    printed at startup.
13. **Applying results in completion order.** The classic. Use `router.gather`, which returns
    in request order, and zip `strict=True` against the sorted actor list. Hand-rolling with
    `asyncio.gather` over `router.call` also moves budget charging to completion order, so the
    same run exhausts its budget at a different call each time it is run.
14. **Swallowing exceptions in `gather`.** `return_exceptions=True` plus a bare `continue`
    turns a bug in C10 or C08 into a silently-degraded society. Only provider/budget/parse
    exceptions may be caught; everything else re-raises.
15. **`StrictUndefined` on a sparse observation.** An agent with no employer, no market, no
    offers, and an empty feed must still render. Build a `PromptVars` dataclass that populates
    every template key with an explicit empty value; do not rely on Jinja defaults.
16. **A model name or the word "simulation" arriving through a *variable*.** `lint_prompts.py`
    scans templates, not rendered values. A goal string the model wrote last week
    ("escape this simulation") is injected verbatim into the next prompt. Run a denylist scan
    over rendered variables under `--keep-prompts` and count hits as `llm.simaware.rate` input.
17. **Not resetting the expectation map on death.** Agent ids are not reused, but naturalised
    external agents and rebuilt projections both re-enter the map. A stale map makes surprise
    negative-biased for the rest of the run.
18. **Counting infants and sleepers in the deliberate-share denominator.** `deliberate_share`
    then drops as the birth rate rises, and the T8 series looks like a policy effect.
19. **Emitting `4002` for all 1,000 agents every tick.** That is 8.6M rows per microscope
    sim-year for a diagnostic. Obey `02 §3.3`: always for deliberate/reflect, sampled for reflex.
20. **Optimistic token estimation.** Under-count by 15% and the prompt overruns, the provider
    returns `finish_reason == "length"`, the JSON body is truncated, the repair loop burns two
    extra calls, and the reported symptom is "the model can't hold the schema".
