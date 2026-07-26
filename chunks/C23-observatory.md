# C23 — Observatory: live map, agent inspector, causal explorer, run comparison

**M1 (C23a) / M6 (C23b)** · `polis/observatory/` + `web/` · **C23a depends on:** C02, C03, C06, C07, C08, C09, C21, C24a · **C23b depends on:** C23a, C22 (scorecard), C24b (gates, manifests), C25 (injections) · **Blocks:** M1 completion (C23a), M6 completion (C23b) · **Size:** L (C23a ≈ 1 week, C23b ≈ 1 week)

> **This brief is split.** §3, §5, §11 and §12 are divided into **a** and **b** parts.
> **C23a gates milestone M1 and must ship early.** Do not begin C23b work until C23a's
> acceptance checklist is green — the inspector is what makes M1 mean anything.

---

## 1. Context

The Observatory is the layer that makes the simulation legible. Goal **G6** is stated plainly: *if you cannot explain a macro chart by drilling into an individual, the layer has failed.* A chart showing unemployment rising is worth nothing on its own — the question a researcher asks next is always "which agent, and why did **that** agent decide **that**?", and the agent inspector is the only object in the system that answers it end to end: perception → retrieved memories → prompt → response → action → outcome, for any agent at any tick. Everything else here (map, charts, causal walk, search, comparison, scorecard) exists to get you to that view with a question already formed. The Observatory is **read-only**, never imports `polis.kernel`, and must not be able to slow the engine down — a dashboard that costs the run 20% of its tick rate is a dashboard that gets turned off during the runs that matter.

---

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/02-ARCHITECTURE.md` | **all** — §2.1 process model, §3.2 kind ranges and ephemerals, §3.3 cognition sampling, §5 tick phases, §7.1 dependency rules |
| `../docs/03-DATA-MODEL.md` | §1.1 `runs`, §1.2 `events` + indexes, §1.3 `llm_calls`, §2 agents/memories/beliefs, §3 world, §10 `metrics`, §12 rebuild |
| `../docs/10-RESEARCH-AND-OBSERVABILITY.md` | **§8 in full — primary source**; §0.3 read-only rule, §0.4 metric storage, §1.10 drift, §2.4 gate report, §12 R3/R5/R11 |
| `../docs/05-WORLD-SPEC.md` | §11.1 static payload, §11.2 ephemeral kinds 90010, 90050–90054 |
| `../docs/04-AGENT-SPEC.md` | §5 `Observation`, §6.3 retrieval scoring, §7 salience, §9 deliberate, §11 validation gates |
| `../docs/08-EXTERNAL-AGENT-PROTOCOL.md` | §11 scorecard (C23b only) |
| Chunks | C02 (`KIND_REGISTRY`, `Event`), C03 (`Database(role="reader")`, `EventRepository`, `MetricRepository`), C06 (ephemeral payloads, static map), C09 (kinds 4002–4009), C22 (`ScorecardRow`), C24 (`MetricRegistry`, `GateResult`), C25 (`scenario_injections`) |

---

## 3a. Scope — in (C23a, M1, minimal)

1. **Read-only FastAPI app** — `polis observe`, `polis_reader` role, static React bundle from `web/dist`, every response carrying `as_of_tick` / `as_of_seq`.
2. **Live map view** — grid, districts, places and agents rendered from ephemeral kinds 90050, 90010, 90051, 90052, 90054 over WebSocket; static geometry fetched once and cached client-side; district choropleth over land value, rent index, school quality, crime rate.
3. **Macro chart panel** — any registered metric, server-side LTTB downsampling, small-multiples by cadence, invariant WARN markers from kind 1010.
4. **Agent inspector — goal G6.** `GET /runs/{run_id}/agents/{agent_id}/tick/{tick}` returning the full `10 §8.2c` payload, plus the adjacent tabs: lifetime timeline, memory stream with `parent_memory_ids` provenance, belief trajectories, ego-net, salience series with the routing cutoff overlaid.
5. **Agent list and detail** — filterable projection browse, agent state at latest tick.
6. **WebSocket fan-out** — one Redis subscription per run, per-connection ring buffer, `lag` frames, the bounded `pin` op.
7. **Performance isolation** — the five rules of `10 §8.5`, plus the `EXPLAIN` CI test.
8. **Tick summary** and `/health`.

## 3b. Scope — in (C23b, M6, full)

9. **Causal explorer** — backward and forward `cause_seq` walks with depth and node caps, and the macro entry point `GET /runs/{id}/why?metric=&tick=&window=` returning ranked root causes by subtree mass, every node clickable through to the inspector.
10. **Event-log full-text search** — Postgres FTS over `payload->>'text'` plus structured filters (kind, actor, subject, tick range, JSON path), with a query builder that **refuses** anything that would sequential-scan a partition.
11. **Run comparison** — 2–6 runs: reproducibility-tuple diff first, then overlaid series with seed bands, the gate matrix, and the ablation ladder. Refuses to overlay two series whose `definition_hash` differs.
12. **External-agent scorecard view** — the nine-dimension vector per `(model, scaffold)` cell with every disclosure column, native reference distribution, and the ineligibility banner.
13. **Sweep views** — `/sweeps`, cells, pre-registration display.
14. **Scenario view** — loaded scenario, triggers fired, injections with per-injection signature status.
15. **LLM call detail** and the mechanism list with `entails` strings.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| Computing metrics, gates, the registry, `definition_hash` | **C24** — you read `metrics` and the gate report |
| Producing ephemerals 90010/90050–90054 | **C06** (engine, PHASE 6) |
| Scorecard *computation* and eligibility | **C22** — you render `ScorecardRow` |
| Scenario execution, signing, `scenario_injections` rows | **C25** |
| `polis replay / verify / rebuild / export` | **C24** |
| **Any** write path: run control, parameter change, event injection, clock advance | **nobody** — the Observatory has no such endpoint and will not grow one |
| Prompt *re-rendering* (needs `polis.agents` + `polis.llm`) | **C24**, mounted optionally — see §9.4 |
| Sprites, animation beyond position interpolation, "game feel" | **nobody.** `01-PRD.md N3`. This is a research instrument. |

---

## 5a. Interfaces you provide (C23a)

```python
# polis/observatory/app.py
def create_app(settings: Settings, *, extra_routers: Sequence[APIRouter] = ()) -> FastAPI:
    """Read-only. Opens Database with role='reader'. `extra_routers` is how the composition
    root mounts C24's optional reconstruction router (§9.4) WITHOUT polis.observatory
    importing polis.research."""
