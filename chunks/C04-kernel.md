# C04 — Clock, tick loop, RNG registry, scheduler, invariants, checkpoints

**M0** · Owner module `polis/kernel` · Depends on: **C01, C02, C03, C05** · Blocks: **everything from M1 onward; completes M0** · Size **L** (1–2 weeks)

---

## 1. Context

This is the engine. It owns simulated time, the ten-phase tick, the only legitimate source
of randomness in the system, the sim-time→tick cadence resolver, the invariant checks that
halt a run when money stops closing, checkpointing, and crash recovery. It contains **no
domain logic**: no agents, no markets, no society. Institutions and phase handlers register
themselves against protocols defined here, and the kernel calls them in a fixed order.

`02 §7.1` binds this hard: `kernel → events, config` and **never** `agents`, `economy`,
`society`, `world`. Every place the kernel appears to need domain knowledge — invariants
over money, institutions in PHASE 5 — is expressed as a Protocol the composition root
satisfies. If you find yourself importing `polis.economy` here, the design is wrong, not
the rule.

M0 is complete when this chunk ticks an empty world for 200 ticks, twice, and produces
byte-identical hash chains.

---

## 2. Required reading

| Source | Why |
|---|---|
| `../docs/02-ARCHITECTURE.md` §4 (all), §5 (all), §5.1, §5.2, §5.3, §7.1, §9, §10, §11 | Binding. §5 is the tick contract, §9 is the invariant table, §11 is the timing budget. |
| `../docs/03-DATA-MODEL.md` §1.1 `runs`, §1.5 `checkpoints`, §10 `metrics` | What a run writes about itself. |
| `../docs/09-MODEL-ROUTING.md` §4.4 (phase-3 arithmetic), §4.5 (ordering), §4.6 (budget) | Why PHASE 3 is the only slow phase and how degradation reaches the loop. |
| `../docs/01-PRD.md` §7.1, §7.2 (V1–V4) | Determinism, throughput, crash-recovery targets. |
| **C01** `polis.config` | `Settings`, `RuntimeConfig`, `canon`, `mechanism`, logging. |
| **C02** `polis.events` | `Event`, `NewEvent`, `EventLog`, `register_kind`, `verify_batch`. |
| **C03** `polis.store` | `EventRepository`, `CheckpointRepository`, `MetricRepository`, `RunRepository`, `BlobStore`, `LedgerRepository` (read side, for invariants). |
| **C05** `polis.llm` | `StubProvider` — every kernel test uses it. `LLMRouter` is invoked from PHASE 3 by a handler, not by the loop. |

---

## 3. Scope — in

1. `polis/kernel/clock.py` — `Clock`, `ClockProfile`, `SimDuration`, sim-time arithmetic.
2. `polis/kernel/rng.py` — `RngRegistry`, numpy generators, `det_uuid`.
3. `polis/kernel/det.py` — `stable`, `stable_dict`, `round6`, `det_uuid`; re-export of the canonicaliser.
4. `polis/kernel/scheduler.py` — `Cadence`, `Scheduler`, sim-time cadence → tick predicate.
5. `polis/kernel/tick.py` — `Phase`, `TickContext`, `PhaseHandler`, `Institution`, `TickLoop`, `INSTITUTION_ORDER`.
6. `polis/kernel/invariants.py` — `Invariant`, `WorldStateView`, the nine INV-* checks, `InvariantRunner`.
7. `polis/kernel/checkpoint.py` — `Checkpointable`, `CheckpointManager`.
8. `polis/kernel/resume.py` — torn-tick detection, `ResumePlan`, replay-to-head.
9. `polis/kernel/telemetry.py` — phase timing against `02 §11`.
10. `polis/kernel/engine.py` — the composition root wiring for `polis run` / `polis run --resume`.
11. `scripts/lint_determinism.py` rule set (C01 built the harness; C04 writes the rules).
12. `polis/cli/commands/run.py`, `resume.py`.

## 4. Scope — out

| Not built here | Owner |
|---|---|
| Any `PhaseHandler` body for phases 1, 2, 3, 4, 8 | C06–C10, C20 |
| Any `Institution` implementation | C11–C19 |
| The metric catalogue (you write the vector the catalogue produces) | C24 |
| `WorldStateView` **implementation** (you define the Protocol and a `NullWorldState` for M0) | C11/C14 for the money parts, C24 for the distributional parts |
| `polis replay`, `polis verify` | C24 / C02 |
| Gateway drain in PHASE 3 (you provide the hook, it stays empty) | C22 |

---

## 5. Interfaces you provide

