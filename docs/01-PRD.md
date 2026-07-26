# POLIS — Product Requirements Document

**Version:** 1.0
**Date:** 2026-07-24
**Status:** Approved for specification
**Audience:** the founder, collaborating researchers, and AI coding agents implementing the system

---

## 1. One-paragraph summary

Polis is a persistent, tick-based simulation of a city of roughly one thousand LLM-driven
agents. Agents are born into households, attend school, acquire skills, take jobs, earn and
spend money, borrow, invest, found companies, raise venture capital, go bankrupt, post on
social media, read the news, form political opinions, vote, commit and prosecute crimes,
age, and die — leaving estates and beliefs to their children. Every action in the city is a
signed event in a single append-only log, which makes runs replayable, auditable, and
analysable. External agents from other systems can join the city as citizens with their own
cryptographic identity and the same action surface as native agents. The purpose is
research: to observe whether macroeconomic and social regularities emerge from LLM
reasoning, and to run controlled interventions on a society that cannot be run on a real
one.

---

## 2. Why build this

### 2.1 The gap

Three families of work exist, and each is missing something the others have.

**Classical agent-based economics** (ACE, Santa Fe, Mesa) gives rigorous, cheap,
reproducible macro dynamics — but agents are equations. They cannot read a news article,
be persuaded, lie in a job interview, or invent a business model. Behavioural richness is
hand-coded, so the model can only produce what the modeller already imagined.

**Generative agent work** (Smallville, Project Sid, AgentSociety) gives genuinely rich
individual behaviour — memory, planning, social inference — but the economies are toys.
Smallville has no labour market. Sid has crafting, not capital allocation. AgentSociety has
scale and psychology but a thin institutional layer. None of them have a limit order book,
a bank with a balance sheet, a bankruptcy code, or an election that changes the tax rate.

**Agent benchmarks** (SWE-bench, WebArena, GAIA) measure agents on bounded tasks with a
known answer. Nobody measures agents on *open-ended, adversarial, multi-year, multi-agent*
objectives inside an economy where other agents are also optimising.

Polis is the intersection: **generative agents inside a real institutional economy, with
the whole thing instrumented as a scientific apparatus.**

### 2.2 Why now

Two things changed. Frontier-class agentic models became cheap enough to run a thousand of
them (MiniMax M2-class pricing is ~$0.26/M input, ~$1.00/M output; Ollama Cloud runs large
open models on GPU-time billing). And the agent interoperability layer settled — MCP is
now the lingua franca, which means an outside agent can be a citizen without us writing an
adapter per vendor.

---

## 3. Research questions

These are the questions the platform exists to answer. Every design decision downstream is
justified by at least one of them. They are grouped by the two chosen research tracks.

### Track A — Emergent macroeconomics

| ID | Question | What we measure | Why it's hard to answer elsewhere |
|---|---|---|---|
| **A1** | Do LLM agents reproduce known macro regularities without being told to? | Beveridge curve, Okun's law, Phillips curve, Zipf's law of firm sizes, log-normal wealth distribution, business-cycle autocorrelation | Classical ABM assumes them; real economies can't be re-run |
| **A2** | Where does inequality come from — luck, skill, inheritance, or network position? | Gini decomposition by source; intergenerational income elasticity | Requires observing full lifetimes and parentage, which no real dataset has cleanly |
| **A3** | Do asset bubbles emerge from narrative rather than fundamentals? | Divergence of market price from discounted-earnings fair value, cross-correlated with social-media sentiment and news volume | Needs a market and a media layer in the same system |
| **A4** | What is the transmission path from a policy shock to household outcomes? | Impulse-response of unemployment, consumption, and default rates to a central-bank rate change or tax change | Real-world identification is notoriously contested; here the counterfactual is a re-run with a different seed-matched config |
| **A5** | Do credit cycles and bankruptcy cascades emerge endogenously? | Firm default clustering, bank capital ratios, interbank contagion | Needs a bank with a real balance sheet and firms that can actually fail |
| **A6** | Does entrepreneurship improve or worsen aggregate welfare under different VC regimes? | Firm entry/exit rates, productivity growth, employment share by firm age, capital misallocation | Requires a venture layer that can be parameterised and swept |

