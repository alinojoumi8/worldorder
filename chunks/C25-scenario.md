# C25 — Scenario / shock DSL, signed researcher injection

**M6** · `polis/research/scenario.py` (+ `polis/research/scenario/`) · **Depends on:** C01 (`RuntimeConfig`, `MECHANISM_REGISTRY`), C02 (kinds, canonical bytes, signatures), C03 (repositories), C04 (`Clock`, `Scheduler`, `InvariantRunner`, `TickContext`, `PhaseHandler`), C10 (`Action`, `origin="scripted"`), C17 (belief update path), C18 (`POLICY_REGISTRY` bounds), C20 (settlement paths), C24b (metrics, gates, `polis verify`) · **Blocks:** M6 completion · **Size:** M (2–4 days)

---

## 1. Context

A society you can only watch is not an experiment. This chunk lets a researcher deliver a **shock** — a rate hike, a large borrower's default, a coordinated smear campaign — declaratively, reproducibly, and with a signature, so that six months later nobody has to guess whether an event was the model's or the researcher's. The shape is taken directly from Block Buzz's YAML workflow engine: triggers and steps, kind-dispatched, hashed, nothing imperative and nothing in Python. Two constraints do all the work. **Every injection is signed by the researcher key and lands in the hash chain**, so in a verified run an unsigned shock cannot exist and no organic event can be mistaken for an injection. And **a scenario may not violate an invariant** — it can deliver exactly the shocks a government or a bankruptcy could deliver, which is the correct expressive limit: a recession scenario that lays people off is a scenario that assumes its own conclusion.

---

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/02-ARCHITECTURE.md` | **all** — §3.1 canonical serialisation, §3.4 signatures, §5 tick phases (PHASE 0 is where every step lands), §5.2 clock profiles, §8.1 `MECHANISM`, §9 invariants |
| `../docs/03-DATA-MODEL.md` | §0 conventions, §1.1 `runs`, §1.2 `events`, §4 ledger, §10 `scenario_injections` |
| `../docs/10-RESEARCH-AND-OBSERVABILITY.md` | **§4 in full — primary source**; §0.5 RNG namespaces, §0.6 amendments, §2.2 invariants, §4.6 kinds, §4.7–4.9 worked scenarios, §5.2 `polis verify`, §12 R9 |
| `../docs/07-SOCIETY-SPEC.md` | §5.4 belief update path, §7.1–§7.4 `RuntimeConfig`, `POLICY_REGISTRY`, bounds check |
| `../docs/06-ECONOMY-SPEC.md` | §10 firm/bank settlement, §1.5 money aggregates |
| `../docs/04-AGENT-SPEC.md` | §11 validation gates, §12.3 death settlement |
| Chunks | C01 (`RuntimeConfig.enact`), C02 (`register_kind`, `NewEvent`, `verify_signature`), C04 (`Scheduler`, `InvariantRunner`, `PhaseHandler`), C10 (`Action`, `origin="scripted"`), C24 (metric registry, gates, `polis verify`, the 99xxx range split) |

---

## 3. Scope — in

1. **The DSL** — YAML schema (`dsl_version: 1`), loader, validator, `scenario_hash = sha256(canonical_yaml)`.
2. **Five trigger types** — `at_tick`, `at_sim_time` (resolved to a tick at load), `on_metric_threshold`, `on_event_kind`, `schedule`.
3. **Nine step types** — `set_parameter`, `inject_event`, `kill_entity`, `spawn_entity`, `force_action`, `publish_falsehood`, `seed_rumour`, `annotate`, `abort_run`.
4. **Selectors** — `{where: <predicate>, sample: N}`, drawn through `rng.get("research.scenario.select", step_id, tick)`.
5. **Value expressions** — literals, `@scale(f)`, `@delta(x)`, and named resolvers such as `@largest_borrower(bk_02)`, expanded at fire time and **recorded expanded** so replay never re-resolves.
6. **Signing** — the injection digest, ed25519 by the researcher key, `sig` into the `Event` envelope and therefore the hash chain.
7. **The invariant guard** — HALT-class invariants run immediately after each step, inside the tick's transaction, with rollback and `abort | skip`.
8. **Recording** — one `scenario_injections` row per injection-class step with `step_id`, `event_seq`, `scenario_hash`, `researcher_pubkey`, `sig`.
9. **Guards and funding** — `max_injections`, `max_seeded_agents`, `funding.account`, `cap_cents`; **no money from nothing**.
10. **Paired control** — `paired_control: true` launches a seed-matched, injection-free twin.
11. **`expects:`** — pre-registered directional expectations, checked by `polis gate`.
12. **CLI** — `polis scenario lint | sign | dry-run`, and `polis run --scenario <file> [--with-control]`.
13. **Three worked scenarios as shipped fixtures** — recession, bank failure, coordinated misinformation.
14. Event kinds **99000–99006, 99010, 99011, 99020, 99021, 99030, 99040, 99041, 99080**.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| Kinds **99050, 99060, 99070, 99090, 99091** | **C24** — see the range split in §6 |
| The metric registry, gates, `polis verify` implementation | **C24** (you supply the injection-class rules `verify` checks) |
| `RuntimeConfig` itself, `POLICY_REGISTRY` contents, the bounds check | **C01 / C18** — you *call* them |
| Settlement logic for a killed agent / firm / bank | **C20 / C11 / C14** — you invoke the normal path |
| The belief update path and `10060` | **C17** — `seed_rumour` writes through it, never by direct table write |
| Feed algorithms, `POST_WRITE`, article composition | **C16 / C17** — `publish_falsehood` with `text: null` lets the carrier compose |
| Rendering scenarios and injections | **C23b** |
| The ablation ladder (`99050`) | **C24** |

---

## 5. Interfaces you provide

```python
# polis/research/scenario/types.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Final, Literal, Mapping, Sequence
from uuid import UUID

