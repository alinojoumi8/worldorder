# POLIS — Implementation Chunks

**Version:** 1.0
**Audience:** AI coding agents and humans implementing POLIS.

A **chunk** is a self-contained unit of work. One agent picks up one chunk, reads the
required documents, implements it, writes its tests, and hands back. Chunks compose because
their interfaces are specified up front, not discovered during implementation.

---

## 0. Before you touch any chunk

Read, in full:

1. `../docs/02-ARCHITECTURE.md` — determinism rules, tick phases, module layout, event kinds. **Binding.**
2. `../docs/03-DATA-MODEL.md` — the schema. **Binding.**
3. Your chunk's "Required reading" section.

Then read your chunk brief. If the brief conflicts with `02` or `03`, `02` and `03` win —
stop and flag it rather than guessing.

### Ground rules that apply to every chunk

| Rule | Detail |
|---|---|
| **Determinism** | No `random`, no `datetime.now()`, no unsorted iteration over mutable state. All randomness via `rng.get(namespace, entity_id, tick)`. See `02 §4`. |
| **Money** | `BIGINT` cents. Never float. All movement through `ledger.post_transaction()`. |
| **Typed** | Full type annotations. `mypy --strict` passes. |
| **Tested** | Every chunk ships its own tests. A chunk with no tests is not done. |
| **StubProvider** | Every test that would otherwise hit an LLM uses `StubProvider` (C05). No test makes a network call. |
| **No new event kinds outside your range** | Kinds are declared in `polis/events/kinds.py` only, inside the range your chunk owns. |
| **No new `ActionType` without a spec amendment** | The enum is closed. If you need one, stop and raise it. |
| **Institutions never import agent cognition** | `polis.economy` and `polis.society` consume `Action`, emit `Event`. Enforced by `import-linter`. |
| **MECHANISM tagging** | Any hard-coded behavioural rule gets `@mechanism(id, entails="...")`. See `02 §8.1`. |

---

## 1. Brief format

Every chunk brief uses this structure. If you are generating a new chunk, match it exactly.

```
# Cxx — Title
Milestone · Owner module · Depends on · Blocks · Estimated size

1.  Context                 — why this exists, in 3-5 sentences
2.  Required reading        — docs and sections, plus chunks whose interfaces you consume
3.  Scope — in              — what you build
4.  Scope — out             — what you explicitly do NOT build (and which chunk does)
5.  Interfaces you provide  — exact signatures other chunks will import
6.  Interfaces you consume  — what already exists
7.  Data model touched      — tables read and written
8.  Event kinds owned       — number, name, payload
9.  Implementation notes    — algorithms, ordering, gotchas
10. Configuration keys      — what you add to the config schema
11. Acceptance criteria     — a testable checklist
12. Tests to write          — named test files and what each asserts
13. Definition of done      — the handback contract
14. Traps                   — the specific ways this chunk goes wrong
```

---

## 2. Milestones

| Milestone | Name | Chunks | Ends when |
|---|---|---|---|
| **M0** | Kernel | C01 C02 C03 C04 C05 | Engine ticks an empty world, writes a verifiable hash-chained log, routes an LLM call through the cache |
| **M1** | Living City | C06 C07 C08 C09 C10 C21 C23a C24a | 1,000 agents move, learn, talk, remember, reflect. Dashboard inspects one agent end-to-end. |
| **M2** | Economy | C11 C12 C14 C24b | Labour clears, firms produce, banks lend, government taxes. **INV-MONEY holds for 5 sim-years.** |
| **M3** | Capital | C13 C15 | Order book runs, firms IPO, VCs fund, companies fail |
| **M4** | Polity | C16 C17 C18 C19 | Social media with swappable feeds, news, elections that change policy, courts |
| **M5** | Generations | C20 | Partnering, birth, inheritance of wealth and beliefs, death settlement |
| **M6** | Open World | C22 C23b C25 | External agents join via MCP, scenario DSL injects shocks |

**M2 is the highest-risk milestone.** Accounting closure (`INV-MONEY`, validity gate V2) is
where simulations of this kind quietly break. Do not start M3 until V2 holds for five
consecutive sim-years.

---

## 3. Dependency graph