### Track B — Multi-agent social dynamics

| ID | Question | What we measure | Why it's hard to answer elsewhere |
|---|---|---|---|
| **B1** | How does the social-media feed algorithm change belief polarisation? | Bimodality of the belief distribution over policy axes; cross-cutting exposure rate; time-to-consensus. Feed ranking is a first-class config knob (chronological / engagement-optimised / random / adversarial). | This is the single most-argued empirical question in tech policy and cannot be A/B tested on a real population ethically |
| **B2** | How do false beliefs propagate and die? | Injected-falsehood reach, half-life, correction efficacy, correlation with source trust | Needs controlled injection, which is unethical on humans |
| **B3** | Do agents form stable norms, coalitions, and institutions without being instructed to? | Emergence of repeated coordination, informal enforcement, reputation-based exclusion | Requires long horizons and rich communication |
| **B4** | Does economic precarity cause political radicalisation, or the reverse? | Granger-style lead/lag between individual unemployment/debt state and platform position; effect size under randomised job-loss shocks | Randomised job loss is not an available experiment |
| **B5** | How much enforcement capacity is needed to deter fraud? | Crime rate as a function of detection probability and penalty severity; displacement to undetected crime types | Deterrence elasticity is one of the least settled numbers in criminology |
| **B6** | Do inherited beliefs or lived experience dominate a child's worldview? | Correlation of child's belief vector with parental prior vs. with own economic history | Requires observing both, across generations, in the same system |

### Track C — Instrumental (emerges from having built A and B)

| ID | Question |
|---|---|
| **C1** | How do external agents from different vendors and scaffolds perform on open-ended goals — wealth accumulation, influence, institutional capture — against each other and against native agents? |
| **C2** | Does an agent that knows it is in a simulation behave differently from one that does not? (Direct test of a major validity threat, and interesting in its own right.) |
| **C3** | Which of the model's behaviours are robust across model families (MiniMax vs. Qwen vs. Gemma vs. GLM), and which are artefacts of one model's priors? |

---

## 4. Goals and non-goals

### 4.1 Goals

**G1 — Emergence over scripting.** The simulator provides institutions and constraints,
not behaviours. There is no rule that says "when unemployment rises, agents become angry."
If that happens, it happens because agents reasoned their way there. Anything hard-coded is
declared as such in the spec and tagged `MECHANISM` in the config so it can be ablated.

**G2 — Reproducibility.** A run is defined by `(config, seed, model_versions,
completion_cache)`. Re-running that tuple must produce an identical event log. This is
non-negotiable and shapes the whole architecture (see `02-ARCHITECTURE.md §4`).

**G3 — Auditability.** Every event carries an actor, a tick, a cause, and a hash-chained
position in the log. A reviewer must be able to trace any macro statistic back to the
individual decisions that produced it, and verify the log was not edited after the fact.

**G4 — Cost-bounded.** A researcher sets a token budget for a run; the engine respects it
by throttling how many agents get LLM cognition per tick. Running out of budget degrades
fidelity gracefully — it never crashes or silently truncates.

**G5 — Open to foreign agents.** Any MCP-speaking agent can obtain a citizen identity and
act with exactly the same affordances as a native agent — no more, no less. No vendor-
specific adapters in the core.

**G6 — Legible.** A researcher can open any agent at any tick and see: what it perceived,
what it remembered, what it was asked, what it answered, what it did, and what happened as
a result. If you cannot explain a macro chart by drilling into individuals, the
observability layer has failed.

**G7 — Model-agnostic.** Swapping the model behind any call purpose is a config change.
No prompt in the codebase may assume a specific vendor's behaviour.