DSL_VERSION: Final[int] = 1

TriggerType = Literal["at_tick", "at_sim_time", "on_metric_threshold", "on_event_kind", "schedule"]
StepAction  = Literal["set_parameter", "inject_event", "kill_entity", "spawn_entity",
                      "force_action", "publish_falsehood", "seed_rumour", "annotate", "abort_run"]

INJECTION_CLASS: Final[frozenset[StepAction]] = frozenset({
    "set_parameter", "inject_event", "kill_entity", "spawn_entity",
    "force_action", "publish_falsehood", "seed_rumour"})          # these MUST be signed

@dataclass(frozen=True, slots=True)
class Trigger:
    id: str
    type: TriggerType
    # at_tick / at_sim_time
    tick: int | None = None                    # at_sim_time is resolved to a tick AT LOAD
    # on_metric_threshold
    metric: str | None = None
    op: Literal["lt", "le", "gt", "ge"] | None = None
    value: float | None = None
    sustained_for: str | None = None           # SimDuration spec, e.g. "60d"
    cooldown_ticks: int = 0
    # on_event_kind
    kind: int | None = None
    match: Mapping[str, Any] | None = None     # JSON-path predicates, e.g. {"$.liabilities_cents": {"gte": 5_000_000_0}}
    actor_in: tuple[str, ...] | None = None
    subject_in: tuple[str, ...] | None = None
    # schedule
    cadence: Mapping[str, str] | None = None   # {every: "1d", from: "Y3-M01-D01", until: "Y5-M01-D01"}
    at: str | None = None                      # "09:00"
    jitter_ticks: int = 0
    max_fires: int | None = None

@dataclass(frozen=True, slots=True)
class Selector:
    where: str                                  # closed predicate grammar; see §9.4
    sample: int | None = None

@dataclass(frozen=True, slots=True)
class Step:
    id: str
    trigger: str                                # a Trigger.id
    action: StepAction
    params: Mapping[str, Any]
    if_: str | None = None                      # guard predicate over metric/state
    selector: Selector | None = None

@dataclass(frozen=True, slots=True)
class Guards:
    respect_invariants: Literal[True] = True    # the loader REJECTS any attempt to set this false
    on_guard_violation: Literal["abort", "skip"] = "abort"
    max_injections: int = 200
    max_seeded_agents: int = 100

@dataclass(frozen=True, slots=True)
class Funding:
    account: str                                # 'government' | 'central_bank' | 'scenario_endowment'
    cap_cents: int = 0

@dataclass(frozen=True, slots=True)
class Expectation:
    metric: str
    direction: Literal["up", "down", "flat"]
    within_sim_days: int

@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    name: str
    dsl_version: int
    researcher_key: str                         # pubkey id; the private key signs each injection
    researcher_pubkey: str                      # 64 hex, resolved at load from the keyring
    research_questions: tuple[str, ...]
    guards: Guards
    funding: Funding
    paired_control: bool
    expects: tuple[Expectation, ...]
    triggers: tuple[Trigger, ...]
    steps: tuple[Step, ...]
    scenario_hash: str                          # sha256(canonical_yaml)
    source_path: str
```

```python
# polis/research/scenario/loader.py
def load(path: Path, *, clock: Clock, keyring: "ResearcherKeyring",
         metric_registry: Mapping[str, Any]) -> Scenario:
    """Parse + validate + hash. Every check below is BLOCKING at load, not at fire time:
      - dsl_version == 1
      - unique trigger ids and step ids; every step.trigger resolves
      - guards.respect_invariants is absent or true; `false` is a load error
      - every `metric` in a trigger or `if` exists in the metric registry
      - every `set_parameter.parameter` is in POLICY_REGISTRY or the scenario-writable set,
        and its literal/expanded value satisfies that registry's admissible range
      - set_parameter NEVER names a `mechanisms:` key (that would invalidate mechanism_manifest)
      - inject_event.kind is on the injectable allowlist in kinds.py
      - every selector `where` parses against the closed predicate grammar (§9.4)
      - publish_falsehood.target_proposition is CHECKABLE against the log (§9.6)
      - at_sim_time resolves to a tick under this run's clock profile
      - Σ declared endowments ≤ funding.cap_cents
      - researcher_key resolves to a pubkey
    Raises ScenarioError with the offending step_id and a one-line reason."""

class ScenarioError(PolisError):
    step_id: str | None; trigger_id: str | None; reason: str

# polis/research/scenario/signing.py
def injection_digest(scenario_hash: str, step_id: str, tick: int,
                     params: Mapping[str, Any]) -> bytes:
    """sha256(scenario_hash ‖ step_id ‖ tick.to_bytes(8,'big') ‖ canonical_json(params))"""
def sign_step(sk: bytes, scenario_hash: str, step_id: str, tick: int,
              params: Mapping[str, Any]) -> str: ...          # 128 lowercase hex
def verify_step(pubkey_hex: str, scenario_hash: str, step_id: str, tick: int,
                params: Mapping[str, Any], sig_hex: str) -> bool: ...

class ResearcherKeyring:
    def pubkey(self, key_id: str) -> str: ...
    def private(self, key_id: str) -> bytes: ...              # from POLIS_RESEARCHER_KEYFILE
    @staticmethod
    def sign_file(path: Path, key_id: str) -> Path: ...       # writes <file>.sig
```

```python
# polis/research/scenario/engine.py
@dataclass(frozen=True, slots=True)
class FiredTrigger:
    trigger_id: str; tick: int; fire_count: int
    evaluated_value: Any; source_event_seq: int | None

@dataclass(frozen=True, slots=True)
class StepOutcome:
    step_id: str
    applied: bool
    skipped_reason: Literal["guard_failed","invariant_guard","target_missing",
                            "max_fires","cap_exceeded","selector_empty"] | None
    target_ids: tuple[str, ...]
    resulting_seqs: tuple[int, ...]
    injection_id: str | None
    sig: str | None

