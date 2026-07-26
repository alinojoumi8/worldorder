# C08 — Memory stream, retrieval, reflection, embeddings

**M1** · `polis/agents/memory/` · **Depends on:** C02 (events), C03 (store), C04 (kernel/rng), C05 (llm router), C07 (agent core) · **Blocks:** C09, C16, C17, C19, C20, C23a · **Size:** L

---

## 1. Context

An agent's identity is a function of what it remembers and what it has concluded from what
it remembers. This chunk is the whole of that: an append-only memory stream, a two-stage
retriever cheap enough to run at 8M rows, a reflection procedure that turns concrete
observations into abstract insights with **verifiable citations**, and a forgetting policy
that keeps the stream bounded. It is a direct descendant of Park et al.'s memory stream,
hardened for determinism and for cost. The citation-validation step in §9.5 is what
separates an auditable reflection tree (goal G6) from a pile of plausible sentences, and it
is the single most important behaviour in this chunk.

---

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/02-ARCHITECTURE.md` | **all** — §3.2 kinds, §3.3 cognition sampling, §4 determinism, §5 tick phases, §7.1 dependency rules |
| `../docs/03-DATA-MODEL.md` | **all** — §2.3 `memories`, §2.4 `beliefs`, §2.1 `agents`, §2.6 `relationships`, §11 storage |
| `../docs/04-AGENT-SPEC.md` | §6 in full (**primary source**), §5 perception, §10 reflect output, §12.3 death |
| `../docs/09-MODEL-ROUTING.md` | §3.1–3.2 purposes (`IMPORTANCE`, `EMBED`, `REFLECT`), §4.2 fallbacks, §4.6 budget, §5 cache |
| `../docs/07-SOCIETY-SPEC.md` | §5.1 proposition registry, §5.5 belief-update gates, §5.8 kinds 10060–10069 |
| Chunks | C02 (`Event`, `emit`), C03 (repositories), C04 (`RngRegistry`, `stable`), C05 (`Router`, `Purpose`), C07 (`AgentState`, `Observation`) |

---

## 3. Scope — in

1. `MemoryStream` — write policy, the `ALWAYS_REMEMBER` kind set, memory-type discipline.
2. Two-tier importance scoring: a heuristic table, plus batched `IMPORTANCE` calls for the
   ambiguous band, applied with a deterministic one-tick deferral.
3. `BatchedEmbedder` — `EMBED` purpose, 64:1 batching, content-addressed cache, 768-d assert.
4. `Retriever` — HNSW ANN top-100, then full `recency × importance × relevance` rerank with
   per-candidate-set normalisation, a token cap, and deterministic tie-breaking.
5. Access bookkeeping: `last_accessed_tick`, `access_count`, buffered to PHASE 6.
6. `ReflectionEngine` — trigger detection, context building, **citation validation**,
   `reflection` memory writes with `parent_memory_ids`.
7. Belief forwarding (via a `BeliefWriter` protocol) and goal-stack application, for both the
   REFLECT and DELIBERATE paths.
8. `Forgetter` — eviction score, reflection protection, active-plan protection.
9. `MemoryArchive` — death archival and the archived-query surface for reporters, courts,
   and researchers.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| The `REFLECT` LLM call itself (prompt assembly, router invocation, repair, `identity_summary`) | **C09** |
| Salience scoring — you *consume* a salience float, you do not compute it | **C09** |
| Perception and `Observation` construction | **C07** |
| `beliefs` table writes, `PROPOSITION_REGISTRY`, kinds 10060–10069 | **C16/C17** (`polis.society.beliefs`) |
| The completion cache, budget admission, repair loop | **C05** |
| Death settlement (ledger, housing, employment); you only archive memories | **C20** |
| Memory-driven feed ranking or news sourcing | **C16/C17** |

---

## 5. Interfaces you provide

```python
# polis/agents/memory/types.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence

MemoryType = Literal["observation", "reflection", "plan", "semantic"]

@dataclass(frozen=True, slots=True)
class Memory:
    memory_id:          int
    agent_id:           str
    tick:               int
    type:               MemoryType
    text:               str
    importance:         float                 # 0..1
    embedding:          tuple[float, ...] | None
    source_event_seq:   int | None
    parent_memory_ids:  tuple[int, ...]
    subject_ids:        tuple[str, ...]
    last_accessed_tick: int
    access_count:       int
    archived:           bool

@dataclass(frozen=True, slots=True)
class MemoryDraft:
    agent_id:          str
    type:              MemoryType
    text:              str
    subject_ids:       tuple[str, ...] = ()
    source_event_seq:  int | None = None
    source_kind:       int | None = None
    parent_memory_ids: tuple[int, ...] = ()
    importance_hint:   float | None = None     # set by REFLECT; bypasses the heuristic tier

@dataclass(frozen=True, slots=True)
class ScoredMemory:
    memory:    Memory
    score:     float
    recency:   float          # normalised over the candidate set
    importance: float         # normalised over the candidate set
    relevance: float          # normalised over the candidate set