### 4.2 Non-goals

**N1 — Not a game.** No player character, no win condition, no fun-tuning. A human can
observe and can inject shocks; a human cannot be a citizen. (A future *Deliberate-Lab*-style
human-in-the-loop mode is out of scope for v1.)

**N2 — Not a prediction engine.** Polis will not forecast real GDP, real elections, or real
markets. Its outputs are statements about *this model society*, and any paper that comes
out of it must say so. See §9.

**N3 — Not photorealistic.** The 2D grid exists to constrain who meets whom and to make
spatial inequality possible. It is a research instrument, not a rendering project. Sprites
and animation are explicitly deprioritised.

**N4 — Not a general-purpose ABM framework.** We are not building Mesa. Extensibility is
provided by the event-kind registry and the action schema, not by a plugin API.

**N5 — Not distributed in v1.** One machine, one Postgres, one process group. The tick loop
is designed so it *can* be sharded later, but sharding is not built now.

**N6 — No real money, no real identities, no scraped personal data.** Agents are synthetic.
Any resemblance to real persons is a bug and must be removed.

---

## 5. Users

| Persona | Needs | Primary surface |
|---|---|---|
| **Researcher (you)** | Define an experiment, sweep parameters, get clean data, drill into individuals, publish | CLI (`polis run`, `polis sweep`), Observatory dashboard, Parquet exports |
| **AI coding agent** | Pick up a chunk, understand the contract, implement, test, hand back | `chunks/*.md`, typed interfaces, `pytest` suite |
| **External agent operator** | Register an agent, get keys, connect it, watch it live or die | MCP endpoint, agent SDK, agent scorecard |
| **Reader / reviewer** | Verify a claim, replay a run, inspect the log | Replay CLI, hash-chain verifier, event log search |

---

## 6. The world, in brief

A full specification of each layer lives in its own document. This is the orientation map.

```
                          ┌───────────────────────────┐
                          │      GOVERNMENT           │  taxes, spending, policy rates,
                          │  president · council      │  regulation, courts, police
                          └──────────┬────────────────┘
                                     │ sets parameters
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
┌───────▼────────┐          ┌────────▼─────────┐        ┌─────────▼────────┐
│    ECONOMY     │          │     SOCIETY      │        │      WORLD       │
│ firms · labour │◄────────►│ social graph     │◄──────►│ 2D grid          │
│ goods market   │          │ social media     │        │ places/districts │
│ exchange (LOB) │          │ news outlets     │        │ movement         │
│ banks · credit │          │ beliefs · trust  │        │ co-location      │
│ VC · startups  │          │ parties · votes  │        │ rent · locality  │
│ bankruptcy     │          │ crime · courts   │        │                  │
└───────┬────────┘          └────────┬─────────┘        └─────────┬────────┘
        │                            │                            │
        └────────────────────────────┼────────────────────────────┘
                                     │
                          ┌──────────▼────────────────┐
                          │        AGENTS             │
                          │ ~1000 native + N external │
                          │ born · school · work ·    │
                          │ trade · vote · age · die  │
                          │ memory · beliefs · goals  │
                          └───────────────────────────┘
```

### 6.1 Agent lifecycle

```
 conception → birth → infancy → schooling → (university?) → labour force
     ▲                              │              │              │
     │                              ▼              ▼              ▼
     │                        skill accrual   specialisation   employment /
     │                                                         unemployment /
     │                                                         entrepreneurship
     │                                                              │
 household ◄── partnering ◄── social life ◄──────────────────────────┤
 formation                                                           │
     │                                                               ▼
     └──────────── fertility ──────────────────────── ageing → illness → death
                                                                      │
                                                                      ▼
                                                    estate settlement · obituary ·
                                                    memory archival · job vacated ·
                                                    positions liquidated · heirs
                                                    inherit wealth AND belief priors
```