class ScenarioEngine:
    """Two PhaseHandlers and one event hook.
      Phase.CLOCK (0)   -> apply()        : ALL steps land here, in step-id order
      Phase.METRICS (9) -> observe()      : evaluates on_metric_threshold, queues for next tick
      on append (P6)    -> on_event()     : evaluates on_event_kind, queues for next tick
    Nothing in this DSL mutates state mid-tick (02 §5)."""
    def __init__(self, scenario: Scenario, *, clock: Clock, scheduler: Scheduler,
                 rng: RngRegistry, runtime: RuntimeConfig, invariants: InvariantRunner,
                 keyring: ResearcherKeyring, repo: "ScenarioRepository",
                 resolvers: "ResolverTable") -> None: ...

    def register(self, loop: TickLoop) -> None: ...           # installs both handlers
    async def apply(self, ctx: TickContext) -> tuple[StepOutcome, ...]: ...
    async def observe(self, ctx: TickContext) -> tuple[FiredTrigger, ...]: ...
    def on_event(self, ev: Event) -> tuple[FiredTrigger, ...]: ...
    def forced_actions(self, tick: int) -> tuple[Action, ...]:
        """Read in PHASE 3 by the composition root. Each carries origin='scripted' and
        passes PHASE 4 validation like any other action."""
    def summary(self) -> Mapping[str, Any]: ...               # -> 99005 payload

# polis/research/scenario/repository.py
class ScenarioRepository(Repository):
    async def record(self, *, injection_id: str, scenario_id: str, step_id: str, tick: int,
                     kind: str, payload: Mapping[str, Any], researcher_pubkey: str,
                     sig: str, event_seq: int, scenario_hash: str) -> None: ...
    async def by_run(self) -> list[Mapping[str, Any]]: ...
    async def orphans(self) -> list[Mapping[str, Any]]:
        """Injection-class 99xxx events with no scenario_injections row. `polis verify`
        FAILS on a non-empty result."""

# polis/research/scenario/resolvers.py
class ResolverTable:
    """The closed set of @-expressions. Adding one is a spec change, not a convenience."""
    def resolve(self, expr: str, ctx: TickContext) -> Any: ...
    # @scale(f) @delta(x)                   -> against the value in force at fire time
    # @largest_borrower(<bank_id>)          -> agent|firm id
    # @largest_depositor(<bank_id>)
    # @carriers                             -> agents tagged by a prior spawn_entity step
    # $event.<path>                         -> from the triggering event, on_event_kind only

# polis/research/scenario/predicates.py
def parse_where(expr: str) -> "Predicate": ...                # closed grammar, §9.4
def evaluate(pred: "Predicate", ctx: TickContext) -> tuple[str, ...]: ...   # -> sorted ids

# polis/research/scenario/organic.py
def is_organic(db: Database, run_id: UUID, seq: int) -> bool:
    """No ancestor along cause_seq in kinds 99000-99999. THE shared filter (10 §12 R9).
    Every analysis of 'organic' propagation calls this one function; a per-notebook
    reimplementation is how an injected item gets counted as organic spread."""