async def serve(settings: Settings) -> None: ...          # `polis observe` entrypoint

# polis/observatory/envelope.py
@dataclass(frozen=True, slots=True)
class AsOf:
    as_of_tick: int
    as_of_seq: int
    engine_tick: int | None                # from Redis heartbeat; None if engine not running
    lag_ticks: int                         # engine_tick - as_of_tick, 0 if unknown

def envelope(payload: Mapping[str, Any], asof: AsOf) -> Mapping[str, Any]:
    """EVERY response body goes through this. A response without as_of_tick is a bug
    (10 §12 R11): a stale projection must be visible, never silently believed."""
```

```python
# polis/observatory/readmodels/base.py
class ReadModel:
    def __init__(self, db: Database, run_id: UUID) -> None: ...
    async def as_of(self) -> AsOf: ...

# polis/observatory/readmodels/runs.py
class RunReadModel(ReadModel):
    async def list(self, *, sweep_id: UUID | None = None,
                   tags: Sequence[str] = ()) -> list[Mapping[str, Any]]: ...
    async def summary(self) -> Mapping[str, Any]: ...
    async def manifest(self) -> Mapping[str, Any]:
        """Reproducibility tuple + metric_manifest + mechanism_manifest + ablations + scale."""
    async def tick_summary(self, tick: int) -> Mapping[str, Any]: ...

# polis/observatory/readmodels/metrics.py
Downsample = Literal["none", "lttb", "mean", "last"]

class MetricReadModel(ReadModel):
    async def catalogue(self) -> list[Mapping[str, Any]]:
        """Registry slice for THIS run, read from runs.metric_manifest, not from an import:
        id, unit, cadence, rq, definition, analogue, analogue_caveat, definition_hash."""
    async def series(self, metric: str, *, from_tick: int, to_tick: int | None,
                     downsample: Downsample = "lttb", points: int = 2000
                     ) -> Mapping[str, Any]:
        """Returns {metric, unit, cadence, definition_hash, points: [[tick, value], ...],
        downsampled: bool, source_points: int}. Downsampling happens in SQL/Python on the
        server. NEVER stream 43,200 points to a browser."""
    async def markers(self, *, from_tick: int, to_tick: int) -> Mapping[str, Any]:
        """Shock markers from kind 99001, invariant WARN markers from 1010, and shaded
        bands for windows with an active set_parameter (99010 -> 99011)."""

# polis/observatory/readmodels/world.py
class WorldReadModel(ReadModel):
    async def static_map(self) -> Mapping[str, Any]:
        """districts + places + tiles digest + path geometry. Immutable per run; served with
        a long Cache-Control and an ETag over the world hash."""
    async def map_at(self, tick: int) -> Mapping[str, Any]:
        """Historical occupancy and district state. Live comes over WS, never here."""

# polis/observatory/readmodels/agents.py
class AgentReadModel(ReadModel):
    async def list(self, *, where: Mapping[str, Any], order: str, limit: int,
                   cursor: str | None) -> Mapping[str, Any]: ...
    async def state(self, agent_id: str, *, tick: int | None = None) -> Mapping[str, Any]: ...
    async def timeline(self, agent_id: str, *, from_tick: int, to_tick: int,
                       kinds: Sequence[int] = ()) -> list[Mapping[str, Any]]: ...
    async def memories(self, agent_id: str, *, mtype: str | None,
                       from_tick: int, to_tick: int) -> list[Mapping[str, Any]]:
        """Includes parent_memory_ids so the UI can draw reflection provenance: an abstract
        belief traces back to the concrete observations that produced it (03 §2.3)."""
    async def beliefs(self, agent_id: str, *, tick: int) -> Mapping[str, Any]:
        """Belief vector at `tick` plus the 10060 update history behind each proposition."""
```

```python
# polis/observatory/inspector.py  — GOAL G6. The most important object in this chunk.
@dataclass(frozen=True, slots=True)
class InspectorPayload:
    perception:  Mapping[str, Any]          # the Observation as built in PHASE 1, + digest_hash
    salience:    Mapping[str, Any]          # score, components, cutoff, rank, routed_mode
    retrieval:   list[Mapping[str, Any]]    # memory_id, type, text, importance, recency,
                                            # relevance, score, rank, parent_memory_ids
    prompt:      Mapping[str, Any]          # template, template_hash, tokens_in, rendered,
                                            # source, hash_matches
    response:    Mapping[str, Any]          # raw_text, parsed_ok, repair_attempts, reasoning,
                                            # cache_hit, latency_ms, cost_usd, sim_aware_flag
    action:      Mapping[str, Any]          # action_id, type, params, origin, validation{5 gates}
    outcome:     Mapping[str, Any]          # events[], ledger_legs[], deltas{wealth,needs,
                                            #   beliefs,relationships}
    as_of_seq:   int
    gaps:        tuple[str, ...]            # named absent sections, never silently empty

class Inspector:
    def __init__(self, db: Database, run_id: UUID,
                 reconstructor: "PromptReconstructor | None" = None) -> None: ...
    async def at(self, agent_id: str, tick: int) -> InspectorPayload: ...

class PromptReconstructor(Protocol):
    """Implemented in polis.research (C24) and injected by the composition root.
    polis.observatory never imports it — see §9.4."""
    async def render(self, run_id: UUID, agent_id: str, tick: int,
                     template: str) -> tuple[str, str]: ...     # (rendered, prompt_hash)
