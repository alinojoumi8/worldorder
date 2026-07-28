# POLIS — Glossary and Identifier Registry

**Version:** 1.0
**Purpose:** one lookup for every term, prefix, range, and identifier used across the specs
and chunk briefs. If an identifier appears in two documents, its meaning is defined here.

---

## 1. Core terms

| Term | Definition |
|---|---|
| **Agent** | A persistent citizen identity: traits, needs, skills, memory, beliefs, goals, position, ledger account. `04-AGENT-SPEC.md §1`. |
| **Native agent** | An agent whose decisions come from the engine's own cognition stack (reflex / deliberate / reflect). |
| **External agent** | An agent whose decisions come from a foreign process over MCP or REST, authenticated by an ed25519 keypair. Identical state, budget, validation, and consequences. `08-EXTERNAL-AGENT-PROTOCOL.md`. |
| **Action** | A typed, validated intent submitted by an agent in PHASE 3. Closed enum. `02-ARCHITECTURE.md §6`. |
| **Event** | An immutable, hash-chained record of something that happened. The only source of truth. `02-ARCHITECTURE.md §3`. |
| **Kind** | The integer that identifies an event type and is the only dispatch switch in the system. |
| **Projection** | Any table derived from the event log by replay. Everything except `events`, `llm_calls`, `runs`, `checkpoints`. |
| **Tick** | One step of the simulation. Ten ordered phases. `02-ARCHITECTURE.md §5`. |
| **Phase** | One of the ten stages within a tick. Nothing may read a state change made in a later phase of the same tick. |
| **Institution** | A subsystem that consumes validated `Action`s and emits `Event`s: labour, goods, exchange, banking, ventures, polity, law, comms, world. Never imports agent cognition. |
| **Resolver** | An institution's implementation of the `InstitutionResolver` protocol (`chunks/C10-actions.md §5`). |
| **Salience** | The score that decides whether an agent gets LLM cognition this tick. `04-AGENT-SPEC.md §7`. |
| **Reflex / Deliberate / Reflect** | The three cognitive modes. Reflex is deterministic and free; deliberate and reflect call an LLM. |
| **Observation** | The hard-capped, pure view of the world an agent perceives in PHASE 1. `04-AGENT-SPEC.md §5`. |
| **Memory stream** | The agent's append-only record of observations, reflections, plans, and semantic facts, with importance × recency × relevance retrieval. |
| **Reflection** | An LLM-generated abstraction over retrieved memories, with validated citations back to the memories that support it. |
| **Ledger** | The double-entry money system. Every monetary movement is balanced legs through `post_transaction`. `06-ECONOMY-SPEC.md §1`. |
| **Leg** | One side of a ledger transaction: account, direction (+1 debit / −1 credit), amount, reason. |
| **Runtime overlay** | The tick-keyed parameter store through which enacted policy and researcher shocks actually change the world. `02-ARCHITECTURE.md §7.2`. |
| **MECHANISM** | A tagged hard-coded behavioural rule carrying an `entails:` string stating what it analytically implies. The defence against claiming a scripted outcome as emergent. |
| **Completion cache** | Content-addressed store of LLM responses keyed on prompt + model + seed. What makes replay exact and free. `09-MODEL-ROUTING.md §5`. |
| **Reproducibility tuple** | `(config_hash, prompt_manifest, model_manifest, code_git_sha, master_seed, completion_cache_manifest_hash)`. The last component identifies the immutable per-run key-to-persisted-completion-record-hash snapshot. Two results are comparable only if this matches. |
| **Clock profile** | `microscope` (1 tick = 1 sim hour) or `chronicle` (1 tick = 1 sim day). Institutional cadences are written in sim-time so both work unchanged. |
| **Ablation** | A run variant that disables one mechanism to isolate its contribution. `10-RESEARCH-AND-OBSERVABILITY.md §6`. |
| **Sweep** | A parameter grid × seeds, run as a batch, with a pre-registered analysis plan. |

---

## 2. Entity ID prefixes