```

**CLI** (registered on C01's Typer app):

| Command | Effect |
|---|---|
| `polis scenario lint <file>` | Schema, allowlists, selector resolvability, checkable-claim check, invariant-reachability check. No signing, no run. Exit 0/1. |
| `polis scenario sign <file> --key rk_ali_2026` | Computes `scenario_hash`, signs each step digest, writes `<file>.sig` |
| `polis scenario dry-run <file> --against <run_id>` | Replays the referenced run and reports which triggers fire, when, and on what target sets — writing nothing |
| `polis run <config> --scenario <file> [--with-control]` | Runs it; `--with-control` launches the seed-matched injection-free twin |

---

## 6. Interfaces you consume

| From | Symbol | Notes |
|---|---|---|
| C01 | `RuntimeConfig.enact(parameter, value, effective_tick, policy_id, event_seq)` | **`set_parameter` writes through this, exactly as the policy engine does.** A researcher shock and an in-world election use one mechanism. |
| C01 | `MECHANISM_REGISTRY`, `canonical_json`, `sha256_hex` | mechanism-key rejection, hashing |
| C02 | `register_kind`, `NewEvent`, `verify_signature`, `KIND_REGISTRY`, `INJECTABLE_KINDS` | your kinds; the injectable allowlist |
| C03 | `Database`, `Repository`, `MetricRepository.series` | recording, threshold triggers |
| C04 | `Clock` (`tick_at`, `ticks_for`, `sim_time_at`), `Scheduler.register/fires`, `RngRegistry.get`, `InvariantRunner.run/should_halt`, `TickContext`, `PhaseHandler`, `det.stable` | timing, guard, ordering |
| C10 | `Action`, `ActionType`, `Origin` (`"scripted"`) | `force_action` |
| C17 | the belief update path behind `10060 BELIEF_UPDATED` | `seed_rumour` writes through it |
| C18 | `POLICY_REGISTRY`, the `07 §7.4` bounds check | `set_parameter` has exactly the reach of a law |
| C20 / C11 / C14 | the normal settlement paths | `kill_entity` |
| C24 | `METRIC_REGISTRY`, `GateResult`, `polis verify` | metric validation, `expects:` checking |

> **Kind range split with C24 — binding.** `10 §0.1` gives 99000–99999 to the scenario DSL,
> but five of those kinds are experiment/observation records C24a needs at M1, before this
> chunk exists. **C24 owns 99050, 99060, 99070, 99090, 99091. C25 owns everything else in the
> range.** Both register with `owner="polis.research"`. Neither declares the other's; a test
> in each chunk asserts the split.

---

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `scenario_injections` | **W** | one row per injection-class step; `10 §0.6` adds `step_id TEXT NOT NULL`, `event_seq BIGINT NOT NULL`, `scenario_hash CHAR(64) NOT NULL` |
| `events` | **W** (via `ctx.emit`) | your half of 99000–99999, plus the domain events a step causes, each with `cause_seq` pointing at the injection |
| `runs` | R + W (`tags`) | `scenario_hash` in `RUN_STARTED`; `tags += 'scenario:<id>'`, `'paired_control'` on the twin |
| `metrics` | **R** | `on_metric_threshold`, `if` guards, `expects:` |
| `policies` | indirect | `set_parameter` produces the same downstream record a policy enactment does |
| `agents`, `firms`, `banks`, `loans`, `beliefs`, `posts`, `articles` | indirect only | **never a direct write** — always through the owning institution's path |
| `ledger_entries` | indirect only | `spawn_entity` endowments move through `ledger.post_transaction` from `funding.account` |

**A scenario has no direct table write anywhere.** If a step needs one, the step is wrong.

---

## 8. Event kinds owned

Range **99000–99999 minus C24's five**. Owner `polis.research`. Signed where marked; signed events carry the researcher signature in `Event.sig` and therefore enter the hash chain (`02 §3.4`).

| Kind | Name | Payload | Signed |
|---|---|---|---|
| 99000 | `SCENARIO_LOADED` | `scenario_id, name, scenario_hash, dsl_version, researcher_pubkey, triggers_n, steps_n, guards` | yes |
| 99001 | `SHOCK_INJECTED` | `injection_id, scenario_id, step_id, action, parameter, old_value, value, target_ids[], trigger_id` | **yes** |
| 99002 | `SCENARIO_TRIGGER_FIRED` | `scenario_id, trigger_id, trigger_type, condition, evaluated_value, fire_count` | no |
| 99003 | `SCENARIO_STEP_APPLIED` | `step_id, action, params, target_ids[], resulting_seqs[]` | no |
| 99004 | `SCENARIO_STEP_SKIPPED` | `step_id, reason, detail` | no |
| 99005 | `SCENARIO_COMPLETED` | `scenario_id, steps_applied, steps_skipped, last_tick` | no |
| 99006 | `SCENARIO_ABORTED` | `scenario_id, reason, invariant_id, step_id` | no |
| 99010 | `PARAMETER_SET` | `parameter, scope, old_value, new_value, ramp_ticks, revert_at_tick, step_id` | **yes** |
| 99011 | `PARAMETER_REVERTED` | `parameter, scope, from_value, to_value, step_id` | yes |
| 99020 | `ENTITY_KILLED` | `entity_id, entity_type, cause, settlement_seqs[]` | **yes** |
| 99021 | `ENTITY_SPAWNED` | `entity_id, entity_type, spec, funding_txn_id` | **yes** |
| 99030 | `ACTION_FORCED` | `actor_id, action_type, params, original_mode, accepted, reject_reason` | **yes** |
| 99040 | `FALSEHOOD_PUBLISHED` | `item_id, item_kind, carrier_id, target_proposition, claimed_value, true_value, checkable, source_event_seqs[]` | **yes** |
| 99041 | `RUMOUR_SEEDED` | `proposition, claimed_value, confidence, seed_agent_ids[], source_label` | **yes** |
| 99080 | `RESEARCHER_NOTE` | `text, author, refs[]` | yes |

`actor_id` is `null` on every scenario kind: a shock has no citizen actor. Downstream domain events point back with `cause_seq`, which is what makes `is_organic()` a one-function query.

---

## 9. Implementation notes

### 9.1 The universal timing rule

> **Triggers are evaluated at phase boundaries; ALL steps apply at PHASE 0.**

| Trigger | Evaluated | Steps apply |
|---|---|---|
| `at_tick` | PHASE 0 | same tick |
| `at_sim_time` | **load time** — the Clock resolves it to a tick, so by run start it *is* an `at_tick` | that tick |
| `on_metric_threshold` | PHASE 9, after the metric is written; fires once the condition has held for `sustained_for` | PHASE 0 of the **next** tick |
| `on_event_kind` | PHASE 6, on append | PHASE 0 of the **next** tick — never mid-commit |
| `schedule` | PHASE 0, through the **same** `Scheduler` as institutional cadences | same tick |

Nothing in this DSL mutates state mid-tick. That is what preserves "no phase reads a state change made later in the same tick" (`02 §5`) and what makes a scenario run replayable. A step that "just needs to fire in PHASE 5" is a step that needs to be two steps a tick apart.

`schedule` jitter draws from `rng.get("research.scenario.jitter", trigger_id, tick)`; selector sampling from `rng.get("research.scenario.select", step_id, tick)`. Both are seeded and both must be, or a scenario is not replayable.

### 9.2 `set_parameter` writes through `RuntimeConfig`

```python
old = runtime.get(parameter, tick)
new = resolvers.resolve(value_expr, ctx)          # @scale/@delta expanded HERE, recorded expanded
policy_engine_bounds_check(parameter, new)        # 07 §7.4 — a shock has the reach of a law, no more
seq = ctx.emit(NewEvent(kind=99010, payload={...old, new, ramp_ticks, revert_at_tick, step_id},
                        sig=sign_step(...))).seq
runtime.enact(parameter, new, effective_tick=tick, policy_id=f"sc:{scenario_id}:{step_id}",
              event_seq=seq, enacted_tick=tick)