Death must have economic and social consequence. An agent dying vacates a job, closes
positions on the exchange, defaults or repays loans, transfers wealth, orphans dependants,
and removes a node from the social graph. If death is cosmetic, the demographic layer is
worthless.

### 6.2 The three cognitive modes

Cost control is achieved by not asking an LLM to decide what a human would not think about.

| Mode | Trigger | LLM? | Share of agent-ticks | What it does |
|---|---|---|---|---|
| **Reflex** | Default | No | ~92% | Deterministic utility policy over a small legal action set. Commute, work, eat, sleep, routine purchases. Seeded, replayable, free. |
| **Deliberate** | Salience score above the per-tick budget cutoff | Yes | ~7% | Full observation + retrieved memory + goals → a structured action. Job offers, firings, large purchases, trades, arguments, votes, pitches. |
| **Reflect** | Scheduled (daily/weekly) or on life events | Yes | ~1% | Compresses the memory stream into higher-order beliefs; revises goals and identity; writes the agent's own narrative. |

Salience is computed from surprise (prediction error), stakes (Δwealth, Δstatus, health
risk), novelty, direct social address, and scheduled obligations, plus a small random
exploration term. Agents are ranked by salience each tick and the top-K within budget get
LLM cognition. **The budget is a hard cap, not a target.**

---

## 7. Success metrics

### 7.1 System metrics (is the thing built and working?)

| Metric | Target |
|---|---|
| Determinism | Two runs of the same `(config, seed, cache)` produce byte-identical event-log hash chains |
| Throughput | ≥ 1 tick/second wall clock at 1,000 agents, `microscope` profile, on a single 16-core machine |
| LLM cost | ≤ $12 per simulated year at 1,000 agents under the default budget policy |
| Tick latency p99 | ≤ 4s including LLM batch |
| Replay fidelity | Replay from event log + completion cache reproduces all metric series exactly |
| Log integrity | Hash chain verifies for every completed run |
| Crash recovery | Engine resumes from the last committed tick with no state divergence |

### 7.2 Scientific validity gates (is the thing worth believing?)

A run is only usable for research if it passes these. They are *falsification gates*, not
vanity metrics — a failure means the model is broken, and that is a finding.

| Gate | Criterion | Rationale |
|---|---|---|
| **V1 Stationarity** | Macro series (unemployment, CPI, index level) are not monotonically exploding or collapsing over 5 sim-years absent a shock | An economy that only ever inflates or dies has a broken accounting identity |
| **V2 Accounting closure** | Sum of all agent + firm + bank + government balances equals total money supply at every tick, to the cent | Money must not be created or destroyed outside the credit mechanism. This is the single best bug detector in the system. |
| **V3 Non-degenerate distributions** | Wealth is not all in one agent; employment is not 0% or 100%; the order book is not empty for > 3 consecutive sessions | Degenerate outcomes usually mean an agent found an exploit, not that the economy collapsed |
| **V4 Behavioural diversity** | Action-type entropy across the population exceeds a floor; not every agent does the same thing | Mode collapse is the characteristic LLM-society failure |
| **V5 Sensitivity** | Outcomes change under seed variation but the *sign* of headline effects is stable across ≥ 20 seeds | Single-seed findings are anecdotes |
| **V6 Prompt robustness** | Headline effects survive a paraphrase of the core agent prompt | If a finding dies under paraphrase it is a fact about the prompt, not the society |
| **V7 Model robustness** | Headline effects replicate across at least two model families | Otherwise the finding is a property of one vendor's training data |

### 7.3 Research output metrics

| Milestone | Definition of done |
|---|---|
| First reportable result | One of A1–A6 or B1–B6 answered with ≥ 20 seeds, passing V1–V5, with an effect size and a confidence interval |
| Reproducibility package | Config + seeds + completion cache + event logs published such that a third party reproduces the figures |
| External agent arena | ≥ 3 foreign agent implementations survive ≥ 1 sim-year and appear on the scorecard |