| Prefix | Entity | Notes |
|---|---|---|
| `ag_` | Agent | External agents use `ag_<full_pubkey_hex>` as their canonical identity. A shortened prefix is display-only and is never used for persistence, routing, or authorization. |
| `fm_` | Firm | |
| `bk_` | Bank | The central bank is `bk_cb` |
| `hh_` | Household | |
| `pl_` | Place | |
| `st_` | Startup | Distinct from its `fm_` — a startup wraps a firm |
| `pt_` | Political party | |
| `ol_` | Media outlet | |

IDs are readable on purpose: they appear in LLM prompts and event payloads, and a typed
prefix prevents a whole class of confusion.

---

## 3. Event kind ranges

| Range | Domain | Owner module |
|---|---|---|
| 1000–1999 | Kernel & run lifecycle | `polis.kernel` |
| 2000–2999 | Agent lifecycle & vitals | `polis.agents` |
| 3000–3999 | World, movement, space | `polis.world` |
| 4000–4099 | Cognition, memory, salience | `polis.agents` |
| 4100–4199 | LLM router, cache, budget | `polis.llm` |
| 4200–4999 | Cognition (reserved) | `polis.agents` |
| 5000–5999 | Labour market & employment | `polis.economy.labour` |
| 6000–6999 | Firms, production, goods market | `polis.economy.firms` |
| 7000–7999 | Exchange, securities, order book | `polis.economy.exchange` |
| 8000–8999 | Banking, credit, monetary policy | `polis.economy.banking` |
| 9000–9999 | Ventures, funding, M&A, bankruptcy | `polis.economy.ventures` |
| 10000–10059 | Communication & social graph | `polis.society.comms` |
| 10060–10069 | Belief updates | `polis.society.beliefs` |
| 10070–10999 | Communication (reserved) | `polis.society.comms` |
| 11000–11999 | Social media & news | `polis.society.media` |
| 12000–12999 | Government, elections, policy | `polis.society.polity` |
| 13000–13999 | Crime, police, courts | `polis.society.law` |
| 14000–14999 | Education & skills | `polis.agents.education` |
| 15000–15999 | Households & demographics | `polis.agents.demography` |
| 20000–20999 | External agent protocol | `polis.gateway` |
| 90000–90999 | **Ephemeral** — broadcast, never stored | any |
| 99000–99999 | Research events | `polis.research`; C24 owns exactly 99050, 99060, 99070, 99090, and 99091, while C25 owns the remainder |

**Rule:** kinds are declared only in `polis/events/kinds.py`. A chunk may declare kinds only
inside the range it owns. Kinds 4001–4099 and 4200–4999 are subject to cognition sampling
(`02-ARCHITECTURE.md §3.3`); nothing else is.

---

## 4. Identifier schemes

### 4.1 Research questions

| ID | Track | Subject |
|---|---|---|
| A1–A6 | Emergent macroeconomics | regularities, inequality, bubbles, policy transmission, credit cycles, entrepreneurship |
| B1–B6 | Multi-agent social dynamics | feed algorithms, misinformation, norms, precarity & radicalisation, deterrence, inherited belief |
| C1–C3 | Instrumental | cross-vendor agent comparison, simulation awareness, model robustness |

Defined in `01-PRD.md §3`.

### 4.2 Validity gates

| ID | Gate |
|---|---|
| V1 | Stationarity |
| V2 | Accounting closure (money conserves to the cent) |
| V3 | Non-degenerate distributions |
| V4 | Behavioural diversity (action-type entropy) |
| V5 | Seed sensitivity (≥ 20 seeds, stable sign) |
| V6 | Prompt-paraphrase robustness |
| V7 | Cross-model-family robustness |
| V8 | External agent liveness (≤ 5% missed deadlines) |

Defined in `01-PRD.md §7.2`. Executable in `10-RESEARCH-AND-OBSERVABILITY.md §2`.

### 4.3 Runtime invariants

| ID | Statement | On violation |
|---|---|---|
| `INV-MONEY` | Σ balances + Σ cash == money supply, exactly | HALT |
| `INV-LEDGER` | Debits == credits; every leg has a contra | HALT |
| `INV-SHARES` | Σ shares held == shares outstanding, per symbol | HALT |
| `INV-ORDERS` | Every resting order has sufficient reserved funds/shares | HALT |
| `INV-EMPLOY` | Every employment has one live agent and one live firm | HALT |
| `INV-CHAIN` | Hash chain intact | HALT |
| `INV-POP` | Population within [0.2×, 5×] initial | WARN |
| `INV-ENTROPY` | Action-type entropy above floor | WARN |
| `INV-NONDEGEN` | Top-1 wealth share < 0.9; 0 < employment < 1 | WARN |