@dataclass(frozen=True, slots=True)
class ReflectContext:
    agent_id:        str
    tick:            int
    recent:          tuple[Memory, ...]                 # 40 most recent, newest first
    trigger:         Literal["accumulator", "life_event"]
    trigger_detail:  str
    accumulated:     float

@dataclass(frozen=True, slots=True)
class Insight:
    statement:    str
    supported_by: tuple[int, ...]
    importance:   float

@dataclass(frozen=True, slots=True)
class BeliefUpdate:
    proposition: str
    value:       float
    confidence:  float

@dataclass(frozen=True, slots=True)
class GoalUpdates:
    add:      tuple[str, ...] = ()
    complete: tuple[str, ...] = ()
    drop:     tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class ReflectOutput:
    insights:        tuple[Insight, ...]
    identity_summary: str
    belief_updates:  tuple[BeliefUpdate, ...]
    goal_stack:      tuple[str, ...]

@dataclass(frozen=True, slots=True)
class ReflectApplied:
    written_memory_ids: tuple[int, ...]
    dropped_insights:   tuple[tuple[str, str], ...]   # (statement, reason)
    beliefs_forwarded:  int
    goals_after:        tuple[str, ...]
```

```python
# polis/agents/memory/protocols.py
class BeliefWriter(Protocol):
    """Implemented by polis.society.beliefs (C16/C17). polis.agents NEVER imports society;
    the concrete writer is injected at the composition root (polis.research / polis.cli)."""
    def apply_llm_belief_updates(
        self, agent_id: str, tick: int,
        updates: Sequence[BeliefUpdate],
        llm_call_id: str | None,
    ) -> int:
        """Runs the 07 §5.5 gates, writes `beliefs`, emits 10060/10062.
        Returns the number applied. MUST be safe to call with an empty sequence."""

class NullBeliefWriter:
    """M1 default until C16 lands. Drops updates, counts them, emits nothing."""
```

```python
# polis/agents/memory/stream.py
class MemoryStream:
    def __init__(self, repo: MemoryRepository, importance: ImportanceScorer,
                 embedder: BatchedEmbedder, cfg: MemoryConfig, log: EventLog) -> None: ...

    def should_write(self, *, salience: float, source_kind: int | None) -> bool:
        """salience > cfg.threshold or source_kind in ALWAYS_REMEMBER."""

    def stage(self, draft: MemoryDraft, tick: int, salience: float) -> None:
        """Buffer a write. No I/O, no LLM. Callable from any phase."""

    def flush(self, tick: int) -> tuple[int, ...]:
        """PHASE 6 only. Assigns memory_ids in `stable()` order by
        (agent_id, type, text), scores heuristic importance, enqueues the ambiguous band
        for the tick+1 IMPORTANCE batch, enqueues embeddings, emits 4010. Returns new ids."""

    def resolve_deferred(self, tick: int) -> None:
        """PHASE 7, after router.flush_deferred(tick): apply the IMPORTANCE scores and the
        embeddings queued during PHASE 6. Idempotent per tick."""
```

```python
# polis/agents/memory/importance.py
class ImportanceScorer:
    def heuristic(self, draft: MemoryDraft, state: AgentState) -> float: ...
    def is_ambiguous(self, score: float) -> bool: ...
    async def score_batch(self, drafts: Sequence[MemoryDraft], tick: int) -> list[float]:
        """<= cfg.importance.llm_batch_size (20) per IMPORTANCE call.
        On SchemaRepairExhausted / budget DEGRADE: return the heuristic scores (09 §4.2)."""

# polis/agents/memory/embeddings.py
class BatchedEmbedder:
    async def embed(self, texts: Sequence[str], tick: int) -> list[tuple[float, ...]]:
        """Cache-first by sha256(normalise(text)); misses batched at cfg.embedding.batch_size.
        Asserts len(vec) == cfg.embedding.dim (768) on every miss. Order-preserving."""
    def cache_stats(self) -> Mapping[str, int]: ...

# polis/agents/memory/retrieval.py
class Retriever:
    async def retrieve(
        self, agent_id: str, query: str, tick: int,
        k: int | None = None, max_tokens: int | None = None,
        exclude_ids: frozenset[int] = frozenset(),
    ) -> tuple[ScoredMemory, ...]: ...

    async def retrieve_recent(
        self, agent_id: str, tick: int, n: int,
    ) -> tuple[Memory, ...]:
        """No embedding, no ANN. Used for the reflection window."""

    def pending_access_flush(self, tick: int) -> int:
        """PHASE 6 only. Applies buffered last_accessed_tick / access_count. Returns rows."""

