# POLIS implementation notes

This file records implementation findings that amend or narrow a binding specification.
No finding here changes scientific behaviour silently.

## C22 external-agent gateway handback

- `polis_remember` and recall-touch updates cross the bounded Redis handoff because the
  gateway has no write authority. Remember therefore returns `pending: true` with a null
  memory id. The final id and any eviction are authoritative in event 20060 and may appear
  in a later observation. Protocol v1 has no dedicated `memory.receipt` frame. This is the
  explicit amendment recorded in protocol section 4.6.
- The engine is the only producer of observation JSON. It serialises the PHASE 1
  `Observation.as_dict()` with canonical bytes and the gateway serves that blob unchanged.
  New observation fields must be coordinated at that producer; no gateway projection may
  rebuild or enrich it.
- The engine-facing Redis adapter lives in `polis/cli/wiring/external.py`. It is outside
  `polis/gateway/` so the isolated HTTP process cannot import agent, kernel, or writable
  store internals.
- `sdk/pyproject.toml` builds the same `polis.gateway.sdk` source tree as the standalone
  `polis-agent-sdk` wheel. The wheel contains only the SDK namespace and its client
  dependencies; it does not package the engine, agent internals, or store.
- The native deliberate response and signed transport envelope remain distinct layers.
  Both use the generated `actions.v1.json` parameter bundle; transport-only run, tick,
  nonce, action id, session, and signature fields are not native cognition fields.
- Verification latency is exposed by gateway-local metrics. The 50-citizen by 40-request
  p50/p95 benchmark must be recorded from the deployment host before a C1 report; a local
  workstation number is not treated as a portable performance claim.

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

## M3 / non-fixture mechanical calibration

The Stage 0 configuration is an explicit reflex-only calibration instrument, not research
evidence. It uses the existing 1,000-agent economy, a declared founder-owned genesis
listing, and a default-disabled zero-intelligence trader with seeded reservation prices.
Every order is limited by the actor's cash or available holdings. There is no market maker,
guaranteed fill, wash trade, transfer, or post-genesis subsidy.

Stage 0 freezes five seeds over two simulated years and requires V2 and V3 on every run.
One-seed or shorter invocations remain tuning diagnostics and cannot mark themselves
accepted. The aggregate records the base and per-run configuration hashes, code SHA,
terminal hashes, active mechanism manifest, gate roster, seed roster, and duration.

The first one-year probe at `labour.min_match_score_bp = 5_500` passed V2 and four V3
sub-checks but collapsed toward full employment. The specification's declared labour
calibration lever was raised to `6_500`; seed `2026072701` then passed V2 and every V3
sub-check, ending at 259 bp unemployment with only three startup-day failures.

The frozen five-seed/two-year run subsequently passed V2 and V3 for every seed. An initial
formal attempt found `INV-INTEREST` at tick 421 because capitalized interest was not
classified consistently across repayment and write-off. The corrected lifecycle treats
only unresolved capitalized interest as payable or forgivable; all five reruns completed
720 ticks without invariant violations.

Calibration retains only the event kinds needed to recompute V2/V3, while the event log
still validates, sequences, and hash-chains every event. This avoids treating RAM retention
as evidence and keeps the formal gate viable at 1,000 agents.

For live cognition, the action response schema is generated from the actions legal in the
current observation and carries each action's typed parameter contract. Listed securities,
cash, available holdings, and open orders are exposed through a read-only, pre-indexed
economic observation adapter. Perception does not create holdings or otherwise mutate
simulation state.

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

### Pilot outcome

The 80-tick/1,000-agent MiniMax M3 pilot passed all nine declared gates. It completed
5,600 logical calls with 30 schema-repair attempts and 6,072 actual provider wire
attempts, staying below the 8,000-attempt run ceiling. All 5,600 responses were
schema-valid; the run produced five action types and two null actions (0.035714%).
Configured token pricing yielded USD 2.98517640, below the USD 25 cost ceiling.