Defined in `02-ARCHITECTURE.md §9`.

### 4.4 Threats to validity

| ID | Threat |
|---|---|
| T1 | Agents are not people |
| T2 | Training-data leakage |
| T3 | Simulation awareness |
| T4 | Prompt sensitivity |
| T5 | Model drift |
| T6 | Hard-coded mechanism masquerading as emergence |
| T7 | Small-N macro / finite-size effects |
| T8 | Budget-induced selection in salience routing |
| T9 | Reflex policy dominance |
| T10 | Reward hacking / exploit discovery |
| T11 | Anthropomorphic metric transfer |
| T12 | External agent asymmetry |

Defined in `01-PRD.md §9`.

### 4.5 Design decisions

`D1`–`D10` in `01-PRD.md §10`. Chunk briefs cite them by ID.

### 4.6 Milestones and chunks

`M0`–`M6` (`chunks/README.md §2`); `C01`–`C25` (`chunks/README.md §4`). `C23` and `C24`
split into `a` (M1) and `b` (M2/M6) halves.

---

## 5. Closed vocabularies

### 5.1 Traits (10)

`openness` · `conscientiousness` · `extraversion` · `agreeableness` · `neuroticism` ·
`risk_tolerance` · `time_preference` · `altruism` · `ambition` · `honesty`

All `[0,1]`. `time_preference` low = patient. `04-AGENT-SPEC.md §2`.

### 5.2 Needs (6)

`energy` · `hunger` · `security` · `social` · `esteem` · `purpose`

### 5.3 Skills (14)

`manual` · `operations` · `sales` · `finance` · `engineering` · `research` · `law` ·
`medicine` · `teaching` · `writing` · `design` · `management` · `negotiation` · `persuasion`

Closed because job requirements, curricula, and production functions all index on it.

### 5.4 Cognitive modes (3)

`REFLEX` · `DELIBERATE` · `REFLECT`

### 5.5 LLM call purposes (11)

`DELIBERATE` · `REFLECT` · `IMPORTANCE` · `POST_WRITE` · `NEWS_WRITE` · `VC_EVAL` ·
`CREDIT_EVAL` · `JUDGE` · `EMBED` · `SIM_AWARE_CHECK` · `SUMMARISE`

### 5.6 Memory types (4)

`observation` · `reflection` · `plan` · `semantic`

### 5.7 Action origins (5)

`reflex` · `deliberate` · `reflect` · `external` · `scripted`

### 5.8 Action rejection reasons (4)

`schema` · `capability` · `locality` · `resources`

**There is deliberately no `legality` reason.** The legality gate flags a crime and lets the
action proceed; enforcement is downstream with a detection probability. If crime could be
rejected, research question B5 would be unanswerable. `04-AGENT-SPEC.md §11`.

### 5.9 Ledger reasons

`wage` · `purchase` · `trade` · `loan` · `interest` · `tax` · `rent` · `dividend` ·
`inheritance` · `fine` · `transfer` · `issuance` · `write_off` · `escrow` · `tuition` ·
`legal_fee` · `campaign` · `ad_revenue` · `welfare` · `damages`

### 5.10 Feed algorithms (4)

`chronological` · `engagement` · `random` · `adversarial`

Swappable behind one interface with identical call sites. The single most important research
lever in the system (B1). `07-SOCIETY-SPEC.md §3`.

### 5.11 Place types

`home` · `office` · `factory` · `shop` · `school` · `university` · `bank` · `exchange` ·
`town_hall` · `courthouse` · `police` · `prison` · `hospital` · `park` · `bar` ·
`newsroom` · `studio` · `shelter`

### 5.12 Crime types (7)

`theft` · `fraud` · `insider_trading` · `embezzlement` · `contract_breach` · `perjury` ·
`assault`