# polis/agents/memory/reflection.py
class ReflectionEngine:
    def is_triggered(self, agent_id: str, tick: int) -> tuple[bool, str, str]:
        """-> (triggered, trigger_kind, detail). Pure; safe to call in PHASE 2."""
    def note_life_event(self, agent_id: str, kind: int, seq: int) -> None: ...
    async def build_context(self, agent_id: str, tick: int) -> ReflectContext: ...
    async def memories_for_question(
        self, agent_id: str, question: str, tick: int,
    ) -> tuple[ScoredMemory, ...]:
        """Top cfg.reflection.per_question_k (12) by the full three-term score."""
    def validate_citations(
        self, agent_id: str, insights: Sequence[Insight], shown_ids: frozenset[int],
    ) -> tuple[tuple[Insight, ...], tuple[tuple[str, str], ...]]:
        """-> (kept, dropped[(statement, reason)]). See §9.5. Pure and synchronous."""
    def apply_output(
        self, agent_id: str, tick: int, out: ReflectOutput,
        shown_ids: frozenset[int], llm_call_id: str | None,
    ) -> ReflectApplied: ...

# polis/agents/memory/goals.py
class GoalStack:
    def apply(self, agent_id: str, tick: int, updates: GoalUpdates,
              llm_call_id: str | None) -> tuple[str, ...]:
        """Single implementation for BOTH the DELIBERATE and REFLECT paths. Emits 4023."""
    def replace(self, agent_id: str, tick: int, stack: Sequence[str],
                llm_call_id: str | None) -> tuple[str, ...]: ...
    def active(self, agent_id: str) -> tuple[str, ...]: ...

# polis/agents/memory/forgetting.py
class Forgetter:
    def evict_if_over_cap(self, agent_id: str, tick: int) -> int:
        """PHASE 6, after flush(). Returns rows evicted. Emits 4013."""

# polis/agents/memory/archive.py
class MemoryArchive:
    def archive_agent(self, agent_id: str, tick: int) -> int:
        """Sets archived = TRUE for every memory. Emits 4014. Idempotent."""
    def query_archived(self, *, subject_id: str | None = None,
                       agent_id: str | None = None,
                       tick_range: tuple[int, int] | None = None,
                       limit: int = 100) -> tuple[Memory, ...]: ...