```

```python
# polis/observatory/live.py
class LiveHub:
    """ONE Redis subscription per run, fanned out to browser sockets. Never one
    subscription per tab (10 §8.4)."""
    def __init__(self, redis: Redis, *, ring: int = 256, rate_hz: int = 10,
                 max_channels: int = 16, max_pins: int = 32) -> None: ...
    async def start(self, run_id: UUID) -> None: ...
    async def attach(self, ws: WebSocket, run_id: UUID) -> None: ...
    async def pin(self, run_id: UUID, agents: Sequence[str]) -> None:
        """The ONE client->engine influence permitted (10 §0.3 item 4). Bounded at 32.
        Selects ephemeral 90050 membership only: never the log, never state, never an
        RNG draw. Published to a dedicated pin key the engine reads at PHASE 6 render
        time; it does not enter the tick's transaction."""
    def stats(self) -> Mapping[str, Any]: ...                   # dropped frames, subscribers
```

**C23a HTTP surface** — all `GET`, all read-only, all enveloped, base `/api/v1`:

| Path | Returns |
|---|---|
| `/health` | Liveness, DB and Redis reachability, engine heartbeat age |
| `/runs`, `/runs/{run_id}`, `/runs/{run_id}/manifest` | Run list, row + derived summary, reproducibility tuple |
| `/runs/{run_id}/metrics/catalogue` | Registry slice with `definition_hash` |
| `/runs/{run_id}/metrics?metric=&from_tick=&to_tick=&downsample=&points=` | Metric series |
| `/runs/{run_id}/metrics/markers?from_tick=&to_tick=` | Shock, WARN and shaded-band markers |
| `/runs/{run_id}/ticks/{tick}` | Counts by kind, llm calls, cost, routing split, invariant results |
| `/runs/{run_id}/map/static`, `/runs/{run_id}/map?tick=` | Static geometry; historical occupancy |
| `/runs/{run_id}/agents?where=&order=&limit=&cursor=` | Filtered agent list |
| `/runs/{run_id}/agents/{agent_id}` | Agent state at latest tick |
| **`/runs/{run_id}/agents/{agent_id}/tick/{tick}`** | **The inspector payload** |
| `/runs/{run_id}/agents/{agent_id}/timeline?from=&to=&kinds=` | Event timeline |
| `/runs/{run_id}/agents/{agent_id}/memories?type=&from=&to=` | Memory stream + provenance |
| `/runs/{run_id}/agents/{agent_id}/beliefs?tick=` | Belief vector + `10060` history |
| `/runs/{run_id}/agents/{agent_id}/salience?from=&to=` | Salience series with the cutoff overlaid |
| `WS /api/v1/ws/live?run_id=` | `10 §8.4` protocol |

## 5b. Interfaces you provide (C23b)

```python
# polis/observatory/causal.py
@dataclass(frozen=True, slots=True)
class CausalNode:
    seq: int; kind: int; kind_name: str; tick: int
    actor_id: str | None; subject_ids: tuple[str, ...]
    depth: int; summary: str                       # rendered from the kind's schema, not free text

@dataclass(frozen=True, slots=True)
class CausalGraph:
    root_seq: int
    nodes: tuple[CausalNode, ...]
    edges: tuple[tuple[int, int], ...]             # (cause_seq, seq)
    truncated: bool
    truncation_reason: Literal["depth", "nodes", "none"]

class CausalExplorer:
    async def causes(self, seq: int, *, depth: int = 12) -> CausalGraph:
        """Backward WITH RECURSIVE over ev_cause. cause_seq < seq always, so the graph is
        acyclic and the walk terminates without a visited set."""
    async def effects(self, seq: int, *, depth: int = 12, limit: int = 200) -> CausalGraph:
        """Forward. Fanned out, so BOTH caps bind and `truncated` is set explicitly."""
    async def why(self, metric: str, tick: int, *, window: int) -> Mapping[str, Any]:
        """The macro entry point. Selects the events that MOVED the metric in the window
        (per the metric's declared `moved_by` kind list), walks each backwards, clusters the
        roots by kind, ranks by subtree mass, and returns
        {metric, tick, window, roots: [{kind, kind_name, seq, subtree_size, share,
         example_seqs[]}], covered_share, truncated}."""

# polis/observatory/search.py
@dataclass(frozen=True, slots=True)
class EventQuerySpec:
    run_id: UUID
    kinds: tuple[int, ...] = ()
    actor_id: str | None = None
    subject_id: str | None = None
    from_tick: int | None = None
    to_tick: int | None = None
    text: str | None = None                        # FTS over payload->>'text'
    json_path: str | None = None                   # jsonb_path_ops
    cursor: str | None = None
    limit: int = 100

class QueryRefused(Exception):
    """Raised when a spec has neither `kinds` nor a tick range. 10 §8.2e: every events query
    must carry a run_id AND either a kind or a tick range."""

class EventSearch:
    async def search(self, q: EventQuerySpec) -> Mapping[str, Any]: ...
    async def one(self, seq: int) -> Mapping[str, Any]:
        """Decoded event + kind name + the kind's payload schema, so the UI renders a
        payload it has never seen before without a code change (02 §1.2)."""
    def plan(self, q: EventQuerySpec) -> str: ...  # the EXPLAIN text; used by the CI index test

# polis/observatory/compare.py
@dataclass(frozen=True, slots=True)
class TupleDiff:
    field: Literal["config_hash","prompt_manifest","model_manifest","code_git_sha",
                   "mechanism_manifest","metric_manifest","ablations","scale"]
    values: Mapping[str, Any]                      # run_id -> value
    declared: bool                                 # True iff it is the sweep's treatment axis
    blocking: bool

@dataclass(frozen=True, slots=True)
class ComparisonView:
    run_ids: tuple[UUID, ...]
    tuple_diffs: tuple[TupleDiff, ...]             # rendered FIRST, undeclared diffs in red
    metric_drift: tuple[Mapping[str, Any], ...]    # 10 §1.10 query result
    series: Mapping[str, Any]                      # overlaid, with seed bands where a cell
    gate_matrix: Mapping[str, Mapping[str, str]]   # run -> gate -> verdict
    ablation_ladder: Mapping[str, Any] | None      # Δ and CI per arm, if the runs form one
    refused: tuple[str, ...]                       # metrics refused for drift