```
                                  C01 scaffold
                                       │
              ┌────────────────┬───────┴────────┬────────────────┐
              ▼                ▼                ▼                ▼
           C02 events      C03 store        C05 llm         (config/CLI)
              │                │                │
              └────────┬───────┴────────────────┘
                       ▼
                   C04 kernel  ─────────────── M0 complete
                       │
       ┌───────────────┼───────────────┬──────────────┐
       ▼               ▼               ▼              ▼
   C06 world      C07 agent core   C10 actions    C24a metrics
       │               │               │
       │               ├──► C08 memory │
       │               │       │       │
       │               │       ▼       │
       │               └──► C09 cognition ◄──┘
       │                       │
       └───────► C21 education ┤
                               │
                          C23a observatory(min) ─── M1 complete
                               │
       ┌───────────────┬───────┴───────┐
       ▼               ▼               ▼
   C11 labour      C12 goods       C14 banking
   + firms         + CPI           + credit
       └───────────────┴───────────────┘
                       │
                  C24b metrics(full) ────────── M2 complete
                       │
              ┌────────┴────────┐
              ▼                 ▼
         C13 exchange      C15 ventures ───────── M3 complete
              └────────┬────────┘
                       ▼
                  C16 social
                       │
                       ▼
                   C17 news
                       │
                       ▼
                  C18 polity
                       │
                       ▼
                   C19 law ────────────────────── M4 complete
                       │
                       ▼
                  C20 demography ──────────────── M5 complete
                       │
              ┌────────┼────────┐
              ▼        ▼        ▼
         C22 gateway C23b obs C25 scenario ────── M6 complete
```

**Parallelisable sets** (safe to work simultaneously once their deps are done):

- `{C02, C03, C05}` after C01
- `{C06, C07, C10, C24a}` after C04
- `{C11, C12, C14}` after M1
- `{C13, C15}` after M2
- `{C16}` after M3
- `{C17}` after C16
- `{C18}` after C17
- `{C19}` after C18
- `{C22, C23b, C25}` after M5

---

## 4. Chunk index

| ID | Title | Milestone | Module | Size |
|---|---|---|---|---|
| [C01](C01-scaffold.md) | Repo scaffold, config system, CLI | M0 | `polis/config`, `polis/cli` | S |
| [C02](C02-event-log.md) | Event log, kind registry, hash chain | M0 | `polis/events` | M |
| [C03](C03-store.md) | Postgres schema, migrations, repositories, partitioning | M0 | `polis/store` | L |
| [C04](C04-kernel.md) | Clock, tick loop, RNG registry, scheduler, invariants, checkpoints | M0 | `polis/kernel` | L |
| [C05](C05-llm.md) | LLM router, providers, completion cache, budget, structured output | M0 | `polis/llm` | L |
| [C06](C06-world.md) | Grid generation, places, pathfinding, movement, housing | M1 | `polis/world` | L |
| [C07](C07-agent-core.md) | Agent state, traits, needs, skills, reflex policy | M1 | `polis/agents` | M |
| [C08](C08-memory.md) | Memory stream, retrieval, reflection, embeddings | M1 | `polis/agents/memory` | L |
| [C09](C09-cognition.md) | Salience scoring, routing, deliberate, reflect | M1 | `polis/agents/cognition` | L |
| [C10](C10-actions.md) | Action schema, validators, budget, resolution dispatch | M1 | `polis/agents/actions` | M |
| [C11](C11-labour-firms.md) | Labour market, firms, production | M2 | `polis/economy` | L |
| [C12](C12-goods.md) | Goods market, consumption, CPI | M2 | `polis/economy/goods.py` | M |
| [C13](C13-exchange.md) | Limit order book, matching, market data | M3 | `polis/economy/exchange` | L |
| [C14](C14-banking.md) | Banks, credit, central bank, monetary policy | M2 | `polis/economy/banking.py` | L |
| [C15](C15-ventures.md) | Startups, VC, funding rounds, M&A, bankruptcy | M3 | `polis/economy/ventures.py` | L |
| [C16](C16-social.md) | Communication, social graph, social media, feed algorithms | M4 | `polis/society` | L |
| [C17](C17-news-beliefs.md) | News outlets, journalism, belief dynamics | M4 | `polis/society/media` | L |
| [C18](C18-polity.md) | Parties, elections, voting, policy engine | M4 | `polis/society/polity.py` | L |
| [C19](C19-law.md) | Crime, detection, police, courts, judgments | M4 | `polis/society/law.py` | L |
| [C20](C20-demography.md) | Households, partnering, fertility, death settlement, inheritance | M5 | `polis/agents/demography.py` | L |
| [C21](C21-education.md) | Schools, curricula, enrolment, skill accrual | M1 | `polis/agents/education.py` | M |
| [C22](C22-gateway.md) | External agent gateway: MCP, REST/WS, auth, budgets, SDK | M6 | `polis/gateway` | L |
| [C23](C23-observatory.md) | Observatory API, live map, agent inspector, causal explorer | M1/M6 | `polis/observatory`, `web/` | L |
| [C24](C24-research.md) | Metric catalogue, invariants, experiment harness, replay, exports | M1/M2 | `polis/research` | L |
| [C25](C25-scenario.md) | Scenario DSL, shock injection, signed researcher actions | M6 | `polis/research/scenario.py` | M |

