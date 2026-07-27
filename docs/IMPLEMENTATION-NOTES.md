# POLIS implementation notes

This file records implementation findings that amend or narrow a binding specification.
No finding here changes scientific behaviour silently.

## M0 / C03

### Nested event partition primary key

PostgreSQL 17 rejects the documented combination of:

- `events PARTITION BY LIST (run_id)`;
- each run partition further `PARTITION BY RANGE (tick)`; and
- primary key `(run_id, seq)`.

A unique constraint on a subpartitioned table must contain every partition key, including
`tick`. The M0/M1 migration therefore uses primary key `(run_id, tick, seq)` and a separate
non-unique lookup index on `(run_id, seq)`. Global sequence uniqueness within a run remains
enforced by the single engine writer and verified by the hash-chain verifier.

### Milestone-scoped schema

C03's full migration plan names M2–M6 economy and society tables. The initial implemented
schema contains only M0/M1 core, agent, world, and research tables. Later tables will be
introduced and exercised by their owning milestones rather than remaining unvalidated,
unused schema.

## M1 / C23a

### Inspector trace projection

`03-DATA-MODEL.md` defines the authoritative event, LLM-call, agent, memory and world
tables but does not name a materialized inspector table. M1 adds `cognition_traces` as a
strictly derived read model so C23a can answer the end-to-end inspector route without
reconstructing every phase on each request. It is not simulation truth and may be deleted
and rebuilt from deterministic replay.

### Bounded reflection backlog

C09 says reflection force-routes may exceed the cognition call budget. In the M1 one-call
reflection implementation, synchronized threshold crossings caused hundreds of agents to
enter REFLECT together and suppressed the intended 7% DELIBERATE lane for long intervals.
M1 deterministically queues due reflections by accumulated importance and agent id, using
only the call reserve above the deliberate target. The importance/life-event trigger and
cooldown are unchanged; overflow remains armed for the next tick.

### Event stakes are not chronic need pressure

The first M1 calibration incorrectly used `1 - min(needs)` as the salience `stakes`
component. This contradicted `04-AGENT-SPEC.md`, where stakes are deltas from wealth,
health, employment, relationship or legal events. Once a non-restored M1 need reached
zero, every routine tick became permanently high-stakes, producing roughly one memory
per agent per tick and a projected memory-cap collision near tick 2,951.

M1 observations now carry an explicit event-stakes value of zero. Later milestones own
the event-delta calculations for their institutions. Unmet needs continue to drive reflex
choice and wellbeing, but they no longer masquerade as event stakes. This intentionally
changes the frozen M1 golden hash and is covered by a regression test.

## M2 / C11

### Integer economy projections

`03-DATA-MODEL.md` prints firm productivity and application match scores as `NUMERIC`.
`06-ECONOMY-SPEC.md §0.1` supersedes those columns with integer basis-point values. The
M2 migrations therefore use `productivity_bp` and `match_score_bp`; no binary float or
decimal value can reach money, ranking, or replay-critical economy state.

Firm `capital_cents` is the non-ledger real productive asset required by Rule L1.
`liquid_cents` is the denormalized ledger net worth used by M-6. The earlier M2 foundation
temporarily used `capital_cents` for both concepts; C11 separates them explicitly.

### Genesis deposit and central-bank settlement

The direct genesis-deposit shorthand in `06-ECONOMY-SPEC.md §13.2` conflicts with the
per-bank deposit identity M-5. Genesis uses the coherent four-part issuance pattern:
recipient deposit, matching bank deposit liability, matching commercial-bank reserves,
and central-bank issuance. No balancing entry is fabricated outside the ledger.

The central bank owns a reserve settlement account and matching deposit-liability account
so taxes can settle into `dep:gv_treasury@bk_cb` through the same six-leg cross-bank
transfer used elsewhere. A central bank's reserve claim on itself is excluded from M0;
the treasury deposit is included. Counting both would double-count every tax receipt.

### Scripted baseline placement

The implemented M1 action path predates C10's institution-resolver registry. C11 therefore
constructs typed `Action` objects with `origin="scripted"` inside the PHASE 7
`MechanicalPolicy` fallback and resolves them in the documented labour type order. Native
M1 actions still pass through the existing PHASE 4/5 path, and the frozen M1 action schema
and golden run remain unchanged. A later integration chunk must route deliberate economic
actions through the full C10 registry before any result is attributed to LLM economic
choice.

### Goods-kind renumbering

C11 keeps firm kinds in 6000–6099. Per the ratified chunk contract, every C12 goods kind
moves to its documented value plus 100 (6100–6199); the illustrative 6020 row in
`02-ARCHITECTURE.md` is stale and must not be registered.

## M2 / C12

### CPI base includes contemporaneous policy

The fixed basket stores consumer prices inclusive of the sales-tax and health-subsidy
policy active at tick 0. Transaction prices use the same construction. Using untaxed
posted prices as the base and tax-inclusive transactions thereafter would manufacture an
inflation jump at tick 1 even when firms had not changed a price.

Genesis inventory and base prices use the same Cobb-Douglas production function as the
daily firm engine, evaluated at target headcount and the declared seed effective-labour
assumption. This prevents the base basket from pricing output as if labour were free or
linear while live production uses diminishing returns. Per-SKU yields remain explicit
calibration parameters; the food and transport yields are sized against their configured
annual subsistence quantities.

