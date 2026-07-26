# C24 — Metric catalogue, invariants, validity gates, experiment harness, replay, exports

**M1 (C24a) / M2 (C24b)** · `polis/research/` · **C24a depends on:** C01, C02, C03, C04 · **C24b depends on:** C24a, C11, C12, C14 (M2), and reads from every later chunk · **Blocks:** M1 completion (C24a), M2 completion (C24b), C23 (metric catalogue, gate report), C25 (invariant guard, scenario gates) · **Size:** L (C24a ≈ 3–4 days, C24b ≈ 2 weeks)

> **This brief is split.** §3, §5, §11 and §12 are divided into **a** and **b** parts.
> **C24a gates M1**: without a metric writer and running invariants, a 1,000-agent run
> produces no evidence that it worked. Ship C24a with M1 and C24b with M2.

---

## 1. Context

This chunk is where a simulation becomes a research instrument. Everything else produces behaviour; this produces **numbers you are allowed to believe**. Three things make that possible and each is a defence against a named threat: a **metric registry** whose definitions are stated purely in terms of simulation state and hashed into every run (T11, R1); **invariants and validity gates** that halt or disqualify a run rather than letting a broken economy be published (T10, V2); and an **experiment harness** with pre-registration, ablations and a mechanism checklist, because the completion cache makes re-analysis free and therefore makes p-hacking free (T6, T8, T9, R6). The most expensive mistake available in this system is launching a 480-cell sweep and discovering the price afterwards, so cost estimation is a first-class feature and it refuses to launch.

---

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/02-ARCHITECTURE.md` | **all** — §3 log, §4 determinism, §4.4 completion cache, §5 PHASE 9, §7.1 dependency rules, §8.1 `MECHANISM`, §9 invariants |
| `../docs/03-DATA-MODEL.md` | §0 conventions, §1.1 `runs`, §1.2 `events`, §1.3 `llm_calls`, §4 ledger, §10 `metrics`/`sweeps`, §11 retention, §12 rebuild |
| `../docs/10-RESEARCH-AND-OBSERVABILITY.md` | **all — primary source.** §0.4 storage contract, §0.6 amendments, §1 catalogue, §2 gates, §3 harness, §5 replay, §6 ablations, §7 checklist, §9 exports, §10 statistics, §11 checklist, §12 threats |
| `../docs/01-PRD.md` | §3 research questions, §7.1 system metrics, §7.2 V1–V7, §9 threats T1–T12 |
| `../docs/06-ECONOMY-SPEC.md` | §1.7 (INV-MONEY sub-checks), §12 (M1–M28 definitions), §16.3 (cadences) |
| `../docs/07-SOCIETY-SPEC.md` | §5.7, §10 (social/political/legal/mobility definitions) |
| `../docs/08-EXTERNAL-AGENT-PROTOCOL.md` | §6.6–§6.7 (**V8**, ratified) |
| Chunks | C02 (`register_kind`), C03 (`MetricRepository`, `EventRepository`, `RunRepository`, `LedgerRepository`), C04 (`Invariant`, `InvariantRunner`, `WorldStateView`, `TickContext`, `Phase`), C05 (`Purpose`, cost model, cache modes), C25 (scenario kinds; you own the other half of 99xxx) |

---

## 3a. Scope — in (C24a, M1)

1. **The metric registry** — `@metric(...)` decorator, the eight binding fields, `definition_hash`, duplicate detection at import, the per-run metric-count budget.
2. **The collector** — a `PhaseHandler` at PHASE 9 that evaluates due metrics, asserts its own duration against the 50 ms budget, and writes one batched `metrics` row set per tick.
3. **The `metrics` writer** — long/narrow, cents as integer-valued doubles, rates in basis points, **nulls never written**, cadence asserted by the writer.
4. **INV-\* implementations** — every invariant of `02 §9` as a concrete `Invariant` registered into C04's `INVARIANT_REGISTRY`, computed against `WorldStateView` so the money ones are inert under `NullWorldState` in M1 and live from M2.
5. **HALT / WARN policy** — 1010 emission, forced checkpoint, `runs.status='halted'`, non-zero exit; `--continue-on-violation` permanently tagging the run.
6. **System metrics** (`10 §1.8`) — the `sys.*` family, which is how you find out the society you are studying is an artefact of the budget, the parser, or the router.
7. **Kind 99070** `METRIC_DEFINITION_REGISTERED`, emitted once per metric at tick 0, and `runs.metric_manifest`.
8. `polis metrics catalogue --format md|json` and the CI diff against `docs/10`.

## 3b. Scope — in (C24b, M2 and after)

9. **The complete metric catalogue** — economic (§1.3), social (§1.4), political (§1.5), legal (§1.6), demographic (§1.7), system (§1.8), every one with a formal simulation-state definition, a separately-named analogue, and a required caveat.
10. **`polis/research/relationships.py`** — Beveridge, Okun, Phillips, Zipf, business-cycle autocorrelation as *relationships between* metrics, each gated on the four preconditions of `10 §1.9`.
11. **Validity gates V1–V8** as executable procedures returning `GateResult`; `polis gate --run` (V1–V4) and `--sweep` (V5–V7, aggregate V1–V4); **V8** via `polis verify --arena`.
12. **The experiment harness** — pre-registered experiment YAML, load-time validation, `polis sweep`, cell identity, ordering, isolation, failure policy, budget circuit breaker.
13. **Cost estimation** — two probes, cross-cell hit rate, p50/p90, **both clock profiles modelled honestly**, refusal above cap without `--yes`.
14. **Parallel execution and resumability** — bounded worker pool over sorted cells, `--resume` refusing on `analysis_plan_hash` mismatch.
15. **The ablation ladder** — every flag in `10 §6.1`, `LAS` computation, the reading table.
16. **`polis mechanism-check`** — steps 3, 4, 6 and 12 machine-generated; `gates/mechanism_check.json`.
17. **`polis replay`, `polis verify`, `polis rebuild`**, `polis package verify|load`, the reproducibility package layout.
18. **Parquet export** — the 20 tables of `10 §9.2`, `--verify` recomputing checksums **from the log**, `EXPORT_MANIFEST.json`.
19. **`notebooks/`** — the 17 starter notebooks, executed in CI against the golden run.
20. **`polis paper-check`**.
21. Kinds **99050, 99060, 99070, 99090, 99091**.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `Invariant` protocol, `InvariantRunner`, `WorldStateView` protocol, the tick loop | **C04** — you implement invariants, you do not run them |
| Economic metric *definitions* M1–M28 | **`06-ECONOMY-SPEC.md §12` governs.** You restate, add cadence + RQ, and implement |
| Social/political/legal/mobility *definitions* | **`07-SOCIETY-SPEC.md §10` governs.** Same rule |
| The scenario DSL, triggers, steps, injection signing, kinds 99000–99006/99010/99011/99020/99021/99030/99040/99041/99080 | **C25** |
| Rendering any of this | **C23** |
| Scorecard computation and V8 *measurement* | **C22** — you *enforce* V8 |
| The completion cache implementation, provider cost tables | **C05** — you consume `llm_calls` |
| Model routing decisions | **C05 / `09-MODEL-ROUTING.md`** |

---

## 5a. Interfaces you provide (C24a)

```python
# polis/research/metrics/registry.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Final, Literal, Mapping, Protocol, Sequence

Unit = Literal["cents","bp","index_bp","count","ratio_bp","dimensionless_float",
               "usd","tokens","ticks","sim_days","sim_years"]
Cadence = Literal["tick","sim_day","sim_week","sim_month","sim_quarter",
                  "sim_year","on_event","end_of_run"]