class RunComparer:
    async def compare(self, run_ids: Sequence[UUID], *, metrics: Sequence[str],
                      allow_metric_drift: bool = False) -> ComparisonView:
        """Order is binding: tuple diff, then drift check, then series. On non-empty drift
        it REFUSES the affected metric and shows the banner (10 §1.10, §12 R1/R2)."""

# polis/observatory/scorecard_view.py
class ScorecardReadModel(ReadModel):
    async def rows(self, *, at_tick: int | None = None) -> Mapping[str, Any]:
        """Reads kind 20070 snapshots (or C22's /scorecard for a live gateway). Renders the
        VECTOR: nine dimensions, native reference rows, every disclosure column, and the
        ineligibility banner. Computes no composite scalar and offers no sort-by-overall."""
```

**C23b additional HTTP surface:**

| Path | Returns |
|---|---|
| `/runs/{run_id}/events?kind=&actor=&subject=&from_tick=&to_tick=&q=&json_path=&cursor=` | Log search |
| `/runs/{run_id}/events/{seq}` | One event, decoded, with kind name and schema |
| `/runs/{run_id}/events/{seq}/causes?depth=` | Backward causal walk |
| `/runs/{run_id}/events/{seq}/effects?depth=&limit=` | Forward causal walk |
| `/runs/{run_id}/why?metric=&tick=&window=` | Ranked root causes |
| `/runs/{run_id}/gates` | Gate report (`10 §2.4` JSON) |
| `/runs/{run_id}/mechanisms?active=true` | Active mechanisms with `entails` strings |
| `/runs/{run_id}/scenario` | Scenario, triggers fired, injections with signature status |
| `/runs/{run_id}/llm_calls/{call_id}` | One call: purpose, model, params, tokens, cost, cache, parse |
| `/runs/{run_id}/firms`, `/markets/{symbol}/ohlcv`, `/elections`, `/cases`, `/banks` | Domain projections |
| `/sweeps`, `/sweeps/{sweep_id}`, `/sweeps/{sweep_id}/cells` | Experiment views + pre-registration |
| `/compare?runs=a,b,c&metric=&allow_metric_drift=` | Tuple diff + aligned series |
| `/runs/{run_id}/scorecard?at_tick=` | The external-agent scorecard vector |

---

## 6. Interfaces you consume

| From | Symbol | Notes |
|---|---|---|
| C01 | `Settings`, `ObservatorySettings`, `canonical_json` | |
| C02 | `KIND_REGISTRY`, `spec(kind)`, `is_ephemeral` | kind names and payload schemas for rendering |
| C03 | `Database.open(role="reader")`, `EventRepository.scan/count/by_cause`, `MetricRepository.series`, `RunRepository.get/list` | **reader role only** |
| C06 | ephemeral payload shapes 90010, 90050–90054; `map/static` payload | `05 §11` |
| C09 | kinds 4002 `SALIENCE_SCORED`, 4003 `COGNITION_ROUTED`, 4004/4005, 4007 | the inspector's `salience` block and `identity_summary` |
| C08 | `memories.parent_memory_ids`, retrieval score components | the `retrieval` block |
| C10 | `ActionOutcome`, the five gate names | the `action.validation` block |
| C22 | `ScorecardRow` shape, kind 20070 | C23b render only |
| C24 | `runs.metric_manifest`, gate report JSON, `PromptReconstructor` | injected, never imported |
| C25 | `scenario_injections`, kinds 99001/99010/99011 | markers and the scenario view |

> **This chunk owns no events and no writes.** If you find yourself needing one, you have
> found a design error, not a missing feature.

---

## 7. Data model touched

| Table / view | R/W | Notes |
|---|---|---|
| `runs`, `events`, `llm_calls`, `metrics`, `checkpoints` | **R** | via `polis_reader` |
| `agents`, `agent_skills`, `memories`, `beliefs`, `households`, `relationships` | **R** | inspector and agent tabs |
| `districts`, `places`, `tiles`, `place_paths` | **R** | static map, fetched once per run |
| `firms`, `employments`, `ohlcv`, `orders`, `trades`, `loans`, `banks` | **R** | domain panels (C23b) |
| `posts`, `articles`, `elections`, `votes`, `policies`, `crimes`, `court_cases` | **R** | domain panels (C23b) |
| `external_agents`, `external_latency`, `v_agent_control` | **R** | scorecard view (C23b) |
| `sweeps`, `scenario_injections` | **R** | comparison and scenario views (C23b) |
| **Everything** | **never W** | `polis_reader` has `SELECT` only; a test asserts the role cannot write |

Two indexes may be **requested** if the `EXPLAIN` test shows a sequential scan; they are added by a migration in C03's chain, not here, and each must be justified against a named endpoint.

---

## 8. Event kinds owned

**None.** The Observatory neither declares nor emits any kind. It *consumes* ephemerals 90010, 90020, 90050–90054 and persisted kinds across every range.

The one client-originated signal, `pin`, deliberately produces no event: it changes which agents appear in ephemeral 90050 and touches nothing persisted, hashed, or RNG-consuming. If pinning ever needs to be in the log, the design is wrong.

---

## 9. Implementation notes

### 9.1 The read-only rule, enforced not promised

Four mechanisms, all checkable:

1. `Database.open(role="reader")` — `polis_reader` has `SELECT` only and no `INSERT` on `events` (`02 §2.1`). `tests/integration/test_observatory_readonly.py` asserts the role cannot write.
2. `import-linter` contract: `polis.observatory` may import `polis.store`, `polis.events`, `polis.config` and nothing else. Never `polis.kernel`, never `polis.agents`, never `polis.research`.
3. The Redis client is opened in subscribe-only mode; `publish` is not called anywhere in the package (AST test).
4. No route handler may advance the clock, inject an event, change a parameter, or start/stop a run. **There is no such endpoint, and adding one is the failure this section exists to prevent.** Shocks are injected by the signed scenario DSL before the run starts, never by a dashboard button.

### 9.2 The agent inspector — how each block is sourced

| Block | Source | Failure mode if absent |
|---|---|---|
| `perception` | The `4001 PERCEPTION_BUILT` digest for `(agent, tick)` joined to the reconstructed `Observation` for that tick, or the stored blob where the run kept one | If the agent was reflex and unsampled (`02 §3.3`), the digest row does not exist. Report `gaps: ["perception"]` — **never** rebuild an approximation and present it as fact. |
| `salience` | `4002` payload: score, components, weights, cutoff from `4003` for the tick, rank computed from `4003.salience_p50/p90` plus the agent's score | `4002` is sampled for reflex agents. Absent → `gaps: ["salience"]` |
| `retrieval` | `memories` joined to the `4004.retrieval_k_used`; per-memory recency/importance/relevance recomputed with the same scorer weights and `last_accessed_tick` **as of that tick** | Recomputing with today's `last_accessed_tick` gives a different score than the run saw. Use the tick's value or mark the score `approximate: true` |
| `prompt` | `llm_calls.prompt_text` if the run carries `keep_prompts`; else reconstruction (§9.4); else `source: "unavailable"` | See §9.4 |
| `response` | `llm_calls` row by `call_id` from `4004.llm_call_id` | Cache hits have `latency_ms` = lookup time and `cost_usd = 0`; render that, do not hide it |
| `action` | The action's own event plus the five gate verdicts from `ACTION_REJECTED` or the resolver's success event | `legality: "clean"` vs `"flagged"` — flagged is not rejected (`04 §11`) |
| `outcome` | `events WHERE cause_seq = <action event seq>`, `ledger_entries WHERE event_seq IN (...)`, and state deltas diffed across the tick boundary | Empty `events` is legitimate (an `IDLE`); render "no downstream effects", never a spinner |

**`gaps` is not optional.** A block that is absent because of sampling is a different thing from a block that is absent because of a bug, and the UI must show which. Silently rendering an empty panel is how a researcher concludes the agent thought nothing when in fact nothing was logged.

### 9.3 Cognition sampling is the inspector's hardest constraint

At `cognition_sample_rate: 0.02`, ~93% of agent-ticks are reflex and ~98% of those have no `4001`/`4002` row. The inspector will therefore be **empty for most (agent, tick) pairs**, and that is correct behaviour, not a defect. Three consequences to build for:

1. The UI's default entry point is not "pick an agent and a tick". It is "pick an agent, then jump to its next **deliberate** tick" — `GET /agents/{id}/timeline?kinds=4004,4006` gives the list. Land the user somewhere that has content.
2. Serve `sampled: true|false` on every block so the difference between "did not happen" and "was not recorded" is on screen.
3. Recommend `runs.tags @> {keep_prompts}` and a raised `cognition_sample_rate` for **inspection runs** (50 agents, 500 ticks). Say so in the UI when a run has neither: the run is cheap, not broken.

### 9.4 Prompt reconstruction without importing the engine

`llm_calls.prompt_text` is off by default (`03 §1.3`) because it multiplies storage ~10×. `10 §8.2c` requires the endpoint to re-render the template and compare `prompt_hash`. But re-rendering needs `polis.agents.cognition.build_prompt` and `polis.llm.PromptLibrary`, and `polis.agents → kernel`, which the Observatory may not import.

**Resolution.** Reconstruction is a `PromptReconstructor` **Protocol** declared here and implemented in `polis/research/inspect.py` (C24, the composition root, which may import everything). `polis observe --with-reconstruction` builds the implementation and passes it as an `extra_router` / constructor argument. `polis.observatory` never imports `polis.research`; the import-linter contract stays intact.

`prompt.source` therefore takes **three** values: `stored` · `reconstructed` · `unavailable`. `10 §8.2c` lists only the first two — **this is a spec amendment to raise, not to apply silently.**

`hash_matches: false` is rendered as a loud, blocking error, not a warning. A mismatch means either the reconstruction path or the prompt manifest is wrong, and if you cannot reconstruct the prompt then G6 has failed and the run is not legible.

### 9.5 Performance isolation — the dashboard must not cost the engine

Five rules, all from `10 §8.5`, all testable:

1. **Live data comes from Redis, not Postgres.** The map view issues **zero** queries per tick. If you find yourself writing a per-tick SQL query for the map, stop.
2. The engine's PHASE 6 publish is fire-and-forget onto a bounded queue. Slow Redis → ephemerals dropped and counted in `sys.ephemeral.dropped`; **the tick never blocks**.
3. With no subscriber on the map channel, kinds 90050 and 90051 are **not computed** at all (C06 honours this; the Observatory's job is to signal subscription accurately and to unsubscribe on the last socket close). `--no-ephemerals` disables the whole path for benchmark runs.
4. Historical queries run as `polis_reader` against a read replica where configured, else the primary with `statement_timeout=5s` and `max_pool=8`. A hung dashboard query must die before it matters.
5. Every `events` query carries a `run_id` **and** a `kind` or `tick` predicate. `QueryRefused` enforces it in code; `tests/integration/test_observatory_index_usage.py` runs `EXPLAIN` over every endpoint's canonical query and asserts index usage.

Continuous detector for R3: correlate `sys.engine.tick_wall_ms_p99` with connected-client count and alert on a positive slope. Ship the query; a claim of isolation that is never measured is a claim.

### 9.6 WebSocket protocol

`GET /api/v1/ws/live?run_id=<uuid>`, JSON frames exactly as `10 §8.4`. Client ops: `subscribe`, `unsubscribe`, `pin`, `ping`. Server ops: `hello`, `tick`, `eph`, `metrics`, `events`, `halt`, `lag`, `pong`.

| Rule | Detail |
|---|---|
| Fan-out | **One** Redis subscription per run (`polis:run:<id>:{tick,eph,metrics}`), fanned out to sockets. One per tab is the mistake that makes 20 open tabs a load test against the engine's Redis. |
| Backpressure | Per-connection ring of 256 frames; on overflow drop oldest and send `lag {dropped, reason}`. The engine is never blocked by a slow client. |
| Rate | 10 frames/s/connection; `metrics` and `eph` coalesced per tick. |
| Reconnect | `since_tick` replays from **Postgres projections**, not Redis — Redis has no history. Gaps are explicit, never silently filled. |
| Historical | Past ticks are HTTP. The socket is for live only. Serving history over WS is how the ring buffer becomes a memory leak. |
| Limits | Advertised in `hello`: `max_channels: 16`, `max_pins: 32`, `max_frame_bytes: 262144`, `rate_hz: 10`. |

### 9.7 The `why?` query (C23b)

The macro entry point exists to answer *"why did unemployment spike at tick 4,201?"* with a **ranked list of root causes and the number of downstream separations each accounts for**, every node clickable through to the inspector.

```
1. moved_by = the metric's declared kind list (M4 -> 5011 FIRED, 5012 QUIT, 5013, 5042)
2. movers   = events of those kinds in [tick - window, tick]
3. for each mover: walk causes() to depth D, collect the root (depth == max reached)
4. cluster roots by kind; subtree_size = number of movers reaching that root
5. rank by subtree_size desc; covered_share = Σ subtree_size / |movers|
```

`covered_share < 1` is normal and must be shown: some movers have no `cause_seq` chain (institutional cadences, vitals), and pretending otherwise invents causality. `moved_by` lives in the metric registry (C24), not in the Observatory — a metric that has not declared its movers gets `why?` disabled with an explicit message, not a guess.

### 9.8 Run comparison order is binding (C23b)

Render in this order and no other: **(1)** the reproducibility-tuple diff, with undeclared differences in red; **(2)** the `10 §1.10` metric-drift query; **(3)** overlaid series with seed bands; **(4)** the gate matrix; **(5)** the ablation ladder. On a non-empty drift result the view **refuses** to overlay the affected metric and shows the drift banner. `allow_metric_drift=true` is permitted, stamps `metric_drift: true` onto the response, and the UI carries that stamp onto every rendered chart — not into a tooltip.

The reason this is a UI rule and not an analysis rule: the chart is the artefact that ends up in a slide deck, and by then nobody remembers which flag was passed.

### 9.9 `web/` — the front end

React + TypeScript + Vite, built to `web/dist` and served as static files by the same FastAPI app. Deck.gl (or plain canvas) for the map, uPlot for charts — both chosen because they render 2,000 downsampled points without a layout pass. No sprites, no animation beyond linear position interpolation between ticks (`01-PRD.md N3`).

Four views in C23a: **Map**, **Charts**, **Agents** (list → detail), **Inspector**. Four more in C23b: **Causal**, **Search**, **Compare**, **Arena**. A persistent lag banner renders whenever `lag_ticks > 5`; it is not dismissible.

---

## 10. Configuration keys

```yaml
observatory:
  enabled: true
  bind: "127.0.0.1:8080"
  read_replica_url: null            # falls back to the primary as polis_reader
  statement_timeout_ms: 5000
  max_pool: 8
  static_dir: "web/dist"
  live:
    ring_frames: 256
    rate_hz: 10
    max_channels: 16
    max_pins: 32
    max_frame_bytes: 262144
  series:
    default_points: 2000
    max_points: 10000
    default_downsample: lttb        # lttb | mean | last | none
  causal:                           # C23b
    max_depth: 12
    max_nodes: 200
    why_default_window: 240
  search:                           # C23b
    max_limit: 500
    require_kind_or_tick: true      # QueryRefused when false-y; do not set to false
  inspector:
    reconstruct_prompts: false      # true requires `polis observe --with-reconstruction`
    lag_banner_ticks: 5