```

---

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C02 | `EventLog.emit(kind, actor_id, subject_ids, cause_seq, payload)` | 4010–4029 |
| C02 | `polis.events.kinds` constants | `ALWAYS_REMEMBER`, `LIFE_EVENT` sets |
| C03 | `MemoryRepository` (`insert_many`, `by_agent_recent`, `ann_search`, `update_access`, `delete_ids`, `set_archived`) | all persistence |
| C03 | `RelationshipRepository.strength(a, b)` | importance tie term |
| C04 | `RngRegistry.get(ns, entity_id, tick)`, `polis.kernel.det.stable` | sampling, ordering |
| C05 | `LLMRouter.call(purpose, agent_id, tick, variables, schema_name=None, *, deferred=False) -> CallResult` | `IMPORTANCE`, issued with `deferred=True` |
| C05 | `LLMRouter.flush_deferred(tick) -> list[CallResult]` | PHASE 7 await |
| C05 | `LLMRouter.embed(texts, *, tick, owner_id="") -> list[list[float]]` | `EMBED`; pass `owner_id=""` so the key is text-only |
| C05 | `CallResult.parsed`, `.call_id`, `.degraded`, `.parsed_ok` | result handling |
| C07 | `AgentState` (traits, needs, `wealth_cents`, `goals`, `stage`), `Observation` | importance, gating |
| — | `BeliefWriter` (injected) | belief forwarding |

> **Coordination item for C05.** C05's shipped signature has no way to select among several
> templates for one purpose, and `REFLECT` needs two (`questions`, `insights`). Add
> `template: str | None = None` to `LLMRouter.call` and `CallRequest`, defaulting to the
> `RouteSpec`'s template. C09 needs the same thing. **Raise it jointly with C09; do not
> inline a prompt in Python (`04 §13`) and do not smuggle the selection through
> `schema_name`.**

---

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `memories` | **RW** | Insert, update `importance`/`embedding`/`last_accessed_tick`/`access_count`, `DELETE` on eviction, `archived = TRUE` on death |
| `agents` | R + W(`goals`) | Traits/wealth/stage for importance; `goals` written by `GoalStack` |
| `relationships` | R | `strength` for the importance tie term |
| `events` | W | via C02 only |
| `beliefs` | **never directly** | through `BeliefWriter` |

**Declared deviations, both additive and both to be reported in the handback:**

1. **Eviction is a hard `DELETE`**, against `03 §0`'s soft-delete convention. At 1,000 agents
   × 3,000 memories the table is 3M rows resident; a soft flag makes the cap meaningless and
   `03 §11`'s 30 GB estimate wrong. Nothing is lost: `4010 MEMORY_WRITTEN` carries the full
   text and `4013 MEMORY_EVICTED` carries the decision, so a replay reconstructs both.
2. **`4010 MEMORY_WRITTEN` is exempt from the `02 §3.3` cognition-sampling policy.** That
   policy names perception digests and salience scores — diagnostics. `4010` is a state
   change and the only durable record of an evicted memory. Sampled kinds in this chunk are
   `4011` and `4012` only.

---

## 8. Event kinds owned

**Range: 4010–4029.** (C09 owns 4002–4009; C07 owns 4001; 4100–4199 is `polis.llm`.)

| Kind | Name | Payload | Persisted |
|---|---|---|---|
| 4010 | `MEMORY_WRITTEN` | `memory_id, agent_id, type, text, importance, importance_tier ∈ {heuristic,llm,reflect}, source_event_seq, source_kind, subject_ids, parent_memory_ids, always_remember (bool)` | always |
| 4011 | `MEMORY_IMPORTANCE_SCORED` | `agent_id, memory_ids[], heuristic[], llm[], llm_call_id, degraded (bool)` | sampled |
| 4012 | `MEMORY_RETRIEVED` | `agent_id, query_hash, k, returned_ids[], scores[], components[{r,i,v}], ann_candidates, token_cost` | sampled |
| 4013 | `MEMORY_EVICTED` | `agent_id, n_evicted, cutoff_score, sample_ids[] (≤50), protected_reflections, protected_plans` | always |
| 4014 | `MEMORIES_ARCHIVED` | `agent_id, n_archived, cause` | always |
| 4020 | `REFLECTION_PRODUCED` | `memory_id, agent_id, statement, parent_memory_ids[], importance, question, question_index, llm_call_id` | always |
| 4021 | `REFLECTION_TRIGGERED` | `agent_id, trigger ∈ {accumulator, life_event}, detail, accumulated, ticks_since_last` | always |
| 4022 | `REFLECTION_INSIGHT_DROPPED` | `agent_id, statement, reason ∈ {no_citations, unknown_memory, foreign_memory, archived_memory, not_shown, duplicate}, cited_ids[], llm_call_id` | always |
| 4023 | `GOAL_STACK_UPDATED` | `agent_id, before[], after[], added[], completed[], dropped[], origin ∈ {deliberate, reflect}, llm_call_id` | always |

4024–4029 reserved, unused.

---

## 9. Implementation notes

### 9.1 Write policy

```
write iff  salience > memory.threshold (0.25)  OR  source_kind ∈ ALWAYS_REMEMBER
```

`ALWAYS_REMEMBER` is a `Final[frozenset[int]]` in `policy.py`. At M1 it holds the kinds that
exist: `2001 AGENT_BORN`, `2002 AGENT_DIED`, `5010 HIRED`, `5011 FIRED`, `5012 QUIT`,
`9030 BANKRUPTCY_FILED`, `13040 JUDGMENT_RENDERED`, `15010 HOUSEHOLD_FORMED`. Later chunks
(C18 election results, C20 union formation) **edit this one frozenset**. Do not build a
registration API for it — one file, one list, per `02 §1.8`.

`LIFE_EVENT` (reflection trigger) is a separate, smaller frozenset: own `5011`, own `9030`,
own conviction `13040`, kin `2002`, first-child `2001`. "Own"/"kin" is a predicate over
`actor_id`/`subject_ids`, not a property of the kind.

`@mechanism("memory.write_threshold", entails="Only events above a salience threshold enter memory. Therefore agents are structurally incapable of recalling routine experience, and any finding that agents over-weight dramatic events is partly entailed. Ablate with --memory-threshold 0.")`

### 9.2 Two-tier importance

```
base   = IMPORTANCE_BASE.get(source_kind, cfg.importance.default_base)     # 0.30
wealth = min(abs(d_wealth_cents) / (wealth_cents + cfg.importance.floor_cents), 1.0)
tie    = max((relationship_strength(agent, s) for s in subject_ids), default=0.0)
novel  = 1.0 / (1.0 + prior_count_of_kind_for_agent)
score  = clip(base + 0.30*wealth + 0.20*tie + 0.10*novel, 0.0, 1.0)
```

`IMPORTANCE_BASE` is a literal table keyed by kind (birth/death 0.95, fired/hired 0.85,
bankruptcy 0.85, verdict 0.80, loan originated 0.55, trade executed 0.40, goods purchased
0.15, moved 0.05, …). Use `.get()` with a default — kinds from unimplemented chunks must not
raise.

**Ambiguous band `[0.4, 0.7]`** → queued for a batched `IMPORTANCE` call of up to 20 drafts.
`IMPORTANCE` is an ancillary purpose and is deferred off the critical path (`09 §4.4`) using
C05's mechanism, not a hand-rolled one:

- PHASE 6: `flush()` writes the row at the **heuristic** score with
  `importance_tier = "heuristic"`, and issues `router.call(IMPORTANCE, ..., deferred=True)`.
- PHASE 7: the kernel calls `router.flush_deferred(tick)`; `resolve_deferred(tick)` applies
  the returned scores, emits `4011`, and rewrites `importance_tier = "llm"`.

The deferral is deterministic (a fixed phase, never opportunistic). Two consequences to
record: retrieval and eviction *within* the tick use the provisional score, and if the kernel
commits events only in PHASE 6, `4011` lands in tick+1's batch. Both are bounded and
reproducible; neither is a bug.

**Reflections do not use the `IMPORTANCE` purpose.** The `REFLECT` output already carries a
per-insight `importance` (`04 §10`); use it (clamped to `[0,1]`) via `MemoryDraft.importance_hint`
and set `importance_tier = "reflect"`. This satisfies `04 §6.2`'s intent at zero marginal cost.
**Record this decision in the handback.**

`@mechanism("memory.importance_heuristic", entails="Importance is a fixed function of event kind, wealth delta, and tie strength. Therefore the ordering of what agents retain is imposed, not learned, and any result about which experiences shape behaviour is conditioned on this table.")`

### 9.3 Embeddings

`EMBED` on the free line, local lane, ~400 calls/tick. Cache key is `sha256(normalise(text))`
where `normalise` lowercases, collapses whitespace, and strips trailing punctuation — memory
text repeats heavily across agents ("Acme laid off 20 people"), so the hit rate is high and
worth having. Batch misses at 64. Assert `len(vec) == 768` on every miss and fail hard on
mismatch: a dimension change is a schema migration (`09 §2.4`), not a config change.

A memory written at tick *t* has `embedding = NULL` until `resolve_deferred(t)` runs. Rows
with a NULL embedding are **invisible to the ANN stage** but visible to `retrieve_recent`.
Track `memory.null_embedding_count` as a metric; a value that grows monotonically means the
backfill is silently failing and retrieval has quietly become recency-only.

### 9.4 Two-stage retrieval

```sql
-- Stage 1: ANN, per agent, in the WHERE clause (never post-filter).
SET LOCAL hnsw.ef_search = :ef_search;          -- default 120, >= 2 * ann_k / 2
SELECT memory_id, 1 - (embedding <=> :q) AS relevance, ...
  FROM memories
 WHERE run_id = :run AND agent_id = :agent AND NOT archived AND embedding IS NOT NULL
 ORDER BY embedding <=> :q
 LIMIT :ann_k;                                  -- 100
