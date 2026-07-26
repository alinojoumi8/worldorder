# POLIS — Agent Specification

**Version:** 1.0
**Owner module:** `polis/agents/`
**Depends on:** `02-ARCHITECTURE.md` (tick phases, determinism, action envelope), `03-DATA-MODEL.md` (`agents`, `agent_skills`, `memories`, `beliefs`)

---

## 1. What an agent is

An agent is a persistent identity with:

- **Traits** — stable dispositions, set at birth, barely mutable. They condition prompts and parameterise the reflex policy.
- **Needs** — fast-moving drives that create pressure to act.
- **Skills** — a competence vector that gates jobs and determines wages.
- **Memory** — an append-only stream of observations, reflections, plans, and semantic facts.
- **Beliefs** — structured, queryable stances and credences.
- **Goals** — an LLM-maintained stack of what the agent is trying to do.
- **Position** — a place on the grid and a household.
- **A ledger account** — the only way it can hold money.

Native and external agents differ in exactly one thing: **where the decision comes from**.
Everything else — state, memory, budget, validation, consequences — is identical. This is
the fairness guarantee behind threat T12.

---

## 2. Traits

Ten dimensions, all in `[0,1]`, stored in `agents.traits`.

| Trait | Effect |
|---|---|
| `openness` | Weight on novelty in salience; willingness to change occupation, city district, or belief |
| `conscientiousness` | Reflex reliability (attends work, repays loans, studies); reduces impulse purchases |
| `extraversion` | Social action frequency; posting rate; size of social graph |
| `agreeableness` | Cooperation in bargaining; reduces rival-relationship formation |
| `neuroticism` | Amplifies stakes term in salience; increases panic selling and job-loss distress |
| `risk_tolerance` | Portfolio allocation, entrepreneurship propensity, crime propensity |
| `time_preference` | Discount rate; savings rate; education persistence. **Low value = patient.** |
| `altruism` | Charitable transfers, whistleblowing, voting against self-interest |
| `ambition` | Job search intensity, candidacy, firm founding |
| `honesty` | Probability of misrepresentation in speech, posts, pitches, and testimony |

### 2.1 Generation and inheritance

At world genesis, traits are drawn from a multivariate normal with a configured correlation
matrix (the Big-Five correlations are not independent), clipped to `[0,1]`.

At birth:

```
child_trait = clip(
    0.5 * (mother_trait + father_trait) * heritability
  + (1 - heritability) * population_mean
  + N(0, sigma_trait)
)
```

`heritability` defaults to 0.4 and is a declared `MECHANISM`. It is the knob for research
question B6 (inherited vs lived experience) — sweeping it from 0 to 1 separates the two
channels cleanly.

**Every draw uses `rng.get("agent.traits", child_id)`.**

---

## 3. Skills

A closed vocabulary of 14 skills. Closed because job requirements, curricula, and
production functions all index on it, and an open vocabulary makes the labour market
unmatched.

```
manual · operations · sales · finance · engineering · research · law ·
medicine · teaching · writing · design · management · negotiation · persuasion
```

| Property | Rule |
|---|---|
| Level | `[0,1]`, stored in `agent_skills.level` |
| Growth (school) | `Δ = school.quality × curriculum[skill] × learning_rate × (1 - level)` per sim-day |
| Growth (work) | `Δ = occupation_intensity[skill] × 0.25 × learning_rate × (1 - level)` per sim-day worked |
| Learning rate | `0.5 + 0.5 × conscientiousness`, scaled by an age curve peaking at 18 and decaying after 35 |
| Decay | `Δ = -decay_rate × level` per sim-month unused (`decay_rate` default 0.004) |
| Wage link | Wage offer is a function of `Σ occupation_weights[s] × level[s]`; see `06-ECONOMY-SPEC.md` |

Skill accumulation and decay are the mechanism behind unemployment scarring — a long
unemployment spell degrades skills, lowering re-employment probability. That is an
emergent, not scripted, poverty trap, and it is one of the more interesting things this
layer can produce.

---

## 4. Needs

Six drives in `[0,1]`, stored in `agents.needs`. They decay each tick and are restored by
actions. They exist to give the reflex policy something to optimise so that routine
behaviour is sensible without an LLM.