```

**This is the same call the policy engine makes when an election changes a tax rate.** One tick-keyed overlay, one enactment list, one `get(parameter, tick)` read path. A researcher shock and an in-world law are therefore indistinguishable to every consumer of the parameter — which is exactly what you want, because it means the shock exercises the code the model actually runs.

| Rule | Detail |
|---|---|
| Closed set | The parameter must be in `POLICY_REGISTRY` or the scenario-writable config set, and satisfy its admissible range |
| **No mechanism keys** | `set_parameter` may **never** name a `mechanisms:` key. Mechanisms are set at run start or not at all; changing one mid-run invalidates `runs.mechanism_manifest` and every arm comparison drawn from it |
| Expansion | `@scale(0.70)` / `@delta(+0.07)` resolve against the value **in force at fire time** and are recorded expanded in the 99010 payload, so replay never re-resolves and cannot drift |
| Ramp | `ramp_ticks` schedules a series of enactments, each its own 99010, each signed — not one enactment with a hidden interpolation |
| Revert | `revert_after` schedules the inverse enactment and emits 99011 |

### 9.3 The invariant guard

`guards.respect_invariants` is `true` and **the loader rejects any attempt to set it false.** Mechanically:

1. Steps apply at PHASE 0, inside the tick's transaction.
2. **Immediately after each step**, the `InvariantRunner` runs the HALT-class invariants — INV-MONEY, INV-LEDGER, INV-SHARES, INV-ORDERS, INV-EMPLOY — without waiting for PHASE 9.
3. On violation the step is **rolled back within the transaction**, `99004 SCENARIO_STEP_SKIPPED{reason: 'invariant_guard', invariant_id}` is emitted, and the scenario aborts or skips per `on_guard_violation` (default **abort**: a scenario that silently did not happen is worse than a run that failed).
4. `set_parameter` additionally passes the policy-engine bounds check.

A scenario is therefore incapable of creating money, destroying shares, orphaning an employment record, or leaving a resting order unfunded.

### 9.4 The predicate grammar is closed

`selector.where` and `if` accept a small, parsed, **non-eval** grammar:

```
expr    := term (('and' | 'or') term)*
term    := field op value | func '(' args ')' | 'not' term | '(' expr ')'
op      := == != < <= > >= in
field   := agent.<col> | firm.<col> | household.<col> | tag
func    := has_deposit_at(<bank_id>) | employed_by(<firm_id>) | in_district(<district_id>)
         | holds(<symbol>) | age_between(a,b) | wealth_quintile(q) | believes(<prop>, op, v)
```

`eval()` and `exec()` are banned in this package (AST test). A predicate returns a **sorted** id tuple; `sample: N` then draws from that sorted tuple with the seeded RNG. Unsorted evaluation is the classic way a scenario becomes irreproducible while looking deterministic.

### 9.5 Steps that must not shortcut

| Step | The rule that makes it honest |
|---|---|
| `kill_entity` | Runs the **normal** settlement path (`04 §12.3` for agents, `06 §10` for firms and banks). There is **no raw delete**: a scenario cannot make an entity vanish without its obligations resolving. The write-off lands on the lender's balance sheet as a real loss and INV-MONEY holds across it. |
| `spawn_entity` | Any endowment is funded from `funding.account` within `cap_cents`, through balanced ledger legs. **No money from nothing.** An endowment above the cap is `99004{reason: 'cap_exceeded'}`. |
| `force_action` | The action enters PHASE 3 with `origin="scripted"` and **passes PHASE 4 validation like any other**. A forced illegal action is rejected and logged (99030 with `accepted: false`), not privileged. `mode: replace` displaces the agent's own action; `mode: append` costs an extra slot and must be declared. |
| `inject_event` | The kind must be on the injectable allowlist in `kinds.py`. A kind whose handler moves money is injectable **only** if the payload carries balanced legs. |
| `publish_falsehood` | The claim must be checkable against the event log at load time; an uncheckable claim is a **load error**, because an unmeasurable falsehood is useless for B2. `text: null` means the carrier's own `POST_WRITE` call composes it — the scenario supplies the intent, the model supplies the words. |
| `seed_rumour` | Beliefs are written through the normal update path (`07 §5.4`), producing `10060` per agent with `source='injected'`, never by direct table write. Bounded by `guards.max_seeded_agents`. |
| `annotate` | No state effect. The lab notebook, in the log, at the tick it refers to. |
| `abort_run` | Ends cleanly: `status='halted'`, `halt_reason` set, 99006. |

### 9.6 Ground truth for `publish_falsehood`

`true_value` for a proposition is computed **from the event log at load time** — for `fact.acme_is_fraudulent`, "are there `13010` fraud events with Acme as perpetrator?" — so `posts.truthfulness` is exact rather than labelled. This is what makes B2 measurable here and unmeasurable in the field. A proposition with no log-derivable truth value fails `polis scenario lint` with `checkable: false` and the step must be rewritten or removed.

### 9.7 Signing, recording, and why `polis verify` can be strict

```
injection_digest = sha256(scenario_hash ‖ step_id ‖ tick.to_bytes(8,"big") ‖ canonical_json(params))
sig              = ed25519_sign(researcher_privkey, injection_digest)
```

`sig` goes into the `Event` envelope and therefore into the hash chain. One `scenario_injections` row is written per injection-class step with `step_id`, `event_seq`, `scenario_hash`, `researcher_pubkey`, `sig`.

`polis verify` (C24) then checks every injection-class 99xxx event against the run's declared researcher pubkey and **fails verification** if any is missing, invalid, or lacks a row. The consequence is the point: **in a verified run, an unsigned shock cannot exist**, so no organic event can be mistaken for an injection and no injection can hide as organic. That is what makes `misinfo.organic_share` meaningful, and it is why `02 §3.4` signs scenario injections despite not signing native actions.

Observation-class kinds (99002–99006, and C24's 99060/99070) are engine-emitted and unsigned; chain integrity covers them.

### 9.8 Paired control and identification

`paired_control: true` launches a second run with the **same seeds**, the same config, and `steps: []`. The estimate is the **seed-paired difference in the impulse response**, never a before/after within one run — a before/after confounds the shock with everything else the run was doing. The control run carries `tags += 'paired_control'` and `parent_run_id` pointing at the treated run.

`expects:` entries are pre-registered directional predictions, checked by `polis gate` against the paired difference. They are **not** gates on the run's validity: a shock that fails to move unemployment is a finding about the model, and suppressing it would be the worst kind of dishonesty this repository can commit.

### 9.9 The three shipped fixtures

`configs/scenarios/`, each with a `.sig`, each linted in CI, each with a smoke test that runs 200 ticks against `StubProvider` and asserts the triggers fire and the invariants hold.

**A — `sc_recession_v1`** (A4, A5, B4). Monetary tightening with fiscal contraction: `money.policy_rate` 0.04 → 0.11 ramped over 720 ticks at `t_onset`; `welfare.unemployment_benefit_cents` `@scale(0.70)` with `revert_after: 360d`; a recovery cut triggered by `unemployment_rate ≥ 1200 bp sustained 60d`; a monthly `annotate`.

> **What it must not do.** It does not fire anyone, does not touch beliefs, does not adjust firm behaviour. Every labour-market and credit response must come from agents and institutions reacting to **two prices**. A recession scenario that lays people off is a scenario that assumes its own conclusion.

**B — `sc_bank_failure_v1`** (A5, B2). A large borrower is killed through the **normal** bankruptcy path at `t_default`; a `seed_rumour` on `trust.institution.bank.bk_02` fires on `9030` with liabilities ≥ 50,000,000 cents; an `annotate` fires on `bank.deposit_outflow_bp.bk_02 ≥ 2000 sustained 3d`.

> **Why it is built this way.** The bank is **not** killed. The write-off lands on the lender's balance sheet as a real loss, INV-MONEY holds across it, and everything downstream — capital ratio, discount-window use, interbank exposure, deposit behaviour, whether the bank actually fails — is the model's answer rather than the scenario's assumption. If the scenario set `bk_02.status='failed'`, the contagion result would be a tautology.

**C — `sc_coord_misinfo_v1`** (B2, B1, A3). Twenty carriers spawned with `honesty: 0.1, extraversion: 0.9` from a capped `scenario_endowment`; daily `publish_falsehood` by 6 sampled carriers on `fact.acme_is_fraudulent` with `text: null`; `force_action REPOST` by 8 other carriers on `11010` authored by a carrier; a fact-check `inject_event 11033` at `t_correction`.

> Ground truth is computed from the log at load time, so `truthfulness` is exact. The full design is this scenario × `society.feed_algorithm ∈ {chronological, engagement, random, adversarial}` × 20 seeds, plus the paired control. The adversarial arm is an **upper bound, never evidence**.

### 9.10 `MECHANISM` declaration

```python
@mechanism("research.scenario_injection",
           entails="A scenario sets parameters, kills or spawns entities, forces actions, and "
                   "seeds beliefs at declared ticks. Any movement in a metric within the "
                   "declared response window of an injection is partly attributable to the "
                   "injection by construction, not to endogenous dynamics. Therefore every "
                   "scenario result is reported as a seed-paired difference against the "
                   "injection-free control run, and no organic-propagation claim may be made "
                   "without the is_organic() filter.")