```

`<=>` is cosine **distance**. `relevance = 1 - distance`. Getting this sign wrong returns the
100 *least* relevant memories and nothing downstream will tell you.

```python
# Stage 2: rerank the candidate set.
recency_raw = decay ** ((tick - m.last_accessed_tick) / ticks_per_sim_day)   # decay 0.995
r, i, v = minmax(recency_raw), minmax(importance), minmax(relevance)         # over candidates
score   = w_r*r + w_i*i + w_v*v
order   = sorted(candidates, key=lambda c: (-c.score, c.memory.memory_id))
```

- **Normalise each component to `[0,1]` across the candidate set before weighting.** Raw
  cosine sits in a narrow band (~0.6–0.9) and raw recency near 1.0; weighting first makes
  `w_r = w_i = w_v = 1.0` mean something entirely different from what `04 §6.3` says.
- `minmax` with `max == min` returns `1.0` for every element. This is common early in a run.
- Token cap: accumulate `ceil(len(text) / 3.5) + 8` per memory; stop before exceeding
  `max_tokens` (600). Under-estimating tokens here is how the `max_prompt_tokens` cap gets
  breached in C09.
- Tie-break by `memory_id` ascending. Never rely on the DB's row order.

**Access bookkeeping.** Retrieval runs in PHASE 3, which must not mutate state (two
retrievals for the same agent in one tick must see identical recency). Returned ids are
buffered in `_pending_access: dict[int, int]` and applied by `pending_access_flush()` in
PHASE 6 as one batched `UPDATE ... FROM (VALUES ...)`. Candidates that were scored but not
returned are **not** touched.

### 9.5 Reflection

**Trigger.** Per agent, `_accumulator[agent] += importance` for every memory written
**except** those with `type == "reflection"`. Fires when
`_accumulator > cfg.reflection.threshold` (4.0) **or** a `LIFE_EVENT` was noted since the
last check. On fire: emit `4021`, reset the accumulator to 0.0, and set
`_cooldown_until[agent] = tick + cfg.reflection.min_gap_ticks` (default 24). Without both the
reflection-exclusion and the cooldown, reflections feed the accumulator that triggers
reflections and the agent reflects every tick until the budget dies.

**Procedure** (`04 §6.4`, implemented literally, split across C08 and C09):

| # | Step | Owner |
|---|---|---|
| 1 | `build_context` → 40 most recent memories | C08 |
| 2 | `REFLECT` call, template `questions` → up to `max_questions` (3) questions | **C09** |
| 3a | `memories_for_question(q)` → top 12 by the full three-term score | C08 |
| 3b | `REFLECT` call, template `insights` → 1–3 insights per question, each citing memory ids | **C09** |
| 4 | `validate_citations` then write `reflection` memories | C08 |
| 5 | Belief + goal updates | C08 |
| 5b | `identity_summary` write-back | **C09** |

Cost: `1 + Q` calls per reflection, ~3,000 tokens each. At `reflection.max_questions: 1` this
collapses to 2 calls — that is the cost lever, and `09 §3.2`'s ~10 `REFLECT` calls/tick
corresponds to ~2.5 reflections/tick at the default.

**Citation validation — the load-bearing part.** For each insight, every id in
`supported_by` must satisfy **all** of:

| Check | Drop reason |
|---|---|
| `supported_by` is non-empty | `no_citations` |
| The id exists in `memories` | `unknown_memory` |
| `memories.agent_id == self` | `foreign_memory` |
| `NOT archived` | `archived_memory` |
| The id is in `shown_ids` — the exact set rendered into *that question's* prompt | `not_shown` |
| The statement is not byte-identical to an insight already kept this reflection | `duplicate` |

**A single failing citation drops the entire insight**, not just the citation. An insight with
its bad citations stripped is indistinguishable from an insight that was never grounded, and
the whole point of `parent_memory_ids` is that the provenance chain is complete. Every drop
emits `4022`. Report `memory.reflection.citation_drop_rate` per run; above 0.20 the reflection
tree is not trustworthy and the run's qualitative claims must say so.

Kept insights are written as `type="reflection"` with `parent_memory_ids` = the validated ids
and `subject_ids` = the union of the cited memories' subjects. Emit `4020` per insight.

**Belief updates** go to `BeliefWriter.apply_llm_belief_updates()`. C08 does **not** validate
propositions, clamp values, or emit 10060 — all of that is `07 §5.5` and belongs to the belief
owner. C08's only job is to forward them and count. Under `NullBeliefWriter` (M1) they are
dropped and counted; that is expected and must not raise.

**Goal updates** go to `GoalStack.replace()` for REFLECT (`goal_stack` is a full replacement)
and `GoalStack.apply()` for DELIBERATE (`goal_updates` is a delta). Cap the stack at
`cfg.goals.max_stack` (7), dedupe on exact string, preserve insertion order, emit `4023`.

### 9.6 Forgetting

```
eviction_score(m) = 0.6 * recency_norm(m) + 0.4 * m.importance
                  * (1.5 if m.type == "reflection" else 1.0)
