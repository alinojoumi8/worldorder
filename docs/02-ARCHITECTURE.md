# POLIS — Architecture

**Version:** 1.0
**Status:** Normative. Every convention here is binding on all chunks.

> **Read this in full before implementing any chunk.** Naming, event kinds, module paths,
> determinism rules, and the tick contract are defined here and nowhere else. A chunk that
> violates this document is wrong even if its tests pass.

---

## 1. Design principles

1. **The log is the truth.** All state is a projection of an append-only event log. If it
   isn't in the log, it didn't happen. Adopted directly from Buzz.
2. **`kind` is the only dispatch switch.** A new capability is a new kind integer plus a
   payload schema. Existing readers ignore kinds they don't know and never break.
3. **Determinism is a property, not an aspiration.** Every source of nondeterminism —
   RNG, iteration order, concurrency, LLM sampling, wall-clock time — is explicitly
   channelled through a controlled mechanism. See §4.
4. **Simultaneous submission, deterministic resolution.** Agents never observe another
   agent's action from the same tick. Institutions resolve all submitted actions with a
   documented, order-independent rule.
5. **Protocol at the boundary, not imports.** The gateway never imports agent internals;
   external agents never import anything. Composition happens through typed schemas.
   Adopted from Buzz's ACP/MCP separation.
6. **Cheap by default.** No LLM call happens unless a budget-aware router authorises it.
7. **Fail loud on invariants, degrade quietly on capacity.** An accounting violation halts
   the run. An LLM budget exhaustion silently downgrades agents to reflex.