```

---

## 10. Configuration keys

```yaml
scenario:
  file: null                        # set by `polis run --scenario`
  with_control: false               # or `paired_control: true` inside the scenario
  researcher_keyfile: "${POLIS_RESEARCHER_KEYFILE}"
  require_signature: true           # a run with an unsigned scenario is refused
  max_injections_hard: 1000         # process-level ceiling above any scenario's own guard
  dry_run_max_ticks: 100000
  scenario_writable_parameters:     # in addition to POLICY_REGISTRY; explicit, closed
    - money.policy_rate
    - welfare.unemployment_benefit_cents
    - tax.income_rate_bp
    - bank.reserve_requirement_bp
```

`scenario_writable_parameters` is deliberately a short, explicit list. Anything not in it and not in `POLICY_REGISTRY` is a load error, and widening the list is a reviewed change — it is the boundary between "a shock a government could deliver" and "a shock only a researcher could deliver".

---

## 11. Acceptance criteria

1. `load()` rejects, each with the offending `step_id` and a one-line reason: `dsl_version != 1`; duplicate trigger or step id; an unresolvable `step.trigger`; `respect_invariants: false`; an unknown metric; a `set_parameter` naming a parameter outside `POLICY_REGISTRY ∪ scenario_writable_parameters`; a value outside the admissible range; a `set_parameter` naming a `mechanisms:` key; an `inject_event` kind off the allowlist; an unparseable `where`; an uncheckable `publish_falsehood`; endowments exceeding `funding.cap_cents`.
2. `scenario_hash` is stable across processes and changes on any semantic edit to the YAML.
3. `at_sim_time` is resolved to a tick **at load** under the run's clock profile, and the resolved tick appears in 99000's payload.
4. **All steps apply at PHASE 0.** An `on_event_kind` trigger firing at PHASE 6 of tick T applies at PHASE 0 of T+1; an `on_metric_threshold` evaluated at PHASE 9 of T likewise.
5. `sustained_for`, `cooldown_ticks` and `max_fires` are honoured; a threshold that dips below and returns restarts the sustain window.
6. `schedule` fires through C04's `Scheduler`, and `jitter_ticks` draws from `rng.get("research.scenario.jitter", trigger_id, tick)`.
7. Selector evaluation returns a **sorted** id tuple; `sample: N` draws through `rng.get("research.scenario.select", step_id, tick)`; the same seed selects the same targets twice.
8. `eval` and `exec` appear nowhere in `polis/research/scenario/` (AST test).
9. `set_parameter` calls `RuntimeConfig.enact` and nothing else; a subsequent `runtime.get(parameter, tick)` returns the new value and `get(parameter, tick-1)` the old.
10. `@scale` / `@delta` are expanded at fire time and **recorded expanded** in the 99010 payload; a replay re-resolves nothing.
11. `ramp_ticks` produces one signed 99010 per step of the ramp; `revert_after` produces a 99011 at the right tick.
12. Every injection-class step emits a signed event whose `Event.sig` verifies against the run's declared researcher pubkey, and writes exactly one `scenario_injections` row with `step_id`, `event_seq`, `scenario_hash`.
13. `ScenarioRepository.orphans()` is empty for a clean run; a manually deleted row makes `polis verify` fail.
14. A step that would break INV-MONEY is **rolled back inside the transaction**, emits `99004{reason: 'invariant_guard'}`, and the scenario aborts under the default `on_guard_violation`.
15. `kill_entity` runs the normal settlement path: employment terminated, resting orders cancelled and reserves released, debts settled against the estate, residual distributed — and INV-MONEY holds across the tick.
16. `spawn_entity` endowments move through balanced ledger legs from `funding.account`; exceeding `cap_cents` emits `99004{reason: 'cap_exceeded'}` and spawns nothing.
17. `force_action` produces an `Action` with `origin="scripted"` that passes through PHASE 4; an illegal forced action is rejected and 99030 carries `accepted: false` with the reject reason.
18. `publish_falsehood` with `text: null` produces a post composed by the carrier's own `POST_WRITE` call, and `posts.truthfulness` is computed against the log rather than asserted.
19. `seed_rumour` produces one `10060` per seeded agent with `source='injected'`, written through C17's path, bounded by `max_seeded_agents`.
20. `is_organic()` returns `False` for any event with a 99xxx ancestor along `cause_seq` and `True` otherwise, and is the only implementation of that filter in the repository.
21. `paired_control: true` launches a seed-matched run with `steps: []`, tagged `paired_control`, with `parent_run_id` set.
22. `expects:` entries are recorded in the run manifest and evaluated by `polis gate` against the paired difference; a failed expectation is **reported, not fatal**.
23. `polis scenario lint` passes all three shipped fixtures and fails a mutated copy of each in the intended way.
24. `polis scenario dry-run --against <run_id>` reports fire ticks and target sets and writes nothing (asserted by a row-count diff before and after).
25. `polis scenario sign` produces a `.sig` whose signatures verify, and `require_signature: true` refuses to run an unsigned scenario.
26. This chunk declares only its half of 99xxx; a test asserts no overlap with C24's `{99050, 99060, 99070, 99090, 99091}` and that every declared kind is in `KIND_REGISTRY` with a payload schema.
27. Determinism: a 500-tick run with `sc_recession_v1` at a fixed seed produces an identical hash chain twice, including every injection signature.
28. `@mechanism("research.scenario_injection", ...)` is declared and appears in `runs.mechanism_manifest` whenever a scenario is loaded.
29. `mypy --strict polis/research/scenario` and `import-linter` pass.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/scenario/test_loader_rejections.py` | Each of the twelve load-time rejections in criterion 1, by `step_id` and reason |
| `tests/unit/scenario/test_scenario_hash.py` | Stability across processes; sensitivity to a semantic edit; insensitivity to comment/whitespace changes only if the canonical form says so |
| `tests/unit/scenario/test_triggers_timing.py` | `at_sim_time` resolved at load; `on_event_kind` at P6 → applies at P0 of T+1; `on_metric_threshold` at P9 → P0 of T+1; `schedule` through `Scheduler`; sustain, cooldown, `max_fires` |
| `tests/unit/scenario/test_predicates.py` | Grammar coverage; sorted output; `sample` reproducible under a fixed seed; a malformed predicate raises at load |
| `tests/unit/scenario/test_no_eval.py` | AST scan: no `eval`, no `exec`, no `__import__` in the package |
| `tests/unit/scenario/test_resolvers.py` | `@scale`/`@delta` against the value in force; `@largest_borrower`; `@carriers`; `$event.<path>`; an unknown expression raises |
| `tests/unit/scenario/test_signing.py` | Digest layout; signature verifies; a mutated `params`, `step_id`, `tick` or `scenario_hash` invalidates it |
| `tests/unit/scenario/test_kind_split.py` | Declares only its half of 99xxx; no overlap with C24's five; every kind has a payload schema |
| `tests/integration/test_set_parameter.py` | Writes through `RuntimeConfig.enact`; `get(p, t)` vs `get(p, t-1)`; ramp emits one signed 99010 per step; `revert_after` emits 99011; a `mechanisms:` key is refused |
| `tests/integration/test_invariant_guard.py` | A money-creating step rolls back inside the transaction, emits `99004{invariant_guard}`, aborts under the default policy and skips under `skip` |
| `tests/integration/test_kill_entity_settlement.py` | Normal settlement path invoked; orders cancelled and reserves released; debts settled; **INV-MONEY holds across the tick** |
| `tests/integration/test_spawn_funding.py` | Balanced legs from `funding.account`; `cap_cents` enforced; over-cap spawns nothing and emits `99004{cap_exceeded}` |
| `tests/integration/test_force_action.py` | `origin="scripted"`; passes PHASE 4 like any other action; an illegal forced action is rejected and 99030 records `accepted: false` |
| `tests/integration/test_publish_falsehood.py` | Ground truth computed from the log at load; `truthfulness` exact; `text: null` routes to the carrier's own composition |
| `tests/integration/test_seed_rumour.py` | One `10060` per agent with `source='injected'`; written through C17's path; bounded by `max_seeded_agents` |
| `tests/integration/test_injection_recording.py` | One `scenario_injections` row per injection-class step; `orphans()` empty; a deleted row fails `polis verify` |
| `tests/integration/test_organic_filter.py` | `is_organic()` on injected vs organic chains; a planted 99xxx ancestor at depth 4 is still detected |
| `tests/integration/test_paired_control.py` | Seed-matched twin with `steps: []`, tagged and parented; the seed-paired difference is computable end to end |
| `tests/integration/test_scenario_fixtures.py` | All three fixtures lint, sign, verify, and run 200 ticks against `StubProvider` with triggers firing and invariants holding |
| `tests/integration/test_dry_run_writes_nothing.py` | Row counts across every table identical before and after `polis scenario dry-run` |
| `tests/determinism/test_scenario_determinism.py` | 500 ticks with `sc_recession_v1`, same seed → identical hash chain, identical signatures, identical selector draws |