never evict: m.type == "plan" and m.text matches an entry in GoalStack.active(agent)
```

Runs once per agent per tick in PHASE 6, **after** `flush()`, never mid-phase. Evicting
between a reflection's retrieval and its write makes citation validation fail spuriously.
Evict ascending by `(score, memory_id)` until at `max_per_agent`. Emit one `4013` per agent
per pass, with at most 50 sample ids. Memories written this tick are still at their
provisional heuristic importance when eviction runs; that is accepted, and it is why the
ambiguous band is narrow.

`@mechanism("memory.forgetting_policy", entails="Memories are evicted by a fixed recency/importance blend with reflections protected 1.5x. Therefore abstractions persist longer than the observations that produced them, and any finding that agents' beliefs become detached from evidence over time is partly entailed. Ablate with --memory-cap 0 (no eviction).")`

### 9.7 Death

On `2002 AGENT_DIED` (PHASE 8, called by C07 at M1 and C20 from M5):
`archive_agent()` sets `archived = TRUE` for every memory and emits `4014`. Archived memories
are excluded from every living-agent retrieval path by the `NOT archived` predicate in the
stage-1 query. They remain reachable through `MemoryArchive.query_archived()`, which C17
(obituaries), C19 (evidence), and C24 (research export) use. Archival is idempotent.

### 9.8 Determinism checklist

- Draft flush order: `stable(drafts, key=lambda d: (d.agent_id, d.type, d.text))`.
- Embedding batches: sort by memory_id before zipping vectors back; a reordered batch attaches
  the wrong vector to the wrong memory and the failure is silent.
- IMPORTANCE batches: partition deterministically by sorted memory_id, not by dict iteration.
- Every RNG draw declares a namespace: `rng.get("memory.sample", agent_id, tick)` for the
  `4012` sampling decision.
- No `set` iteration reaches a payload; convert to a sorted tuple.

---

## 10. Configuration keys

```yaml
memory:
  threshold: 0.25                     # MECHANISM memory.write_threshold
  max_per_agent: 3000
  importance:
    default_base: 0.30
    ambiguous_band: [0.40, 0.70]
    llm_tier: true                    # false => heuristic only, the ablation arm
    llm_batch_size: 20
    floor_cents: 100_000              # wealth denominator floor
  retrieval:
    k: 12
    ann_k: 100
    ef_search: 120
    weights: {recency: 1.0, importance: 1.0, relevance: 1.0}
    decay_rate: 0.995
    max_tokens: 600
  embedding:
    dim: 768                          # MUST equal memories.embedding vector(768)
    batch_size: 64
    cache_entries: 100_000
  reflection:
    threshold: 4.0
    recent_window: 40
    max_questions: 3
    per_question_k: 12
    max_insights_per_question: 3
    min_gap_ticks: 24
  forgetting:
    eviction_weights: {recency: 0.6, importance: 0.4}
    reflection_protection: 1.5
  goals:
    max_stack: 7
  sample_rate: 0.02                   # for 4011/4012, matches cognition_sample_rate