```python
# polis/kernel/clock.py
EPOCH: Final[datetime] = datetime(2100, 1, 1, 0, 0, 0)      # UTC-naive, microsecond == 0

@dataclass(frozen=True, slots=True)
class ClockProfile:
    name: Literal["microscope", "chronicle"]
    ticks_per_sim_day: int          # 24 | 1
    days_per_sim_year: int          # 360
    seconds_per_tick: int           # 3600 | 86400
    days_per_sim_week: int = 7
    days_per_sim_month: int = 30
    days_per_sim_quarter: int = 90

PROFILES: Final[Mapping[str, ClockProfile]]
def profile_from_settings(s: ClockSettings) -> ClockProfile: ...

@dataclass(frozen=True, slots=True)
class SimDuration:
    years: int = 0; quarters: int = 0; months: int = 0
    weeks: int = 0; days: int = 0; hours: int = 0
    @classmethod
    def parse(cls, spec: str) -> "SimDuration":
        """'4y' '1q' '1mo' '2w' '1d' '6h'; named aliases 'daily' 'weekly' 'biweekly'
        'monthly' 'quarterly' 'annually'. Raises ConfigError on anything else."""

class Clock:
    def __init__(self, profile: ClockProfile, *, start_tick: int = 0,
                 epoch: datetime = EPOCH) -> None: ...
    @property
    def tick(self) -> int: ...
    @property
    def sim_time(self) -> datetime: ...
    @property
    def profile(self) -> ClockProfile: ...
    def advance(self) -> int: ...                       # -> new tick
    def sim_time_at(self, tick: int) -> datetime: ...   # pure
    def tick_at(self, when: datetime) -> int: ...
    def ticks_for(self, d: SimDuration) -> int: ...
    def sim_day(self, tick: int | None = None) -> int: ...
    def sim_week(self, tick: int | None = None) -> int: ...
    def sim_month(self, tick: int | None = None) -> int: ...
    def sim_quarter(self, tick: int | None = None) -> int: ...
    def sim_year(self, tick: int | None = None) -> int: ...
    def hour_of_day(self, tick: int | None = None) -> float: ...
    def starts_new(self, unit: Literal["day","week","month","quarter","year"],
                   tick: int) -> bool: ...
    def dump(self) -> Mapping[str, Any]: ...
    def load(self, state: Mapping[str, Any]) -> None: ...
    name: ClassVar[str] = "clock"
```

```python
# polis/kernel/rng.py
class RngRegistry:
    """Every stream is content-derived, so the registry carries no consumption state.
    get() returns a FRESH Random each call: there is no cross-call stream position.
    A caller needing k draws takes all k from one returned object."""
    def __init__(self, master_seed: int) -> None: ...
    @property
    def master_seed(self) -> int: ...
    @property
    def draws(self) -> int: ...                          # telemetry only; never hashed
    def seed_for(self, namespace: str, entity_id: str = "", tick: int | None = None) -> int:
        """int.from_bytes(sha256(f"{master_seed}|{namespace}|{entity_id}|{tick or ''}"
                                 .encode()).digest()[:8], "big")   -- 02 §4.1, verbatim"""
    def get(self, namespace: str, entity_id: str = "", tick: int | None = None) -> random.Random: ...
    def numpy(self, namespace: str, entity_id: str = "",
              tick: int | None = None) -> np.random.Generator: ...
    def dump(self) -> Mapping[str, Any]: ...             # {"master_seed": n, "version": 1}
    def load(self, state: Mapping[str, Any]) -> None: ...
    name: ClassVar[str] = "rng"

class SeedSource(Protocol):                              # what C05's router accepts
    def seed_for(self, namespace: str, entity_id: str = "", tick: int | None = None) -> int: ...
```

```python
# polis/kernel/det.py
from polis.config.canon import canonical_json, canonical_bytes, sha256_hex, round_floats  # re-export (09 §5.1)

T = TypeVar("T"); K = TypeVar("K"); V = TypeVar("V")
def stable(items: Iterable[T], *, key: Callable[[T], Any]) -> list[T]: ...
def stable_dict(d: Mapping[K, V]) -> list[tuple[K, V]]: ...
def round6(x: float) -> float: ...
def det_uuid(namespace: str, *parts: object) -> UUID:
    """uuid5 over '|'.join(str(p)). THE only way to make a UUID inside the engine;
    uuid4 is banned by scripts/lint_determinism.py."""
def det_id(prefix: str, namespace: str, *parts: object) -> str:
    """'<prefix>_<hex16>' — 03 §0 typed-prefix ids (ag_, fm_, pl_, ...)."""
```

```python
# polis/kernel/scheduler.py
@dataclass(frozen=True, slots=True)
class Cadence:
    id: str                                     # 'payroll', 'market_session', 'election'
    spec: str                                   # SimDuration spec, or 'HH:MM-HH:MM', or 'daily@HH:MM'
    phase: Literal[0, 7, 8, 9]
    owner: str
    align: Literal["day","week","month","quarter","year","epoch","session"] = "day"
    offset: SimDuration = SimDuration()

class Scheduler:
    def __init__(self, clock: Clock) -> None: ...
    def register(self, c: Cadence) -> None: ...          # duplicate id -> ConfigError
    def registered(self) -> tuple[Cadence, ...]: ...     # sorted by id
    def fires(self, cadence_id: str, tick: int) -> bool: ...   # pure
    def due(self, tick: int) -> tuple[str, ...]: ...     # sorted by cadence_id
    def due_for_phase(self, tick: int, phase: int) -> tuple[str, ...]: ...
    def next_fire(self, cadence_id: str, after_tick: int) -> int: ...
    def is_open(self, cadence_id: str, tick: int) -> bool: ...  # session cadences
```