```

`--no-ephemerals` is an **engine** flag (C06/C04), named here because it is the escape hatch when R3 fires.

---

## 11a. Acceptance criteria — C23a (gates M1)

1. `import-linter` passes: `polis.observatory` imports no `polis.kernel`, no `polis.agents`, no `polis.research`.
2. An AST scan of `polis/observatory/` finds no Redis `publish` call and no SQL `INSERT`/`UPDATE`/`DELETE`.
3. Running as `polis_reader`, an attempted write raises; the test asserts the failure rather than assuming the role is configured.
4. **Every** response body contains `as_of_tick` and `as_of_seq`; a route returning a bare payload fails a response-schema test that covers all registered routes.
5. `lag_ticks > 5` renders a non-dismissible banner in the UI and is present in the JSON.
6. The map renders 1,000 agents live from ephemerals alone, and a network trace shows **zero** Postgres queries per tick while the map is open.
7. Static geometry is fetched once per run and served with an ETag; a reload issues a `304`.
8. With no client subscribed to the map channel, the engine emits no 90050 or 90051 (asserted against the engine's counters).
9. A metric series over 43,200 ticks returns ≤ `points` samples with `downsampled: true` and `source_points` set; the raw series is never streamed.
10. Cadence is respected: a `sim_quarter` metric returns only its computed points and **no forward fill** anywhere in the response.
11. **The inspector returns all seven blocks for a deliberate agent-tick**, end to end: perception, salience, retrieval, prompt, response, action, outcome.
12. For a reflex, unsampled agent-tick the inspector returns `gaps: ["perception","salience","prompt","response"]` with `sampled: false`, and renders as "not recorded", never as "nothing happened".
13. Retrieval scores in the inspector reproduce the run's ranking: the top-`k` order matches `4004`'s recorded `retrieval_k_used` set.
14. `prompt.source == "unavailable"` when neither `prompt_text` nor a reconstructor is present, and the UI says so explicitly.
15. With `--with-reconstruction`, `hash_matches` is `true` for a golden-run deliberate tick, and a deliberately corrupted prompt manifest produces `hash_matches: false` rendered as a blocking error.
16. `outcome.events` matches exactly `events WHERE cause_seq = <action seq>`; `outcome.ledger_legs` sum to zero for any tick with a money-moving action.
17. Memory provenance: an inspector-linked reflection walks back through `parent_memory_ids` to concrete observations in the UI.
18. `POST`/`PUT`/`PATCH`/`DELETE` on any observatory path returns `405`; the router registry contains no non-GET route except the WS upgrade.
19. `pin` is bounded at 32, affects 90050 membership only, and produces no event, no state change and no RNG draw.
20. Ring-buffer backpressure: a client that stops reading receives `lag` and is not disconnected; the engine's tick timing is unchanged.
21. Reconnect with `since_tick` replays from Postgres and reports an explicit gap where one exists.
22. `tests/integration/test_observatory_index_usage.py` runs `EXPLAIN` over every C23a endpoint's canonical query; none shows a sequential scan on an `events` or `memories` partition.
23. With the dashboard open and 5 clients attached during a 500-tick run, `sys.engine.tick_wall_ms_p99` is within 3% of the same run with no clients.
24. `mypy --strict polis/observatory` passes; `web/` builds clean with `tsc --noEmit`.

## 11b. Acceptance criteria — C23b

1. `causes()` on a fixture chain returns nodes in depth order and terminates without a visited set (proved by asserting `cause_seq < seq` for every edge).
2. `effects()` sets `truncated: true` with `truncation_reason` when either the depth or node cap binds; the cap is never silently exceeded.
3. `why?metric=unemployment_rate&tick=T` returns roots ranked by `subtree_size` with `covered_share` reported; every returned `seq` resolves in the inspector.
4. A metric with no `moved_by` declaration returns `why` disabled with an explicit message, not an empty list.
5. Event search refuses a spec with neither `kinds` nor a tick range (`QueryRefused`), including through the HTTP layer.
6. FTS finds a planted phrase in `payload->>'text'` and the `EXPLAIN` shows `ev_fts`; a `json_path` filter shows `ev_payload`.
7. Comparison renders the tuple diff **first**; two runs differing in `code_git_sha` show it flagged and `blocking: true`.
8. Two runs whose `metric_manifest` disagrees on a metric's `definition_hash` are **refused** for that metric and the drift banner appears; `allow_metric_drift=true` stamps `metric_drift: true` onto the response and onto every rendered chart.
9. The gate matrix renders V1–V8 per run from the gate report, including `n/a` verdicts.
10. The ablation ladder renders Δ and CI per arm when the selected runs form one, and is absent (not empty) when they do not.
11. The scorecard view renders all nine dimensions plus every disclosure column, shows native reference rows, and offers **no** composite score and no overall sort.
12. A run tagged `invalid_for_cross_agent_comparison`, `paused_for_external` or `custody_delegated` shows the ineligibility banner and its rows are marked ineligible with reasons.
13. `arena.live_scorecard: false` (the default) makes the scorecard view show completed runs only.
14. The scenario view lists every injection with its signature-verification status; an unsigned injection-class event is shown as a failure, not omitted.
15. `EXPLAIN` index-usage test extended to every C23b endpoint; the causal walk uses `ev_cause`, subject filters use `ev_subjects`.
16. C23a's criteria 1–5 and 18 still hold with the C23b routers mounted.

---

## 12a. Tests to write — C23a

| File | Asserts |
|---|---|
| `tests/unit/observatory/test_envelope.py` | Every registered route's response passes the `AsOf` schema; `lag_ticks` arithmetic; unknown engine tick yields `None`, not `0` |
| `tests/unit/observatory/test_downsample.py` | LTTB preserves endpoints and extrema; `points` respected; `source_points` correct; cadence gaps are not filled |
| `tests/unit/observatory/test_inspector_blocks.py` | Each of the seven blocks built from fixtures; `gaps` populated for every absent block; `sampled` flag correct |
| `tests/unit/observatory/test_inspector_retrieval.py` | Recomputed scores reproduce the recorded top-`k` order; `approximate: true` when `last_accessed_tick` cannot be reconstructed |
| `tests/unit/observatory/test_no_write_ast.py` | AST scan: no `publish`, no `INSERT/UPDATE/DELETE`, no `polis.kernel`/`polis.agents`/`polis.research` import |
| `tests/unit/observatory/test_routes_readonly.py` | Route table contains no non-GET route besides the WS upgrade; every verb returns 405 |
| `tests/integration/test_observatory_readonly.py` | `polis_reader` write attempt raises; the assertion is on the failure, not on config |
| `tests/integration/test_inspector_end_to_end.py` | **G6 regression.** Golden 100-tick run: one deliberate agent-tick reconstructed end to end and cross-checked against `13_agent_walkthrough.ipynb`'s numbers |
| `tests/integration/test_live_map.py` | 1,000 agents over WS from ephemerals; zero Postgres queries per tick; unsubscribe stops 90050 computation |
| `tests/integration/test_ws_backpressure.py` | Stalled reader gets `lag`, is not dropped, and engine tick timing is unchanged |
| `tests/integration/test_ws_reconnect.py` | `since_tick` replays from projections; an explicit gap is reported when Redis history is missing |
| `tests/integration/test_observatory_index_usage.py` | `EXPLAIN` over every endpoint's canonical query; no seq scan on `events`/`memories` |
| `tests/integration/test_observatory_isolation.py` | 500 ticks × 5 clients: `sys.engine.tick_wall_ms_p99` within 3% of the no-client run |

## 12b. Tests to write — C23b

| File | Asserts |
|---|---|
| `tests/unit/observatory/test_causal_walk.py` | Depth order; acyclicity via `cause_seq < seq`; both caps set `truncated` with a reason; forward fan-out bounded |
| `tests/unit/observatory/test_why_ranking.py` | Roots ranked by subtree mass; `covered_share < 1` reported honestly; missing `moved_by` disables the query |
| `tests/unit/observatory/test_query_refusal.py` | `QueryRefused` for kind-less, tick-less specs, through the read model and through HTTP |
| `tests/unit/observatory/test_compare_order.py` | Tuple diff rendered first; undeclared diff flagged blocking; drift refuses the metric; `allow_metric_drift` stamps the response |
| `tests/unit/observatory/test_scorecard_view.py` | Nine dimensions and all disclosure columns present; no composite anywhere; ineligibility banner and reasons |
| `tests/integration/test_event_search.py` | FTS hit on a planted phrase using `ev_fts`; JSON-path filter using `ev_payload`; cursor paging is stable across inserts |
| `tests/integration/test_causal_explorer.py` | On the golden run, `why?metric=unemployment_rate` returns roots whose seqs all resolve in the inspector |
| `tests/integration/test_compare_drift.py` | Two runs with differing `definition_hash` are refused; the banner appears; the override stamps every chart payload |
| `tests/integration/test_scenario_view.py` | Every injection's signature status shown; an unsigned injection-class event renders as a failure |

---

## 13. Definition of done

All of `chunks/README.md §5`, plus:

**C23a (gates M1):**
1. `polis observe` serves the API and the built `web/dist`; `GET /health` reports DB, Redis and engine heartbeat age.
2. The four C23a views work against a live 1,000-agent run and against a completed run.
3. `import-linter` contract `observatory_read_only` in `.importlinter`.
4. The G6 walkthrough is demonstrated: from a macro chart, click a tick, reach an agent, and read its perception → memories → prompt → response → action → outcome in full.
5. Handback records: (a) the `prompt.source: "unavailable"` third value as a `10 §8.2c` amendment to raise; (b) the measured tick-timing delta with 5 clients attached; (c) the recommended inspection-run config (`keep_prompts`, raised `cognition_sample_rate`) with measured storage cost.

**C23b:**
6. The four C23b views work; `why?` answers the unemployment question on the golden run with clickable roots.
7. Every C23b endpoint appears in the `EXPLAIN` index-usage test.
8. Handback records the `moved_by` registry field required from C24 and its coverage across the metric catalogue.

---

## 14. Traps

1. **Dashboard load degrading the engine (R3).** The single most likely way this chunk does damage. The map view issuing one query per tick per client is invisible at 1 client and fatal at 8. Live data is Redis-only; measure `tick_wall_ms_p99` against client count and treat a positive slope as a bug, not a tuning opportunity.
2. **One Redis subscription per browser tab.** Works beautifully in development with one tab. At a demo with twenty tabs open it multiplies the engine's publish fan-out by twenty and the room watches the tick rate collapse.
3. **Streaming the raw metric series.** 43,200 points × 30 metrics is ~10 MB per chart load and the browser stalls before the query does. Downsample server-side, always, and report `downsampled: true` so nobody reads a smoothed extremum as data.
4. **Forward-filling cadence gaps in the API.** A `sim_quarter` metric aligned to a daily one by fill produces a beautifully clean spurious lead–lag (R5). Nulls stay null; filling is an analysis decision made once, visibly, in a notebook.
5. **Rendering an empty inspector panel for an unsampled tick.** The researcher concludes the agent thought nothing. It thought; it was not recorded. `gaps` and `sampled` must be on screen, not in a log line.
6. **Reconstructing perception "approximately" when the digest row is missing.** A plausible-looking `Observation` that the agent never saw is worse than a blank panel, because it will be quoted in a paper.
7. **Recomputing retrieval scores with today's `last_accessed_tick`.** Memory access freshens on retrieval (`04 §6.3`), so the score you compute now is not the score that produced the ranking. Use the tick's values or mark it approximate.
8. **Treating `hash_matches: false` as a warning.** It means the prompt shown is not the prompt sent. That is a total failure of G6 and it must block, not tint.
9. **Adding "just one" write endpoint.** A pause button, a re-run button, a "nudge this agent" debug affordance. Every one of them puts a wall-clock- and operator-dependent mutation into a run that is supposed to be reproducible, and none of them leaves a trace in the log.
10. **Letting `pin` grow.** Unbounded pinning turns 90050 from 2.5 KB/tick into 20× everything else combined. 32 pins, enforced server-side, advertised in `hello`.
11. **Serving history over the WebSocket.** The ring buffer becomes unbounded, the reconnect path becomes two code paths, and gaps get silently filled. Past ticks are HTTP.
12. **Silently comparing runs with different reproducibility tuples (R2).** Two runs that differ in `prompt_manifest` overlaid on one axis is a "treatment effect" that is actually a prompt edit. The tuple diff is rendered *first*, before the chart, or people will not look at it.
13. **Overlaying two series whose `definition_hash` differs (R1).** The same metric id computed two ways is the single most likely route to a wrong published number. Refuse; do not warn.
14. **Putting the drift stamp in a tooltip.** The chart is what ends up in the deck. If `metric_drift: true` is not burned into the rendered image's caption, the override is decorative.
15. **A composite scorecard score, or a default sort by "overall".** It becomes a leaderboard and every reading of it is the misreading T12 exists to prevent. Vector, whole, unsorted by rank.
16. **Showing a stale projection as live (R11).** The dashboard reads tick 4,201 while the engine is at 4,241. Cosmetic if visible, dangerous if not; the lag banner is not dismissible and not a toast.
17. **An `events` query without a `kind` or tick predicate.** One `WHERE actor_id = ...` across a 150M-row partition takes the read pool down, and with it the map for everyone. `QueryRefused` in code, `EXPLAIN` in CI.
18. **Forgetting the forward causal walk fans out.** Backward is bounded by depth; forward is bounded by depth *and* branching. Without a node cap, `effects()` on a payroll event returns the whole tick.
19. **`covered_share` hidden.** A `why?` answer that accounts for 40% of the movers and presents itself as complete invents causality with the authority of a ranked list.
20. **Importing `polis.research` for prompt reconstruction "just here".** It drags `polis.agents` and therefore `polis.kernel` into a read-only process. Inject the Protocol; the composition root wires it.
21. **Building the front end as a product.** Sprites, easing curves and a day/night shader are the most enjoyable work in this repo and the least useful. `01-PRD.md N3`: this is a research instrument.