Sizes: **S** ≈ 1 day, **M** ≈ 2–4 days, **L** ≈ 1–2 weeks of focused agent work.

---

## 5. Handback contract

A chunk is done when all of the following are true. Report them explicitly.

1. All acceptance criteria in the brief are met.
2. `pytest` passes, including the new tests named in the brief.
3. `mypy --strict polis/<your module>` passes.
4. `ruff check` and `ruff format --check` pass.
5. `import-linter` passes (you did not violate the dependency rules in `02 §7.1`).
6. The determinism test for your module passes: same seed → identical output, twice.
7. Any new config keys are documented in the brief and added to the pydantic schema.
8. Any new event kinds are registered in `polis/events/kinds.py` with a payload schema.
9. You wrote down anything you had to decide that the spec did not cover, and flagged
   anything you believe is wrong in the spec. **Do not silently patch a spec conflict.**

---

## 6. Working notes for coding agents

- **Read the spec before writing code.** These documents are dense on purpose; the design
  decisions in them are load-bearing and were made for stated reasons.
- **When the spec is silent, prefer the boring option** and write down what you chose.
- **When the spec is wrong, say so.** It was written before the code existed. A brief that
  cannot be implemented as written is a finding, not an obstacle to route around.
- **Do not add abstraction for future flexibility.** One implementation, no plugin points,
  no strategy patterns with a single strategy. See `02 §1.8`.
- **The LLM call is the bottleneck.** Do not micro-optimise Python. Optimise prompt length,
  cache hit rate, and batching.

---

## 7. Ratified cross-chunk rulings

The chunk briefs were written in parallel and surfaced real conflicts at their boundaries.
These are the rulings. **They are binding and override any contrary text in an individual
brief.** Where a brief already resolved a conflict correctly, the ruling confirms it so no
implementer has to re-litigate.

### 7.1 Spec amendments (folded into `02` and `03`)

| # | Amendment | Where it landed |
|---|---|---|
| A1 | New `ActionType`s: `RENT_HOME` (world), `DECLARE_DIVIDEND` (ventures), `FOUND_PARTY` (polity) | `02 §6.2` |
| A2 | New `places.type`: `prison`, `shelter`. New `households.tenure`: `shelter` | `03 §3.1`, `§2.5` |
| A3 | Kinds 4100–4199 → `polis.llm` (not cognition-sampled). Kinds 4000–4099 and 4200–4999 remain `polis.agents` | `02 §3.2` |
| A4 | Kinds 10060–10069 → `polis.society.beliefs` | `02 §3.2` |
| A5 | C24 owns 99050/99060/99070/99090/99091; C25 owns the remainder of 99000–99999 | `11 §3` |
| A6 | New module `polis/config/runtime.py` — the tick-keyed parameter overlay | `02 §7.2` |
| A7 | `ledger_accounts.account_type` += `issuance`; `ledger_entries.reason` += `write_off`, `escrow`, `tuition`, `legal_fee`, `campaign`, `ad_revenue`, `welfare`, `damages` | `03 §4` |
| A8 | New LLM purpose `CREDIT_EVAL` | `02 §8`, `11 §5.5` |
| A9 | Validity gate **V8** — external agent liveness | `01 §7.2` |
| A10 | `runs` += `metric_manifest`, `mechanism_manifest`, `ablations`, `scale`. `llm_calls` += `lane`, `cache_mode`. `scenario_injections` += `step_id`, `event_seq`, `scenario_hash`. `sweeps` += `preregistration`, `analysis_plan_hash`, `cost_estimate_usd` | `03 §1`, `§10` |
| A11 | `tokens_per_tick` corrected to `300_000` so `calls_per_tick: 90` actually binds | `02 §8` |
| A12 | Cost target is profile-explicit: ~$12/sim-year `chronicle`, ~$250–400/sim-year `microscope` | `01 §7.1` |

### 7.2 Interface rulings