```python
# polis/kernel/invariants.py
class Severity(StrEnum):
    HALT = "halt"; WARN = "warn"

@dataclass(frozen=True, slots=True)
class Ok:
    invariant_id: str
@dataclass(frozen=True, slots=True)
class Violation:
    invariant_id: str; expected: str; actual: str
    detail: Mapping[str, Any]; severity: Severity
Result: TypeAlias = Ok | Violation

class WorldStateView(Protocol):
    """Kernel's read-only window on domain state. Implemented by the composition root,
    NOT imported from polis.economy (02 §7.1). NullWorldState satisfies it for M0."""
    tick: int
    def money_supply_cents(self) -> int: ...
    def total_balances_cents(self) -> int: ...
    def ledger_imbalance_cents(self) -> int: ...
    def share_ledger(self) -> Mapping[str, tuple[int, int]]: ...     # symbol -> (held, outstanding)
    def order_reserve_shortfalls(self) -> Sequence[Mapping[str, Any]]: ...
    def employment_anomalies(self) -> Sequence[Mapping[str, Any]]: ...
    def population(self) -> int: ...
    def initial_population(self) -> int: ...
    def action_type_counts(self) -> Mapping[str, int]: ...
    def top1_wealth_share(self) -> float: ...
    def employment_rate(self) -> float: ...
    def chain_ok(self) -> bool: ...

class Invariant(Protocol):
    id: str
    severity: Severity
    frequency: Literal["tick", "sim_day", "checkpoint"]
    def check(self, state: WorldStateView) -> Result: ...

INVARIANT_REGISTRY: Final[dict[str, Invariant]]
def register_invariant(inv: Invariant) -> None: ...

class InvariantRunner:
    def __init__(self, clock: Clock, *, continue_on_violation: bool = False,
                 enabled: frozenset[str] | None = None,
                 overrides: Mapping[str, Severity] | None = None) -> None: ...
    def due(self, tick: int) -> tuple[str, ...]: ...
    def run(self, tick: int, state: WorldStateView) -> list[Result]: ...
    def should_halt(self, results: Sequence[Result]) -> bool: ...
    def summary(self) -> Mapping[str, int]: ...
```

```python
# polis/kernel/tick.py
class Phase(IntEnum):
    CLOCK=0; PERCEIVE=1; SALIENCE=2; DECIDE=3; VALIDATE=4
    RESOLVE=5; COMMIT=6; INSTITUTIONS=7; VITALS=8; METRICS=9

PHASE_BUDGET_MS: Final[Mapping[Phase, int]] = {
    Phase.CLOCK: 1, Phase.PERCEIVE: 80, Phase.SALIENCE: 20, Phase.DECIDE: 3000,
    Phase.VALIDATE: 20, Phase.RESOLVE: 100, Phase.COMMIT: 150,
    Phase.INSTITUTIONS: 100, Phase.VITALS: 30, Phase.METRICS: 50,
}
INSTITUTION_ORDER: Final[tuple[str, ...]] = (
    "movement", "communication", "labour", "goods", "exchange",
    "banking", "ventures", "polity", "law", "world")

@dataclass(slots=True)
class TickContext:
    run_id: UUID; tick: int; sim_time: datetime
    clock: Clock; rng: RngRegistry; scheduler: Scheduler
    log: EventLog; runtime: RuntimeConfig; settings: Settings
    due: tuple[str, ...] = ()
    observations: dict[str, Any] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)          # agent_id -> reflex|deliberate|reflect
    actions: list[Any] = field(default_factory=list)             # C10 Action; typed Any here
    rejected: list[Any] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    timings: dict[Phase, float] = field(default_factory=dict)
    halt_reason: str | None = None
    def emit(self, draft: NewEvent) -> Event: ...                # -> log.stage(draft, tick, sim_time)

class PhaseHandler(Protocol):
    phase: Phase
    name: str
    order: int                                                   # ties broken by name
    async def run(self, ctx: TickContext) -> None: ...

class Institution(Protocol):
    name: str                                                    # must be in INSTITUTION_ORDER
    async def resolve(self, ctx: TickContext, actions: Sequence[Any]) -> None: ...

@dataclass(frozen=True, slots=True)
class TickReport:
    tick: int; sim_time: datetime
    events: int; ephemerals: int; actions: int; rejected: int
    llm_calls: int; cost_usd: Decimal
    timings_ms: Mapping[str, float]; over_budget: tuple[str, ...]
    violations: tuple[Violation, ...]; chain_hash: str
    halted: bool; halt_reason: str | None

@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: UUID; first_tick: int; last_tick: int; ticks: int
    events: int; wall_seconds: float; chain_hash: str
    status: Literal["completed", "halted", "failed"]; halt_reason: str | None

class TickLoop:
    def __init__(self, *, clock: Clock, rng: RngRegistry, scheduler: Scheduler,
                 log: EventLog, runtime: RuntimeConfig, settings: Settings,
                 invariants: InvariantRunner, checkpoints: CheckpointManager,
                 state: WorldStateView, telemetry: "PhaseTimer") -> None: ...
    def register(self, h: PhaseHandler) -> None: ...
    def register_institution(self, i: Institution) -> None: ...  # name must be in INSTITUTION_ORDER
    async def run_tick(self) -> TickReport: ...
    async def run(self, until_tick: int, *,
                  on_tick: Callable[[TickReport], None] | None = None) -> RunReport: ...
```