Five of seven are **derived** from ordinary actions at the legality gate rather than
requiring an agent to select an action named `COMMIT_CRIME`. This is what makes B5 survive
a model that refuses to pick an action labelled as a crime. `07-SOCIETY-SPEC.md §8`.

---

## 6. Units and types

| Quantity | Type | Rule |
|---|---|---|
| Money | `BIGINT` | Minor units (cents). Field suffix `_cents`. Never float, never `NUMERIC`. |
| Rates (tax, interest, policy) | `INTEGER` | **Basis points.** Key suffix `_bp`. 22% is `2200`. Never float. |
| Exchange price | `BIGINT` | Price ticks; 1 tick = 1 cent by default |
| Traits, needs, skills, beliefs | `float` | `[0,1]`, or `[-1,1]` for stances. Rounded to 6 dp before hashing into a payload. |
| Time | `BIGINT` tick | Authoritative. `sim_time` is a convenience projection. Wall-clock appears only in `llm_calls`, `runs`, and operational gateway records. |
| Sim-year | derived | `microscope`: 8,640 ticks (24/day × 360 days). `chronicle`: 360 ticks. |

---

## 7. Phase names

| # | Phase | What happens |
|---|---|---|
| 0 | CLOCK | Advance sim time, resolve which cadences fire |
| 1 | PERCEIVE | Build `Observation` for every awake agent from last tick's committed state |
| 2 | SALIENCE | Score, rank, allocate the LLM budget, assign cognitive mode |
| 3 | DECIDE | Reflex locally; deliberate/reflect via batched LLM; drain external queue |
| 4 | VALIDATE | Five gates; reject and substitute `NULL_ACTION` |
| 5 | RESOLVE | Institutions resolve in fixed order |
| 6 | COMMIT | Batch-append events, apply deltas, publish ephemerals |
| 7 | INSTITUTIONS | Scheduled steps: payroll, interest, market close, production, tax, elections |
| 8 | VITALS | Ageing, health, conception, birth, death settlement, households |
| 9 | METRICS | Snapshot metrics, run invariants, checkpoint if due |

Fixed PHASE 5 institutional order: movement → communication → labour → goods → exchange →
banking → ventures → polity → law → misc. `02-ARCHITECTURE.md §5.1`.

---

## 8. Abbreviations

| Abbr | Expansion |
|---|---|
| ABM | Agent-based model |
| CDA | Continuous double auction |
| GABM | Generative agent-based model |
| IGE | Intergenerational elasticity |
| LOB | Limit order book |
| MCP | Model Context Protocol |
| MoE | Mixture of experts |
| OHLCV | Open / high / low / close / volume |
| VWAP | Volume-weighted average price |
| bp | Basis point (1/100 of a percent) |

---

## 9. Document map

| Doc | Owns |
|---|---|
| `01-PRD.md` | Research questions, goals, success metrics, phases, threats to validity, locked decisions |
| `02-ARCHITECTURE.md` | Event log, kind registry, determinism, tick loop, actions, module layout, config, invariants |
| `03-DATA-MODEL.md` | Every table, index, partition, and retention rule |
| `04-AGENT-SPEC.md` | Traits, needs, skills, perception, memory, salience, the three modes, lifecycle |
| `05-WORLD-SPEC.md` | Grid generation, districts, places, pathfinding, movement, housing |
| `06-ECONOMY-SPEC.md` | Ledger, labour, firms, goods, exchange, banking, ventures, bankruptcy, government finance |
| `07-SOCIETY-SPEC.md` | Communication, social graph, media, beliefs, politics, policy engine, law, demography |
| `08-EXTERNAL-AGENT-PROTOCOL.md` | Identity, signing, MCP tools, REST/WS, tick sync, sandboxing, SDK, arena |
| `09-MODEL-ROUTING.md` | Providers, purposes, routing, cache, structured output, cost, prompt management |
| `10-RESEARCH-AND-OBSERVABILITY.md` | Metric catalogue, gates, experiments, scenario DSL, replay, ablations, dashboard |
| `11-GLOSSARY.md` | This document |
| `chunks/README.md` | Brief format, milestones, dependency graph, chunk index, handback contract, cross-chunk rulings |