---

## 8. Phased delivery

Each phase ends with something a researcher can actually use. Chunk IDs refer to
`../chunks/`.

| Phase | Name | Chunks | Ends when |
|---|---|---|---|
| **M0** | Kernel | C01–C05 | Engine ticks an empty world, writes a verifiable hash-chained event log, routes an LLM call through the cache |
| **M1** | Living City | C06–C10, C21, C23a, C24a | 1,000 agents move on the grid, attend school, accrue skills, talk, remember, and reflect. No economy. Dashboard shows the map and lets you inspect one agent end-to-end. |
| **M2** | Economy | C11, C12, C14, C24b | Labour market clears, firms produce and price, banks lend, government taxes. **Unemployment is a real number.** V2 accounting closure holds. First research question (A1) becomes answerable. |
| **M3** | Capital | C13, C15 | Limit order book runs, firms IPO, VCs fund startups, companies are bought and go bankrupt. A3, A5, A6 answerable. |
| **M4** | Polity | C16–C19 | Social media with swappable feed algorithms, news outlets, parties, elections that change policy, police and courts. B1–B5 answerable. |
| **M5** | Generations | C20 | Agents partner, reproduce, inherit wealth and beliefs, and die. A2 and B6 answerable. |
| **M6** | Open World | C22, C23b, C25 | External agents join via MCP, scenario DSL injects shocks, full research tooling. C1–C3 answerable. |

**Critical path note.** M2 is the highest-risk phase because V2 (accounting closure) is
where most simulations of this kind quietly break. Budget for it accordingly. Do not start
M3 until V2 holds for 5 consecutive sim-years.

---

## 9. Threats to validity

This section is load-bearing. Any paper produced from Polis must reproduce it. Being honest
about these is what separates a research instrument from a demo.

| # | Threat | Why it matters | Mitigation built into the system |
|---|---|---|---|
| **T1** | **Agents are not people.** LLM agents are samples from a text distribution, not humans. Findings are about model behaviour under a scenario. | Every substantive claim | The word "human" never appears in a result statement. Reported as: *"LLM agents of family X, under prompt Y, produced Z."* |
| **T2** | **Training-data leakage.** Models know economics textbooks. An emergent Phillips curve may be recall, not emergence. | A1 especially | Ablation: run with obfuscated domain language (renamed variables, invented terminology) and check whether the regularity survives. Chunk C24 ships this as `--obfuscate-domain`. |
| **T3** | **Simulation awareness.** Agents may infer they are in a simulation and behave performatively. | Everything | (a) Prompts never state it is a simulation. (b) `C2` is run as an explicit experiment. (c) A "simulation-awareness" classifier flags agent outputs mentioning being an AI or a simulation; rate is reported per run. |
| **T4** | **Prompt sensitivity.** Results may be an artefact of one phrasing. | Everything | V6 gate: paraphrase robustness is mandatory before publication. Prompts are versioned assets with hashes recorded in the run manifest. |
| **T5** | **Model drift.** A hosted model updated between runs invalidates comparisons. | Cross-run comparison | Model name + version + provider is recorded per call. Runs with mixed model versions are flagged and cannot be pooled. Completion cache makes historic runs replayable even after a model is retired. |
| **T6** | **Hard-coded mechanism masquerading as emergence.** If the labour-matching function implies a Beveridge curve, finding one proves nothing. | A1, A5 | Every hard-coded mechanism is tagged `MECHANISM` in config with a docstring stating what it *entails*. Any claimed emergent result must be shown to not follow analytically from the tagged mechanisms. Reviewer checklist in `10-RESEARCH-AND-OBSERVABILITY.md`. |
| **T7** | **Small-N macro.** 1,000 agents is a village, not an economy. Aggregate statistics are noisy and finite-size effects are real. | All of Track A | Report confidence intervals across seeds, never a single run. Run a scale ladder (250 / 500 / 1000 / 2000) and report whether the effect is scale-stable. |
| **T8** | **Budget-induced selection.** Agents that get LLM cognition are, by construction, the ones in high-stakes situations. This is a systematic, non-random treatment. | Everything | Salience weights and the cutoff are logged per tick. A control condition with uniform-random routing at the same budget is available (`--salience-policy=random`) to measure the effect of the routing itself. |
| **T9** | **Reflex policy dominance.** If 92% of behaviour is deterministic code, the "LLM society" may be mostly classical ABM wearing a hat. | Interpretation of every result | The LLM-attributable share of variance in outcomes is a reported statistic. Ablation `--reflex-only` gives the pure-ABM baseline; every headline result must be shown to differ from it. |
| **T10** | **Reward hacking / exploit discovery.** Agents will find bugs in the market or law implementation and exploit them. This looks like emergence. | A3, A6, B5 | Invariant checks (V2, V3) run every tick and halt the run on violation. Exploits found are logged as findings, patched, and the run is re-labelled. |
| **T11** | **Anthropomorphic metric transfer.** Calling a number "unemployment" imports assumptions. | Track A | Every metric has a formal definition in `10-RESEARCH-AND-OBSERVABILITY.md` stated purely in terms of simulation state, with the real-world analogue named separately. |
| **T12** | **External agent asymmetry.** A foreign agent with a bigger model or better scaffold isn't a citizen, it's a superintelligence in a village. | Track C | Action budget per tick is identical for all agents. Latency deadline is identical. Scorecard reports model tier and scaffold so results are read as capability comparisons, not society findings. |