```python
# polis/kernel/checkpoint.py
class Checkpointable(Protocol):
    name: str
    def dump(self) -> Mapping[str, Any]: ...
    def load(self, state: Mapping[str, Any]) -> None: ...

@dataclass(frozen=True, slots=True)
class Checkpoint:
    run_id: UUID; tick: int; last_seq: int; chain_hash: str
    uri: str; bytes: int; payload_hash: str; created_at: datetime

class CheckpointManager:
    def __init__(self, blobs: BlobStore, repo: CheckpointRepository, *,
                 interval: int = 500, compress: bool = True) -> None: ...
    def due(self, tick: int) -> bool: ...
    async def write(self, run_id: UUID, tick: int, *, last_seq: int, chain_hash: str,
                    components: Sequence[Checkpointable]) -> Checkpoint: ...
    async def latest(self, run_id: UUID, *, at_or_before: int | None = None) -> Checkpoint | None: ...
    async def restore(self, run_id: UUID, tick: int,
                      components: Sequence[Checkpointable]) -> Checkpoint: ...

# polis/kernel/resume.py
@dataclass(frozen=True, slots=True)
class ResumePlan:
    run_id: UUID; checkpoint_tick: int; last_complete_tick: int
    resume_from_tick: int; replay_from_seq: int
    truncate_after_seq: int; orphan_events: int; chain_hash: str

async def plan_resume(events: EventRepository, checkpoints: CheckpointRepository,
                      run_id: UUID) -> ResumePlan: ...
async def execute_resume(plan: ResumePlan, *, events: EventRepository,
                         projections: ProjectionRouter,
                         components: Sequence[Checkpointable],
                         manager: CheckpointManager) -> None: ...

# polis/kernel/telemetry.py
class PhaseTimer:
    def __init__(self, *, budgets: Mapping[Phase, int] = PHASE_BUDGET_MS,
                 window: int = 200, sample_every: int = 25) -> None: ...
    @contextmanager
    def phase(self, p: Phase) -> Iterator[None]: ...
    def report(self, tick: int) -> Mapping[str, float]: ...       # kernel.phase.{n}.{p50,p95,last_ms}
    def over_budget(self, tick: int) -> tuple[str, ...]: ...
    def should_emit(self, tick: int) -> bool: ...
```

---

## 6. Interfaces you consume

| From | What |
|---|---|
| C01 | `Settings`, `ClockSettings`, `RuntimeConfig` (a `Checkpointable`), `canonical_json`, `mechanism`, `get_logger`, `PolisError` |
| C02 | `Event`, `NewEvent`, `EventLog`, `CommitResult`, `register_kind`, `Persistence`, `verify_batch`, `GENESIS_PREV_HASH` |
| C03 | `Database`, `EventRepository`, `CheckpointRepository`, `MetricRepository`, `RunRepository`, `LedgerRepository`, `ProjectionRouter`, `BlobStore` |
| C05 | `StubProvider` (tests), `LLMRouter.tick_metrics` (PHASE 9 telemetry), `BudgetGuard.begin_tick/end_tick` |

---

## 7. Data model touched

| Table | Access |
|---|---|
| `runs` | write — create at tick 0 (`RunRecord` incl. `metric_manifest`, `mechanism_manifest`, `ablations`, `scale`), `update_progress` each checkpoint, `finish` at end/halt |
| `events` | write via `EventLog` → `EventRepository`; read on resume |
| `checkpoints` | write on checkpoint, read on resume |
| `metrics` | write in PHASE 9 (kernel-owned `kernel.*` metrics only; C24 owns the catalogue) |
| `ledger_accounts`, `ledger_entries` | **read only**, through `WorldStateView`, for INV-MONEY / INV-LEDGER |

---

## 8. Event kinds owned

C02 owns 1001–1006 (run/tick framing). C04 owns **1010–1099**.

| Kind | Name | Persistence | Payload (required) |
|---|---|---|---|
| 1010 | `INVARIANT_VIOLATED` | persisted | `invariant_id`, `expected`, `actual`, `detail`, `halting` (bool) |
| 1011 | `INVARIANT_WARNED` | persisted | `invariant_id`, `expected`, `actual`, `detail` |
| 1020 | `CHECKPOINT_WRITTEN` | persisted | `tick`, `last_seq`, `chain_hash`, `uri`, `bytes`, `payload_hash` |
| 1021 | `CHECKPOINT_RESTORED` | persisted | `checkpoint_tick`, `resume_from_tick`, `replay_from_seq`, `orphan_events` |
| 1030 | `CADENCE_FIRED` | persisted | `cadence_id`, `phase`, `owner` |
| 1040 | `TICK_TIMING` | persisted | `tick`, `phase_ms` (object), `over_budget` (array) — emitted every `telemetry.timing_sample_every` ticks |
| 1050 | `PHASE_FAILED` | persisted | `phase`, `handler`, `error_type`, `message` |
| 90002 | `PHASE_TIMING` | **ephemeral** | `tick`, `phase_ms`, `inflight` |