| Need | Decay/sim-day | Restored by | Consequence at 0 |
|---|---|---|---|
| `energy` | 1.0 | `SLEEP` | Health penalty, action failure probability |
| `hunger` | 1.0 | `EAT` (consumes food SKU, costs money) | Health penalty, then death hazard |
| `security` | 0.05 | Employment, savings, housing | Boosts stakes term in salience; drives political stance |
| `social` | 0.3 | `SAY`, `DIRECT_MESSAGE`, co-location with friends | Health penalty, reduced reputation |
| `esteem` | 0.1 | Promotion, likes, election wins, wealth relative to peers | Drives status-seeking consumption and posting |
| `purpose` | 0.05 | Goal progress, founding, teaching, office | Increases probability of occupation change |

Need decay is a `MECHANISM`. It is the most obvious place where hard-coded structure could
manufacture a finding, so ablation `--needs-off` exists and is run as a control for any
result about consumption or labour supply.

---

## 5. Perception (PHASE 1)

```python
@dataclass(frozen=True, slots=True)
class Observation:
    tick: int
    sim_time: datetime
    self_state: SelfView          # needs, health, wealth, employment, skills, goals
    place: PlaceView              # where I am, what's here, what's affordable/legal here
    co_located: tuple[AgentBrief, ...]     # capped at 12, ranked by relationship strength
    inbox: tuple[MessageBrief, ...]        # capped at 10
    feed: tuple[PostBrief, ...]            # capped at 15, produced by the feed algorithm
    news: tuple[ArticleBrief, ...]         # capped at 3
    market: MarketView | None              # quotes for held/watched symbols
    employer: EmployerView | None          # firm health, colleagues, my standing
    offers: tuple[OfferBrief, ...]         # job offers, term sheets, proposals awaiting me
    obligations: tuple[Obligation, ...]    # loan due, rent due, court date, exam
    digest_hash: str                       # sha256; used for the surprise term
```

**Rules:**

1. Perception is a **pure function of last tick's committed state**. It never reads a
   change made during the current tick. This is what makes simultaneous submission safe.
2. Every list is hard-capped. Uncapped context is the #1 cause of runaway token cost, and a
   cap forces the ranking question to be explicit and therefore studiable.
3. Perception is built for **every** agent, including reflex agents — the reflex policy and
   the salience score both consume it. It must be cheap: target < 80 μs/agent.
4. Perception never contains hidden information. If an agent can see it, it is because a
   spec says the agent can see it. Insider trading is only meaningful if information
   asymmetry is real.

---

## 6. Memory

Direct descendant of Park et al.'s memory stream, hardened for scale and determinism.

### 6.1 Memory types

| Type | Written by | Example |
|---|---|---|
| `observation` | Perception → salient events only | "Acme laid off 20 people. I was one of them." |
| `reflection` | REFLECT mode | "I keep getting fired from firms that took VC money. Growth companies are unstable." |
| `plan` | DELIBERATE when the agent sets a goal | "Save 200,000 by next year to found a company." |
| `semantic` | Extracted structured fact | `{subject: fm_acme, predicate: solvency, object: distressed, confidence: 0.7}` |

### 6.2 Writing

Not every event becomes a memory — that would be 20k memories/agent/sim-year.

```
write_memory  iff  event_salience(agent, event) > memory_threshold  (default 0.25)
               or  event.kind in ALWAYS_REMEMBER    (birth, death, hire, fire, marriage,
                                                     bankruptcy, verdict, election result)
```

**Importance scoring** — two-tier, for cost:

- **Heuristic tier (free, ~95% of writes):** a table of base importance per event kind,
  modulated by `|Δwealth| / wealth`, relationship strength to the subject, and novelty.
- **LLM tier (`IMPORTANCE` purpose, cheap model):** used only for `reflection` and for
  observations whose heuristic score lands in an ambiguous band `[0.4, 0.7]`. Batched — one
  call scores up to 20 candidate memories.

### 6.3 Retrieval

```
score(m) = w_r · recency(m) + w_i · importance(m) + w_v · relevance(m, query)

recency(m)   = decay_rate ^ (ticks_since_last_access / ticks_per_sim_day)   # default 0.995
importance(m)= m.importance
relevance(m) = cosine(embed(query), m.embedding)
```

Defaults `w_r = w_i = w_v = 1.0`, all three normalised to `[0,1]` across the candidate set
before weighting. Retrieval returns the top `k` (default 12) under a token cap.

**Two-stage retrieval for cost:** an ANN query over `memories.embedding` (HNSW index) fetches
the top 100 by relevance, then the full three-term score reranks those 100. Full-table
scoring at 8M memories is not viable and is not needed.