@dataclass(frozen=True, slots=True)
class MetricSpec:
    id: str
    unit: Unit
    cadence: Cadence
    rq: tuple[str, ...]                  # 01-PRD §3 ids, or ("SYS",)
    definition: str                      # PURELY simulation state. No human institution named.
    analogue: str                        # named SEPARATELY, never in `definition`, never in `id`
    analogue_caveat: str                 # required; empty string is rejected
    governed_by: str                     # "06-ECONOMY-SPEC.md §12 M4", or "10 §1.5 (owned here)"
    definition_hash: str                 # sha256(id ‖ definition ‖ unit ‖ cadence ‖ dedent(source))
    moved_by: tuple[int, ...] = ()       # event kinds that move it; powers C23b's `why?`
    fn: Callable[["MetricState"], float] | None = None

def metric(*, id: str, unit: Unit, cadence: Cadence, rq: Sequence[str], definition: str,
           analogue: str, analogue_caveat: str, governed_by: str,
           moved_by: Sequence[int] = ()) -> Callable[[Callable[["MetricState"], float]],
                                                     Callable[["MetricState"], float]]:
    """Registers at import. Raises MetricError on: duplicate id, empty rq, empty
    analogue_caveat, an `analogue` word appearing inside `definition`."""

METRIC_REGISTRY: Final[dict[str, MetricSpec]]
def spec(metric_id: str) -> MetricSpec: ...
def manifest() -> dict[str, str]: ...                       # -> runs.metric_manifest
def due(cadence: Cadence, tick: int, clock: Clock) -> bool: ...
def catalogue_markdown() -> str: ...                        # `polis metrics catalogue --format md`
class MetricError(PolisError): ...
```

```python
# polis/research/metrics/state.py
class MetricState(Protocol):
    """The read-only window a metric function may use. Deliberately narrow: a metric that
    needs something absent here is either mis-specified or needs a new accessor, and adding
    one is a reviewed change, not a convenience."""
    tick: int
    clock: Clock
    def agents(self) -> Sequence[Mapping[str, Any]]: ...
    def alive_adults(self) -> Sequence[Mapping[str, Any]]: ...
    def firms(self) -> Sequence[Mapping[str, Any]]: ...
    def ledger_balances(self) -> Mapping[str, int]: ...
    def events_in_window(self, kinds: Sequence[int], from_tick: int) -> Sequence[Event]: ...
    def metric_at(self, metric_id: str, tick: int) -> float | None: ...   # prior values only
    def llm_calls_for_tick(self) -> Sequence[Mapping[str, Any]]: ...
    def counter(self, name: str) -> float: ...              # engine counters (phase timings, drops)

# polis/research/metrics/collector.py
class MetricCollector:                                       # PhaseHandler, phase = Phase.METRICS
    phase: Phase = Phase.METRICS
    name: str = "metrics"
    order: int = 10
    def __init__(self, repo: MetricRepository, state: MetricState, clock: Clock,
                 *, max_metrics: int = 400, budget_ms: int = 50) -> None: ...
    async def run(self, ctx: TickContext) -> None:
        """Evaluates every metric whose cadence is due, in registry id order. Writes ONE
        batched COPY. Asserts its own wall duration against `budget_ms` and emits a warning
        counter on overrun (10 §12 R12). Nulls are NOT written: an absent row means
        'not computed at this tick', never 'zero'."""
    def emit_definitions(self, ctx: TickContext) -> None:    # 99070, once, at tick 0
```

```python
# polis/research/invariants/__init__.py — concrete Invariants registered into C04's registry
class MoneyConservation:      id="INV-MONEY";    severity=HALT; frequency="tick"
class LedgerBalance:          id="INV-LEDGER";   severity=HALT; frequency="tick"
class ShareConservation:      id="INV-SHARES";   severity=HALT; frequency="tick"
class OrderReserves:          id="INV-ORDERS";   severity=HALT; frequency="tick"
class EmploymentIntegrity:    id="INV-EMPLOY";   severity=HALT; frequency="tick"
class ChainIntegrity:         id="INV-CHAIN";    severity=HALT; frequency="checkpoint"
class PopulationBounds:       id="INV-POP";      severity=WARN; frequency="sim_day"
class ActionEntropy:          id="INV-ENTROPY";  severity=WARN; frequency="sim_day"
class NonDegenerate:          id="INV-NONDEGEN"; severity=WARN; frequency="sim_day"
class PriceStability:         id="INV-PRICE";    severity=WARN; frequency="sim_day"   # -> HALT at bound

def register_all(runner: InvariantRunner) -> None: ...
def posthoc_money_check(db: Database, run_id: UUID) -> Mapping[str, Any]:
    """The V2 re-derivation, run outside the engine:
       SELECT tick, SUM(direction*amount_cents) FROM ledger_entries GROUP BY tick
         HAVING SUM(...) <> 0            -> must be empty
       SELECT SUM(balance_cents) FROM ledger_accounts   -> must be exactly 0
    Also returns ticks_checked, because the most dangerous failure is not a violated
    invariant, it is an invariant that quietly stopped running."""
```

**Invariant implementation note.** Every invariant is computed from `WorldStateView` (C04's protocol), never from a direct table read inside the tick. In M1 the composition root supplies `NullWorldState`, so the money/share/order/employment invariants return `Ok` trivially and the population/entropy/non-degeneracy ones are live. From M2 the real view is supplied and the same code becomes load-bearing. **No `if milestone` branch anywhere.**

## 5b. Interfaces you provide (C24b)

```python
# polis/research/gates.py
@dataclass(frozen=True, slots=True)
class GateResult:
    id: Literal["V1","V2","V3","V4","V5","V6","V7","V8"]
    verdict: Literal["pass","fail","n/a"]
    statistic: Mapping[str, Any]
    threshold: Mapping[str, Any]
    window: Mapping[str, int] | None
    query: str
    notes: str
    shock_free: bool = False

class Gate(Protocol):
    id: str
    scope: Literal["run", "sweep"]
    def evaluate(self, ctx: "GateContext") -> GateResult: ...

async def gate_run(db: Database, run_id: UUID) -> Mapping[str, Any]:   # V1-V4 + invariants
async def gate_sweep(db: Database, sweep_id: UUID) -> Mapping[str, Any]:# V5-V7 + aggregated V1-V4
async def gate_arena(db: Database, run_id: UUID) -> GateResult:         # V8; emits 20090 via C22's kind
def gate_report(results: Sequence[GateResult], *, run_id: UUID,
                code_git_sha: str, invariants: Mapping[str, Any]) -> Mapping[str, Any]:
    """The 10 §2.4 JSON. DETERMINISTIC given the run and the code sha: two evaluations
    must produce byte-identical output. Emits 99060 per gate."""
```

```python
# polis/research/experiments/definition.py
@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    unit_of_replication: Literal["seed"]
    estimator: str; contrast: str; test: str; effect_size: str; ci: str
    multiplicity: str; exclusions: str
    minimum_detectable_effect: float
    prediction: str                                  # non-empty; the sign, stated before the run

@dataclass(frozen=True, slots=True)
class Experiment:
    id: str; title: str; research_questions: tuple[str, ...]; owner: str
    base_config: Path; base_config_hash: str
    grid: Mapping[str, Sequence[Any]]
    design: Literal["factorial","one_at_a_time","list"]
    seeds: tuple[int, ...]                           # EXPLICIT integers, never a range expression
    scale_ladder: tuple[int, ...]
    primary: tuple[str, ...]; secondary: tuple[str, ...]; guardrail: tuple[str, ...]
    analysis_plan: AnalysisPlan
    analysis_plan_hash: str
    required_ablations: tuple[str, ...]
    gates_required: tuple[str, ...]
    budget_usd_max: float; halt_at_pct: int; cache_mode: Literal["live","replay","hybrid"]
    parallel: int; retention: Literal["full","metrics_only"]
    headline_cells: tuple[Mapping[str, Any], ...]
    deviations: tuple[Mapping[str, str], ...] = ()