---

## 9. Implementation notes

**9.1 Sim-time arithmetic.** `sim_time_at(tick) = EPOCH + timedelta(seconds=tick × seconds_per_tick)`.
A sim-year is 360 days = 12 months of 30 days = 4 quarters of 90 days. **A sim-week is 7
days and therefore does not tile the year** (360 / 7 = 51 weeks + 3 days). This is
intentional: `biweekly` payroll drifts against month boundaries exactly as it does in
reality. Do not "fix" it by redefining a week as 6 days — several cadences (`payroll`,
`market_session`) are specified in weeks and the drift is a documented property, not a bug.
`sim_week(tick) = sim_day(tick) // 7`, counted from epoch, never reset at year boundaries.

**9.2 `Clock` is pure except for `advance()`.** Every query takes an optional `tick` and
defaults to the current one. Nothing in the engine may call `datetime.now()`; the linter
enforces it. Wall-clock time appears only in `runs.started_at/ended_at`,
`checkpoints.created_at`, `llm_calls.latency_ms`, and `PhaseTimer` — never in an event
payload or in state.

**9.3 `RngRegistry` has no state.** Because the seed is a hash of
`(master_seed, namespace, entity_id, tick)`, a stream is fully re-derivable and the
registry's checkpoint payload is `{"master_seed": n, "version": 1}`. Two consequences worth
stating loudly: resume needs no RNG replay, and a subsystem that adds or removes draws
cannot perturb any other subsystem *provided it passes `tick=`*. Tick-scoped streams are
what make partial re-implementation safe (`02 §4.1`). `get()` returns a **fresh** `Random`
each call — there is deliberately no cached stream object, because a cached object's
position depends on call order and that is exactly the nondeterminism being eliminated.

**9.4 The determinism linter rules** (`scripts/lint_determinism.py`, AST-based, run in CI):

| Rule | Detail | Allowlist |
|---|---|---|
| No `import random` / `from random import …` | module level or function level | `polis/kernel/rng.py`, `polis/llm/providers/stub.py` |
| No `numpy.random.<x>` except `default_rng` | `seed`, `RandomState`, `rand`, `choice` on the global | `polis/kernel/rng.py` |
| No `datetime.now` / `utcnow` / `date.today` / `time.time` / `time.monotonic` | | `polis/cli/**`, `polis/kernel/telemetry.py`, `polis/store/**` (`created_at` columns), `polis/llm/**` (`latency_ms`) |
| No `uuid.uuid1/uuid4` | use `det_uuid` | `polis/cli/commands/run.py` (run_id) |
| No `sorted()`-free iteration over a `set` literal or `set()` result inside a `for` that calls `ctx.emit` | heuristic; warns rather than fails | — |
| No `os.environ` reads outside `polis/config` | config is the only env reader | `polis/llm/providers/**` (api-key env) |

The allowlist is a checked-in file with a required one-line justification per entry. CI
fails on an unjustified entry.

**9.5 The ten phases.** `run_tick()` executes phases 0–9 in order. Handlers registered for a
phase run in `(order, name)` order — sorted, never registration order. Rules:

- **PHASE 0** advances the clock, emits `TICK_STARTED` (1002) with `due_cadences`, emits
  `CADENCE_FIRED` (1030) per due cadence, and populates `ctx.due`.
- **PHASE 1–3** are the only phases permitted to use `asyncio.gather`. Results are
  re-ordered with `stable(results, key=…)` before anything mutates (`02 §4.3`).
- **PHASE 3** ends by sorting `ctx.actions` by `(actor_id, action_id)`. The loop does this,
  not the handler, so a badly behaved handler cannot leak completion order downstream.
- **PHASE 5** iterates `INSTITUTION_ORDER` and calls each registered institution once with
  the full validated action list. An institution registered under a name not in
  `INSTITUTION_ORDER` fails registration. Missing institutions are simply skipped — this is
  what lets M0 tick an empty world.
- **PHASE 6** calls `log.commit(tick)` exactly once. Nothing may `emit` after PHASE 6
  except phases 7–9, whose events belong to the same tick and are committed by a **second**
  `commit(tick)` call at the end of PHASE 9. Document this: a tick makes **two** commits
  (6 and 9), and `TICK_COMPLETED` is the last event of the second. The torn-tick detector
  keys on `TICK_COMPLETED`, so a crash between them is recoverable.
- **PHASE 9** snapshots `ctx.metrics`, runs `InvariantRunner`, emits `TICK_COMPLETED`
  (1003), checkpoints if due, commits.

**9.6 Exceptions.** `02 §10`: an unhandled exception in an institution or handler **halts**.
`run_tick` catches, emits `PHASE_FAILED` (1050) and `RUN_HALTED` (1005), commits what it
can, writes a checkpoint, sets `status='failed'`, and re-raises. Never swallow. The only
exception class the loop absorbs is a domain-declared `RecoverableActionError` from PHASE 4
validation, which is rejection, not failure.