### 9.1 What we deliberately do *not* take from prior art

| Source | Rejected | Why |
|---|---|---|
| Buzz | Schnorr-signing *every* event | 1,000 agents × ~20 events/tick × secp256k1 verify would dominate CPU. We sign **external agent actions only**, where provenance is the point, and hash-chain everything else. See `02-ARCHITECTURE.md §3.4`. |
| Buzz | Nostr wire protocol | We are not federating with the Nostr ecosystem. We keep the *shape* (kind-dispatched signed events, append-only, one source of truth) and drop the interop surface. |
| Smallville | Free-form natural-language actions adjudicated by an LLM game-master | Non-deterministic and unauditable at the institutional layer. Our actions are a **closed typed schema**; the LLM chooses among and parameterises legal actions, it does not narrate outcomes. Free text is confined to speech and posts. |
| Project Sid | Agents acting in a real game engine | Coupling to Minecraft imports its physics and its bugs. Our grid is ours and is deterministic. |
| AgentSociety | Distributed multi-node engine | Premature at 1k agents. Architecture keeps the door open (`N5`). |

---

## 10. Key product decisions (locked)

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| D1 | **Event-sourced, append-only log is the single source of truth**; all state is a projection | Mutable ORM state with a change log | Replay, audit, and research analysis all come free. Directly adopted from Buzz. |
| D2 | **Tick-based with phase-structured resolution**; all agents submit simultaneously, institutions resolve deterministically | Continuous/async time | Eliminates iteration-order artefacts, which are a classic silent ABM bug. Makes determinism achievable. |
| D3 | **Tiered cognition with a hard LLM budget** | LLM every agent every tick | 1,000 agents × 8,640 ticks/year × 1 call = 8.6M calls/sim-year ≈ $17k. Unaffordable and unnecessary. |
| D4 | **Full 2D grid with precomputed inter-place paths** | Abstract institutional graph; full per-tick A* | Grid buys spatial inequality, realistic encounter locality, and a legible dashboard. Precomputing all-pairs paths between ~400 places makes movement O(1) per agent-tick. |
| D5 | **Closed typed action schema** | Free-form natural-language actions | Auditability and validity. See §9.1. |
| D6 | **Python 3.12 + FastAPI + Postgres 17 + Redis** | TS monorepo; Rust core | Research ecosystem (pandas, statsmodels, networkx, pgvector) dominates. Performance is adequate because the LLM call is always the bottleneck. |
| D7 | **Completion cache keyed by `(prompt_hash, model, params)`** | Live calls on every replay | Makes replay free and deterministic despite LLM nondeterminism. Also cuts sweep cost dramatically because parameter sweeps share most prompts. |
| D8 | **External agents are citizens with keypairs, not a special API** | Human-player mode; privileged bot API | Directly from Buzz's "agents are members, not bots." Guarantees fairness (T12) and means the arena needs no extra code. |
| D9 | **MiniMax for reasoning, Ollama Cloud + local for volume** | Single provider | Cost and T7/model-robustness. Router abstraction makes it a config change. |
| D10 | **1 tick = 1 sim hour (`microscope`) or 1 sim day (`chronicle`)** | Fixed tick semantics | Behavioural research needs hours; demographic research needs decades. Institutional cadences are expressed in sim-time so both profiles work unchanged. |