The offline replay opened no provider lane and reproduced terminal chain hash
`b4fecd7855530a19f96d70618ffda3545b9a65d1e44099362e41566d79b371c4` exactly.
This validates the bounded calibration harness; it does not promote the pilot to the full
five-seed/five-year M3 research gate.

## M5 / C20 demography handback

### C15/C20 ownership and the death-settlement port

C20 owns the mortality trigger, intestacy weights, dependant reassignment, relationship and
memory effects, and the final widened `AGENT_DIED` event. The concrete C15 bridge owns order
cancellation, liquidation, the debt and tax waterfall, distributions, write-offs, and every
9xxx event and ledger leg used by cases A-D. C20 calls that bridge exactly once through:

`settle_death(agent_id, tick, *, heirs: Sequence[tuple[str, int]] | None, ctx: Any)
-> Sequence[Event]`

The same port exposes `case_for`, `estate_account_id`, `gross_cents`, `open_order_count`, and
`open_loan_count` so orchestration can report and verify the canonical estate without
duplicating C15 logic. `tests/invariants/test_death_settlement.py` is the settlement gate,
including open buy and sell orders, a loan larger than the estate, escheat, zero residual
escrow, and exact money, ledger, order, and share invariants.

The five coordination decisions are fixed as follows:

1. C15 emits the 9xxx waterfall events and ledger legs; C20 emits 2006-2009, 2051,
   household/tie/memory effects, and the widened 2002.
2. Kind 2002 is widened in place with estate, debt, write-off, tax, heir, escheat,
   transaction, and case details.
3. The old `AgentPopulation.mark_dead` path raises at M5; all deaths use the atomic
   settlement.
4. Case A leaves the decedent's accounts open and defers closing while bankruptcy is open.
5. Inheritance tax uses `tax.inheritance_bp`, read through `RuntimeOverlay.bp` as integer
   basis points; no float rate or second key is accepted.

The source brief's nominal 2003-2059 allocation overlaps existing C07 and C21 kinds. The
implemented conflict-free ranges are 2005-2009, 2051, and demography-owned subranges
15001-15004, 15010-15013, 15020-15023, 15030, and 15040-15041.

### Households, fiscal child costs, and replay

PHASE 8 runs dissolution, conception, gestation, birth, child costs, migration in,
migration out, mortality, and settlement in stable order. Courtship and union formation
remain bilateral agent actions. Child costs use balanced purchase legs to a real firm:
the household pays the amount net of benefit, the treasury funds the benefit, and a
headless shelter household is fully state-funded. Government spending is recorded and any
same-tick overdraft is financed before the invariant pass.

Migration, household state, parent IDs, generation, home, and death fields are projected by
migration 0018 and reproduced by `polis rebuild`. Birth belief inheritance is a local
O(number of inherited propositions) calculation and makes no provider call; the 0.0 and
1.0 heritability endpoints are covered directly, so the B6 sweep adds simulation runs but
no LLM spend.

### Three-year calibration and F10

The reproducible command
`python scripts/validate_c20_calibration.py --agents 300 --years 3 --progress-ticks 100`
completed all 1,080 chronicle ticks in 2,687.827 seconds with no halt. The corrected
lifecycle accounting separates the 300 tick-zero genesis records from births: it recorded
0 lifecycle births, 180 arrivals, 72 departures, and 362 living residents at the end.
Yearly lifecycle-birth/arrival/departure counts were `0/55/17`, `0/60/27`, and
`0/60/28`.

The reflex-only calibration's mean deliberate share was exactly 0.0 in every year, so the
births-versus-deliberate-share correlation is undefined rather than zero. The absence of
lifecycle births across all three years is an F10 warning and cannot be presented as evidence
that budget selection is absent. A non-reflex multi-budget sweep is still required before
demographic findings use this mechanism. The calibration also exposed and fixed two
long-horizon lifecycle gaps: death now settles funded accrued wages before estate closure
and records unfunded terminal claims as `PAYROLL_SHORTFALL`; ordinary payroll
deterministically opens a deposit at an active bank for a living worker whose prior bank
account was resolved.