| # | Conflict | Ruling |
|---|---|---|
| R1 | `canonical_json` placed in `polis/kernel/det.py`, but `02 §7.1` forbids `events → kernel` | Lives in **`polis/config/canon.py`** (C01). `kernel` re-exports for convenience. `events` imports from `config`. |
| R2 | `polis.llm` owns kinds 4100–4199 but may not import `polis.events` | C05 registers `KindSpec`s **as data** and emits through an injected `EventEmitter` protocol. The composition root wires it. |
| R3 | Kernel phase-timing telemetry would put wall-clock into the hash chain | C04 ships `telemetry.deterministic` mode which omits timing from hashed payloads. Default **on** for research runs, off for profiling. |
| R4 | Runtime overlay had three incompatible shapes across `07`, C01, and C11 | **C11's typed unit-explicit accessors** (`.bp` `.cents` `.flag` `.brackets`) **plus C01's `.enact()`**. No `.get()` returning `Any`. `02 §7.2` is now the single definition. |
| R5 | `07 §7.2` writes tax rates as floats; `02 §4.6` bans floats for money-adjacent values | **Basis points, integer.** Every rate key is renamed to suffix `_bp`. C18 owns the rename in `POLICY_REGISTRY`. |
| R6 | `reflex_decide` arity: C07 needs world state, C09 lists a 3-arg form | **4-arg**: `reflex_decide(obs, profile, world, rng)`. C07 owns the signature; C09 calls it. |
| R7 | `LLMRouter.call` has no `template` parameter, but REFLECT needs two templates | C05 adds `template: str` to `call()`. Prompt selection is the caller's, prompt *hashing* is the router's. |
| R8 | `SelfView.last_action_rejected` is a 2-tuple in C07 but C10 produces an `ActionOutcome` | Widen to **`ActionOutcome`**. A narrower native view than external view would violate T12. |
| R9 | C06 duplicates `RentHomeParams` | C10 owns every action params model. C06 imports it. No duplicated pydantic models anywhere. |
| R10 | C09 hand-rolls `asyncio.gather` | Use **`LLMRouter.gather`** so budget is charged in request order and degradation is deterministic. |
| R11 | V8 threshold configured in two places | One key: **`research.gates.external_miss_rate_max`**. The gateway reads it. Config load asserts there is no second definition. |
| R12 | PHASE 5 slot 2 (communication) has three claimants: C16, C17, C20 | **C16 ships the `CommunicationResolver` facade** with `compose()`. C17's news actions and C20's relational actions are composed into it, never registered separately. One resolver per slot. |
| R13 | C20 lives in `polis/agents`, which may not import `polis.economy` or `polis.society` | All ledger / exchange / graph / belief calls go through Protocols in **`polis/agents/ports.py`**, injected at the composition root. Same pattern as C08's `BeliefWriter`. |
| R14 | C20's assigned kind range 2003–2059 collides with C07 and C21 | C20 registers only in **2005–2009 and 2051**. C07 and C21 keep what they already hold. |
| R15 | Death settlement ownership between C15 (bankruptcy) and C20 (demography) | C20 **orchestrates** the eight steps and **delegates** the insolvency path to C15's `bankruptcy.settle_death`. C20 does not reimplement the waterfall. |
| R16 | `polis_remember` / `polis_recall` cannot synchronously return engine-assigned ids because the gateway cannot write | Respond `{pending: true}`; event 20060 is authoritative and a later observation may expose the final id. Protocol v1 has no dedicated receipt frame. |
| R17 | Observatory needs prompt reconstruction but may not import `polis.agents` or `polis.llm` | Injected `PromptReconstructor` protocol implemented in C24. `prompt.source` gains a third value, `unavailable`. |
| R18 | Who owns the external JSON projection of `Observation` | **C07** owns `to_external_json()`. C22 owns the published schema and the byte-identity test between them. |
| R19 | C06 cannot import agent or society state to rank co-located agents | C06 accepts an injected `ColocationContext` and `DistrictInputs`. The import fence holds. |
| R20 | `schools.quality` has two possible writers | **C21 is the sole writer.** C06 emits kind 3063 with the district inputs; C21 consumes it. |
| R21 | Tuition crosses the `agents → economy` fence | C21 emits `TUITION_DUE` (14040); the economy drains it. Same pattern for any agent-side money event. |

### 7.3 Frozen interfaces

Three interfaces are frozen at handback and may not change without a written amendment,
because every downstream chunk compiles against them:

1. **`InstitutionResolver`** (C10 §5) — implemented by C06, C11–C19.
2. **`Observation`** and its sub-views (C07 §5) — consumed by C09, C10, C22.
3. **`Ledger.post_transaction(legs, *, tick, cause)`** (C11 §5) — called by every module that
   moves money. `reason` lives on each `Leg`, not on the transaction.

### 7.4 Merge gates

These tests block a merge regardless of which chunk is being merged:

| Test | Asserts |
|---|---|
| `tests/invariants/test_money_closes.py` | `INV-MONEY` holds to the cent over a 5,000-tick stub run |
| `tests/invariants/test_crime_is_possible.py` | No code path rejects an action for illegality; `RejectReason` has no `legality` member |
| `tests/invariants/test_death_settlement.py` | `INV-MONEY` holds across a death with open exchange orders, an outstanding loan, and no heirs |
| `tests/determinism/test_same_seed.py` | Two runs of the same tuple produce identical hash chains |
| `tests/determinism/test_projection_rebuild.py` | Live state and rebuilt-from-log state are identical after 500 ticks |
| `tests/unit/test_kind_ranges.py` | No kind is declared outside its owner's range; no two chunks claim the same number |