`last_accessed_tick` and `access_count` are updated on retrieval — memories that get used
stay fresh. This is what produces path-dependent identity: an agent who once thought a lot
about a betrayal keeps thinking about it.

### 6.4 Reflection

Triggered when either:

- the sum of importance of memories since the last reflection exceeds `reflection_threshold`
  (default 4.0), **or**
- a `LIFE_EVENT` kind fires (death of kin, job loss, bankruptcy, conviction, first child).

Procedure (Park et al., adapted):

1. Retrieve the 40 most recent memories.
2. Ask the model (`REFLECT` purpose): *"What are the 3 most salient high-level questions
   about this person's situation?"*
3. For each question, retrieve the top 12 relevant memories and ask for **1–3 insights,
   each citing the memory IDs that support it.**
4. Write each insight as a `reflection` memory with `parent_memory_ids` set. Citations are
   validated — an insight citing a memory the agent doesn't have is dropped.
5. Update `beliefs` and `goals` from the same call's structured output.

Step 4's citation validation matters: it is the difference between a reflection tree you
can audit (G6) and a pile of plausible sentences.

### 6.5 Forgetting and death

- Hard cap `max_memories_per_agent` (default 3,000). On overflow, evict the lowest
  `0.6·recency + 0.4·importance`. Reflections get a ×1.5 protection multiplier; `plan`
  memories tied to an active goal are never evicted.
- On death: all memories are marked `archived = TRUE`. Archived memories are invisible to
  living agents but remain queryable by reporters (writing an obituary), courts (evidence),
  and researchers. The dead leave a record.

---

## 7. Salience and cognition routing (PHASE 2)

The core cost-control mechanism. Everything about it is logged because it is a systematic
treatment assignment (threat T8).

```
salience(agent) =
      w_surprise  · surprise
    + w_stakes    · stakes
    + w_novelty   · novelty
    + w_social    · social
    + w_scheduled · scheduled
    + ε                                    # ε ~ U(0, exploration_epsilon)
```

| Term | Definition |
|---|---|
| `surprise` | Normalised distance between the observation digest and the agent's expectation, computed as `1 - jaccard(current_digest_features, ewma_digest_features)`. Cheap and effective: a day like every other day scores ~0. |
| `stakes` | `max` over: `|Δwealth| / (wealth + floor)`, health delta, employment status change, relationship status change, legal jeopardy. Scaled by `(1 + neuroticism)`. |
| `novelty` | 1 if the `(place_type, situation_type)` pair is unseen in the agent's history, decaying with repetition. Scaled by `openness`. |
| `social` | 1 if directly addressed (DM, offer, accusation, proposal, mention); 0.4 if a strong-tie is present and something happened to them; else 0. |
| `scheduled` | 1 on obligation ticks: loan due, election day, exam, court date, market open with resting orders, board meeting. |

**Routing:**

```
1. Compute salience for every awake agent.
2. Force-route: agents with a scheduled obligation of class MANDATORY → DELIBERATE
   (before budget allocation; these always get cognition).
3. Force-route: agents meeting reflection triggers → REFLECT.
4. Rank the remainder descending. Allocate DELIBERATE top-down until either
   calls_per_tick or tokens_per_tick is exhausted.
5. Everyone else → REFLEX.
6. External agents → always DELIBERATE, from a separate budget line.
```

**Logged per tick:** the cutoff score, the number routed to each mode, and the full
salience component breakdown for every agent that crossed the line (kind 4002). The control
condition `salience.policy: random` routes the same *number* of agents uniformly at random,
which isolates the effect of the routing policy itself.

---

## 8. Reflex mode

A deterministic utility policy. No LLM, no network, no randomness beyond the RNG registry.

```python
def reflex_decide(obs: Observation, profile: ReflexProfile, rng: Random) -> Action:
    candidates = legal_actions(obs)                  # from the place + state
    scored = [(a, utility(a, obs, profile)) for a in candidates]
    return softmax_sample(scored, temperature=profile.temperature, rng=rng)
```

**The reflex action set is deliberately narrow** (mitigating threat T9). Reflex may only
produce:

```
MOVE_TO · IDLE · SLEEP · EAT · WORK · STUDY ·
BUY_GOOD (necessities only, at posted price, below a value cap) ·
SAY (from a small template set, to a co-located strong tie) ·
REPAY_LOAN (scheduled amount) · NULL_ACTION
```