```

---

## 11. Acceptance criteria

1. A memory is written iff `salience > threshold` or `source_kind ∈ ALWAYS_REMEMBER`; both
   branches are exercised and an `ALWAYS_REMEMBER` event at salience 0.0 is written.
2. Heuristic importance is deterministic and bounded to `[0,1]` for every kind in the table
   and for an unknown kind.
3. A draft in the ambiguous band is written in PHASE 6 with `importance_tier = "heuristic"`
   and updated in PHASE 7 with `importance_tier = "llm"`, emitting exactly one `4011`.
4. With `importance.llm_tier: false`, or when the `IMPORTANCE` call raises, the heuristic
   score stands and no exception escapes.
5. `BatchedEmbedder` issues `ceil(unique_misses / 64)` calls, returns vectors in input order,
   and raises on a dimension other than 768.
6. Retrieval returns at most `k`, never exceeds `max_tokens`, and its three components are
   each in `[0,1]` after normalisation.
7. Retrieval with an all-identical candidate set does not divide by zero and returns a
   `memory_id`-ascending order.
8. `last_accessed_tick`/`access_count` change only after `pending_access_flush()`, and two
   retrievals in the same tick return identical `recency` values.
9. A retrieved memory's recency is strictly greater on a later tick than an equally-old
   memory that was never retrieved.
10. An insight citing a memory belonging to a **different agent** is dropped with reason
    `foreign_memory` and emits `4022`.
11. An insight citing a real, self-owned memory that was **not in that question's prompt** is
    dropped with reason `not_shown`.
12. An insight with zero citations is dropped.
13. Kept insights are written with `parent_memory_ids` exactly equal to the validated
    citation set, and `4020` is emitted per insight.
14. Reflection does not retrigger on the tick after it fires (accumulator reset + cooldown),
    and `reflection` memories never increment the accumulator.
15. Belief updates from REFLECT reach `BeliefWriter` unmodified; with `NullBeliefWriter` the
    call is a no-op and nothing raises.
16. At `max_per_agent + 1` memories, exactly one is evicted; a `plan` memory matching an
    active goal is never evicted even when it is the lowest-scoring row.
17. After `archive_agent`, no living-agent retrieval returns any of that agent's memories, and
    `query_archived` returns all of them.
18. Two runs at the same seed against `StubProvider` produce byte-identical `memories` tables
    and identical 4010–4023 payload sequences.
19. `mypy --strict polis/agents/memory` and `import-linter` pass; `polis.agents.memory`
    imports nothing from `polis.society` or `polis.economy`.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/memory/test_write_policy.py` | Threshold branch, `ALWAYS_REMEMBER` branch, unknown kind default, mechanism tag present |
| `tests/unit/memory/test_importance_heuristic.py` | Bounds, monotonicity in each term, unknown-kind default, zero-wealth division |
| `tests/unit/memory/test_importance_deferral.py` | Provisional (PHASE 6) → LLM rewrite (PHASE 7) via `flush_deferred`; one `4011`; degrade path keeps the heuristic; `resolve_deferred` idempotent |
| `tests/unit/memory/test_embedder.py` | Batch count, order preservation, cache hits, dim assertion raises, NULL-embedding rows excluded from ANN |
| `tests/unit/memory/test_retrieval_scoring.py` | Normalisation before weighting, `max == min` case, cosine sign (`1 - distance`), token cap, `memory_id` tie-break |
| `tests/unit/memory/test_retrieval_access.py` | No mutation during PHASE 3; flush applies; retrieved memory outranks an unretrieved coeval later |
| `tests/unit/memory/test_citation_validation.py` | **Six drop reasons, one test each**; whole-insight drop; `4022` payload; kept insights' `parent_memory_ids` |
| `tests/unit/memory/test_reflection_trigger.py` | Accumulator fires and resets; reflections excluded; cooldown; `LIFE_EVENT` path; `4021` |
| `tests/unit/memory/test_goal_stack.py` | add/complete/drop, cap, dedupe, `4023` payload for both origins |
| `tests/unit/memory/test_forgetting.py` | Eviction ordering, reflection ×1.5, active-plan protection, `4013` payload, runs only in PHASE 6 |
| `tests/unit/memory/test_archive.py` | Archival hides from retrieval, idempotent, `query_archived` returns them, `4014` |
| `tests/integration/test_memory_reflection_loop.py` | 200 ticks, 20 agents, `StubProvider`: reflections produced, every `parent_memory_ids` entry resolves to a self-owned memory, no runaway reflection |
| `tests/integration/test_memory_belief_forwarding.py` | REFLECT belief updates reach a recording fake `BeliefWriter`; `NullBeliefWriter` no-ops |
| `tests/determinism/test_memory_determinism.py` | Same seed twice → identical `memories` rows and identical event payload sequence |
| `tests/unit/memory/test_kind_registry.py` | Every kind 4010–4023 registered with a payload schema; none outside the range |