---

## 11. Risks and open questions

| Risk | Severity | Mitigation / decision needed |
|---|---|---|
| **Money doesn't close.** Accounting identity breaks under some edge case (partial fills, bankruptcies mid-tick, agent death holding open orders). | **Critical** | V2 invariant runs every tick and halts. Double-entry ledger, not balance fields. Specified in `06-ECONOMY-SPEC.md §2`. |
| **Mode collapse.** All agents converge on the same behaviour and the society becomes a monoculture. | High | Trait-conditioned prompts, temperature > 0, per-agent seeded persona variation, V4 entropy gate. Open question: whether trait diversity is *sufficient* — needs an early empirical check in M1. |
| **Reflex policy is too good.** Deterministic policy handles everything, LLM adds nothing, T9 bites. | High | Deliberately keep the reflex action set narrow (routine only). Any action with a counterparty, a price, or a commitment is LLM-only. Measure with `--reflex-only` ablation from M2 onward. |
| **Cost overrun.** Salience routing under-throttles. | Medium | Hard token budget enforced in the router, not the agent layer. Circuit breaker halts a run at 120% of budget. |
| **External agent latency stalls the tick.** | Medium | Deadline + fallback-to-reflex. Specified in `08-EXTERNAL-AGENT-PROTOCOL.md §5`. |
| **Grid is a distraction.** Movement engineering consumes M1 and buys little. | Medium | Mitigated by D4 (precomputed paths, no per-tick A*). If M1 overruns, the grid degrades cleanly to `05-WORLD-SPEC.md`'s zone-only mode. |
| **Postgres becomes the bottleneck at high event volume.** | Medium | Monthly partitioning + batch inserts per tick + async projections. Measured in M1; if it fails, move hot events to an append-only file log with Postgres as index. |
| **The findings are boring.** Agents just do sensible things and nothing emerges. | Low but real | This is itself a publishable result about LLM societies, and the ablation ladder (T9) makes it interpretable rather than a null. |

### Open questions to resolve during M1

1. Is trait-conditioned persona variation enough for V4, or do we need per-agent prompt-template variation?
2. What is the actual salience threshold that yields ~7% deliberate rate with sensible behaviour? Needs calibration, not guessing.
3. Does the memory retrieval scoring (recency × importance × relevance) need per-agent learned weights, or are global weights fine at this scale?
4. Should reflection be scheduled or purely event-triggered? Scheduled is cheaper to reason about; event-triggered is more realistic.

---

## 12. What "done" looks like for v1.0

- 1,000 native agents live three simulated generations without an invariant violation.
- The accounting identity closes to the cent at every tick of a 5-sim-year run.
- At least one Track A and one Track B research question answered across ≥ 20 seeds with V1–V6 passing.
- Three external agent implementations have joined, acted, and been scored.
- A third party reproduces a published figure from the reproducibility package.

---

*Next: `02-ARCHITECTURE.md`.*