**Everything with a counterparty, a negotiated price, or a commitment is LLM-only.** Job
applications, offers, resignations, trades, loans, pitches, votes, crimes, posts, and
lawsuits can never come from reflex. If the reflex policy could do them, the "LLM society"
claim would be hollow.

`ReflexProfile` is derived from traits at birth (deterministic function, no learning in
v1). Utility weights map needs to actions: hungry → eat, tired → sleep, work hours + employed
→ commute and work, etc.

**External agents that miss their deadline fall back to this exact policy**, so a slow
foreign agent behaves like a distracted citizen rather than a statue.

---

## 9. Deliberate mode

### 9.1 Prompt structure

Templates live in `prompts/` as versioned Jinja2 files, hashed into `runs.prompt_manifest`.

```
SYSTEM
  You are {{name}}, {{age}}, living in {{city}}.
  {{trait_narrative}}                      # traits rendered as prose, not numbers
  {{identity_summary}}                     # rolling self-narrative from REFLECT
  Your current goals: {{goals}}

USER
  ## Right now
  {{sim_time}} — you are at {{place}}. {{place_description}}
  {{needs_narrative}}   Money: {{wealth}}. {{employment_line}}
  ## People here
  {{co_located}}
  ## Waiting for you
  {{offers}} {{obligations}} {{inbox}}
  ## What you've been seeing
  {{feed}} {{news}}
  ## What comes to mind
  {{retrieved_memories}}                   # top-k from §6.3
  ## What you can do
  {{legal_actions_with_schemas}}           # the closed action set, filtered to legal ones

  Choose ONE action. Respond with JSON matching the schema.
```

**Rules:**

- The word "simulation", "agent", "AI", "model", and "game" never appear in any prompt.
  (Threat T3. The `disclose_simulation: true` ablation exists solely to test the effect.)
- Traits are rendered as **narrative**, never as numbers. `"You are cautious with money and
  slow to trust"`, not `"risk_tolerance: 0.2"`. Numbers invite the model to optimise the
  number.
- Legal actions are presented **with their parameter schemas**, so the model chooses among
  and parameterises real options rather than inventing one.
- Total prompt is capped at `max_prompt_tokens` (default 3,000). The retrieval `k` and feed
  slice shrink to fit, in that order.

### 9.2 Output schema

```json
{
  "reasoning": "string, 1-3 sentences, why",
  "action": {"type": "APPLY_FOR_JOB", "params": {"vacancy_id": "…"}},
  "speech": "string | null",
  "belief_updates": [{"proposition": "…", "value": 0.3, "confidence": 0.6}],
  "goal_updates": {"add": ["…"], "complete": ["…"], "drop": ["…"]}
}
```

Enforced with JSON-schema-constrained decoding where the provider supports it, and a repair
loop (max 2 retries with the validation error appended) where it doesn't. After 2 failed
repairs: fall back to reflex, emit `LLM_CALL_FAILED`, count it. Parse failure rate per model
is a reported run statistic — it is how you find out that a cheap model can't hold the
schema.

`reasoning` is stored verbatim in the action and never parsed by code (`02-ARCHITECTURE.md §6.1`).

### 9.3 Batching

Deliberate calls for a tick are issued concurrently (`asyncio.gather`, bounded by
`llm.max_concurrency`, default 32) and results reordered by `actor_id` before application.
Prompt prefixes are ordered so the stable system portion comes first, maximising provider-
side prefix caching.

---

## 10. Reflect mode

One call, `REFLECT` purpose, higher temperature. Outputs:

```json
{
  "insights": [{"statement": "…", "supported_by": [memory_ids], "importance": 0.0-1.0}],
  "identity_summary": "2-4 sentences: who am I now, what do I want",
  "belief_updates": [...],
  "goal_stack": ["…", "…"]
}
```

`identity_summary` is written back to the agent and injected into every future deliberate
prompt. This is the cheapest available mechanism for long-horizon character continuity —
the agent's self-concept is a compressed, persistent, LLM-authored artefact rather than a
re-derivation from raw memory every tick.

---

## 11. Action validation (PHASE 4)

Every action passes five gates in order. The first failure rejects.

| Gate | Check | On failure |
|---|---|---|
| **Schema** | `params` validates against the type's pydantic model | `ACTION_REJECTED{reason: "schema"}` |
| **Capability** | Does this actor have standing? (only a firm owner posts a vacancy; only a licensed lawyer files) | `reason: "capability"` |
| **Locality** | Is the actor physically able? (must be at the exchange, or hold a brokerage account, to trade) | `reason: "locality"` |
| **Resources** | Funds, shares, inventory, action slots, time | `reason: "resources"` |
| **Legality** | Is it a crime? — **does not reject.** Flags the action, records a `crimes` row, and lets it proceed. | proceeds, flagged |