def load_experiment(path: Path) -> Experiment:
    """Validation at LOAD, all blocking: every metric id exists in the registry; every grid
    key exists in the config schema; seeds explicit and unique; exactly one `primary` per
    research question; `prediction` non-empty; and `required_ablations` covers EVERY
    MECHANISM whose `entails` string mentions a primary or secondary metric. A missing
    ablation is a load error, not a review finding (10 §3.2)."""

# polis/research/experiments/sweep.py
@dataclass(frozen=True, slots=True)
class Cell:
    cell_id: str            # sha256(base_config_hash ‖ canonical_json(overrides) ‖ seed ‖ ablation_key)[:16]
    overrides: Mapping[str, Any]
    seed: int
    ablation_key: str
    merged_config_hash: str

def enumerate_cells(exp: Experiment) -> tuple[Cell, ...]: ...          # sorted by cell_id

class SweepRunner:
    async def dry_run(self, exp: Experiment) -> Mapping[str, Any]: ...  # cell list, hashes, arms
    async def estimate(self, exp: Experiment) -> "CostEstimate": ...
    async def launch(self, exp: Experiment, *, parallel: int, yes: bool = False,
                     cache_mode: str | None = None) -> UUID: ...
    async def resume(self, exp: Experiment, sweep_id: UUID) -> UUID:
        """Recomputes the cell list, joins `runs` on tags @> {'cell:<id>'}, launches only
        cells with no completed run. REFUSES on analysis_plan_hash mismatch — a changed
        experiment file is a pre-registration violation, not a merge conflict."""

# polis/research/experiments/cost.py
@dataclass(frozen=True, slots=True)
class ProbeResult:
    profile: Literal["microscope","chronicle"]
    ticks: int; calls_per_tick: float
    tokens_in_mean: float; tokens_out_mean: float
    cache_hit_rate: float; cost_per_tick_usd: Decimal; cv: float

@dataclass(frozen=True, slots=True)
class CostEstimate:
    profile: Literal["microscope","chronicle"]
    cells: int; ticks_per_cell: int; sim_years_per_cell: float
    base_probe: ProbeResult; cross_probe: ProbeResult
    cross_cell_hit_rate: float                  # h_x — decides whether the sweep costs 1x or 20x
    cost_cold_usd: Decimal; cost_warm_usd: Decimal
    cost_sweep_p50_usd: Decimal; cost_sweep_p90_usd: Decimal
    usd_per_sim_year: Decimal
    budget_usd_max: Decimal
    exceeds_budget: bool
    table_markdown: str

async def estimate(exp: Experiment, *, db: Database) -> CostEstimate: ...
```

```python
# polis/research/ablations.py
ABLATIONS: Final[Mapping[str, "Ablation"]]      # keys are the 10 §6.1 flag names

@dataclass(frozen=True, slots=True)
class Ablation:
    key: str
    overrides: Mapping[str, Any]                # applied to the merged config
    holds_fixed: str
    isolates: str
    threat: str
    mandatory_for: tuple[str, ...]

def apply(cfg: Mapping[str, Any], keys: Sequence[str]) -> Mapping[str, Any]: ...
def ablation_key(keys: Sequence[str]) -> str: ...            # sorted, joined; "" for baseline

@dataclass(frozen=True, slots=True)
class LASResult:
    delta: float; ci: tuple[float, float]; r2: float; las: float; n_seeds: int

def las(full: Mapping[int, float], reflex: Mapping[int, float]) -> LASResult:
    """LAS(y) = 1 - R^2 of y_full,s on y_reflex,s across seeds. A DESCRIPTIVE decomposition,
    not a causal variance decomposition, and it must be described that way wherever it
    appears (10 §6.3)."""

# polis/research/mechanism_check.py
@dataclass(frozen=True, slots=True)
class MechanismRow:
    id: str; module: str; source_location: str; configured_value: Any
    entails: str; implicated: bool | None; justification: str; ablation_run_ids: tuple[UUID, ...]

async def check(db: Database, run_id: UUID, claim: str) -> Mapping[str, Any]:
    """Pre-fills 10 §7 steps 3, 4, 6 and 12. Step 3 is MACHINE-GENERATED from
    runs.mechanism_manifest and never typed by hand — the failure this checklist exists to
    catch is a mechanism nobody remembered. Steps 5, 8 and 9 are left as required human
    sentences and the artefact is incomplete without them."""