**9.7 Invariants.** `02 §9`, implemented against `WorldStateView`:

| id | Statement | Frequency | Severity |
|---|---|---|---|
| `INV-MONEY` | `total_balances_cents() == money_supply_cents()` exactly | tick | HALT |
| `INV-LEDGER` | `ledger_imbalance_cents() == 0` | tick | HALT |
| `INV-SHARES` | per symbol, held == outstanding | tick | HALT |
| `INV-ORDERS` | `order_reserve_shortfalls()` empty | tick | HALT |
| `INV-EMPLOY` | `employment_anomalies()` empty | tick | HALT |
| `INV-CHAIN` | `chain_ok()` | checkpoint | HALT |
| `INV-POP` | population in `[0.2×, 5×]` initial | sim_day | WARN |
| `INV-ENTROPY` | action-type Shannon entropy ≥ `invariants.entropy_floor` | sim_day | WARN |
| `INV-NONDEGEN` | `top1_wealth_share < 0.9` and `0 < employment_rate < 1` | sim_day | WARN |

HALT path: emit 1010 with `halting: true` → checkpoint → emit 1005 → `commit` → set
`runs.status='halted'` with `halt_reason` → exit non-zero. `--continue-on-violation`
downgrades HALT to WARN **and stamps `runs.tags += ['invariant_violated']`**, so a run that
continued past a violation can never be silently pooled with one that did not. WARN emits
1011 and increments a counter. `NullWorldState` returns values that satisfy every invariant,
so M0's empty world is green.

**9.8 Checkpoints.** Payload is `{name: component.dump()}` for every `Checkpointable`
(`Clock`, `RngRegistry`, `RuntimeConfig`, `Scheduler` state, plus every projection owner
registered by later chunks), canonicalised, optionally zstd-compressed, stored via
`BlobStore` at `checkpoints/<run_hex>/<tick:012d>.json.zst`. `payload_hash` is the sha256 of
the **uncompressed** canonical bytes. The row goes in `checkpoints` (`03 §1.5`) in the same
transaction as the 1020 event. A checkpoint is never trusted blindly: `restore` verifies
`payload_hash` and, if `chain_hash` disagrees with the log's hash at `last_seq`, refuses and
falls back to full replay (`02 §5.3`).

**9.9 Resume.** `polis run --resume <run_id>`:

1. `last_complete_tick` = highest tick with a `TICK_COMPLETED`.
2. `truncate_after_seq` = seq of that `TICK_COMPLETED`; delete any events beyond it
   (a torn tick). `orphan_events` records how many.
3. Load the newest checkpoint with `tick <= last_complete_tick`; restore components.
4. Replay events from `checkpoint.last_seq + 1` to `truncate_after_seq` through
   `ProjectionRouter` to bring projections forward.
5. Re-open `EventLog` with `start_seq = truncate_after_seq` and
   `start_prev_hash = <hash of that event>`.
6. Emit `RUN_RESUMED` (1006) and `CHECKPOINT_RESTORED` (1021); continue at
   `last_complete_tick + 1`.

The chain continues unbroken across the restart. A resumed run must produce the same
terminal `chain_hash` as an uninterrupted one — that is the crash-recovery acceptance test.

**9.10 Timing telemetry.** `PhaseTimer.phase()` wraps each phase with `perf_counter`.
Rolling p50/p95 over a 200-tick window per phase. Over-budget phases (`02 §11`) are logged
at WARN with the budget and the observed value, exported as
`kernel.phase.{n}.p50_ms`/`.over_budget`, published ephemerally as 90002 every tick, and
persisted as 1040 every `telemetry.timing_sample_every` ticks. Timings never enter a hash.
`polis run` prints, at startup, the projected wall-clock completion from
`PHASE_BUDGET_MS` totals and the LLM-bound estimate from C05 (`09 §4.4`) — researchers
should learn a 43,200-tick run takes weeks *before* launching it.

**9.11 `@mechanism` in the kernel.** `demographic_acceleration` is a declared mechanism
(`02 §5.2`). Tag the function that applies it:
`@mechanism("clock.demographic_acceleration", entails="Agents age N sim-years per elapsed sim-year; any lifecycle result is conditional on N.")`.

---

## 10. Configuration keys

```yaml
clock:
  profile: microscope              # microscope | chronicle
  ticks_per_sim_day: 24            # derived; 1 for chronicle
  days_per_sim_year: 360
  demographic_acceleration: 4.0    # MECHANISM
  allow_nonstandard: false

kernel:
  strict_phase_order: true         # a handler emitting after PHASE 6 raises
  fail_fast: true                  # unhandled handler exception halts (02 §10)
  max_actions_per_tick: 20000      # guard against an action storm

invariants:
  continue_on_violation: false
  enabled: null                    # null = all; or an explicit id list
  entropy_floor: 1.2               # nats, INV-ENTROPY / V4
  pop_bounds: [0.2, 5.0]
  severity_overrides: {}           # {INV-POP: halt} for a strict run

checkpoint:
  interval: 500
  compress: true
  keep_last: 5                     # 0 = keep all
  verify_on_restore: true

telemetry:
  timing_sample_every: 25
  phase_budget_warn: true
  window: 200
  redis_publish: true
```