The legality gate is deliberately permissive: crime must be *possible* for B5 (deterrence)
to be studiable. Enforcement happens downstream in `polis/society/law.py` with a detection
probability, not by making the action impossible.

Rejections are visible to the agent in the next tick's `Observation`, so agents can learn
what doesn't work.

---

## 12. Lifecycle

### 12.1 Birth

Triggered by the fertility hazard in PHASE 8 (`07-SOCIETY-SPEC.md` / `demography`).

```
AGENT_BORN emits with:
  traits          ← §2.1 inheritance
  belief priors   ← blend(mother.beliefs, father.beliefs) × heritability_beliefs
                     + population prior × (1 - heritability_beliefs), with noise
  household       ← parents' household
  wealth          ← 0 (children hold no assets; the household provides)
  skills          ← all 0
  home_place      ← household home
  memories        ← empty
```

Inheriting **belief priors** as well as traits is what makes B6 answerable and is a
deliberate design choice: cultural transmission is the phenomenon of interest, so it must
be an explicit, parameterised channel rather than an accident of prompt design.

### 12.2 Ageing and stages

| Stage | Age (sim-years) | Constraints |
|---|---|---|
| Infant | 0–5 | No actions except needs. Perception builds but salience is floored. Fully dependent. |
| Child | 6–11 | Primary school mandatory. Limited action set. Small social graph. |
| Adolescent | 12–17 | Secondary school. Can work part-time. Full social/media access. Peer effects strongest. |
| Adult | 18–64 | Full action set. |
| Elder | 65+ | Retirement eligible. Rising mortality hazard. Skill decay accelerates. |

Ages advance by `demographic_acceleration × elapsed_sim_time`.

### 12.3 Death

`mortality_hazard(age, health, wealth_percentile, district.crime_rate)` — Gompertz–Makeham
baseline modulated by health, with a socioeconomic gradient. Declared `MECHANISM`; the
gradient's magnitude is a swept parameter, since "does the model reproduce the wealth–
mortality gradient without being told to" is itself a Track A question.

**Death settlement is a transaction, not a flag.** On `AGENT_DIED`, in one atomic step:

1. Cancel all resting exchange orders; release reserved funds and shares.
2. Terminate employment → `FIRED{reason: death}` → firm gets a vacancy.
3. Close positions per the will, or liquidate if intestate and heirs want cash.
4. Settle debts against the estate. Shortfall → creditor write-off (a real loss on the
   lender's balance sheet, which is how death propagates into the credit system).
5. Distribute the residual estate to heirs via balanced ledger legs. No heirs → escheat to
   government.
6. Vacate housing; dissolve or restructure the household; reassign dependants.
7. Archive memories; mark relationships `ended`; bereave strong ties (health and `social`
   need hit, elevated salience for several ticks).
8. Emit an obituary-eligible event that reporter agents may pick up.

**`INV-MONEY` must hold across the death transaction.** This is the single most common
place for accounting closure to break, and it is worth an integration test of its own
(`tests/invariants/test_death_settlement.py`).

---

## 13. Prompt asset discipline

| Rule | Reason |
|---|---|
| Prompts live in `prompts/`, never inline in Python | Versioning, hashing, paraphrase ablation (V6) |
| Every template has a `# version:` header and is hashed into `runs.prompt_manifest` | Reproducibility tuple |
| Every template has a `paraphrase/` sibling used by the V6 gate | Robustness testing is not optional |
| No template may name a provider or model | G7 model-agnosticism |
| No template may reveal simulation status | T3 |

---

## 14. Open calibration questions (resolve in M1)

These are listed in `01-PRD.md §11` and are called out here because M1's chunks own them:

1. What salience cutoff yields ~7% deliberate rate with behaviour that a reader finds
   sensible? Calibrate empirically on a 2,000-tick run; do not guess.
2. Is trait-conditioned prompt variation enough to pass V4 (behavioural entropy), or do we
   need per-agent template variation?
3. Are global retrieval weights (`w_r`, `w_i`, `w_v`) adequate, or do they need per-agent
   variation?
4. Is reflection better scheduled or purely event-triggered?

Each should be answered with a small sweep and the result written back into this document.

---

*Next: `05-WORLD-SPEC.md`.*