---

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis scenario lint | sign | dry-run` and `polis run --scenario … [--with-control]` implemented and documented in `--help`.
2. Three fixtures in `configs/scenarios/` (`sc_recession_v1.yaml`, `sc_bank_failure_v1.yaml`, `sc_coord_misinfo_v1.yaml`), each with a `.sig`, each linted and smoke-tested in CI.
3. Alembic revision adding `step_id`, `event_seq`, `scenario_hash` to `scenario_injections` (if C24's amendment migration has not already landed it — coordinate; do not write it twice).
4. Every owned kind registered with a payload schema; the 99xxx range split agreed with C24 and asserted by a test in each chunk.
5. `@mechanism("research.scenario_injection", ...)` declared.
6. `is_organic()` exported as **the** shared filter, and C24's notebooks 08 and 12 import it rather than reimplementing it.
7. Handback records: (a) the closed predicate grammar as implemented, with anything a fixture needed that the grammar could not express; (b) the resolver table as implemented; (c) the `scenario_writable_parameters` list actually required by the three fixtures, and any parameter that had to be added; (d) any `10 §4.3` step semantics that could not be implemented as written, **flagged, not reinterpreted**.

---

## 14. Traps

1. **A scenario that assumes its own conclusion.** The defining failure of this chunk. Firing people to produce a recession, setting `bank.status='failed'` to produce contagion, or writing beliefs directly to produce polarisation each yields a result that is a restatement of the injection. Deliver prices and defaults; let the model answer.
2. **Killing the bank instead of a borrower.** The contagion result becomes a tautology and it will not be obvious in the figure. Kill through the normal path and let the balance sheet do the work.
3. **A raw delete in `kill_entity`.** Every counterparty takes an unexplained loss, INV-MONEY breaks on the next tick, and the run halts with a message that points at the ledger rather than at the scenario.
4. **Money from nothing in `spawn_entity`.** Twenty carriers with 200,000 cents each is 4,000,000 cents that must come from a funded account through balanced legs. Skip that and V2 fails for the whole run, which voids every other result in it.
5. **Applying a step mid-tick.** "It only needs to fire in PHASE 5" breaks the phase contract, makes the run unreplayable, and the symptom appears later as a `DIVERGED at seq N` in `polis replay` that nobody attributes to the scenario.
6. **Re-resolving `@scale` at replay.** The value in force at fire time in the replay may differ by a cent from the original, the enactment differs, and the chain diverges. Expand once and record expanded.
7. **`set_parameter` on a `mechanisms:` key.** It invalidates `runs.mechanism_manifest`, silently breaks step 12 of the MECHANISM checklist, and makes every ablation contrast in the run incomparable.
8. **Bypassing `RuntimeConfig`.** Writing the parameter into `Settings` directly means the policy engine and the scenario use two different overlays, `get(p, t)` returns different answers depending on who asked, and the election that changes the same parameter later does nothing visible.
9. **An unseeded selector draw.** `random.sample` over a set looks deterministic in one process and is not across two. Sorted tuple, seeded RNG, namespaced by `step_id` and `tick`.
10. **Iterating a set to build target ids.** The most common source of "the scenario replays differently on the CI machine". Sort before you sample and sort before you emit.
11. **`eval()` in the predicate evaluator.** It is four lines and it turns a YAML file into arbitrary code execution against the engine's process, with the researcher's private key in memory.
12. **Skipping instead of aborting by default.** A scenario that silently did not happen is worse than a run that failed: the run looks complete, the control comparison is meaningless, and nothing in the output says so. Default `abort`.
13. **Forgetting to sign a new step type.** A future step lands in `INJECTION_CLASS` conceptually but not in the frozenset, `polis verify` passes, and an unsigned shock exists in a "verified" run. Drive the signing decision off `INJECTION_CLASS` membership, and test that every injection-class kind produced by any step carries a `sig`.
14. **An injection with no `scenario_injections` row.** Verification fails and localising it costs an afternoon. Emit the event and write the row in the same transaction, never in two places.
15. **A per-notebook "organic" filter (R9).** `misinfo.organic_share` near zero while the text claims organic propagation is the exact shape of this failure. One `is_organic()`, imported everywhere.
16. **Reporting before/after within one run.** A shock at Y3-M01 followed by a rise in unemployment confounds the shock with everything else the run was doing — including the school term, the payroll cadence, and the election. Use the seed-paired control; that is what `paired_control` is for.
17. **Treating `expects:` as a gate on the run.** A shock that fails to move the metric is a finding. Making it fatal converts a null result into a deleted run, which is the worst available outcome and leaves no trace.
18. **An uncheckable falsehood.** A claim with no log-derivable truth value makes `truthfulness` a label rather than a measurement, and B2 becomes unanswerable in exactly the way it is unanswerable in the field.
19. **Composing the false post yourself.** `text: null` exists so the carrier's own `POST_WRITE` call writes it. A researcher-authored string is a fixed stimulus, which is a legitimate design but a *different* one and must be declared, because it removes the model's language from the treatment.
20. **Letting `scenario_writable_parameters` grow.** Every addition widens the gap between "a shock a government could deliver" and "a shock only a researcher could deliver", and the second kind is much harder to defend in a paper. Keep the list short and make widening it a review.
21. **Forgetting the ramp is many enactments.** One enactment with an interpolating getter means `runtime.get(p, t)` is not a pure lookup, the value is not in the log, and the Observatory's shaded band has nothing to draw. Emit one signed 99010 per ramp step.