---

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis/agents/memory/` exports `MemoryStream`, `Retriever`, `ReflectionEngine`,
   `BatchedEmbedder`, `Forgetter`, `GoalStack`, `MemoryArchive`, `BeliefWriter`,
   `NullBeliefWriter` with exactly the signatures in §5.
2. Kinds 4010–4023 registered in `polis/events/kinds.py` with payload schemas.
3. The three `@mechanism` declarations exist with `entails` strings.
4. Handback records: the two declared deviations in §7; the reflection-importance decision in
   §9.2; the `LLMRouter.call(template=)` coordination item raised jointly with C09; the measured
   `citation_drop_rate` and `null_embedding_count` on the 200-tick integration run.
5. A one-paragraph note on `04 §14` open question 3 (are global `w_r/w_i/w_v` adequate) with
   whatever the integration run showed.

---

## 14. Traps

1. **Recency from `tick`, not `last_accessed_tick`.** The formula in `04 §6.3` uses
   `ticks_since_last_access`. Using creation tick makes recency a pure function of age, kills
   the reinforcement loop, and deletes the path-dependent-identity property the whole design
   exists for. This is the easiest mistake in the chunk and the hardest to notice.
2. **Weighting before normalising.** Raw relevance lives in ~[0.6, 0.9] and raw recency in
   ~[0.95, 1.0]; sum them unnormalised and importance decides nothing. Normalise over the
   *candidate set*, not over the corpus, not over the returned set.
3. **Cosine sign flip.** pgvector `<=>` is distance. `ORDER BY embedding <=> q` is correct;
   `relevance = embedding <=> q` is catastrophic and silent.
4. **Post-filtering the ANN by `agent_id`.** Query the whole index and filter in Python and
   you get ~1 of your own memories in the top 100. `agent_id` must be in the `WHERE` clause,
   and `hnsw.ef_search` must be raised or recall under a selective filter collapses.
5. **Mutating access bookkeeping inside PHASE 3.** Makes retrieval order-dependent within a
   tick, breaks determinism, and breaks perception purity. Buffer, then flush in PHASE 6.
6. **Citation validation that only checks existence.** An insight citing memory 4711 that
   exists but belongs to another agent, or that was retrieved for a *different question*,
   must be dropped. Existence checks pass hallucinations; the `shown_ids` check is the one
   that actually works.
7. **Stripping bad citations instead of dropping the insight.** Produces reflections whose
   provenance is partial and unmarked. Auditability is all-or-nothing here.
8. **Reflection recursion.** Reflections are memories; memories feed the accumulator; the
   accumulator triggers reflection. Without excluding `type == "reflection"` **and** a
   cooldown, a single agent consumes the entire `REFLECT` budget for a hundred ticks.
9. **Eviction racing reflection.** Evicting between `memories_for_question` and
   `apply_output` makes a legitimate citation fail `unknown_memory`. Eviction is PHASE 6, once,
   after all writes.
10. **NULL embeddings that never get filled.** A dropped `resolve_deferred` call leaves rows
    permanently invisible to the ANN stage. Retrieval degrades to recency-only and still
    returns 12 plausible memories, so nothing looks broken. Metric it.
11. **Embedding batch reorder.** `router.embed` returns a list; zip it against an unsorted
    iterable and vectors attach to the wrong memories. Every agent then retrieves someone
    else's semantics. Sort by `memory_id` on both sides.
12. **Token estimate too optimistic.** `len(text) // 4` under-counts for short, punctuated,
    ID-heavy memory text. C09's prompt cap is computed from your number; be pessimistic
    (`/ 3.5`) or you cause `finish_reason == "length"` and a parse-failure spike two chunks away.
13. **`IMPORTANCE_BASE[kind]` with `[]` instead of `.get()`.** The table cannot list kinds
    from chunks that do not exist yet. A `KeyError` at C11 integration is a guaranteed outcome.
14. **Writing `beliefs` directly.** `polis.agents` importing `polis.society` fails
    `import-linter`, and duplicating the `07 §5.5` gates guarantees they diverge. Forward
    through the protocol even though it feels like indirection.
15. **Model-supplied insight importance of 1.0 for everything.** Clamp, and watch the
    distribution: if it collapses, reflections become uneviectable and the cap starves
    observations. Report the histogram.
16. **Soft-deleting evictions "to be safe".** The cap stops binding, `memories` grows without
    limit, and the 30 GB/sim-year estimate becomes 300 GB. If you want the record, it is
    already in `4010`.
17. **Archiving in PHASE 5.** Death settlement is PHASE 8. Archiving early hides memories from
    the dying agent's own last-tick reflection and from same-tick institution logic.
18. **Assuming `REFLECT` is one call.** It is `1 + Q`. Forgetting the per-question calls when
    reporting cost understates the reflect line by 3–4×, and C09's budget arithmetic depends
    on your number.