8. **Delete it if you can.** Any abstraction that exists "for future flexibility" and has
   one implementation is deleted. (Buzz's principle; it is correct.)

---

## 2. System topology

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                                  CLIENTS                                       │
│                                                                                │
│  Observatory (React)   polis CLI (Typer)    External agents                    │
│  charts · map · agent  run · sweep ·        (Hermes, OpenClaw, Claude Code,    │
│  inspector · log search replay · verify      any MCP client)                    │
└───────┬──────────────────────┬──────────────────────────┬─────────────────────┘
        │ HTTP/WS              │ in-process               │ MCP (stdio/HTTP) or
        │                      │                          │ REST + WS, ed25519-signed
        ▼                      ▼                          ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                            polis.observatory        polis.gateway               │
│                            (FastAPI)                (FastAPI + MCP server)      │
│                                     │                        │                  │
│                                     └───────────┬────────────┘                  │
│                                                 ▼                               │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                            polis.kernel                                   │  │
│  │  Clock · TickLoop (10 phases) · RngRegistry · InvariantRunner ·           │  │
│  │  Scheduler (sim-time cadences) · CheckpointManager                        │  │
│  └────┬──────────┬──────────┬───────────┬───────────┬──────────┬────────────┘  │
│       │          │          │           │           │          │                │
│  ┌────▼────┐ ┌───▼────┐ ┌───▼─────┐ ┌───▼──────┐ ┌──▼──────┐ ┌▼─────────────┐  │
│  │ agents  │ │ world  │ │ economy │ │ society  │ │ events  │ │ llm          │  │
│  │ cogni-  │ │ grid   │ │ labour  │ │ social   │ │ kinds   │ │ router       │  │
│  │ tion    │ │ places │ │ firms   │ │ media    │ │ log     │ │ providers    │  │
│  │ memory  │ │ paths  │ │ goods   │ │ news     │ │ hash    │ │ cache        │  │
│  │ salience│ │ move   │ │ exchange│ │ beliefs  │ │ chain   │ │ budget       │  │
│  │ actions │ │ rent   │ │ banking │ │ polity   │ │ schemas │ │ structured   │  │
│  │         │ │        │ │ ventures│ │ law      │ │         │ │ output       │  │
│  └─────────┘ └────────┘ └─────────┘ └──────────┘ └────┬────┘ └──┬───────────┘  │
│                                                       │         │               │
│                          ┌────────────────────────────┘         │               │
│                          ▼                                      ▼               │
│                    polis.store (repositories, projections, migrations)          │
└──────────────────────────┬──────────────────┬───────────────────┬──────────────┘
                           │                  │                   │
                  ┌────────▼───────┐   ┌──────▼──────┐    ┌───────▼────────┐
                  │  Postgres 17   │   │   Redis 7   │    │  Object store  │
                  │  + pgvector    │   │  pub/sub    │    │  (local FS or  │
                  │  events        │   │  live tick  │    │   S3/MinIO)    │
                  │  projections   │   │  broadcast, │    │  completion    │
                  │  metrics       │   │  gateway    │    │  cache, parquet│
                  │  memories      │   │  queues     │    │  exports,      │
                  │  llm_calls     │   │             │    │  checkpoints   │
                  └────────────────┘   └─────────────┘    └────────────────┘
```

### 2.1 Process model (v1)

One machine. Three long-lived processes:

| Process | Command | Role |
|---|---|---|
| `polis-engine` | `polis run <config>` | Owns the tick loop and **all** state mutation. Single-threaded for mutation, asyncio for I/O. |
| `polis-gateway` | `polis gateway` | Accepts external agents. Never mutates state — it queues signed actions into Redis, which the engine drains in PHASE 3. |
| `polis-observatory` | `polis observe` | Read-only FastAPI + static React bundle. Reads projections and subscribes to Redis for live ticks. |

Only `polis-engine` writes to the `events` table. This is enforced by a Postgres role
(`polis_reader` has no INSERT on `events`).

---

## 3. The event log

### 3.1 Envelope

```python
# polis/events/types.py
@dataclass(frozen=True, slots=True)
class Event:
    seq:         int                    # global monotonic within a run; assigned at append
    run_id:      UUID
    tick:        int
    sim_time:    datetime               # simulated clock, UTC-naive
    kind:        int                    # see §3.2
    actor_id:    str | None             # who did it; None for world/system events
    subject_ids: tuple[str, ...]        # entities materially affected (indexed for query)
    cause_seq:   int | None             # seq of the event that caused this one
    payload:     Mapping[str, Any]      # kind-specific; validated against a JSON Schema
    sig:         str | None             # ed25519 hex; REQUIRED for external-agent-origin
    prev_hash:   str                    # hash of event seq-1
    hash:        str                    # sha256 over canonical serialisation
```

**Canonical serialisation** for hashing (must be byte-identical across implementations):

```
sha256(
    seq.to_bytes(8,"big") ||
    run_id.bytes ||
    tick.to_bytes(8,"big") ||
    sim_time.isoformat().encode() ||
    kind.to_bytes(4,"big") ||
    (actor_id or "").encode() ||
    "\x1f".join(sorted(subject_ids)).encode() ||
    (cause_seq or -1).to_bytes(8,"big",signed=True) ||
    json.dumps(payload, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode() ||
    (sig or "").encode() ||
    bytes.fromhex(prev_hash)
)
```

Genesis `prev_hash` is 64 zeros. `polis verify <run_id>` walks the chain and recomputes.
Tampering with any event breaks every subsequent hash. Adopted from Buzz's `buzz-audit`.

`cause_seq` is what makes the log a causal graph rather than a list. Every downstream
effect points at its trigger, which is how the Observatory can answer *"why did
unemployment spike at tick 4,201?"* by walking backwards.

### 3.2 Kind registry

Kinds live in exactly one file: `polis/events/kinds.py`, as `Final[int]` constants plus a
`KIND_REGISTRY: dict[int, KindSpec]` mapping each to its name, payload schema, persistence
class, and owning module. **Adding a kind anywhere else is a bug.**

| Range | Domain | Owner module | Persisted |
|---|---|---|---|
| 1000–1999 | Kernel & run lifecycle | `polis.kernel` | yes |
| 2000–2999 | Agent lifecycle & vitals | `polis.agents` | yes |
| 3000–3999 | World, movement, space | `polis.world` | yes |
| 4000–4099 | Cognition, memory, salience | `polis.agents` | yes (sampled except 4010 and 4020 — see §3.3) |
| 4100–4199 | LLM router, cache, budget | `polis.llm` | yes (**not** sampled) |
| 4200–4999 | Cognition (reserved) | `polis.agents` | yes (**not** sampled) |
| 5000–5999 | Labour market & employment | `polis.economy.labour` | yes |
| 6000–6999 | Firms, production, goods market | `polis.economy.firms` | yes |
| 7000–7999 | Exchange, securities, order book | `polis.economy.exchange` | yes |
| 8000–8999 | Banking, credit, monetary policy | `polis.economy.banking` | yes |
| 9000–9999 | Ventures, funding, M&A, bankruptcy | `polis.economy.ventures` | yes |
| 10000–10059 | Communication & social graph | `polis.society.comms` | yes |
| 10060–10069 | Belief updates | `polis.society.beliefs` | yes |
| 10070–10999 | Communication & social graph (reserved) | `polis.society.comms` | yes |
| 11000–11999 | Social media & news | `polis.society.media` | yes |
| 12000–12999 | Government, elections, policy | `polis.society.polity` | yes |
| 13000–13999 | Crime, police, courts | `polis.society.law` | yes |
| 14000–14999 | Education & skills | `polis.agents.education` | yes |
| 15000–15999 | Households & demographics | `polis.agents.demography` | yes |
| 20000–20999 | External agent protocol | `polis.gateway` | yes |
| 90000–90999 | **Ephemeral** — broadcast, never stored | any | **no** |
| 99000–99999 | Researcher injection & scenario DSL | `polis.research` | yes |

Ephemeral kinds (90000+) are published to Redis for the Observatory and dropped. Use them
for per-tick position deltas and UI heartbeats — anything where storing 1,000 rows/tick
would bloat the log without informing research. Adopted from Buzz's 20000-range ephemerals.

Selected kinds (the full table is generated from `kinds.py` into `11-GLOSSARY.md`):

| Kind | Name | Payload highlights |
|---|---|---|
| 1001 | `RUN_STARTED` | config hash, prompt manifest, model manifest, code Git SHA, master seed, completion-cache manifest hash, mechanism and metric manifests, kind-registry hash, clock profile, scale |
| 1002 | `TICK_STARTED` | tick, sim_time |
| 1003 | `TICK_COMPLETED` | tick, event count, llm calls, cost |
| 1010 | `INVARIANT_VIOLATED` | invariant id, expected, actual, halting |
| 2001 | `AGENT_BORN` | agent_id, household_id, parents, trait vector, inherited priors |
| 2002 | `AGENT_DIED` | agent_id, cause, age, estate value |
| 2010 | `AGENT_HEALTH_CHANGED` | delta, cause |
| 3001 | `AGENT_MOVED` | from place, to place, path length |
| 3010 | `PLACE_OCCUPANCY` | place_id, occupants (ephemeral variant 90010) |
| 4001 | `PERCEPTION_BUILT` | observation digest hash |
| 4002 | `SALIENCE_SCORED` | score, components, routed_mode |
| 4010 | `MEMORY_WRITTEN` | memory_id, type, importance |
| 4020 | `REFLECTION_PRODUCED` | reflection_id, source memory ids, statement |
| 5001 | `VACANCY_POSTED` | firm, role, skill reqs, wage offer |
| 5010 | `HIRED` / 5011 `FIRED` / 5012 `QUIT` | agent, firm, wage |
| 6120 | `GOODS_PURCHASED` | buyer, seller, sku, qty, unit price |
| 7010 | `ORDER_SUBMITTED` | side, symbol, qty, limit price |
| 7020 | `TRADE_EXECUTED` | buy order, sell order, price, qty |
| 8010 | `LOAN_ORIGINATED` | lender, borrower, principal, rate, term |
| 8030 | `POLICY_RATE_SET` | rate, setter |
| 9010 | `ROUND_CLOSED` | startup, investors, valuation, amount |
| 9030 | `BANKRUPTCY_FILED` | entity, liabilities, assets |
| 11010 | `POST_PUBLISHED` | author, text, topic, in_reply_to |
| 11030 | `ARTICLE_PUBLISHED` | outlet, reporter, headline, slant, source events |
| 12020 | `VOTE_CAST` | voter, election, choice |
| 12030 | `POLICY_ENACTED` | parameter, old value, new value |
| 13010 | `CRIME_COMMITTED` | type, perpetrator, victim, detected |
| 13040 | `JUDGMENT_RENDERED` | case, verdict, penalty |
| 15010 | `HOUSEHOLD_FORMED` | members |
| 20001 | `EXTERNAL_AGENT_REGISTERED` | pubkey, name, declared model/scaffold |
| 99001 | `SHOCK_INJECTED` | scenario id, parameter, value |

### 3.3 Cognition-event sampling

High-volume kinds 4001–4004 and the reserved sampled slots 4005–4009, 4011–4019, and
4021–4099 (perception digests, salience scores, routing, and retrieval) would produce
~1,000 rows/tick even when nothing interesting happens. They are written under a sampling
policy:

- Always written when the agent was routed to `deliberate` or `reflect`.
- Written for a seeded random `cognition_sample_rate` (default 0.02) of reflex agents.
- The **full** LLM prompt and completion are never in the event payload — the payload
  carries an `llm_call_id` foreign key into the `llm_calls` table.

`MEMORY_WRITTEN` (4010) and `REFLECTION_PRODUCED` (4020) are never sampled. They are the
durable provenance spine for the G6 agent inspector, so every memory and reflection remains
visible even when the surrounding high-volume cognition telemetry is sampled.

### 3.4 Signatures

| Origin | Signed? | Mechanism |
|---|---|---|
| Native agent action | No | Provenance is guaranteed by the engine; signing 20k events/tick would dominate CPU |
| External agent action | **Yes, mandatory** | Ed25519 over the canonical action serialisation; canonical `agent_id` is `ag_<full_pubkey_hex>`, where `full_pubkey_hex` is exactly the 32-byte Ed25519 public key encoded as 64 lowercase hexadecimal characters without a `0x` prefix. The separately stored public-key field is verified first; registration/action input is rejected unless it matches the key encoded in `agent_id`. |
| Scenario injection | Yes | Signed by the researcher key so shocks can't be confused with organic events |
| System/world | No | `actor_id` is `null`, chain integrity covers it |

This is the deliberate divergence from Buzz noted in `01-PRD.md §9.1`.

---

## 4. Determinism

The pre-execution identity tuple is `(config_hash, prompt_manifest, model_manifest,
code_git_sha, master_seed, completion_cache_manifest_hash, mechanism_manifest,
metric_manifest, kind_registry_hash, clock_profile, scale)`. Given the same tuple, two runs
must produce identical event-log hash chains. Five sources of nondeterminism, five controls.
The composition root creates one `RunIdentity` snapshot; both `RUN_STARTED` and the initial
`runs` row are serialized from it rather than recomputing individual fields in separate
layers. The completion-cache hash in that snapshot identifies the launch cache; it is not
the exact-records-used manifest, which is a derived output finalized on the run row at
termination alongside the terminal event-chain hash. The event chain commits every recorded
external-agent action. A detached replay bundle for an externally driven run must also
publish the canonical hash of that recorded external-action trace so the trace supplied to
replay can be verified before execution.

### 4.1 Randomness — `RngRegistry`

```python
# polis/kernel/rng.py
class RngRegistry:
    def __init__(self, master_seed: int) -> None: ...
    def get(self, namespace: str, entity_id: str = "", tick: int | None = None) -> Random:
        """Deterministic child stream.
        seed = int.from_bytes(
            sha256(f"{master_seed}|{namespace}|{entity_id}|{tick or ''}".encode()).digest()[:8],
            "big")
        """
```

**Rules (enforced by a lint check in CI):**
- `import random` at module level is banned outside `polis/kernel/rng.py`.
- `numpy.random` global state is banned; use `numpy.random.default_rng(seed)` from the registry.
- Every draw declares its namespace, e.g. `rng.get("labour.match", firm_id, tick)`.
- Tick-scoped streams (`tick=` supplied) make a subsystem's draws independent of how many
  draws other subsystems made earlier. **This is what makes partial re-implementation safe.**

### 4.2 Iteration order

- Never iterate a `set` or a `dict` whose insertion order depends on concurrency.
- Any collection of entities processed in a loop that mutates state is sorted by a stable
  key (`agent_id`, `order_id`) first. Helper: `polis.kernel.det.stable(iterable, key=)`.
- `PYTHONHASHSEED=0` is set in the engine entrypoint.

### 4.3 Concurrency

Asyncio is used only for I/O-bound work (LLM calls, gateway drains, DB batch writes).
**State mutation is single-threaded.** The pattern is always:

```python
results = await asyncio.gather(*(decide(a) for a in agents))   # any completion order
for r in stable(results, key=lambda r: r.actor_id):            # canonical order
    apply(r)                                                    # mutation
```

Process pools may be used for pure compute (pathfinding precompute, metric aggregation)
and must return values, never mutate shared state.

### 4.4 LLM sampling — the completion cache

The single hardest problem: an LLM is nondeterministic even at temperature 0.

**Solution:** a content-addressed completion cache. Key:

```
cache_key = sha256(
    provider || model || model_version || prompt_template_hash ||
    canonical_json(prompt_variables) || canonical_json(sampling_params) ||
    str(call_seed)
)
```

- `call_seed` is drawn from the RngRegistry (`rng.get("llm", agent_id, tick)`), so the same
  agent at the same tick under the same config asks the same question with the same seed.
- **Miss** → call the provider, store `(key, response, usage, latency, provider_request_id)`.
- **Hit** → return the stored response. No network call.
- Modes: `live` (call and cache), `replay` (cache only; a miss is a hard error), `hybrid`
  (cache first, call on miss — used for parameter sweeps that share most prompts).

Consequences:
- Replay is free and exact.
- A sweep over an unrelated parameter reuses most completions, cutting sweep cost 5–20×.
- The cache is a first-class research artefact published alongside a paper. A third party
  reproduces figures in `replay` mode with **zero** API spend and zero dependence on a model
  that may have since been retired (mitigates T5).

### 4.5 Time

`datetime.now()` is banned in the engine. Simulated time comes from `Clock`.
`llm_calls.latency_ms` is elapsed request duration, not a wall-clock timestamp. Wall-clock
values are limited to run metadata and the operational gateway records defined in
`03-DATA-MODEL.md §10`; request timestamps must never enter event payloads or simulated
state.

### 4.6 Floating point

Money is **never** a float. All monetary values are integer minor units (cents) in a
`Money` newtype. Prices on the exchange are integer ticks. Non-monetary continuous
quantities (skill levels, beliefs, health) are floats but are rounded to 6 dp before
hashing into event payloads.

---

## 5. The tick loop

`polis/kernel/tick.py`. Ten phases, strictly ordered, no phase may read a state change made
later in the same tick.

```
┌── PHASE 0 · CLOCK ────────────────────────────────────────────────────────────┐
│ Advance sim clock. Emit TICK_STARTED. Resolve which scheduled cadences fire   │
│ this tick (payroll? market open? election day? school term?).                  │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 1 · PERCEIVE ─────────────────────────────────────────────────────────┐
│ Build an Observation for every awake agent from *last tick's committed state*. │
│ Pure function of state. Parallelisable. Includes: own state, co-located agents,│
│ place affordances, inbox, feed slice, market quotes, employer state, news.     │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 2 · SALIENCE ─────────────────────────────────────────────────────────┐
│ Score every agent. Rank. The LLM budget for this tick (tokens and calls) is    │
│ allocated top-down until exhausted. Assign each agent a mode:                  │
│ REFLEX | DELIBERATE | REFLECT. External agents are always DELIBERATE and do    │
│ not consume the native budget.                                                 │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 3 · DECIDE ───────────────────────────────────────────────────────────┐
│ REFLEX agents: local utility policy, deterministic, instant.                   │
│ DELIBERATE agents: batched LLM calls via the router (async, any order).        │
│ REFLECT agents: memory-compression call; may also emit an action.              │
│ EXTERNAL agents: drain signed actions from the Redis queue, subject to the     │
│ per-agent deadline; miss the deadline → fall back to that agent's reflex policy.│
│ Output: a flat list of Actions. Sorted by actor_id before leaving the phase.   │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 4 · VALIDATE ─────────────────────────────────────────────────────────┐
│ Every Action is checked against: schema, actor capability, resource            │
│ sufficiency (can they afford it?), legality (is it a crime?), rate limits.     │
│ Invalid → ACTION_REJECTED event with a reason + substitute the null action.    │
│ A rejected action still costs the agent its action slot. Agents learn from the │
│ rejection through perception next tick.                                        │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 5 · RESOLVE ──────────────────────────────────────────────────────────┐
│ Institutions consume the validated action set and produce outcomes.            │
│ Resolution order across institutions is FIXED (see §5.1). Within an            │
│ institution, resolution must be documented and order-independent or explicitly │
│ price-time-priority (the exchange).                                            │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 6 · COMMIT ───────────────────────────────────────────────────────────┐
│ Append all events for this tick in one batched transaction. Apply state deltas │
│ to in-memory projections. Publish ephemerals to Redis.                         │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 7 · INSTITUTIONS ─────────────────────────────────────────────────────┐
│ Scheduled institutional steps whose cadence fired in PHASE 0:                  │
│ payroll · rent · interest accrual · loan amortisation · market close & OHLCV · │
│ firm production & pricing · tax collection · school term advance · election    │
│ day · news cycle · policy review.                                              │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 8 · VITALS ───────────────────────────────────────────────────────────┐
│ Ageing · health hazard · illness · death (with full estate settlement) ·       │
│ conception · birth · household formation & dissolution · migration in/out.     │
└───────────────────────────────────────────────────────────────────────────────┘
┌── PHASE 9 · METRICS & INVARIANTS ─────────────────────────────────────────────┐
│ Snapshot the metric vector. Run invariant checks (V2 accounting closure is     │
│ mandatory every tick). A violation emits INVARIANT_VIOLATED and halts unless   │
│ `--continue-on-violation`. Emit TICK_COMPLETED. Checkpoint if due.             │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 5.1 Fixed institutional resolution order

Within PHASE 5, institutions resolve in this order. It is arbitrary but must be constant,
and it is chosen so that markets clear before agents learn prices:

```
1. movement        (positions settle first; co-location determines what else is legal)
2. communication   (speech, DMs, posts — no resource effects)
3. labour          (applications, offers, hires, fires, quits)
4. goods           (purchases at posted prices)
5. exchange        (order book matching, price-time priority)
6. banking         (loan applications, deposits, withdrawals, repayments)
7. ventures        (pitches, term sheets, rounds, M&A)
8. polity          (votes, campaign spend, policy proposals)
9. law             (crimes, reports, arrests, filings, judgments)
10. misc/world     (anything else)
```

Rationale for the ordering is documented in each institution's spec. Any dependency that
requires a different order is a design smell and must be resolved by splitting the action
into two ticks, not by reordering.

### 5.2 Clock profiles

```yaml
clock:
  profile: microscope        # microscope | chronicle
  # microscope: 1 tick = 1 sim hour. 24 ticks/day, 8640 ticks/sim-year (360-day year).
  # chronicle:  1 tick = 1 sim day. 360 ticks/sim-year.
  ticks_per_sim_day: 24      # derived from profile; overridable
  days_per_sim_year: 360
  demographic_acceleration: 1.0   # agents age N sim-years per elapsed sim-year
```

**All institutional cadences are expressed in sim-time, never in ticks.** `payroll:
biweekly`, `market_session: 09:30–16:00`, `election_interval: 4y`. The scheduler converts.
This is what lets both profiles run the same code (decision D10).

`demographic_acceleration` > 1 compresses lifespans so a run can reach three generations
without 700k ticks. It is a declared `MECHANISM` and its value is reported with any
demographic result.

### 5.3 Checkpointing and recovery

Every `checkpoint_interval` ticks (default 500) the engine writes a checkpoint: the full
in-memory projection state + last committed `seq` + RNG registry state, to the object
store. On restart, `polis run --resume <run_id>` loads the newest checkpoint and replays
events after it. Because projections are pure functions of the log, a corrupt checkpoint is
recoverable by full replay (slower, always correct).

---

## 6. Actions

### 6.1 Envelope

```python
# polis/agents/actions/types.py
@dataclass(frozen=True, slots=True)
class Action:
    action_id: UUID
    tick:      int
    actor_id:  str
    type:      ActionType                      # closed enum, see §6.2
    params:    Mapping[str, Any]               # validated by the type's pydantic model
    origin:    Literal["reflex","deliberate","reflect","external","scripted"]
    salience:  float
    reasoning: str | None                      # LLM free text; stored, NEVER parsed
    sig:       str | None                      # required iff origin == "external"
```

`reasoning` is preserved verbatim for audit and qualitative analysis. **No code path may
branch on its content.** This is the boundary that keeps the institutional layer
deterministic while agent cognition stays open-ended (rejection of Smallville's free-form
adjudication, `01-PRD.md §9.1`).

### 6.2 The action taxonomy

`ActionType` is a closed enum. Adding a type requires a spec change and a new validator.
Grouped by owning institution; full parameter schemas live in each domain spec.

| Group | Types |
|---|---|
| **world** | `MOVE_TO`, `IDLE`, `SLEEP`, `EAT`, `RENT_HOME` |
| **speech** | `SAY`, `DIRECT_MESSAGE`, `BROADCAST` |
| **labour** | `APPLY_FOR_JOB`, `ACCEPT_OFFER`, `DECLINE_OFFER`, `QUIT_JOB`, `NEGOTIATE_WAGE`, `POST_VACANCY`, `MAKE_OFFER`, `FIRE_EMPLOYEE`, `WORK` |
| **education** | `ENROL`, `STUDY`, `DROP_OUT`, `TAKE_EXAM` |
| **goods** | `BUY_GOOD`, `SET_PRICE`, `PRODUCE`, `RESTOCK` |
| **exchange** | `SUBMIT_ORDER`, `CANCEL_ORDER`, `SHORT`, `IPO_LIST` |
| **banking** | `OPEN_ACCOUNT`, `DEPOSIT`, `WITHDRAW`, `APPLY_FOR_LOAN`, `REPAY_LOAN`, `DEFAULT` |
| **ventures** | `FOUND_COMPANY`, `PITCH`, `ISSUE_TERM_SHEET`, `INVEST`, `ACQUIRE`, `SELL_STAKE`, `FILE_BANKRUPTCY`, `DECLARE_DIVIDEND` |
| **media** | `POST`, `REPOST`, `LIKE`, `COMMENT`, `FOLLOW`, `UNFOLLOW`, `PUBLISH_ARTICLE`, `RETRACT` |
| **polity** | `FOUND_PARTY`, `JOIN_PARTY`, `ANNOUNCE_CANDIDACY`, `CAMPAIGN`, `VOTE`, `PROPOSE_POLICY`, `LOBBY` |
| **law** | `COMMIT_CRIME`, `REPORT_CRIME`, `FILE_SUIT`, `RETAIN_COUNSEL`, `TESTIFY`, `SETTLE`, `RULE` |
| **social** | `BEFRIEND`, `COURT`, `PROPOSE_UNION`, `DISSOLVE_UNION`, `HAVE_CHILD_INTENT` |
| **meta** | `NULL_ACTION` (the substitute for a rejected action) |

### 6.3 Action budget

Every agent gets `action_slots` per tick (default 1 in `microscope`, 4 in `chronicle`).
This applies identically to native and external agents — the fairness guarantee behind T12.
A `WORK` or `SLEEP` action consumes a slot; so does an exchange order.

---

## 7. Module layout

```
worldorder/
├── docs/                       # these specs
├── chunks/                     # implementation work packages
├── prompts/                    # versioned prompt templates (jinja2), hashed into runs
├── configs/                    # run configs + scenarios
├── migrations/                 # alembic
├── web/                        # Observatory React app
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── invariants/             # V1–V7 gate tests
│   └── determinism/            # same-seed byte-equality tests
└── polis/
    ├── kernel/                 # clock, tick, rng, scheduler, invariants, checkpoint, det
    ├── events/                 # kinds, types, log writer, hash chain, schemas
    ├── store/                  # repositories, projections, migrations glue
    ├── llm/                    # router, providers/{minimax,ollama,openai_compat}, cache,
    │                           #   budget, structured, prompts
    ├── agents/
    │   ├── state.py            # AgentState, traits, needs, skills
    │   ├── memory/             # stream, retrieval, reflection, embeddings
    │   ├── cognition/          # salience, reflex policy, deliberate, reflect
    │   ├── actions/            # types, validators, budget
    │   ├── education.py
    │   └── demography.py
    ├── world/                  # grid, places, pathfinding, movement, districts, rent
    ├── economy/
    │   ├── ledger.py           # double-entry money; the V2 invariant lives here
    │   ├── labour.py
    │   ├── firms.py
    │   ├── goods.py
    │   ├── exchange/           # order book, matching, market data
    │   ├── banking.py
    │   └── ventures.py
    ├── society/
    │   ├── comms.py
    │   ├── graph.py
    │   ├── media/              # social platform, feed algorithms, news outlets
    │   ├── beliefs.py
    │   ├── polity.py
    │   ├── policy.py
    │   └── law.py
    ├── gateway/                # mcp_server.py, rest.py, auth.py, queue.py, sdk/
    ├── observatory/            # api.py, metrics_api.py, live.py
    ├── research/               # metrics, experiments, replay, scenario, exports
    ├── config/                 # settings.py (pydantic-settings), profiles/, canon.py,
    │                           #   runtime.py — see §7.2
    └── cli/                    # typer app
```

### 7.1 Dependency rules (enforced by `import-linter` in CI)

```
kernel   → events, config                          (never: agents, economy, society, world)
events   → config                                  (nothing else)
store    → events, config
llm      → config, store
world    → kernel, events, config
agents   → kernel, events, world, llm, store, config
economy  → kernel, events, world, store, config    (never: agents.cognition)
society  → kernel, events, world, store, config    (never: agents.cognition)
gateway  → events, config, store.readmodels.external only (never: kernel, agents, other store modules)
observatory → store, events, config                (read-only; never kernel)
research → everything (it is the composition root, along with cli)
```

The critical rule: **institutions (`economy`, `society`) never import agent cognition.**
They consume `Action` objects and produce `Event` objects. This is what makes the
institutional layer independently testable and keeps LLM behaviour out of the market logic.

### 7.2 `polis/config/runtime.py` — the runtime parameter overlay

Enacted policy must actually change the world, or the political layer is theatre. The
overlay is the single mechanism by which that happens.

| Property | Rule |
|---|---|
| **Who writes** | The policy application service in `polis.society.policy` is the only writer. `polis.society.polity` (C18) and `polis.research.scenario` (C25) both submit changes through that service, so a researcher shock and an in-world election use the same persistence, event, and overlay path. |
| **Who reads** | `polis.economy` and `polis.society`. Never read a policy-controllable parameter from static config. |
| **Why it lives in `config`** | It is the only package both `economy` and `society` may import under §7.1 without creating a cycle. |
| **Shape** | Tick-keyed. `overlay.bp("tax.income_bp", tick)` returns the value in force at that tick. History is retained so replay and the causal explorer see what the agent saw. |
| **Accessors** | Typed and unit-explicit: `.bp(key, tick)` basis points, `.cents(key, tick)`, `.flag(key, tick)`, `.brackets(key, tick)`. **No `.get()` returning `Any`.** |
| **Units** | All rates are **integer basis points**, never floats (§4.6). Every rate key is suffixed `_bp`. A 22% income tax is `2200`. |
| **Mutation** | `RuntimeConfig.enact(...)` validates the key and temporal ordering, then updates tick-keyed in-memory history only. The `polis.society.policy` application service validates the proposal and stages the society-owned `POLICY_ENACTED` event. The event log is the durable authority; the `policies` row and runtime history are projections of that event. Direct assignment and direct callers outside that service are forbidden. |
| **Registry** | `POLICY_REGISTRY` in `polis/society/policy.py` is the closed set of controllable keys with type, unit, bounds, and owning module. `polis/config/runtime.py` deliberately does not own persistence or events, preserving the dependency boundaries in §7.1. |

#### Policy-application atomicity and recovery

Policy enactment uses an event-as-outbox protocol; there is no attempt to make an in-memory
object participate in a database transaction.

1. The application service validates the proposal, assigns deterministic `policy_id`, and
   stages `POLICY_ENACTED`. `EventLog.stage` assigns the next `event_seq` from the
   hash-chain head; the service then uses the returned event's sequence for the provisional
   policy repository and `RuntimeConfig` overlay. Any changes made to those in-process
   projections before commit are provisional and cannot take effect during the enactment
   tick because `effective_tick > enacted_tick`.
2. The event-sink transaction commits `POLICY_ENACTED` first. A synchronous `policies`
   projection must use that same transaction; an asynchronous projection treats the
   committed event as its durable outbox and upserts by `(run_id, policy_id)`, retaining
   the event-store-assigned `event_seq` as its ordering and idempotency witness.
   A `policies` row must never commit ahead of its source event.
3. If validation, staging, or event commit fails, the event batch is rolled back and the
   run halts. Provisional runtime/repository state is discarded by restoring the last
   checkpoint and replaying only committed events; the process must not continue from the
   mutated objects.
4. If the process crashes after the event commits but before the `policies` projection or
   live overlay is updated, restart loads the last checkpoint and reconciles every committed
   `POLICY_ENACTED` event through the current log head. `project_enactment` reconstructs
   runtime history, while the policies projector inserts or repairs the row idempotently
   from the same event. This policy reconciliation is not limited to events after the
   checkpoint sequence.
5. Exact repeats of `(policy_id, event_seq)` are no-ops. The same `policy_id` with a
   different event sequence, parameter, value, or tick is corruption: reconciliation
   halts the run rather than choosing one copy. A checkpoint may advance its event-sequence
   watermark only after every policy event through that sequence has been applied to both
   the durable projection and the checkpointed runtime overlay. On startup and
   `polis rebuild`, derived policy rows are compared with the ordered `POLICY_ENACTED`
   stream and missing/stale rows are repaired; the table never overrides the log.

This ordering gives one authoritative commit point. At every recovery boundary the only
permitted outcomes are “no committed enactment” or “all projections derived from the
committed enactment”; partial live state is never resumed silently.

---

## 8. Configuration

`pydantic-settings` models in `polis/config/settings.py`. One YAML file fully specifies a
run. The config's canonical hash is recorded in `RUN_STARTED` and in the run manifest.

```yaml
run:
  name: "baseline-1k"
  seed: 20260724
  ticks: 43200                    # 5 sim-years at microscope
  checkpoint_interval: 500

clock:
  profile: microscope
  demographic_acceleration: 4.0   # MECHANISM

population:
  initial_agents: 1000
  age_distribution: pyramid_ca_2020
  trait_model: big_five_plus_econ

world:
  grid: {width: 200, height: 200}
  districts: 6
  places_per_district: 60

llm:
  budget:
    tokens_per_tick: 300_000        # must exceed calls_per_tick × avg tokens/call
    calls_per_tick: 90              #   (90 × ~3,300 ≈ 297k) or calls_per_tick never binds
    usd_per_run: 2000.0             # five microscope years at the PRD's ~$250–400/year
    on_exhaustion: degrade_to_reflex      # degrade_to_reflex | halt
  routing:
    DELIBERATE:     {provider: minimax,  model: MiniMax-M2.7,        temperature: 0.8}
    REFLECT:        {provider: minimax,  model: MiniMax-M2.7,        temperature: 0.9}
    IMPORTANCE:     {provider: ollama,   model: qwen3.5:cloud,       temperature: 0.0}
    POST_WRITE:     {provider: ollama,   model: gemma4:cloud,        temperature: 1.0}
    NEWS_WRITE:     {provider: minimax,  model: MiniMax-M2,          temperature: 0.7}
    VC_EVAL:        {provider: minimax,  model: MiniMax-M2.7,        temperature: 0.4}
    CREDIT_EVAL:    {provider: minimax,  model: MiniMax-M2,          temperature: 0.2}
    JUDGE:          {provider: minimax,  model: MiniMax-M2.7,        temperature: 0.2}
    EMBED:          {provider: ollama,   model: embeddinggemma:cloud}
  cache: {mode: hybrid, path: "s3://polis-cache/"}

salience:
  policy: weighted                # weighted | random (control) | always (debug)
  weights: {surprise: 0.30, stakes: 0.35, novelty: 0.10, social: 0.15, scheduled: 0.10}
  exploration_epsilon: 0.02

mechanisms:                       # every hard-coded behavioural rule, ablatable
  labour_matching: stochastic_skill_match     # MECHANISM
  price_setting: markup_over_cost             # MECHANISM
  fertility_hazard: income_conditional        # MECHANISM
  mortality_hazard: gompertz_makeham          # MECHANISM

society:
  feed_algorithm: engagement      # chronological | engagement | random | adversarial
  outlets: 4

ablations:
  reflex_only: false
  obfuscate_domain: false
  disclose_simulation: false      # for research question C2
```

### 8.1 The `MECHANISM` convention

Any config key that encodes a hard-coded behavioural rule is placed under `mechanisms:` and
its Python implementation carries a `@mechanism("id", entails="...")` decorator whose
`entails` string states in plain English what the rule *analytically implies*. The reviewer
checklist (`10-RESEARCH-AND-OBSERVABILITY.md`) requires that no claimed emergent finding
follows from any active mechanism's `entails`. This is the concrete defence against T6.

---

## 9. Invariants

`polis/kernel/invariants.py`. Each returns `Ok | Violation`. Frequency is configurable but
the defaults below are the minimum.

| ID | Statement | Frequency | On violation |
|---|---|---|---|
| **INV-MONEY** | Σ(all account balances) + Σ(cash) == money_supply, exactly, in cents | every tick | **HALT** |
| **INV-LEDGER** | Every ledger entry has a matching contra-entry; debits == credits | every tick | **HALT** |
| **INV-SHARES** | Σ(shares held) == shares outstanding, per symbol | every tick | **HALT** |
| **INV-ORDERS** | Every resting order has sufficient reserved funds/shares | every tick | HALT |
| **INV-EMPLOY** | Every employment record has exactly one live agent and one live firm | every tick | HALT |
| **INV-CHAIN** | `prev_hash` chain is intact | every checkpoint | HALT |
| **INV-POP** | Population within [0.2×, 5×] initial | every sim-day | WARN |
| **INV-ENTROPY** | Action-type entropy ≥ floor (V4) | every sim-day | WARN |
| **INV-NONDEGEN** | Top-1 wealth share < 0.9; 0 < employment < 1 (V3) | every sim-day | WARN |

HALT writes `INVARIANT_VIOLATED`, checkpoints, and stops. This is deliberate: a run that
violates conservation of money is not a run, it is a bug report.

---

## 10. Error handling

| Class | Example | Behaviour |
|---|---|---|
| **Invariant violation** | Money doesn't close | HALT the run, checkpoint, exit non-zero |
| **Invalid action** | Agent buys with no funds | Reject, emit `ACTION_REJECTED`, substitute `NULL_ACTION`, continue |
| **LLM failure** | Provider 5xx, timeout, malformed JSON after repair retries | Fall back to reflex for that agent-tick, emit `LLM_CALL_FAILED`, continue. Failures are counted and reported per run. |
| **Budget exhausted** | Token cap hit mid-tick | Remaining agents degrade to reflex, emit `BUDGET_EXHAUSTED`, continue |
| **External agent timeout** | No action before the deadline | Fall back to that agent's reflex policy, emit `EXTERNAL_DEADLINE_MISSED`, continue |
| **Store failure** | Postgres unavailable | Retry with backoff; after N failures, HALT (we cannot lose log entries) |
| **Bug** | Unhandled exception in an institution | HALT. Never swallow. A silently-continuing simulation produces unpublishable data. |

---

## 11. Performance targets and budget

At 1,000 agents, `microscope` profile, single 16-core machine, Postgres local:

| Phase | Budget (p50) | Notes |
|---|---|---|
| 0 CLOCK | < 1 ms | |
| 1 PERCEIVE | < 80 ms | Pure, vectorised where possible, process pool if needed |
| 2 SALIENCE | < 20 ms | Pure arithmetic over agent state |
| 3 DECIDE | < 3,000 ms | Dominated by the LLM batch; ~90 concurrent calls |
| 4 VALIDATE | < 20 ms | |
| 5 RESOLVE | < 100 ms | Exchange matching is the hot spot |
| 6 COMMIT | < 150 ms | One batched `COPY`/`executemany` per tick |
| 7 INSTITUTIONS | < 100 ms (amortised) | Spikes on payroll/market-close ticks |
| 8 VITALS | < 30 ms | |
| 9 METRICS | < 50 ms | |
| **Total** | **≈ 1 tick/s** | LLM latency is ~95% of it — everything else is noise |

**Implication:** do not micro-optimise Python. The LLM call dominates. Spend engineering
effort on the cache hit rate and on batching, not on rewriting loops.

Event volume: ~15–25k persisted events/tick at 1k agents → ~150M events/sim-year in
`microscope`. Hence monthly partitioning (§`03-DATA-MODEL.md`) and the cognition sampling
policy (§3.3).

---

## 12. Testing strategy

| Layer | What | Where |
|---|---|---|
| **Unit** | Pure functions: matching, order book, hazard functions, retrieval scoring | `tests/unit/` |
| **Property** | Hypothesis tests on the order book (never crosses, always conserves shares) and the ledger (always balances) | `tests/unit/` |
| **Determinism** | Same seed → identical hash chain, over 200 ticks with a stub LLM | `tests/determinism/` |
| **Invariant** | V1–V7 gates on a 5,000-tick smoke run with a stub LLM | `tests/invariants/` |
| **Integration** | Full tick loop with a recorded completion cache, 50 agents, 500 ticks | `tests/integration/` |
| **Golden** | A frozen 100-tick run whose event-log hash is checked into the repo. Any change to it must be deliberate and explained in the PR. | `tests/integration/golden/` |

**`StubLLM`** is mandatory infrastructure (chunk C05): a deterministic fake provider that
returns schema-valid responses derived from a hash of the prompt. Every test above except
the LLM router's own tests runs against it. Without this, the test suite is neither fast
nor deterministic.

---

## 13. What we borrowed from Buzz, concretely

| Buzz idea | Polis implementation |
|---|---|
| One signed append-only event log as source of truth | `polis.events` — same shape, `kind`-dispatched, hash-chained |
| `kind` integer as the only dispatch switch; new feature = new kind | §3.2 kind registry with reserved ranges |
| Ephemeral kind range that is never stored | 90000–90999 |
| Hash-chain tamper-evident audit | `Event.prev_hash`/`hash`, `polis verify` |
| Agents are members with their own keypairs and audit trail, not bots | External agents: collision-resistant ed25519 identity, `agent_id == "ag_" + full_pubkey_hex`, full key also stored separately, same action surface (`08-EXTERNAL-AGENT-PROTOCOL.md`) |
| Protocol-native boundaries — agent and engine don't import each other | Gateway speaks MCP; core never imports a vendor SDK |
| JSON-in / JSON-out CLI designed for LLM tool calls | `polis-agent-cli` in `polis/gateway/sdk/` |
| YAML workflows with triggers and actions | Scenario DSL (`polis/research/scenario.py`) — same trigger/step shape, used for shock injection |
| Full-text search over the event log so agents answer with receipts | Postgres FTS + pgvector over events and memories; agents and researchers use the same index |
| Scoped capability by identity, not permission flags | Agent capabilities derive from role/assets/office, not from a flag table |
| Bounded everything (frame size, subscriptions, history) | Bounded action slots, LLM budget, memory cap, order book depth |

---

*Next: `03-DATA-MODEL.md`.*