def manifest_diff(runs: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Step 12: manifests must be identical across arms except for the ablated key."""
```

```python
# polis/research/replay.py
async def verify(db: Database, run_id: UUID, *, arena: bool = False) -> Mapping[str, Any]:
    """Recomputes every hash from the 02 §3.1 canonical serialisation, checks prev_hash
    linkage from genesis (64 zeros), verifies ed25519 on every event carrying a sig
    (external actions and 99xxx injections), and checks every injection-class event has a
    matching scenario_injections row signed by the run's declared researcher pubkey.
    Fails on: broken chain, invalid signature, unsigned injection, orphan injection row."""

async def replay(db: Database, run_id: UUID, *, from_tick: int = 0, to_tick: int | None = None,
                 cache_uri: str | None = None, strict: bool = True) -> Mapping[str, Any]:
    """Re-executes with the recorded config, seed and manifests in cache: replay mode; a
    miss is a HARD ERROR. Compares hash chains event by event.
    -> {'verdict': 'IDENTICAL'} or
       {'verdict': 'DIVERGED', 'seq': N, 'field': f, 'expected': x, 'actual': y}
    A `payload` divergence means an unseeded RNG draw or an iteration-order bug; `sig`
    means an external-agent replay problem; `sim_time` means a clock leak."""

async def rebuild(db: Database, run_id: UUID, *, from_tick: int = 0) -> Mapping[str, Any]:
    """Truncates projections and replays the log through the RUNTIME handlers. Returns a
    per-table diff. Non-empty means a handler has a side effect that is not in the log,
    which is always a bug (10 §12 R8)."""

# polis/research/exports/
async def export(db: Database, *, run_id: UUID | None, sweep_id: UUID | None,
                 out: Path, fmt: Literal["parquet","csv"] = "parquet",
                 tables: Sequence[str] = (), sample_events: float = 0.0,
                 verify: bool = False) -> Mapping[str, Any]:
    """Deterministic given the run; re-running overwrites byte-identically. --verify
    recomputes row counts and column checksums FROM THE EVENT LOG and writes them, with
    source_last_seq and the terminal chain_hash, into EXPORT_MANIFEST.json. Rejects an
    export from a partially-committed run."""

def load(sweep_id: UUID, root: Path) -> "ExportBundle":
    """Named tuple of the tables with the manifest attached. RAISES if the manifest reports
    metric drift or a failed --verify (10 §9.3)."""
def align(frames: Mapping[str, Any], *, fill: Mapping[str, str] | None = None) -> Any:
    """Raises on mismatched cadence unless an explicit per-column fill rule is passed.
    Filling is an analysis decision, made once, visibly, in the notebook (R5)."""

# polis/research/paper_check.py
async def paper_check(db: Database, sweep_id: UUID, claim_file: Path) -> Mapping[str, Any]: ...
```

**CLI surface added by this chunk** (registered on C01's Typer app):

| Command | Effect |
|---|---|
| `polis metrics catalogue [--format md\|json]` | Regenerates the `10 §1.3–1.8` tables from the registry |
| `polis gate --run <id> [--out f.json]` / `--sweep <id>` | V1–V4 / V5–V7 + aggregation |
| `polis verify --run <id> [--arena]` | Chain, signatures, injections; `--arena` runs V8 |
| `polis replay --run <id> [--from-tick] [--to-tick] [--cache] [--strict]` | Byte-comparison replay |
| `polis rebuild --run <id> [--from-tick]` | Projection rebuild + diff |
| `polis sweep <exp.yaml> [--estimate] [--dry-run] [--parallel N] [--resume <id>] [--cell …] [--cache-mode …] [--yes]` | The harness |
| `polis seeds --n 20 --from 20260801` | Explicit seed lists; never a range expression in YAML |
| `polis mechanisms --run <id> [--active]` | Machine-generated mechanism dump with `entails` |
| `polis mechanism-check --run <id> --claim "<sentence>"` | Pre-filled `10 §7` checklist |
| `polis export --run\|--sweep <id> [--verify] [--format] [--out] [--sample-events]` | Parquet/CSV |
| `polis package verify\|load <dir>` | Reproducibility package integrity and ingest |
| `polis compare --runs a,b,c --metric m [--allow-metric-drift]` | Tuple diff + drift check first |
| `polis paper-check --sweep <id> --claim <file>` | The `10 §11` checklist |

---

## 6. Interfaces you consume

| From | Symbol | Notes |
|---|---|---|
| C01 | `Settings`, `RuntimeConfig`, `MECHANISM_REGISTRY`, `mechanism_manifest()`, `canonical_json`, `repo_git_sha` | mechanism dump, config merge, hashing |
| C02 | `register_kind`, `Event`, `canonical_event_bytes`, `verify_event`, `verify_signature`, `KIND_REGISTRY` | verify, replay |
| C03 | `Database`, `MetricRepository.write/series`, `EventRepository.scan/count/last_complete_tick`, `RunRepository`, `LedgerRepository.imbalance_cents/total_balance_cents`, `ProjectionRouter` | writer, gates, rebuild |
| C04 | `Invariant`, `Severity`, `Ok`/`Violation`, `InvariantRunner`, `WorldStateView`, `PhaseHandler`, `Phase`, `TickContext`, `Clock`, `RngRegistry` | you implement invariants and one phase handler |
| C05 | `Purpose`, `CacheMode`, cost model, `llm_calls` shape | cost probes, `sys.llm.*` |
| C22 | `external_agents` counters, `external_latency` | V8 inputs, `sys.external.*` |
| C25 | `SCENARIO_KINDS` range constant; injection-class kind set | V1's shock-free window, the organic filter |

> **Kind range split with C25 — agree this before either chunk starts.**
> `10 §0.1` gives 99000–99999 to the scenario DSL, but five of its kinds are experiment and
> observation records that C24a needs at M1, long before C25 exists. Binding split:
> **C24 owns and declares 99050, 99060, 99070, 99090, 99091.**
> **C25 owns and declares 99000–99006, 99010, 99011, 99020, 99021, 99030, 99040, 99041, 99080.**
> Both register with `owner="polis.research"`. Neither declares the other's.

---

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `metrics` | **W** | one batched `COPY` per tick from PHASE 9; nulls never written |
| `runs` | **W** (`metric_manifest`, `mechanism_manifest`, `ablations`, `scale`, status, cost) + R | the `10 §0.6` amendments |
| `sweeps` | **W** + R | plus `preregistration JSONB`, `analysis_plan_hash CHAR(64)`, `preregistered_at`, `cost_estimate_usd`, `cost_actual_usd` |
| `events` | **R** (+ W of 99050/99060/99070/99090/99091 through the engine's log) | gates, verify, replay, export |
| `ledger_entries`, `ledger_accounts` | **R** | V2 post-hoc re-derivation |
| `llm_calls`, `completion_cache` | **R** | `sys.llm.*`, cost probes, cache coverage |
| every projection table | **R**, plus **truncate** under `polis rebuild` only | rebuild is explicitly destructive and run-scoped |
| `external_agents`, `external_latency` | **R** | V8 |
| `scenario_injections` | **R** | `polis verify` cross-check |

**Migration owned here** (additive, no existing column changes): the six `10 §0.6` amendments. No new table is requested — gate results are `metrics` rows (`gate.V3.pass ∈ {0,1}`) plus 99060 events, and export manifests live in the object store beside the Parquet files.

---

## 8. Event kinds owned

**99050, 99060, 99070, 99090, 99091.** Owner `polis.research`. All persisted, all unsigned (engine-emitted; covered by chain integrity).

| Kind | Name | Payload | When |
|---|---|---|---|
| 99050 | `ABLATION_APPLIED` | `ablation_id, params, affected_mechanisms[]` | tick 0, config-derived |
| 99060 | `GATE_EVALUATED` | `gate_id, verdict, statistic, threshold, window, code_git_sha` | per gate evaluation |
| 99070 | `METRIC_DEFINITION_REGISTERED` | `metric_id, definition_hash, unit, cadence, rq[], governed_by` | once per metric, tick 0 |
| 99090 | `EXPERIMENT_CELL_STARTED` | `sweep_id, cell_id, overrides, seed, ablation_key` | per cell |
| 99091 | `EXPERIMENT_CELL_COMPLETED` | `sweep_id, cell_id, status, cost_usd, gates` | per cell |

99070 is redundant with `runs.metric_manifest` **by design**: the log is truth, the column is the index. Drift detection joins the column; a dispute is settled by the log.

---

## 9. Implementation notes

### 9.1 The metric registration contract

```python
@metric(
    id="unemployment_rate",
    unit="bp",
    cadence="sim_day",
    rq=["A1", "A4", "B4"],
    definition="10_000 * |U(t)| // LF(t), with U, E, LF as 06-ECONOMY-SPEC.md §3.10",
    analogue="ILO/BLS U-3 unemployment rate",
    analogue_caveat="U(t) is directly observed from employment records; U-3 is a survey estimate",
    governed_by="06-ECONOMY-SPEC.md §12 M4",
    moved_by=[5011, 5012, 5013, 5042],
)
def unemployment_rate(state: MetricState) -> float: ...
```

Four rules that are enforced, not encouraged:

1. **`definition` is stated purely in terms of simulation state** — tables, event kinds, arithmetic. No sentence may reference a human institution. This is the discharge of **T11**, and the registration decorator rejects a definition containing any word from the `analogue` string.
2. **`analogue` is a separate field.** Never in the definition, never in the id.
3. **`analogue_caveat` is required.** One sentence naming the principal way the two differ. Empty is rejected.
4. **`rq` is non-empty.** A metric that serves no research question and is not `["SYS"]` is deleted (`02 §1.8`).

`definition_hash = sha256(id ‖ definition ‖ unit ‖ cadence ‖ dedent(source of the function body))`. Computed at import, written to `runs.metric_manifest`, emitted as 99070. **Changing a metric's computation changes its hash and therefore makes drift a join, not a memory.**

`polis metrics catalogue --format md` regenerates the `10 §1.3–1.8` tables. A CI check diffs the registry against the document; **if they disagree, the registry is wrong and must be fixed, or the document amended in the same PR.**

### 9.2 Storage contract

| Rule | Detail |
|---|---|
| Ids | Dotted lowercase: `unemployment_rate`, `polarisation.bc.tax.rate.should_rise`, `sys.llm.cache_hit_rate` |
| Per-entity | Entity encoded **in the id** (`bank.capital_ratio_bp.bk_02`), never a separate column |
| Money | Cents as integer-valued doubles — exact below 2^53. Never money as a ratio. |
| Rates | **Basis points** as integer-valued doubles. The export divides by 10,000 exactly once, in `polis/research/exports/`. |
| Floats | `dimensionless_float` only: entropy, correlations, BC, dip, Hill exponent |
| Nulls | **Never written.** Absent means "not computed at this tick", never "zero". |
| Cadence | A property of the metric, asserted by the writer, not chosen by the caller |
| Cardinality | Per-run budget of 400 distinct ids (R12). Over budget → aggregate at write time and move the per-entity breakdown to the export. |

PHASE 9 asserts its own duration against 50 ms. `sys.engine.phase_ms.9` climbing is the R12 symptom and the collector must surface it rather than absorb it.

### 9.3 Invariants: HALT and WARN

**HALT.** Emit `1010 INVARIANT_VIOLATED{invariant_id, expected, actual, halting: true}`, force a checkpoint, set `runs.status='halted'` and `halt_reason`, exit non-zero. **Do not attempt repair.** A run that violates conservation of money is not a run, it is a bug report.

`--continue-on-violation` exists for debugging only and sets a permanent `tags @> {invariant_violated}`. **A run carrying that tag can never appear in a published figure — enforced in the exporter, not by convention.**

**WARN.** Emit `1010` with `halting: false`, increment `sys.invariant.<id>.violations`, continue. A run whose WARN-class violations exceed the per-gate tolerance in V3/V4 fails the corresponding gate.

**INV-MONEY has six sub-checks** (`06 §1.7`) and a post-hoc re-derivation. The re-derivation exists because the in-engine check and the stored ledger can only disagree if one of them is wrong, and finding out which is the entire point.

### 9.4 The gates (C24b)

V1–V4 are **per-run**; V5–V7 are **per-experiment** and cannot be evaluated on one run; V8 is per-run but arena-scoped. Thresholds and procedures are `10 §2.3` verbatim — implement them exactly, including:

- **V1** uses the longest contiguous **shock-free** window: ticks with no ancestor in kinds 99000–99999 along `cause_seq`, at least 5 sim-years. No such window → `n/a`, and the run may not support any A1 claim.
- **V2**'s third clause is `ticks_checked == runs.last_tick + 1`. **The most dangerous failure is not a violated invariant, it is an invariant that quietly stopped running.** A V2 fail voids the run entirely and no other gate result is reported for it.
- **V4** reports **per sub-check**. "Actions diverse, language collapsed" is a real and interesting state and a single boolean would hide it.
- **V5**'s last clause, `between_seed_cv > 0.01`, is a trap detector: a CV near zero means the seeds are not producing different worlds, which means an RNG namespace is missing a `tick=` or an `entity_id`. That is a bug, not a strong result.
- **V6** costs a full second cell at live prices, because the paraphrase changes `prompt_template_hash` and the cache misses completely. Budget for it or the gate is skipped at exactly the moment it matters.
- **V7** requires ≥ 2 model families, ≥ 10 seeds each, `parse_failure_rate < 500 bp` per arm, and no cell mixing `model_version`.
- **V8** (ratified, `08 §6.7`): any operator-driven agent with `miss_rate > external_miss_rate_max` (0.05) tags the run `invalid_for_cross_agent_comparison` and the scorecard refuses to rank it. **C22 measures it; C24 enforces it.** The run stays valid for Track A and Track B.

`gate_report` must be **deterministic given the run and the code sha**: two evaluations produce byte-identical JSON. That is what makes step 7 of the third-party reproduction procedure a `diff`.

### 9.5 Pre-registration is mechanical, not moral

The researcher writes the engine, chooses the metrics, sees the data and picks the test — and the completion cache makes re-analysis free, which makes p-hacking free. A 12 × 20 × 40 sweep offers ~10,000 defensible-looking comparisons, several hundred of which clear p < 0.05 under the null.

1. The analysis plan is in the experiment YAML **before** the run.
2. `polis sweep` hashes it into `sweeps.analysis_plan_hash` and stores `sweeps.preregistration` **before the first cell launches**; the hash goes into every child run's `RUN_STARTED` payload.
3. The report generator reads only declared `primary` and `secondary` metrics. Everything else is reachable but labelled `exploratory: true` and cannot be a headline claim without a fresh confirmatory sweep with **new seeds**.
4. Deviations are permitted and expected; they go in a post-hoc `deviations:` block, each with a reason, and every figure derived from one carries the label.

This does not make anyone honest. It makes dishonesty require a visible edit to a hashed artefact, which is the most tooling can do.

### 9.6 Cost estimation — model both clock profiles honestly

The `$12/sim-year` target in `01-PRD.md §7.1` holds **only for the `chronicle` profile** (1 tick = 1 sim-day, 360 ticks/sim-year). `microscope` (24 ticks/sim-day, 8,640 ticks/sim-year) costs roughly **$250–400/sim-year** at the same per-call price. That is a 20–30× difference and it is the single most common way a sweep budget is wrong by an order of magnitude.

```
ticks_per_sim_year = clock.ticks_per_sim_day * clock.days_per_sim_year    # 8640 | 360
cost_cold   = ticks × calls_per_tick × (t_in·p_in + t_out·p_out)          # first cell, cold cache
cost_warm   = cost_cold × (1 − h_x)
cost_sweep  = cost_cold + (n_cells − 1) × cost_warm + n_V6_cells × cost_cold
p90         = cost_sweep × (1 + 1.28 × cv_probe)
usd_per_sim_year = cost_cold / (ticks / ticks_per_sim_year)
```

The estimator **must**:

1. Run a 200-tick probe of the base cell in `hybrid` mode with a **fresh cache namespace** — a warm cache makes the probe report a price nobody will pay.
2. Run a **second** 200-tick probe at a different grid point to measure `h_x`, the cross-cell hit rate. This is the number that decides whether the sweep costs 1× or 20× the base cell. `h_x < 0.2` means the swept parameter enters the system prompt and the sweep must be priced as N independent runs — say so in the output.
3. Print `usd_per_sim_year` **beside the profile name**, so `microscope: $310/sim-year` is on screen and cannot be mistaken for the headline $12.
4. **Refuse to launch** if `p90 > budget.usd_max` without `--yes`. Write `sweeps.cost_estimate_usd`; write `cost_actual_usd` at completion; report a ratio outside [0.5, 2.0], because a badly wrong estimator is itself a bug worth fixing.

### 9.7 Sweep execution

| Concept | Rule |
|---|---|
| **Cell** | one grid point × one seed × one ablation arm; the unit of scheduling and resume |
| **Ordering** | cells execute in `cell_id` order **after** the cache-warming stage, so the schedule does not depend on machine speed |
| **Stage 1** | run the single designated `cache_seed` cell to completion **before** fanning out. Fanning out first means N cells each paying cold-cache prices for the same prompts. |
| **Isolation** | cells share one Postgres and one completion cache and nothing else |
| **Parallel** | `N ≤ min(cores // 2, db_connections // 4, provider_rate_limit // llm.max_concurrency)`; a bounded worker pool over the sorted list, **no work stealing** — reproducible scheduling beats the last 10% of utilisation |
| **Determinism** | unaffected: cells share no state and the cache is content-addressed, so two cells racing on one key both get the same value |
| **Failure** | a HALTed cell is marked and the sweep continues; above `halt_tolerance` (10%) the sweep aborts, because that is a model bug and not a property of the grid |
| **Resume** | join on `runs.tags @> {'cell:<id>'}`; a `running` cell with a stale heartbeat resumes at the run level via `polis run --resume`; **refuse on `analysis_plan_hash` mismatch** |

### 9.8 The ablation ladder and the mechanism checklist

Every ablation holds seed, config, prompts and model manifest fixed except the named change, and runs on the **same seed list** as its baseline so the comparison is paired. Implement every row of `10 §6.1`: `--reflex-only`, `--salience-policy=random`, `--obfuscate-domain`, `--disclose-simulation`, `--needs-off`, `--mechanism-off=<id>`, `--heritability=0`, `--social-influence-off`, `--backfire-off`, `--no-record-penalty`, `--prompt-set=paraphrase`, `--model-family=<x>`, `--scale=N`, and the feed-algorithm arms.

Two implementation subtleties:

- **`--obfuscate-domain` is a bijective renaming layer applied at prompt-render time only.** The log, the schema and the metrics are untouched. A renaming that reaches the event payload changes the hash chain and makes the arm incomparable to its baseline.
- **`--mechanism-off=<id>` sets the mechanism to the neutral value stated in its own `entails` string**, not to `None` and not to zero-by-guess. If the `entails` string does not name a neutral value, that is a defect in the mechanism declaration and should be raised, not worked around.

`polis mechanism-check` pre-fills steps 3, 4, 6 and 12. Step 3 is machine-generated from `runs.mechanism_manifest` — *the failure this checklist exists to catch is a mechanism nobody remembered*, so a hand-typed list defeats the whole exercise. Steps 5, 8 and 9 require a human sentence each and the artefact is invalid without them. Step 12 diffs manifests across arms: they must be identical except for the ablated key, and this is the check that catches a config-merge accident.

### 9.9 Export must not diverge from the log

`--verify` recomputes per-table row counts and column-wise checksums **from the event log**, not from the projections that produced the file, and writes them into `EXPORT_MANIFEST.json` alongside `source_last_seq` and the run's terminal `chain_hash`. An export from a partially-committed run (`source_last_seq` behind `runs.last_tick`) is **rejected outright**.

Parquet is wide where analysis wants wide and tidy where it wants tidy. Money is `int64` cents, rates are `int32` basis points, ids are dictionary-encoded, `tick` is `int64` and is in every table. In `metrics_wide.parquet`, cells between cadence points are **null, not forward-filled** — filling is an analysis decision, and `align()` raises on mismatched cadence unless an explicit fill rule is passed (R5).

For a sweep, every table gains `run_id`, `seed` and the grid override columns, so `pl.read_parquet(...)` is already a tidy panel over cells and needs no glue code.

### 9.10 Randomness in the research layer

Analysis code is as much a part of the reproducibility tuple as the engine. Every draw goes through `rng.get(namespace, entity_id, tick)`:

| Namespace | Used for |
|---|---|
| `research.bootstrap` | Bootstrap resampling; seeded so figures are reproducible |
| `research.export.sample` | Row sampling in sampled exports |
| `research.scenario.select` / `.jitter` | C25's, listed for completeness |
| `observatory.track` | C23's ephemeral tracked-agent sample |

The `scripts/lint_determinism.py` rule that bans `import random` in the engine extends to `polis/research/` **and to `notebooks/`**. The CI executes each notebook twice and diffs the figure CSVs (R13).

---

## 10. Configuration keys

```yaml
research:
  metrics:
    max_distinct_ids: 400            # R12 cardinality budget
    phase9_budget_ms: 50
    entropy_floor: 0.35              # INV-ENTROPY / V4
    cadence_assert: true
  invariants:
    continue_on_violation: false     # DEBUG ONLY; sets a permanent run tag
    enabled: null                    # null = all; else an explicit id list
    severity_overrides: {}           # never used in a published run
  gates:
    v1_min_window_sim_years: 5
    v3_failure_tolerance: 0.05
    v4_min_pass_share: 0.90
    v5_min_seeds: 20
    v5_min_sign_agreement: 0.80
    v7_max_parse_failure_bp: 500
    external_miss_rate_max: 0.05     # V8; must equal gateway.arena.external_miss_rate_max
  sweep:
    halt_tolerance_pct: 10
    probe_ticks: 200
    cost_cap_usd: 500.0              # refuse above this without --yes, regardless of the file
    warn_ratio_low: 0.5
    warn_ratio_high: 2.0
  export:
    default_format: parquet
    sample_events: 0.0
    verify_by_default: true
  bootstrap:
    resamples: 10000
```

A validator asserts `research.gates.external_miss_rate_max == gateway.arena.external_miss_rate_max`. Two thresholds for one gate is how V8 silently stops being enforced.

---

## 11a. Acceptance criteria — C24a (gates M1)

1. `@metric` raises at import on: a duplicate id, an empty `rq`, an empty `analogue_caveat`, or an `analogue` word appearing inside `definition`.
2. `definition_hash` is stable across processes and changes when — and only when — id, definition, unit, cadence, or the function body changes.
3. `runs.metric_manifest` is populated at run start and matches `manifest()` exactly; one 99070 is emitted per registered metric at tick 0 and never again.
4. The collector writes **one** batched `COPY` per tick and writes **no** row for a metric whose cadence is not due.
5. No `NULL` and no `NaN` reaches `metrics`; a metric returning `NaN`/`inf` raises rather than writing.
6. Money metrics are integer-valued doubles in cents; rate metrics are integer-valued doubles in basis points; a `ratio` written as a float in `[0,1]` fails a unit test.
7. PHASE 9 completes within 50 ms at 1,000 agents on the reference machine, and emits an overrun counter rather than absorbing the overrun.
8. Registering a 401st metric id in one run raises `MetricError` with the R12 remedy in the message.
9. All ten invariants are registered in C04's `INVARIANT_REGISTRY` with the correct severity and frequency from `02 §9`.
10. Under `NullWorldState` (M1), the five money/share/order/employment invariants return `Ok` and INV-POP / INV-ENTROPY / INV-NONDEGEN are live — with **no milestone branch anywhere in the code**.
11. A HALT-class violation emits `1010{halting: true}`, forces a checkpoint, sets `runs.status='halted'` with `halt_reason`, and exits non-zero.
12. A WARN-class violation emits `1010{halting: false}`, increments `sys.invariant.<id>.violations`, and the run continues.
13. `--continue-on-violation` sets `tags @> {invariant_violated}` permanently and the exporter refuses such a run.
14. `posthoc_money_check` returns `ticks_checked` and detects a manually corrupted `ledger_entries` row.
15. The `sys.*` family is written every tick: `deliberate_share`, `salience_cutoff`, `cache_hit_rate`, `parse_failure_rate`, `budget_exhausted`, `tick_wall_ms_p50/p99`, `events_per_tick`, `ephemeral.dropped`.
16. `sys.cognition.deliberate_share`'s denominator excludes infants, sleepers and the dead, matching C09's eligibility rule exactly.
17. `polis metrics catalogue --format md` output diffs clean against `docs/10 §1.8` in CI.
18. Determinism: a 200-tick run at the same seed produces identical `metrics` rows twice, byte for byte.
19. `mypy --strict polis/research/metrics polis/research/invariants` and `import-linter` pass.

## 11b. Acceptance criteria — C24b

1. Every metric in `10 §1.3–1.7` is registered with `definition`, `analogue`, `analogue_caveat`, `governed_by`, and `moved_by` where applicable; `polis metrics catalogue` diffs clean against all six tables.
2. `ige_income_lifetime` and `ige_wealth_age40` are distinct registered ids with distinct hashes and distinct definitions — the registered naming collision of `10 §1.3` cannot recur silently.
3. `relationships.py` refuses to compute Beveridge/Okun/Phillips/Zipf without the four preconditions of `10 §1.9`, naming the missing one.
4. V1 identifies the longest shock-free window by walking `cause_seq` for 99xxx ancestors, and returns `n/a` when none reaches 5 sim-years.
5. V2 fails when `ticks_checked != runs.last_tick + 1`, even with zero violations; a V2 fail suppresses all other gate results for that run.
6. V3, V4 evaluate per sub-check with the documented tolerances and report each separately.
7. V5 fails on `between_seed_cv <= 0.01` with a message naming the likely missing RNG namespace component.
8. V6 and V7 evaluate over a sweep and refuse to pool cells with differing `model_version`.
9. **V8**: a run where any operator-driven agent exceeds `external_miss_rate_max` is tagged `invalid_for_cross_agent_comparison`; `polis verify --arena` returns `fail`; the run still passes V1–V4.
10. `gate_report` is byte-identical across two evaluations of the same run at the same code sha.
11. `load_experiment` rejects: an unknown metric id, an unknown grid key, a range expression for seeds, duplicate seeds, more than one `primary` per RQ, an empty `prediction`, and a **missing required ablation** for any mechanism whose `entails` mentions a primary or secondary metric.
12. `sweeps.analysis_plan_hash` and `preregistration` are written **before** the first cell launches, and the hash appears in every child run's `RUN_STARTED`.
13. `cell_id` is deterministic; re-invoking a sweep with the same file is idempotent and launches nothing.
14. `--dry-run` writes nothing and prints the cell list with per-cell merged config hashes and ablation arms.
15. `--estimate` runs two probes with a fresh cache namespace, reports `h_x`, and prints `usd_per_sim_year` **beside the profile name**.
16. The estimator reports ~$12/sim-year for `chronicle` and $250–400/sim-year for `microscope` on the reference config, and the two are never conflated in any output string.
17. `--estimate` **refuses to launch** when `p90 > budget.usd_max` or `> research.sweep.cost_cap_usd` without `--yes`, exiting non-zero.
18. Cells execute in `cell_id` order after stage 1; the stage-1 cache-warming cell completes before fan-out.
19. `--resume` launches only incomplete cells and **refuses** on `analysis_plan_hash` mismatch.
20. A sweep with a HALTed cell continues; above `halt_tolerance` it aborts with the count in the message.
21. Every ablation in `10 §6.1` is implemented and produces a distinct `ablation_key`; `--obfuscate-domain` leaves the event log, the action schema and the metrics byte-identical in structure.
22. `--mechanism-off=<id>` sets the neutral value named in the mechanism's own `entails` string, and raises if the string names none.
23. `las()` returns `1 - R²` over seed-paired arms and its docstring and every output label state that it is descriptive, not a causal variance decomposition.
24. `polis mechanism-check` machine-generates steps 3, 4, 6, 12 and refuses to emit a "complete" artefact without human text at steps 5, 8, 9.
25. `polis verify` fails on: a tampered event, an invalid external signature, an unsigned injection-class 99xxx event, and an injection with no `scenario_injections` row.
26. `polis replay --strict` on the golden run returns `IDENTICAL`; a single flipped payload byte returns `DIVERGED at seq N: field=payload`.
27. `polis rebuild` returns zero projection diffs on the golden run; a handler with a deliberate side effect is caught and the table is named.
28. `polis export --verify` recomputes checksums **from the log**, writes `source_last_seq` and terminal `chain_hash`, and rejects a partially-committed run.
29. `metrics_wide.parquet` carries per-column `unit`, `cadence` and `definition_hash` metadata and contains **no forward-filled** cells; `align()` raises on cadence mismatch without an explicit fill rule.
30. `exports.load()` raises when the manifest reports metric drift or a failed `--verify`.
31. All 17 notebooks execute in CI against the golden run; executing each twice produces identical figure CSVs.
32. `polis compare` computes the tuple diff and the `10 §1.10` drift query first and refuses on a non-empty drift result without `--allow-metric-drift`.
33. `polis paper-check` evaluates every mechanical item of `10 §11` and prints the manual ones.

---

## 12a. Tests to write — C24a

| File | Asserts |
|---|---|
| `tests/unit/research/test_metric_registry.py` | Duplicate id; empty `rq`; empty caveat; analogue leaking into `definition`; `definition_hash` stability and sensitivity |
| `tests/unit/research/test_metric_units.py` | Cents integer-valued; bp integer-valued; a float ratio rejected; `NaN`/`inf` raises |
| `tests/unit/research/test_collector_cadence.py` | Only due metrics written; nulls never written; one COPY per tick; cardinality budget raises with the remedy |
| `tests/unit/research/test_invariants_money.py` | INV-MONEY six sub-checks against a hand-built ledger; a one-cent imbalance is caught |
| `tests/unit/research/test_invariants_warn.py` | INV-POP / INV-ENTROPY / INV-NONDEGEN thresholds; WARN increments the counter and continues |
| `tests/unit/research/test_null_worldstate.py` | Under `NullWorldState` the money invariants return `Ok` and no code path branches on milestone |
| `tests/integration/test_halt_policy.py` | HALT emits 1010, checkpoints, sets `halted` + `halt_reason`, exits non-zero, attempts no repair |
| `tests/integration/test_continue_on_violation.py` | Permanent run tag; exporter refuses the run |
| `tests/integration/test_posthoc_money.py` | Re-derivation from `ledger_entries` catches a corrupted row; `ticks_checked` correct |
| `tests/integration/test_phase9_budget.py` | 1,000 agents: PHASE 9 within 50 ms; overrun surfaces a counter |
| `tests/determinism/test_metrics_determinism.py` | 200 ticks, same seed, StubProvider → byte-identical `metrics` rows |
| `tests/unit/research/test_catalogue_diff.py` | `polis metrics catalogue --format md` diffs clean against `docs/10 §1.8` |

## 12b. Tests to write — C24b

| File | Asserts |
|---|---|
| `tests/unit/research/test_catalogue_complete.py` | Every `10 §1.3–1.7` metric registered; `ige_income_lifetime` ≠ `ige_wealth_age40`; every id has `moved_by` or documents why not |
| `tests/unit/research/test_relationships_gating.py` | Beveridge/Okun/Phillips/Zipf refuse without the four preconditions, naming the missing one |
| `tests/unit/research/test_gate_v1.py` | Shock-free window detection over `cause_seq`; `n/a` when < 5 sim-years; each series' three sub-tests |
| `tests/unit/research/test_gate_v2.py` | Fails on `ticks_checked` mismatch with zero violations; suppresses other gates |
| `tests/unit/research/test_gate_v3_v4.py` | Per-sub-check verdicts and tolerances; "actions diverse, language collapsed" reported as such |
| `tests/unit/research/test_gate_v5_v7.py` | Sign agreement, bootstrap CI, CV trap detector; V6 sign/CI rule; V7 parse-failure and version-mixing refusal |
| `tests/invariants/test_v8_liveness.py` | Over-threshold miss rate tags the run and fails `verify --arena`; V1–V4 still pass |
| `tests/unit/research/test_experiment_load.py` | Each of the seven load-time rejections, including the missing-required-ablation case |
| `tests/unit/research/test_cell_identity.py` | `cell_id` determinism and idempotent re-invocation; sorted execution order |
| `tests/unit/research/test_cost_estimator.py` | Both profiles priced separately; `usd_per_sim_year` beside the profile name; `h_x < 0.2` prices as N independent runs; refusal above cap without `--yes` |
| `tests/unit/research/test_ablations.py` | Every flag produces its overrides and a distinct key; `--mechanism-off` reads the neutral value from `entails` and raises when absent |
| `tests/unit/research/test_las.py` | `1 - R²` over paired seeds; label asserts "descriptive, not causal" |
| `tests/unit/research/test_mechanism_check.py` | Steps 3/4/6/12 machine-generated; incomplete without human text at 5/8/9; manifest diff catches a config-merge accident |
| `tests/integration/test_sweep_resume.py` | Only incomplete cells relaunch; `analysis_plan_hash` mismatch refuses; stale-heartbeat cell resumes at run level |
| `tests/integration/test_sweep_cache_warming.py` | Stage 1 completes before fan-out; cross-cell hit rate rises accordingly |
| `tests/integration/test_verify.py` | Tampered event; invalid external signature; unsigned injection; orphan `scenario_injections` row |
| `tests/integration/test_replay_strict.py` | Golden run `IDENTICAL`; a flipped payload byte localises to a seq and names `payload` |
| `tests/determinism/test_projection_rebuild.py` | 500 ticks live → rebuild → every projection table diffs empty; a planted side effect is caught and the table named |
| `tests/integration/test_export_verify.py` | Checksums recomputed from the log; partial-run export rejected; no forward fill; `align()` raises on cadence mismatch |
| `tests/integration/test_export_load_refusal.py` | `load()` raises on metric drift and on a failed `--verify` |
| `tests/integration/test_notebooks.py` | All 17 execute against the golden run; two executions produce identical figure CSVs |
| `tests/integration/test_compare_refusal.py` | `polis compare` refuses on drift and on an undeclared tuple difference; the override stamps the manifest |
| `tests/unit/research/test_kind_split.py` | This chunk declares only 99050/99060/99070/99090/99091; no collision with C25's set |

---

## 13. Definition of done

All of `chunks/README.md §5`, plus:

**C24a (gates M1):**
1. `MetricCollector` registered as a PHASE 9 `PhaseHandler`; `metrics` rows land every tick for the `sys.*` family and the M1-available domain metrics.
2. All ten invariants registered; HALT and WARN paths demonstrated end to end.
3. Alembic revision adding the six `10 §0.6` amendments.
4. Kind 99070 registered; `runs.metric_manifest` populated.
5. Handback records: (a) the 99xxx range split agreed with C25 (§6); (b) measured PHASE 9 duration at 1,000 agents; (c) the metric-count budget actually used, against the 400 cap.

**C24b:**
6. The full catalogue registered; `polis metrics catalogue` diffs clean against all six tables of `docs/10`.
7. `polis gate`, `verify`, `replay`, `rebuild`, `sweep`, `export`, `mechanisms`, `mechanism-check`, `compare`, `paper-check`, `seeds`, `package` all implemented and documented in `--help`.
8. `configs/experiments/` ships at least one complete, loadable, pre-registered experiment (the `10 §3.2` B1 example).
9. All 17 notebooks in `notebooks/`, executing in CI against the golden run.
10. A reproducibility package is assembled for the golden run and a third party executes `10 §5.4` end to end with `POLIS_LLM_OFFLINE=1`.
11. Handback records: (d) measured `usd_per_sim_year` for both clock profiles on the reference config, with the probe numbers behind them; (e) measured cross-cell hit rate `h_x` on a real two-point grid; (f) the estimate-vs-actual ratio for the first real sweep; (g) any metric whose `10` definition could not be implemented as written, **flagged, not silently reinterpreted**.

---

## 14. Traps

1. **Metric definition drift (R1).** The single most likely route to a wrong published number. Someone "fixes" `gini_wealth` and now the same id means two things across runs. Never redefine an id: register a new one and let the old runs keep the old. The `definition_hash` join is what makes this findable instead of discoverable-in-review.
2. **Naming an analogue inside the definition.** `"unemployment rate, as measured by the BLS"` imports every assumption T11 exists to keep out. The decorator rejects it; do not route around the rejection by paraphrasing.
3. **Writing zero instead of not writing.** An absent metric row means "not computed at this tick". A zero means "computed, and it was zero". Conflate them and every cadence-aware chart and every regression gains a phantom series of zeros at the daily ticks.
4. **Forward-filling in the exporter.** It makes the Parquet file friendlier and produces suspiciously clean correlations at exactly the cadence ratio (R5). Fill in the notebook, once, visibly.
5. **An invariant that quietly stopped running.** More dangerous than a violated one. `ticks_checked == last_tick + 1` is V2's third clause for exactly this reason, and it must be computed from data, not from the fact that the code exists.
6. **Repairing after a HALT.** "Just nudge the balance by a cent so the run completes." Now the run is unpublishable *and* looks fine. Halt, checkpoint, exit non-zero, fix the bug.
7. **`--continue-on-violation` leaking into a real run.** It exists for debugging. The permanent tag and the exporter refusal are the only things standing between it and a published figure drawn from a run where money was not conserved.
8. **Storing rates as floats in `[0,1]`.** Half the catalogue is in basis points and half is not, the exporter divides by 10,000 in one of two places, and a figure is off by 100× in a way that looks plausible.
9. **Metric cardinality explosion (R12).** Per-proposition × per-entity ids multiply `metrics` and push PHASE 9 past 50 ms. The engine gets slower every sim-year and nobody attributes it to the dashboard's favourite breakdown. Budget 400 ids; move the rest to the export.
10. **Pricing a sweep at the `chronicle` rate and running it at `microscope`.** A 24× under-estimate. The `$12/sim-year` number is real *and* is only true for one profile; print `usd_per_sim_year` next to the profile name, every time, in every output.
11. **Probing against a warm cache.** The estimate comes back at a fraction of the real price, the sweep launches, and the circuit breaker fires at 3 a.m. Fresh cache namespace, always.
12. **Ignoring `h_x`.** If the swept parameter enters the system prompt, cross-cell sharing is zero and the sweep costs N× the base cell rather than 1.3×. That is the difference between $200 and $4,000 and the probe is the only thing that tells you before you spend it.
13. **Fanning out before the cache-warming cell completes.** N cells each pay cold-cache prices for the same prompts. The observed penalty is 5–20×, and it is entirely avoidable by running one cell first.
14. **Work stealing in the scheduler.** It buys 10% utilisation and costs reproducible cell ordering, which means a resumed sweep is a different sweep.
15. **Resuming with a changed experiment file.** `analysis_plan_hash` mismatch is a pre-registration violation, not a merge conflict, and `--resume` must refuse rather than reconcile.
16. **Silently comparing runs with different reproducibility tuples (R2).** Two runs differing in `prompt_manifest` overlaid as a treatment effect. Compute the tuple diff *first*, in `compare`, in the exporter, and in every pooling path — not as a warning at the end.
17. **Exports diverging from the log (R4).** A projection bug means the Parquet says something the events do not, and the notebook figure disagrees with the Observatory. `--verify` must recompute **from the log**, not from the same projections that produced the file, or it verifies nothing.
18. **Exporting a partially-committed run.** `source_last_seq` behind the last tick means the tail of the run is missing and no error is raised anywhere downstream. Reject the export.
19. **A hand-typed mechanism list in the checklist.** The whole point of step 3 is to catch the mechanism nobody remembered; a list written from memory catches exactly the mechanisms you already remembered.
20. **`--mechanism-off` guessing the neutral value.** Zero is not always neutral. The `entails` string names the neutral value; if it does not, the mechanism declaration is defective and that is the finding.
21. **`--obfuscate-domain` reaching the log.** Rename at prompt-render time only. Rename in the event payload and the hash chain differs from the baseline for reasons unrelated to behaviour, and the arm cannot be compared to anything.
22. **Reporting `LAS` as a causal decomposition.** The two arms differ in more than the presence of an LLM — they differ in every downstream state. It is descriptive. Say so in the docstring, the label and the paper.
23. **An unseeded bootstrap (R13).** Two runs of the same notebook produce different CIs, the figure will not reproduce at step 8 of the third-party procedure, and the failure looks like an engine problem. Every draw through `rng.get("research.bootstrap", …)`; CI executes each notebook twice.
24. **Treating V1 as a HALT.** A collapsing economy is diagnostic data and the reason for the failure is itself reportable. V1 is a gate on *usability for a claim*, not on the run's right to exist.
25. **Two thresholds for V8.** `gateway.arena.external_miss_rate_max` and `research.gates.external_miss_rate_max` drifting apart means the gateway tags runs the gate does not fail, or vice versa. Validate they are equal at config load.
26. **Letting the registry and `docs/10` diverge.** They will, within two PRs, unless CI diffs them. When they disagree the registry is what ran, so the registry is what must be corrected — or the document amended in the same PR.