Mechanical necessity purchases are staggered deterministically per `(agent, sku)` from
`gamma_units_per_year`, with at most three purchase actions per agent per sim-day. This
preserves the annual consumption floor without a permanent agent-id priority or a hidden
one-unit-per-tick demand multiplier.

The basket contains every non-capital SKU produced by the genesis firms. Small smoke runs
may contain fewer than the nominal 12-SKU warning floor because they intentionally seed
only three firms; the 1,000-agent calibration seeds all sectors and is the binding basket
coverage gate. A basket is fixed once and never expanded when a later firm enters.

### Household boundary

M2 has agents, firms and individual deposit accounts but intentionally has no household
entity; households, family formation and shared budgets belong to M5. C12's linear
expenditure plan and consumption loop therefore operate per agent in M2. Rent payment and
household pooling remain visibly unavailable until the M5 household projection exists,
rather than fabricating one-person households and later changing the unit of analysis.

## M2 / C14

### Bootstrap issuance boundary

C14's static acceptance text says the literal `iss:` should appear only in the ledger and
central-bank modules. Genesis must nevertheless open and fund the central-bank issuance
account before the central-bank engine exists. `polis.economy.genesis` is therefore the
documented bootstrap exception; after tick zero, only `polis.economy.central` can issue base
money. Every issuance remains an event-backed balanced ledger transaction.

### Optional underwriting ablation

Scorecard underwriting remains the M2 default. When `banking.underwriting: llm` is selected,
the economy uses the existing cached `CREDIT_EVAL` route with the complete scorecard and
the same borrower and bank state as reference. The model can change the approval, amount,
and rate view but cannot bypass capital, reserve, amount, or concentration constraints.
The decision event records the stable `llm_call_id`; the network-blocked StubProvider gate
exercises this route end to end.

### Bank resolution and tax arrears

Both configured bank-resolution modes are implemented. `assume` moves performing loan
receivables without changing borrower payables. `liquidate` sells performing loans at the
configured fire-sale rate, pays remaining deposits into cash after insurance and haircut,
and records base-money issuance if the central bank must buy a loan. Non-performing loans
are written off first in both modes. Banks are not employers in the M2 entity model, so
there are no bank employees to dismiss; adding a synthetic employer solely for the failure
test would conflict with the existing firm and employment projections.

Tax assessments remain cash-basis. Arrears become `txr`/`lnp` claims and use the ordinary
interest, missed-payment, delinquency, default, and close logic. Repayment transfers real
deposits to the treasury and closes principal exactly rather than leaving government claims
outside the amortisation engine.

### Treasury auction bridge

C14 decides when bonds are required, while C13 owns the exchange auction venue in M3.
For M2, deficit financing uses a deterministic primary allocation among eligible banks and
records issue and clear/fail events. The `securities.issuer_firm_id` column stores
`gv_treasury` because the binding shared table has no generic issuer column. C13 will replace
only the venue and price-discovery portion; the treasury decision, coupons, maturities, and
ledger settlement remain C14-owned.

## M3 / bounded live-provider calibration

### MiniMax M3 pilot does not silently replace the routing specification

`02-ARCHITECTURE.md` and `09-MODEL-ROUTING.md` bind the baseline research design to
MiniMax M2.7. The vendor now exposes `MiniMax-M3`, but a model-family update changes the
research instrument. The new M3 configuration is therefore a named, bounded pilot rather
than an edit to `configs/baseline.yaml`. Promotion requires a cached offline replay and a
comparison report against the accepted baseline.

At the 7% deliberate target, 1,000 agents imply roughly 70 deliberate calls per tick. A
five-year chronicle seed is about 126,000 deliberate calls before reflections and other
ancillary purposes. A 10,000-call/five-hour subscription window therefore cannot contain
one full seed. `configs/live-minimax-m3-pilot.yaml` runs 80 ticks with an 8,000-call
run-level hard stop and a persistent 10,000-call/18,000-second provider quota.

### Coding CLIs are bounded provider probes, not simulation workers

The Codex and Grok CLI adapters follow the same stdin/structured-output/event-parsing
shape used by Paperclip's local Codex adapter. Each call runs in an empty temporary
directory, captures bounded output, uses a hard timeout, and enters the normal POLIS
completion cache. Replay mode constructs no CLI process.

Grok runs with tools, web search, MCPs, plugins, cross-session memory, subagents, and
Claude/Cursor compatibility disabled in a temporary clean profile. Codex runs ephemeral,
outside the repository, with user config and rules ignored and a read-only sandbox.
Codex still has an agentic read-only tool surface, so its lane requires the explicit
`extra.allow_readonly_agent: true` acknowledgement. Both committed CLI configurations are
one-call smokes only; neither is eligible for the 1,000-agent cognition lane.

Provider-window reservations are stored in SQLite before process or network invocation.
They survive process restarts and coordinate concurrent lanes sharing a quota scope.
Run-level wire attempts use a second persistent scope keyed by run UUID. Every retry reserves
against both ceilings before network invocation, so failures and cached process restarts
cannot reset or evade the hard cap. Transient provider failures trigger bounded cached
resume; a quota response whose wait exceeds the retry ceiling remains terminal.