---

## 11. Acceptance criteria

- [ ] `polis run --config configs/smoke.yaml` ticks 500 ticks of an empty world with `StubProvider`, writes a verifiable chain, and exits 0.
- [ ] **Determinism:** two 200-tick runs with the same `(config, seed, cache)` produce byte-identical terminal `chain_hash`, under `PYTHONHASHSEED` 0 and 1, in two separate processes.
- [ ] `Clock`: `sim_time_at` is pure; `microscope` gives 8,640 ticks/sim-year and `chronicle` 360; `starts_new('month', t)` is true exactly once per 30 sim-days; week boundaries do not align to year boundaries and a test asserts that.
- [ ] `SimDuration.parse` handles `4y 1q 1mo 2w 1d 6h daily weekly biweekly monthly quarterly annually` and raises on anything else.
- [ ] `RngRegistry.seed_for` matches a hand-computed vector for `(seed=42, ns='labour.match', entity='fm_1', tick=7)` checked into the test file.
- [ ] `rng.get(ns, e, t)` returns a fresh stream: calling it twice with the same arguments yields the same first draw.
- [ ] Adding 1,000 extra draws in namespace A does not change any draw in namespace B at the same tick.
- [ ] `Scheduler.fires` is pure and stable across a checkpoint/restore; `due()` is sorted; `next_fire` is monotone.
- [ ] Phases execute in order 0–9; a handler registered for phase 5 cannot observe a mutation made in phase 7 of the same tick; `strict_phase_order` raises on a post-COMMIT emit outside phases 7–9.
- [ ] Institutions resolve strictly in `INSTITUTION_ORDER`; registration under an unknown name raises.
- [ ] `ctx.actions` is sorted by `(actor_id, action_id)` on exit from PHASE 3, regardless of `gather` completion order (test with a shuffled-latency fake).
- [ ] Each of the nine invariants fires on a purpose-built `WorldStateView` and emits 1010/1011 with the right severity; HALT writes a checkpoint before exiting; `--continue-on-violation` tags the run.
- [ ] Checkpoint round-trip: dump → restore → the next 50 ticks produce the same chain as an uninterrupted run.
- [ ] **Crash recovery:** kill the engine mid-tick (between PHASE 6 and PHASE 9), resume, and the terminal `chain_hash` at tick 500 equals the uninterrupted run's. `orphan_events` > 0 is recorded.
- [ ] A corrupted checkpoint blob (`payload_hash` mismatch) is refused and falls back to full replay.
- [ ] `PhaseTimer` reports p50 per phase; an artificially slow handler is flagged `over_budget` and appears in the 1040 payload.
- [ ] `scripts/lint_determinism.py polis/` exits 0; each of the six rules fails on a purpose-built fixture; an allowlist entry without a justification fails CI.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/kernel/test_clock.py` | Profile derivation; `sim_time_at` purity; ticks/sim-year for both profiles; month/quarter/year boundaries; the 7-day-week non-tiling property; `SimDuration.parse` table; `dump/load`. |
| `tests/unit/kernel/test_rng.py` | Golden `seed_for` vector; fresh-stream semantics; namespace isolation; tick-scoping independence; numpy generator determinism; `dump/load` is `{master_seed, version}` only. |
| `tests/unit/kernel/test_det.py` | `stable` sort stability and total order; `stable_dict`; `round6`; `det_uuid`/`det_id` determinism and prefix format; the canon re-export is the *same object* as `polis.config.canon.canonical_json`. |
| `tests/unit/kernel/test_scheduler.py` | Every cadence spec form; `fires` purity; `due` sorting; `next_fire` monotone; session cadences (`09:30-16:00`) open/close at the right hour in `microscope` and degenerate sanely in `chronicle`. |
| `tests/unit/kernel/test_tick_order.py` | Phase order; handler ordering by `(order, name)`; institution order; post-COMMIT emit guard; PHASE 3 sorting under shuffled completion; two-commit structure. |
| `tests/unit/kernel/test_invariants.py` | One test per INV-*; severity; frequency gating (tick vs sim_day vs checkpoint); `severity_overrides`; `continue_on_violation` tagging; `NullWorldState` is green. |
| `tests/unit/kernel/test_checkpoint.py` | Payload canonicalisation; `payload_hash`; compression round-trip; `keep_last` pruning; corrupted-blob refusal; `chain_hash` disagreement refusal. |
| `tests/integration/kernel/test_resume.py` | Torn-tick truncation; projection replay to head; chain continuity; resumed vs uninterrupted terminal hash equality; `orphan_events` accounting. |
| `tests/determinism/test_two_runs_identical.py` | 200 ticks × 2 processes × 2 `PYTHONHASHSEED` values → identical chain; the M0 exit gate. |
| `tests/determinism/test_phase_isolation.py` | A handler mutating state in phase 7 is invisible to a phase-5 handler in the same tick; a phase-1 handler observes only last tick's committed state. |
| `tests/integration/kernel/test_smoke_500.py` | `configs/smoke.yaml` for 500 ticks with `StubProvider`: no exceptions, chain verifies, `runs.status == 'completed'`, checkpoints at 500-tick intervals exist. |
| `tests/unit/kernel/test_telemetry.py` | Rolling percentiles; over-budget flagging; 1040 sampling cadence; timings absent from every event hash input. |
| `tests/unit/kernel/test_determinism_lint_rules.py` | Six fixtures, one per rule, plus allowlist honouring and unjustified-entry failure. |

---

## 13. Definition of done

`chunks/README.md §5` items 1–9, plus: **M0 exit gate** — `tests/determinism/test_two_runs_identical.py` and `tests/integration/kernel/test_smoke_500.py` both pass; `polis run --resume` demonstrably recovers from a `SIGKILL` mid-tick; the determinism allowlist is checked in with justifications; the `WorldStateView` protocol is stable enough that C11/C14 implement it without editing this chunk.

---

## 14. Traps

1. **`asyncio.gather` result order.** `gather` preserves *argument* order, not completion
   order — which makes it look deterministic until someone switches to `as_completed` or
   adds a `TaskGroup`. Sort explicitly with `stable()` anyway, and test with a fake whose
   latency is shuffled by a seeded RNG. The bug this prevents is invisible for months.
2. **A cached `Random` object.** The moment `get()` memoises the `Random` it returns,
   stream position depends on how many times each subsystem called it, and adding a single
   draw in the labour market changes the weather. Return a fresh object; make it explicit
   in the docstring; test it.
3. **`tick=None` streams.** `rng.get("labour.match", firm_id)` without a tick gives the same
   values every tick — an agent that "randomly" does the same thing forever. Either always
   pass `tick`, or make the omission deliberate and documented (world generation, agent
   birth traits). Consider warning at runtime when a `tick=None` stream is drawn from
   inside `run_tick`.
4. **The 360-day year and 7-day weeks.** A reviewer will "fix" the non-tiling. Write the
   test that asserts it and a comment that says why.
5. **`demographic_acceleration` applied in two places.** If both the clock and the vitals
   phase scale ageing, agents age N² times too fast and nobody notices until generation
   three never arrives. It belongs in exactly one place; tag it `@mechanism` and assert
   single application.
6. **Two commits per tick.** PHASE 6 and PHASE 9 both commit. If you only commit at 9, a
   crash loses the whole tick including institution effects; if you only commit at 6,
   phases 7–9 emit into a closed batch. Get this right or resume is unsound. The
   `TICK_COMPLETED`-based torn-tick detector depends on it.
7. **Invariants reading uncommitted state.** PHASE 9 runs after the PHASE 6 commit but
   before the PHASE 9 commit. `WorldStateView` must read the *in-memory* projections, not
   the database, or INV-MONEY tests state that is one commit stale and reports a violation
   that does not exist. State this in the Protocol docstring.
8. **INV-MONEY as a float comparison.** "To the cent, exactly" means integer equality.
   Any `abs(a-b) < 1e-6` here defeats the single best bug detector in the system.
9. **HALT that does not checkpoint.** `02 §9` requires a checkpoint on halt. A run that
   halts without one cannot be inspected at the point of failure, which is the only reason
   halting is better than crashing.
10. **`--continue-on-violation` without a tag.** A run that continued past an accounting
    violation looks identical to a clean run in every export. Tag it on the `runs` row and
    make `polis gate` refuse it.
11. **Checkpoint payload containing wall-clock or object identity.** `dump()` must be pure
    data. A component that serialises `id(obj)`, a `datetime.now()`, or a set (whose JSON
    order is arbitrary) makes `payload_hash` unstable and restores subtly wrong state.
    Canonicalise and hash; the hash instability shows up immediately.
12. **Resume replaying events the checkpoint already contains.** Off-by-one on
    `checkpoint.last_seq + 1` double-applies a tick's projections. Non-idempotent handlers
    then double-count wealth. Test the boundary explicitly at `last_seq` and `last_seq + 1`.
13. **`perf_counter` values leaking into an event.** 1040's payload carries phase timings.
    That is fine *because 1040 is only emitted every 25 ticks and its payload is not
    otherwise consumed* — but it does enter the hash chain, so **two runs on different
    hardware produce different chains.** Resolution: 1040 must be **ephemeral-equivalent** —
    either move all timing to kind 90002 only, or round timings to a coarse bucket and
    accept that determinism tests must run on one machine. **Choose the first**: emit 1040
    with `phase_ms` omitted under `telemetry.deterministic: true` (the default in CI and in
    every determinism test), carrying only `over_budget` flags derived from a fixed
    threshold. Flag this trade-off in the handback; it is the one place where telemetry and
    determinism genuinely conflict.
14. **`max_actions_per_tick` unset.** A bug in a future chunk that emits an action per
    agent per place produces 20M actions and an OOM two hours in. Bound it in the kernel,
    where the bound is cheap.
15. **Institutions mutating `ctx.actions`.** PHASE 5 hands each institution the same list.
    An institution that filters it in place changes what later institutions see, silently
    breaking `INSTITUTION_ORDER`'s guarantee. Pass a read-only view
    (`tuple(ctx.actions)`), not the list.
