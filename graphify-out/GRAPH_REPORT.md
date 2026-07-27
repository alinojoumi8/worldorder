# Graph Report - worldorder  (2026-07-26)

## Corpus Check
- 274 files · ~595,246 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3296 nodes · 8984 edges · 207 communities (194 shown, 13 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 1117 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `3ae0f7fe`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 15
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Lane
- Community 25
- Community 26
- invariants.py
- Community 28
- goods.py
- Community 30
- Community 31
- Community 32
- Community 33
- EventRepository
- RuntimeConfig
- load_settings
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 51
- Community 52
- Community 53
- Community 54
- Community 55
- Community 56
- Community 57
- Community 59
- POLIS — Data Model
- C24 — Metric catalogue, invariants, validity gates, experiment harness, replay, exports
- C23 — Observatory: live map, agent inspector, causal explorer, run comparison
- POLIS — Product Requirements Document
- POLIS — Agent Specification
- test_ventures.py
- C25 — Scenario / shock DSL, signed researcher injection
- C08 — Memory stream, retrieval, reflection, embeddings
- C16 — Communication, social graph, social media, feed algorithms
- C17 — News outlets, journalism, claim checking, belief dynamics
- C20 — Households, partnering, fertility, migration, death settlement, inheritance
- C10 — Action schema, validators, budget, resolution dispatch
- C18 — Parties, elections, voting, offices, the policy engine
- C19 — Crime, detection, police, courts, judgments, incarceration
- C09 — Salience scoring, cognition routing, deliberate, reflect
- POLIS implementation notes
- 1.6 Worked examples — the legs
- Components
- Event
- Clock
- EventReader
- C02 — Event log, kind registry, hash chain
- C03 — Postgres schema, migrations, repositories, partitioning
- C04 — Clock, tick loop, RNG registry, scheduler, invariants, checkpoints
- C05 — LLM router, providers, completion cache, budget, structured output
- C07 — Agent state, traits, needs, skills, reflex policy
- C13 — Limit order book, matching, market data
- C15 — Startups, VC, funding rounds, M&A, bankruptcy
- C01 — Repo scaffold, config system, CLI
- C06 — Grid generation, places, pathfinding, movement, housing
- C11 — Ledger, labour market, firms and production
- C12 — Goods market, consumption, CPI
- C14 — Banks, credit, central bank, treasury, monetary policy
- C21 — Schools, curricula, enrolment, skill accrual
- POLIS — Economy Specification
- 6. Exchange
- 7. Banking and credit
- 8. Law and crime
- WorldStateView
- POLIS — World Specification
- 3. Labour market
- 4. Firms and production
- 8. Ventures
- 4. The MCP server
- 10. Social metrics
- 11. Threats and failure modes
- 3. Social media platform
- 4. News and journalism
- POLIS — External Agent Protocol
- 1. The metric catalogue
- 4. The scenario / shock DSL
- Product
- useUrlParam
- POLIS — Society Specification
- fiscal.py
- README.md
- POLIS — Implementation Chunks
- 5. Belief dynamics
- 9. Demographics
- 2. Identity, registration, and lifecycle
- POLIS — Model Routing, Caching, and Cost Control
- POLIS — Research and Observability
- invariants.py
- 10. Bankruptcy
- 0. Scope, ownership, and conventions
- 6. Politics
- 7. Policy engine
- 6. Tick synchronisation
- 5. The completion cache
- 3. The experiment harness
- 3. Districts and endogenous differentiation
- 5. Movement
- 7. Housing and rent
- 2. Determinism, arithmetic, and shared machinery
- 5. Goods market
- 2. Social graph
- 10. Onboarding a foreign agent
- 2. The provider abstraction
- 4. Routing policy
- 7. Cost model and budgeting
- 8. Prompt management
- 0. Scope, ownership, conventions
- surface-brief-body.md
- web-src-app-tsx.md
- POLIS — Documentation Index
- 4. Places
- 8. Time-of-day and weekly rhythm
- 11. Government finance
- 13. Calibration and initial conditions
- 9. Mergers and acquisitions
- 11. The arena and scorecard
- 3. Canonical serialisation and the signature scheme
- 8. Sandboxing and information security
- 10. Model-robustness protocol (V7)
- 10. Statistical practice
- 8. The Observatory
- M1 stabilization and multi-seed validation
- 9. World services
- 1. Design goals and the fairness contract
- 3. Call purposes
- 6. Structured output
- 9. Token accounting and telemetry
- 2. Invariants and validity gates as executable checks
- 5. Replay and reproducibility
- 9. Data export
- M1 Living City acceptance
- M2 Economy acceptance
- 6. Co-location and encounter
- 12. Threats and failure modes
- 7. Rate limiting and abuse
- 11. Local versus cloud
- evidenceForAgent
- 11. Rendering contract
- ExchangeEngine
- Ledger
- NullWorldState
- load_settings
- partition.py
- Scheduler
- rebuild.py
- EconomyWorldState
- stream.py
- central.py
- test_router_repair.py
- MechanicalPolicy
- test_exchange_properties.py
- test_stub.py
- CacheSettings
- _event
- engine.py
- 9. Implementation notes
- __init__.py
- hashing.py
- Any
- live_provider_smoke.py
- SeedSource

## God Nodes (most connected - your core abstractions)
1. `Event` - 180 edges
2. `Settings` - 139 edges
3. `NewEvent` - 126 edges
4. `RngRegistry` - 125 edges
5. `AgentPopulation` - 118 edges
6. `World` - 110 edges
7. `Action` - 108 edges
8. `load_settings()` - 96 edges
9. `VentureEngine` - 78 edges
10. `LLMRouter` - 71 edges

## Surprising Connections (you probably didn't know these)
- `test_production_carry_preserves_fractional_output()` --calls--> `production_output_micro()`  [INFERRED]
  tests/unit/economy/test_labour_firms.py → polis/economy/production.py
- `test_canonical_json_rejects_non_primitives()` --indirect_call--> `ConfigError`  [INFERRED]
  tests/unit/config/test_canon.py → polis/config/errors.py
- `test_unknown_key_is_rejected()` --indirect_call--> `ConfigError`  [INFERRED]
  tests/unit/config/test_settings.py → polis/config/errors.py
- `RepairProvider` --uses--> `CacheSettings`  [INFERRED]
  tests/unit/llm/test_router_repair.py → polis/config/settings.py
- `test_occupation_catalogue_uses_the_closed_fourteen_skill_vocabulary()` --calls--> `load_occupations()`  [EXTRACTED]
  tests/unit/economy/test_labour_firms.py → polis/economy/labour.py

## Import Cycles
- None detected.

## Communities (207 total, 13 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.12
Nodes (23): CapTableState, ClaimState, FundingRoundState, Any, VCFundState, acquisition_offer_cents(), integrated_productivity_bp(), _monotone_pro_rata() (+15 more)

### Community 1 - "Community 1"
Cohesion: 0.17
Nodes (35): ConfigError, ProfileNotFound, A requested configuration profile does not exist., Configuration cannot be loaded or validated., AblationSettings, BankingSettings, BankruptcySettings, CacheSettings (+27 more)

### Community 2 - "Community 2"
Cohesion: 0.29
Nodes (8): LLMBudgetSettings, Admission, BudgetGuard, Decimal, StrEnum, _Usage, test_budget_hard_stops_at_run_call_limit(), test_budget_ladder_degrades_then_halts()

### Community 3 - "Community 3"
Cohesion: 0.08
Nodes (20): buildingSeeds, choroplethPalettes, choroplethRanges, districtAgentPositions, districtPolygons, layerOptions, navItems, PrototypeApp() (+12 more)

### Community 4 - "Community 4"
Cohesion: 0.08
Nodes (50): dtype, EducationLevel, EmploymentStatus, PlaceType, Resolution, resolve_actions(), _age(), _education() (+42 more)

### Community 5 - "Community 5"
Cohesion: 0.14
Nodes (22): F, mechanism(), add_inventory(), markup_price(), update_productivity_bp(), create_economy(), _firm_place(), GenesisResult (+14 more)

### Community 6 - "Community 6"
Cohesion: 0.13
Nodes (11): AsyncConnectionPool, Database, Any, AsyncConnection, Persistence and projection repositories., _clear_projections(), Any, LedgerRepository (+3 more)

### Community 7 - "Community 7"
Cohesion: 0.20
Nodes (24): exists, Option, gateway(), main(), observe(), _parse_overrides(), Path, UUID (+16 more)

### Community 8 - "Community 8"
Cohesion: 0.12
Nodes (21): CompletionResponse, CliProvider, _finish_reason(), parse_codex_jsonl(), parse_grok_json(), Decimal, HealthReport, Path (+13 more)

### Community 9 - "Community 9"
Cohesion: 0.27
Nodes (25): _borrower_deposit(), borrower_state(), BorrowerState, capital_cents(), credit_score_bp(), decide(), decide_with_underwriting(), LoanRequest (+17 more)

### Community 10 - "Community 10"
Cohesion: 0.06
Nodes (31): additionalProperties, properties, required, type, additionalProperties, maximum, minimum, type (+23 more)

### Community 11 - "Community 11"
Cohesion: 0.16
Nodes (18): HealthReport, ProviderError, ProviderPermanent, ProviderRateLimited, ProviderTimeout, ProviderTransient, Provider deadline expired., Provider request failed. (+10 more)

### Community 12 - "Community 12"
Cohesion: 0.16
Nodes (22): BankingEngine, Emit, WithdrawalRequest, apply_pending_policy(), CentralContext, _credit_context(), discount_window(), _originate_interbank() (+14 more)

### Community 13 - "Community 13"
Cohesion: 0.15
Nodes (41): Action, ActionType, null_action(), StrEnum, AccountParams, AcquireParams, ActionBudget, ApplyForJobParams (+33 more)

### Community 14 - "Community 14"
Cohesion: 0.20
Nodes (22): BudgetPlan, _combine_legs(), cpi_bp(), _deposit_account(), _district_distance(), GoodsContext, load_skus(), plan_budget() (+14 more)

### Community 15 - "Community 15"
Cohesion: 0.07
Nodes (26): lucide-react, react, react-dom, @types/react, @types/react-dom, typescript, vite, @vitejs/plugin-react (+18 more)

### Community 16 - "Community 16"
Cohesion: 0.12
Nodes (23): cache_key(), CacheMissInReplay, CacheRecord, CacheRenderMismatch, CacheVersionMismatch, CompletionCache, Any, Cached content was rendered from different prompt text. (+15 more)

### Community 17 - "Community 17"
Cohesion: 0.08
Nodes (44): IntEnum, deliberate_decide(), Deliberation, render_prompt(), AgentBrief, build_observations(), _legal_actions(), Observation (+36 more)

### Community 18 - "Community 18"
Cohesion: 0.18
Nodes (19): FastAPI, repo_git_sha(), datetime, Return wall time for operational metadata, never simulation state., utc_now_naive(), create_app(), _freshness(), _json_row() (+11 more)

### Community 19 - "Community 19"
Cohesion: 0.11
Nodes (22): apply_client_message(), live_channel(), LiveClient, LiveHub, Any, UUID, RedisEphemeralPublisher, Redis (+14 more)

### Community 20 - "Community 20"
Cohesion: 0.13
Nodes (19): AgentRecord, allAgents(), DistrictRecord, Freshness, InspectorTrace, liveSocketUrl(), MapAgent, MetricDefinition (+11 more)

### Community 21 - "Community 21"
Cohesion: 0.09
Nodes (22): additionalProperties, items, minItems, type, additionalProperties, properties, required, type (+14 more)

### Community 22 - "Community 22"
Cohesion: 0.09
Nodes (21): DOM, DOM.Iterable, ES2022, src, compilerOptions, allowJs, allowSyntheticDefaultImports, esModuleInterop (+13 more)

### Community 23 - "Community 23"
Cohesion: 0.18
Nodes (12): StoreSettings, HealthReport, MigrationMismatch, The database migration head does not match the application., A reader or unauthorized module attempted a write., WriteForbidden, datetime, Decimal (+4 more)

### Community 24 - "Lane"
Cohesion: 0.12
Nodes (23): build_lanes(), Lane, AsyncClient, UUID, cli_extra_bool(), cli_extra_int(), cli_extra_str(), Any (+15 more)

### Community 25 - "Community 25"
Cohesion: 0.09
Nodes (42): _annual_difference_sign_changes(), _annual_means(), _annual_terminal_values(), evaluate_v1(), evaluate_v2(), evaluate_v3(), _failure_summary(), GateResult (+34 more)

### Community 26 - "Community 26"
Cohesion: 0.25
Nodes (7): Delivered, Engineering evidence, Gates still open, Live calibration evidence, Live-provider stage started, M3 Capital acceptance, Stage 3 diagnostic

### Community 27 - "invariants.py"
Cohesion: 0.20
Nodes (18): check_ledger(), check_money(), EconomyView, issued_base_money_cents(), m0_cents(), m1_cents(), Protocol, Result (+10 more)

### Community 28 - "Community 28"
Cohesion: 0.17
Nodes (6): Emit, decay_skill_bp(), LabourMarket, progressive_income_tax_cents(), Emit, NewEvent

### Community 29 - "goods.py"
Cohesion: 0.15
Nodes (17): is_ephemeral(), KindError, KindRange, KindSpec, Persistence, Any, StrEnum, range_for() (+9 more)

### Community 30 - "Community 30"
Cohesion: 0.23
Nodes (7): BlobStore, Checkpoint, Checkpointable, CheckpointManager, Any, Protocol, UUID

### Community 31 - "Community 31"
Cohesion: 0.18
Nodes (6): UUID, Repository, CheckpointRepository, Any, datetime, MetricRepository

### Community 32 - "Community 32"
Cohesion: 0.19
Nodes (7): EventLog, MemoryEventSink, MemoryEventReader, FailingSink, test_event_log_seals_and_commits_one_chain(), test_failed_commit_rolls_back_chain_head(), test_memory_reader_filters_and_follows_causes()

### Community 33 - "Community 33"
Cohesion: 0.06
Nodes (26): AgentPopulation, Any, Settings, labour_force(), LabourForce, _skill_bp(), _skill_mapping(), skill_value_bp() (+18 more)

### Community 34 - "EventRepository"
Cohesion: 0.20
Nodes (14): write_living_city_projections(), UUID, rebuild_stored_run(), replay_stored_run(), ReplayReport, resume_stored_run(), ResumeReport, verify_stored_run() (+6 more)

### Community 35 - "RuntimeConfig"
Cohesion: 0.16
Nodes (13): MechanismError, A mechanism registration is invalid., A runtime policy overlay violates temporal rules., RuntimeOverlayError, Enactment, LayeredOverlay, Any, Settings-backed overlay with deterministic tick-keyed enactments. (+5 more)

### Community 36 - "load_settings"
Cohesion: 0.10
Nodes (36): Counter, active_mechanisms(), mechanism_manifest(), MechanismSpec, config_hash(), _config_payload(), config_yaml(), _deep_merge() (+28 more)

### Community 37 - "Community 37"
Cohesion: 0.06
Nodes (33): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+25 more)

### Community 38 - "Community 38"
Cohesion: 0.06
Nodes (33): 10. Error handling, 11. Performance targets and budget, 12. Testing strategy, 13. What we borrowed from Buzz, concretely, 1. Design principles, 2.1 Process model (v1), 2. System topology, 3.1 Envelope (+25 more)

### Community 39 - "Community 39"
Cohesion: 0.20
Nodes (9): vite.config.ts, compilerOptions, allowImportingTsExtensions, composite, module, moduleResolution, noEmit, skipLibCheck (+1 more)

### Community 41 - "Community 41"
Cohesion: 0.10
Nodes (15): redundancy_order(), allocate(), Split an integer pool exactly with the deterministic largest-remainder rule., AcquisitionState, BankruptcyCaseState, PitchState, StartupState, TermSheetState (+7 more)

### Community 42 - "Community 42"
Cohesion: 0.60
Nodes (4): BoundLogger, configure_logging(), get_logger(), UUID

### Community 43 - "Community 43"
Cohesion: 0.83
Nodes (3): main(), Path, violations()

### Community 53 - "Community 53"
Cohesion: 0.10
Nodes (24): AccountCode, Deterministic economic institutions for POLIS., Account, account_id(), CommitmentLedger, Entry, LedgerError, LedgerRepository (+16 more)

### Community 63 - "POLIS — Data Model"
Cohesion: 0.07
Nodes (29): 0. Conventions, 10. Research and observability, 11. Storage estimates and retention, 12. Projection rebuild, 1.1 `runs`, 1.2 `events` — the log, 1.3 `llm_calls`, 1.4 `completion_cache` (+21 more)

### Community 64 - "C24 — Metric catalogue, invariants, validity gates, experiment harness, replay, exports"
Cohesion: 0.07
Nodes (29): 10. Configuration keys, 11a. Acceptance criteria — C24a (gates M1), 11b. Acceptance criteria — C24b, 12a. Tests to write — C24a, 12b. Tests to write — C24b, 13. Definition of done, 14. Traps, 1. Context (+21 more)

### Community 65 - "C23 — Observatory: live map, agent inspector, causal explorer, run comparison"
Cohesion: 0.07
Nodes (28): 10. Configuration keys, 11a. Acceptance criteria — C23a (gates M1), 11b. Acceptance criteria — C23b, 12a. Tests to write — C23a, 12b. Tests to write — C23b, 13. Definition of done, 14. Traps, 1. Context (+20 more)

### Community 66 - "POLIS — Product Requirements Document"
Cohesion: 0.07
Nodes (27): 10. Key product decisions (locked), 11. Risks and open questions, 12. What "done" looks like for v1.0, 1. One-paragraph summary, 2.1 The gap, 2.2 Why now, 2. Why build this, 3. Research questions (+19 more)

### Community 67 - "POLIS — Agent Specification"
Cohesion: 0.07
Nodes (27): 10. Reflect mode, 11. Action validation (PHASE 4), 12.1 Birth, 12.2 Ageing and stages, 12.3 Death, 12. Lifecycle, 13. Prompt asset discipline, 14. M1 calibration decisions (+19 more)

### Community 68 - "test_ventures.py"
Cohesion: 0.10
Nodes (25): ActionOrigin, make_action(), Any, GoodsEngine, active_employment(), match_score_bp(), Occupation, visibility_slice() (+17 more)

### Community 69 - "C25 — Scenario / shock DSL, signed researcher injection"
Cohesion: 0.08
Nodes (25): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+17 more)

### Community 70 - "C08 — Memory stream, retrieval, reflection, embeddings"
Cohesion: 0.08
Nodes (23): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+15 more)

### Community 71 - "C16 — Communication, social graph, social media, feed algorithms"
Cohesion: 0.08
Nodes (23): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+15 more)

### Community 72 - "C17 — News outlets, journalism, claim checking, belief dynamics"
Cohesion: 0.08
Nodes (23): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+15 more)

### Community 73 - "C20 — Households, partnering, fertility, migration, death settlement, inheritance"
Cohesion: 0.08
Nodes (23): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+15 more)

### Community 74 - "C10 — Action schema, validators, budget, resolution dispatch"
Cohesion: 0.09
Nodes (22): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+14 more)

### Community 75 - "C18 — Parties, elections, voting, offices, the policy engine"
Cohesion: 0.09
Nodes (22): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+14 more)

### Community 76 - "C19 — Crime, detection, police, courts, judgments, incarceration"
Cohesion: 0.09
Nodes (22): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+14 more)

### Community 77 - "C09 — Salience scoring, cognition routing, deliberate, reflect"
Cohesion: 0.09
Nodes (21): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+13 more)

### Community 78 - "POLIS implementation notes"
Cohesion: 0.08
Nodes (24): Bank resolution and tax arrears, Bootstrap issuance boundary, Bounded reflection backlog, Coding CLIs are bounded provider probes, not simulation workers, CPI base includes contemporaneous policy, Event stakes are not chronic need pressure, Genesis deposit and central-bank settlement, Goods-kind renumbering (+16 more)

### Community 79 - "1.6 Worked examples — the legs"
Cohesion: 0.10
Nodes (21): 1.1 Units and the fundamental representation, 1.2 Rule L1 — the ledger holds money and money-denominated claims only, 1.3.1 Why a bank's interest income needs no leg, 1.3 Chart of accounts, 1.4.1 Sequencing and the tick buffer, 1.4.2 Helper: `transfer`, 1.4 The `post_transaction` contract, 1.5 Money creation and destruction (+13 more)

### Community 80 - "Components"
Cohesion: 0.10
Nodes (19): Buttons, Colors, Components, Design System: POLIS, Do:, Do's and Don'ts, Don't:, Elevation & Depth (+11 more)

### Community 81 - "Event"
Cohesion: 0.16
Nodes (6): FirmEngine, Emit, split_labour_by_revenue(), Append-only event log contracts., UUID, Event

### Community 82 - "Clock"
Cohesion: 0.29
Nodes (17): _build(), _emit(), _order(), Any, test_p10_price_time_priority_fills_earlier_order_first(), test_p11_short_position_never_exceeds_configured_cap(), test_p12_cancel_releases_exact_remaining_reservation(), test_p1_match_cycle_leaves_no_crossed_book() (+9 more)

### Community 83 - "EventReader"
Cohesion: 0.21
Nodes (15): ancestors(), CausalNode, descendants(), explain(), has_ancestor_in_range(), Any, UUID, EventQuery (+7 more)

### Community 84 - "C02 — Event log, kind registry, hash chain"
Cohesion: 0.11
Nodes (17): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+9 more)

### Community 85 - "C03 — Postgres schema, migrations, repositories, partitioning"
Cohesion: 0.11
Nodes (17): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+9 more)

### Community 86 - "C04 — Clock, tick loop, RNG registry, scheduler, invariants, checkpoints"
Cohesion: 0.12
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 87 - "C05 — LLM router, providers, completion cache, budget, structured output"
Cohesion: 0.13
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 88 - "C07 — Agent state, traits, needs, skills, reflex policy"
Cohesion: 0.12
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 89 - "C13 — Limit order book, matching, market data"
Cohesion: 0.12
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 90 - "C15 — Startups, VC, funding rounds, M&A, bankruptcy"
Cohesion: 0.12
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 91 - "C01 — Repo scaffold, config system, CLI"
Cohesion: 0.13
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 92 - "C06 — Grid generation, places, pathfinding, movement, housing"
Cohesion: 0.12
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 93 - "C11 — Ledger, labour market, firms and production"
Cohesion: 0.13
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 94 - "C12 — Goods market, consumption, CPI"
Cohesion: 0.12
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 95 - "C14 — Banks, credit, central bank, treasury, monetary policy"
Cohesion: 0.13
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 96 - "C21 — Schools, curricula, enrolment, skill accrual"
Cohesion: 0.12
Nodes (15): 10. Configuration keys, 11. Acceptance criteria, 12. Tests to write, 13. Definition of done, 14. Traps, 1. Context, 2. Required reading, 3. Scope — in (+7 more)

### Community 97 - "POLIS — Economy Specification"
Cohesion: 0.14
Nodes (13): 0.1 Required amendments to `03-DATA-MODEL.md`, 0.2 Requested additions outside this document's authority, 0. Conventions binding on this document, 12. Macro metrics, 14. Threats and failure modes, 15. Action-type coverage, 16.1 PHASE 5 — action resolution (every tick, fixed order per `02-ARCHITECTURE.md §5.1`), 16.2 PHASE 7 — scheduled institutional steps, fixed internal order (+5 more)

### Community 98 - "6. Exchange"
Cohesion: 0.15
Nodes (13): 6.10 Short selling, 6.11 IPO listing mechanics, 6.12 Property tests, 6.1 Event kinds (exchange), 6.2 Sessions, 6.3 Order types and admission, 6.4 Arrival ordering and time priority, 6.5 Matching algorithm (+5 more)

### Community 99 - "7. Banking and credit"
Cohesion: 0.15
Nodes (13): 7.10 Interbank market, 7.11 Bank failure and resolution, 7.12 How a credit cycle can emerge, 7.1 Event kinds (banking, monetary, treasury finance), 7.2 Bank balance sheet, 7.3 Deposits, reserves, and the reserve constraint, 7.4 Underwriting and credit scoring, 7.5 Origination, accrual, amortisation (+5 more)

### Community 100 - "8. Law and crime"
Cohesion: 0.15
Nodes (13): 8.10 Deterrence as the object of study, 8.11 Civil suits, 8.12 Kinds 13000–13999, 8.1 The taxonomy, 8.2 The legality gate flags, it does not block, 8.3 Material non-public information, defined deterministically, 8.4 Detection, 8.5 Reporting (+5 more)

### Community 101 - "WorldStateView"
Cohesion: 0.10
Nodes (5): Invariant, Protocol, StrEnum, Severity, WorldStateView

### Community 102 - "POLIS — World Specification"
Cohesion: 0.17
Nodes (11): 10. Degradation: zone-only mode, 12. Threats and failure modes, 13.1 ActionType request, 13. Event kinds, 14. Configuration, 15. Mechanism register, 1. Why a grid earns its cost, 2.1 Algorithm (+3 more)

### Community 103 - "3. Labour market"
Cohesion: 0.17
Nodes (12): 3.10 Unemployment: the formal definition (threat T11), 3.11 Why search friction here does not analytically imply a Beveridge curve (threat T6), 3.1 Event kinds, 3.2 Vacancy posting, 3.3 Occupations, 3.4 Search, visibility, and application, 3.5 Match scoring from the 14-skill vector, 3.6 Offer, negotiation, acceptance (+4 more)

### Community 104 - "4. Firms and production"
Cohesion: 0.17
Nodes (12): 4.10 Self-employment, 4.11 The mechanical baseline (`--reflex-only`), 4.1 Event kinds (firms), 4.2 Production function, 4.3 Productivity, 4.4 Capital and inventory, 4.5 Price setting, 4.6 Firm decisions: LLM versus mechanical (+4 more)

### Community 105 - "8. Ventures"
Cohesion: 0.17
Nodes (12): 8.10 The venture liquidation waterfall, 8.11 Failure, 8.1 Event kinds (ventures), 8.2 Startup formation, 8.3 Burn rate and runway, 8.4 VC fund structure, 8.5 Pitch and evaluation (`VC_EVAL`), 8.6 Valuation (+4 more)

### Community 106 - "4. The MCP server"
Cohesion: 0.17
Nodes (12): 4.10 `polis_wait_for_tick`, 4.11 Error envelope, 4.1 Deployment modes and key custody, 4.2 Tool surface and parity register, 4.3 `polis_act`, 4.4 `polis_observe`, 4.5 `polis_recall`, 4.6 `polis_remember` (+4 more)

### Community 107 - "10. Social metrics"
Cohesion: 0.18
Nodes (11): 10.10 Real-world analogues, named separately (T11), 10.1 Polarisation, 10.2 Trust, 10.3 Misinformation, 10.4 Social mobility, 10.5 Network segregation, 10.6 Turnout, 10.7 Crime (+3 more)

### Community 108 - "11. Threats and failure modes"
Cohesion: 0.18
Nodes (11): 11. Threats and failure modes, F10 — Budget-induced demographic collapse, F1 — Opinion monoculture, F2 — Nobody commits crimes, F3 — Elections that change nothing, F4 — The model will not generate a falsehood, F5 — News that just restates the event log, F6 — The feed algorithm has no effect (a B1 null) (+3 more)

### Community 109 - "3. Social media platform"
Cohesion: 0.18
Nodes (11): 3.1 Objects and actions, 3.2 The feed: candidate pool, 3.3 The four ranking functions, 3.4 Reach, impressions, and virality, 3.5 Ledger contact, 3.6 Kinds 11000–11029, 3. Social media platform, `adversarial` (+3 more)

### Community 110 - "4. News and journalism"
Cohesion: 0.18
Nodes (11): 4.10 Kinds 11030–11069, 4.1 Outlets, 4.2 What a reporter can see, 4.3 Newsworthiness and the story list, 4.4 Writing, 4.5 The claim-checking procedure, 4.6 Editorial process, 4.7 Distribution and reach (+3 more)

### Community 111 - "POLIS — External Agent Protocol"
Cohesion: 0.18
Nodes (10): 13. Event kinds 20000–20999, 14. Configuration, 15. Conformance checklist, 5.1 Endpoints, 5.2 WebSocket, 5. REST and WebSocket, 9.1 Minimal working example, 9.2 `polis-agent-cli` (+2 more)

### Community 112 - "1. The metric catalogue"
Cohesion: 0.18
Nodes (11): 1.10 Drift detection, 1.1 Registration contract, 1.2 Standing caveat, reproduced in every output, 1.3 Economic metrics, 1.4 Social metrics, 1.5 Political metrics, 1.6 Legal metrics, 1.7 Demographic metrics (+3 more)

### Community 113 - "4. The scenario / shock DSL"
Cohesion: 0.18
Nodes (11): 4.10 Scenario CLI, 4.1 Shape and lineage, 4.2 Trigger types, 4.3 Step / action types, 4.4 Signing and recording, 4.5 A scenario may not violate invariants, 4.6 Kinds 99000–99999, 4.7 Worked scenario A — recession (+3 more)

### Community 114 - "Product"
Cohesion: 0.18
Nodes (10): Brand Commitments, Capabilities and Constraints, Evidence on Hand, Operating Context, Platform, Positioning, Product, Product Principles (+2 more)

### Community 115 - "useUrlParam"
Cohesion: 0.18
Nodes (11): AgentsView(), CausalView(), ChartsView(), CompareView(), formatMoney(), InspectorView(), MapView(), MetricLine() (+3 more)

### Community 116 - "POLIS — Society Specification"
Cohesion: 0.20
Nodes (9): 12. Scheduled steps and their phases, 13. Implementation checklist, 1.1 The three speech actions, 1.2 Attention: who actually hears an utterance, 1.3 From utterance to perception to memory, 1.4 Conversation is turn-based across ticks, 1.5 Kinds 10000–10039, 1. Communication (+1 more)

### Community 117 - "fiscal.py"
Cohesion: 0.32
Nodes (16): assess_taxes(), close_budget(), collect_taxes(), convert_arrears(), finance_deficit(), fiscal_step(), FiscalContext, government_transfer_legs() (+8 more)

### Community 118 - "README.md"
Cohesion: 0.12
Nodes (22): K, canonical_bytes(), canonical_json(), Any, T, round6(), round_floats(), catalogue_manifest() (+14 more)

### Community 119 - "POLIS — Implementation Chunks"
Cohesion: 0.22
Nodes (9): 0. Before you touch any chunk, 1. Brief format, 2. Milestones, 3. Dependency graph, 4. Chunk index, 5. Handback contract, 6. Working notes for coding agents, Ground rules that apply to every chunk (+1 more)

### Community 120 - "5. Belief dynamics"
Cohesion: 0.22
Nodes (9): 5.1 Proposition vocabulary, 5.2 The four channels, 5.3 Source trust weighting, 5.4 The update rule, 5.5 Validating LLM-authored belief updates, 5.6 Trust tracks accuracy, 5.7 Measuring polarisation formally, 5.8 Kinds 10060–10069 (declared deviation D-1) (+1 more)

### Community 121 - "9. Demographics"
Cohesion: 0.22
Nodes (9): 9.1 Courtship and partnering, 9.2 Households, 9.3 The fertility hazard, 9.4 Conception to birth, 9.5 Child-rearing costs, 9.6 Inheritance of belief priors, 9.7 Migration, 9.8 Kinds 15000–15999 (+1 more)

### Community 122 - "2. Identity, registration, and lifecycle"
Cohesion: 0.22
Nodes (9): 2.1 Keys and `agent_id`, 2.2 Registration handshake, 2.3 Operator declaration, 2.4 Tables, 2.5 Driver vs kind, 2.6 Embodiment, 2.7 Revocation, 2.8 Abandonment and naturalisation — a citizen does not die because a process died (+1 more)

### Community 123 - "POLIS — Model Routing, Caching, and Cost Control"
Cohesion: 0.22
Nodes (8): 0.1 What this document owns, 0.2 Requests on the shared specification, 0.3 Reconciliations, 0. Scope, and requests on the shared specification, 12. Threats and failure modes, 1. Design goals, POLIS — Model Routing, Caching, and Cost Control, Sources

### Community 124 - "POLIS — Research and Observability"
Cohesion: 0.22
Nodes (8): 11. Paper-readiness checklist, 12. Threats and failure modes for this subsystem, 6.1 The ladder, 6.2 Reading a difference, 6.3 LLM-attributable share, 6. Ablations, 7. The MECHANISM reviewer checklist, POLIS — Research and Observability

### Community 125 - "invariants.py"
Cohesion: 0.27
Nodes (13): _cap_table(), _capital_result(), _chain(), _entropy(), _FunctionInvariant, _interest(), InvariantRunner, _ledger() (+5 more)

### Community 126 - "10. Bankruptcy"
Cohesion: 0.25
Nodes (8): 10.1 Event kinds (bankruptcy), 10.2 Trigger conditions, 10.3 Filing, 10.4 Automatic stay, 10.5 Claims, liquidation, and the priority waterfall, 10.6 Discharge and its effects, 10.7 Interaction with agent death (`04-AGENT-SPEC.md §12.3`), 10. Bankruptcy

### Community 127 - "0. Scope, ownership, and conventions"
Cohesion: 0.25
Nodes (8): 0.1 What this document owns, 0.2 Resolution order and visibility, 0.3 Money, 0.4 Entity ID prefixes, 0.5 Declared deviations and requested additions, 0.6 RNG namespaces, 0.7 Configuration, 0. Scope, ownership, and conventions

### Community 128 - "6. Politics"
Cohesion: 0.25
Nodes (8): 6.1 Parties, 6.2 Offices, 6.3 Candidacy, 6.4 Campaigning, 6.5 The vote model, 6.6 Election mechanics, 6.7 Kinds 12000–12029, 6. Politics

### Community 129 - "7. Policy engine"
Cohesion: 0.25
Nodes (8): 7.1 The runtime overlay, 7.2 The closed set of policy-controllable parameters, 7.3 From proposal to enactment, 7.4 Policy cannot violate invariants, 7.5 Fiscal identity, 7.6 Why the change is measurable, 7.7 Kinds 12030–12049, 7. Policy engine

### Community 130 - "6. Tick synchronisation"
Cohesion: 0.25
Nodes (8): 6.1 Timeline within a tick, 6.2 The observation push, 6.3 The deadline window, 6.4 On a miss, 6.5 `pause_for_external` — debug mode, 6.6 Measurement, 6.7 Liveness gate, 6. Tick synchronisation

### Community 131 - "5. The completion cache"
Cohesion: 0.25
Nodes (8): 5.1 Key construction, 5.2 Storage layout, 5.3 Modes, 5.4 Hit-rate expectations, 5.5 Cache warming, 5.6 Invalidation, 5.7 Publication as a reproducibility artefact, 5. The completion cache

### Community 132 - "3. The experiment harness"
Cohesion: 0.25
Nodes (8): 3.1 Pre-registration, and why, 3.2 Experiment definition, 3.3 `polis sweep` semantics, 3.4 Cost estimation before launch, 3.5 Parallel execution, 3.6 Resumability, 3.7 Why the cache makes sweeps cheap, 3. The experiment harness

### Community 133 - "3. Districts and endogenous differentiation"
Cohesion: 0.29
Nodes (7): 3.1 Archetypes, 3.2 `MECHANISM world.rent_response` — rent responds to demand, 3.3 `MECHANISM world.crime_response` — crime responds to enforcement and poverty, 3.4 `MECHANISM world.school_funding` — school quality responds to the local tax base, 3.5 `MECHANISM world.land_value` — land value capitalises rent, amenity, and school quality, 3.6 `MECHANISM world.amenity_response` — amenity responds to the open place mix, 3. Districts and endogenous differentiation

### Community 134 - "5. Movement"
Cohesion: 0.29
Nodes (7): 5.1 State, 5.2 `travel_ticks` derivation, 5.3 Additive indexes, 5.4 Resolution — PHASE 5, step 1, 5.5 Perception in transit, 5.6 Congestion — explicitly not modelled, 5. Movement

### Community 135 - "7. Housing and rent"
Cohesion: 0.29
Nodes (7): 7.1 Representation, 7.2 Rent setting, 7.3 Allocation — PHASE 7, sim-weekly, 7.4 Eviction, 7.5 Homelessness, 7.6 Tie to household formation, 7. Housing and rent

### Community 136 - "2. Determinism, arithmetic, and shared machinery"
Cohesion: 0.29
Nodes (7): 2.1 Integer arithmetic rules, 2.2 Basis-point helpers, 2.3 The largest-remainder allocator, 2.4 Force-routed obligations owned by the economy, 2.5 RNG namespaces owned by `polis/economy/`, 2.6 Economy invariants, 2. Determinism, arithmetic, and shared machinery

### Community 137 - "5. Goods market"
Cohesion: 0.29
Nodes (7): 5.1 Event kinds (goods), 5.2 SKU catalogue, 5.3 Posted-price search, 5.4 Consumption from needs, 5.5 Budget allocation, 5.6 CPI — the formal construction, 5. Goods market

### Community 138 - "2. Social graph"
Cohesion: 0.29
Nodes (7): 2.1 Types, 2.2 Formation, 2.3 Dynamics, 2.4 Kinds 10040–10059, 2.5 Homophily, 2.6 Network statistics are reported, never perceived, 2. Social graph

### Community 139 - "10. Onboarding a foreign agent"
Cohesion: 0.29
Nodes (7): 10.1 What lives where, 10.2 Claude Code, 10.3 Hermes, 10.4 OpenClaw, 10.5 A custom scaffold, 10.6 Conformance, 10. Onboarding a foreign agent

### Community 140 - "2. The provider abstraction"
Cohesion: 0.29
Nodes (7): 2.1 Protocol, 2.2 Capability matrix (verified 2026-07-24), 2.3 `MiniMaxProvider`, 2.4 `OllamaProvider`, 2.5 `OpenAICompatProvider`, 2.6 `StubProvider` — mandatory test infrastructure, 2. The provider abstraction

### Community 141 - "4. Routing policy"
Cohesion: 0.29
Nodes (7): 4.1 Resolution order, 4.2 Fallback chains, 4.3 Circuit breaker, 4.4 Concurrency, and why Ollama Cloud cannot carry the hot path, 4.5 Ordering and determinism, 4.6 Budget admission and the degradation ladder, 4. Routing policy

### Community 142 - "7. Cost model and budgeting"
Cohesion: 0.29
Nodes (7): 7.1 Unit cost of one deliberate call, 7.2 One sim-year, 1,000 agents, 7.3 Where the $12/sim-year target comes from, 7.4 Sensitivity, 7.5 What each budget buys, 7.6 Prefix caching, 7. Cost model and budgeting

### Community 143 - "8. Prompt management"
Cohesion: 0.29
Nodes (7): 8.1 Layout, 8.2 Version header, 8.3 Hashing, 8.4 Rendering determinism, 8.5 Prohibitions, enforced in CI, 8.6 Paraphrase siblings (V6), 8. Prompt management

### Community 144 - "0. Scope, ownership, conventions"
Cohesion: 0.29
Nodes (7): 0.1 What this document owns, 0.2 What this document does not own, 0.3 The read-only rule, 0.4 Metric storage contract, 0.5 Randomness, 0.6 Required amendments to `03-DATA-MODEL.md`, 0. Scope, ownership, conventions

### Community 145 - "surface-brief-body.md"
Cohesion: 0.29
Nodes (6): Audience and job, Chosen direction, Content and constraints, Memorable moment, Scope and mode, Unresolved decisions

### Community 146 - "web-src-app-tsx.md"
Cohesion: 0.29
Nodes (6): Audience and job, Chosen direction, Content and constraints, Memorable moment, Scope and mode, Unresolved decisions

### Community 147 - "POLIS — Documentation Index"
Cohesion: 0.33
Nodes (5): Chunk map, Document map, How to read these docs, POLIS — Documentation Index, Prior art this design draws on

### Community 148 - "4. Places"
Cohesion: 0.33
Nodes (6): 4.1 Vocabulary, 4.2 Mix, capacity, ownership, 4.3 Opening hours and days, 4.4 Affordances — the locality gate, 4.5 Need restoration, 4. Places

### Community 149 - "8. Time-of-day and weekly rhythm"
Cohesion: 0.33
Nodes (6): 8.1 Calendar, 8.2 The rhythm is an outcome, not a rule, 8.3 Commuting, 8.4 `microscope` vs `chronicle`, 8.5 Weather, 8. Time-of-day and weekly rhythm

### Community 150 - "11. Government finance"
Cohesion: 0.33
Nodes (6): 11.1 Taxes, 11.2 Assessment, collection, and arrears, 11.3 Spending, 11.4 The budget, 11.5 Deficit, bonds, and debt service, 11. Government finance

### Community 151 - "13. Calibration and initial conditions"
Cohesion: 0.33
Nodes (6): 13.1 Initial conditions at tick 0, 13.2 Genesis money issuance, 13.3 Making sure the economy neither explodes nor dies, 13.4 Parameters most likely to need tuning, ranked, 13.5 Calibration protocol, 13. Calibration and initial conditions

### Community 152 - "9. Mergers and acquisitions"
Cohesion: 0.33
Nodes (6): 9.1 Event kinds (M&A), 9.2 Valuation, 9.3 Offer, 9.4 Approval, 9.5 Integration or asset sale, 9. Mergers and acquisitions

### Community 153 - "11. The arena and scorecard"
Cohesion: 0.33
Nodes (6): 11.1 What is being compared, 11.2 Dimensions, 11.3 Reporting rules, 11.4 Eligibility, 11.5 What the scorecard is not, 11. The arena and scorecard

### Community 154 - "3. Canonical serialisation and the signature scheme"
Cohesion: 0.33
Nodes (6): 3.1 Byte layout, 3.2 Other signed blobs, 3.3 Replay protection, 3.4 Clock and tick tolerance, 3.5 Reference implementation, 3. Canonical serialisation and the signature scheme

### Community 155 - "8. Sandboxing and information security"
Cohesion: 0.33
Nodes (6): 8.1 The rule, 8.2 Exposed, 8.3 Not exposed, and why, 8.4 Enforcement, 8.5 Side channels, 8. Sandboxing and information security

### Community 156 - "10. Model-robustness protocol (V7)"
Cohesion: 0.33
Nodes (6): 10.1 What counts as a family, 10.2 Held fixed, 10.3 Varied, 10.4 Validity preconditions, 10.5 Comparison, 10. Model-robustness protocol (V7)

### Community 157 - "10. Statistical practice"
Cohesion: 0.33
Nodes (6): 10.1 The unit of replication is the seed, 10.2 Intervals and effect sizes, 10.3 Multiple comparisons, 10.4 The scale ladder (T7), 10.5 Reporting standard, 10. Statistical practice

### Community 158 - "8. The Observatory"
Cohesion: 0.33
Nodes (6): 8.1 Non-negotiables, 8.2 Views, 8.3 API endpoints, 8.4 WebSocket protocol, 8.5 Performance isolation, 8. The Observatory

### Community 159 - "M1 stabilization and multi-seed validation"
Cohesion: 0.33
Nodes (5): Finding and correction, Formal validation, M1 stabilization and multi-seed validation, Reproducibility, Wellbeing interpretation

### Community 160 - "9. World services"
Cohesion: 0.40
Nodes (5): 9.1 The public API, 9.2 `WorldParams`, 9.3 Money: the world computes, the economy posts, 9.4 Land ownership and public infrastructure, 9. World services

### Community 161 - "1. Design goals and the fairness contract"
Cohesion: 0.40
Nodes (5): 1.1 Goals, 1.2 The fairness contract, 1.3 What an external agent can and cannot do, 1.4 Non-goals, 1. Design goals and the fairness contract

### Community 162 - "3. Call purposes"
Cohesion: 0.40
Nodes (5): 3.1 The enum, 3.2 The purpose table, 3.3 Why each recommendation, 3.4 Routing config, 3. Call purposes

### Community 163 - "6. Structured output"
Cohesion: 0.40
Nodes (5): 6.1 Three tiers, 6.2 The repair loop, 6.3 Parse-failure accounting, 6.4 Practical guidance, 6. Structured output

### Community 164 - "9. Token accounting and telemetry"
Cohesion: 0.40
Nodes (5): 9.1 Per call, 9.2 Per tick, 9.3 Per run, 9.4 Dashboards, 9. Token accounting and telemetry

### Community 165 - "2. Invariants and validity gates as executable checks"
Cohesion: 0.40
Nodes (5): 2.1 Two families, two jobs, 2.2 INV-* as executable checks, 2.3 V1–V7 as executable procedures, 2.4 The gate report, 2. Invariants and validity gates as executable checks

### Community 166 - "5. Replay and reproducibility"
Cohesion: 0.40
Nodes (5): 5.1 The reproducibility tuple, 5.2 The three commands, 5.3 The reproducibility package, 5.4 Third-party reproduction of a figure, with zero API spend, 5. Replay and reproducibility

### Community 167 - "9. Data export"
Cohesion: 0.40
Nodes (5): 9.1 `polis export`, 9.2 Parquet schema, 9.3 What a researcher actually touches, 9.4 Starter notebooks, 9. Data export

### Community 168 - "M1 Living City acceptance"
Cohesion: 0.33
Nodes (5): Acceptance evidence, Calibration decisions, Cross-platform portability update, Delivered, M1 Living City acceptance

### Community 169 - "M2 Economy acceptance"
Cohesion: 0.40
Nodes (4): Acceptance evidence, Delivered, M2 Economy acceptance, Scientific boundary

### Community 170 - "6. Co-location and encounter"
Cohesion: 0.50
Nodes (4): 6.1 The occupancy index, 6.2 Ranking and the cap of 12, 6.3 Why this is the payoff, 6. Co-location and encounter

### Community 171 - "12. Threats and failure modes"
Cohesion: 0.50
Nodes (4): 12.1 Failure modes, 12.2 Prompt injection: in-world text is untrusted input, 12.3 Simulation awareness (T3) and external agents, 12. Threats and failure modes

### Community 172 - "7. Rate limiting and abuse"
Cohesion: 0.50
Nodes (4): 7.1 Limits, 7.2 Strike ladder, 7.3 How the gateway protects the tick rate, 7. Rate limiting and abuse

### Community 173 - "11. Local versus cloud"
Cohesion: 0.50
Nodes (4): 11.1 Deployment modes, 11.2 Why `all_local` is attractive and what it costs, 11.3 Mixed deployment in the router, 11. Local versus cloud

### Community 174 - "evidenceForAgent"
Cohesion: 0.50
Nodes (4): DecisionPath(), evidenceForAgent(), EvidenceRail(), TraceBreadcrumb()

### Community 175 - "11. Rendering contract"
Cohesion: 0.67
Nodes (3): 11.1 Static, over HTTP, once per run, 11.2 Live, over WebSocket, ephemeral kinds only, 11. Rendering contract

### Community 182 - "ExchangeEngine"
Cohesion: 0.14
Nodes (7): _bp_ceil(), _coalesce(), ExchangeEngine, Any, Emit, Deterministic, reservation-backed exchange implementation., SecurityState

### Community 183 - "Ledger"
Cohesion: 0.15
Nodes (23): ExchangeState, _index_for(), Ledger, ApplicationState, BankState, BasketState, BondState, DurableState (+15 more)

### Community 184 - "NullWorldState"
Cohesion: 0.15
Nodes (3): _money(), NullWorldState, _population()

### Community 185 - "load_settings"
Cohesion: 0.31
Nodes (11): LiveRunner, call_rows(), main(), percentile(), preflight(), Any, Path, run_resumable() (+3 more)

### Community 186 - "partition.py"
Cohesion: 0.33
Nodes (8): partition_name(), PartitionManager, UUID, run_suffix(), validate_ident(), test_accepted_identifiers_match_the_closed_grammar(), test_identifier_gate_rejects_unsafe_names(), test_partition_names_are_stable_and_bounded()

### Community 187 - "Scheduler"
Cohesion: 0.08
Nodes (17): ClockSettings, Clock, profile_from_settings(), Any, datetime, SimDuration, Deterministic tick kernel., Cadence (+9 more)

### Community 188 - "rebuild.py"
Cohesion: 0.17
Nodes (15): Persistence layer failure., StoreError, Projection, ProjectionContext, ProjectionRouter, Any, AsyncConnection, Protocol (+7 more)

### Community 190 - "stream.py"
Cohesion: 0.15
Nodes (16): MemoryType, embed_text(), MemoryRecord, MemoryStore, _normalise(), Any, ReflectionInsight, Retrieval (+8 more)

### Community 191 - "central.py"
Cohesion: 0.30
Nodes (18): LoanDecision, build(), configured(), emit_at(), found_startup(), test_absorb_integration_pays_redundancy_and_transfers_loan_obligor(), test_acquisition_anchor_and_synergy_ablations_change_runtime_behavior(), test_asset_sale_leaves_loan_with_insolvent_target_shell() (+10 more)

### Community 192 - "test_router_repair.py"
Cohesion: 0.24
Nodes (4): Decimal, HealthReport, RepairProvider, test_router_renders_schema_repairs_and_aggregates_usage()

### Community 193 - "MechanicalPolicy"
Cohesion: 0.21
Nodes (7): Provider-neutral LLM routing., Purpose, StrEnum, LLMRouter, test_router_cache_and_repeat_are_deterministic(), test_router_replays_from_file_cache_without_a_provider(), test_stub_router_succeeds_with_network_blocked()

### Community 194 - "test_exchange_properties.py"
Cohesion: 0.21
Nodes (17): ask_priority(), bid_priority(), call_auction(), continuous_matches(), crosses(), Fill, uncross(), HoldingState (+9 more)

### Community 195 - "test_stub.py"
Cohesion: 0.12
Nodes (24): PolisError, Exception, Base exception for expected POLIS failures., Capabilities, CompletionRequest, AsyncClient, legal_actions_from_prompt(), _pick() (+16 more)

### Community 196 - "CacheSettings"
Cohesion: 0.26
Nodes (6): LocalBlobStore, open_blobs(), Path, Path, test_blob_key_cannot_escape_root(), test_local_blob_round_trip()

### Community 199 - "engine.py"
Cohesion: 0.38
Nodes (9): assert_json_safe(), PayloadSchemaError, Any, schema_hash(), validate_payload(), validator_for(), _walk(), test_non_json_payload_is_rejected() (+1 more)

### Community 203 - "hashing.py"
Cohesion: 0.47
Nodes (8): canonical_event_bytes(), event_hash(), Any, datetime, UUID, recompute(), seal(), verify_event()

### Community 204 - "Any"
Cohesion: 0.29
Nodes (3): Provider, Decimal, Protocol

### Community 205 - "live_provider_smoke.py"
Cohesion: 0.67
Nodes (5): main(), Any, Path, smoke(), write_json()

## Knowledge Gaps
- **1157 isolated node(s):** `$schema`, `type`, `action`, `reasoning`, `confidence` (+1152 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Event` connect `Event` to `Community 5`, `Community 9`, `Community 12`, `Community 14`, `Community 17`, `Community 19`, `Community 25`, `invariants.py`, `Community 28`, `goods.py`, `Community 32`, `Community 33`, `EventRepository`, `Community 41`, `Community 53`, `ExchangeEngine`, `Scheduler`, `rebuild.py`, `test_exchange_properties.py`, `test_ventures.py`, `hashing.py`, `Clock`, `EventReader`, `fiscal.py`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Why does `AgentPopulation` connect `Community 33` to `test_exchange_properties.py`, `Community 4`, `Community 5`, `test_ventures.py`, `Community 9`, `Community 41`, `Community 12`, `Community 14`, `Community 17`, `fiscal.py`, `ExchangeEngine`, `Ledger`, `Community 53`, `Community 28`, `EconomyWorldState`, `central.py`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Why does `PolisError` connect `test_stub.py` to `Community 1`, `RuntimeConfig`, `Community 6`, `engine.py`, `Community 8`, `Community 11`, `Any`, `Community 16`, `Community 23`, `Community 53`, `Ledger`, `rebuild.py`, `goods.py`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Are the 53 inferred relationships involving `Settings` (e.g. with `RoutingResult` and `SalienceScore`) actually correct?**
  _`Settings` has 53 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `RngRegistry` (e.g. with `Resolution` and `Reflection`) actually correct?**
  _`RngRegistry` has 36 INFERRED edges - model-reasoned connections that need verification._
- **Are the 56 inferred relationships involving `AgentPopulation` (e.g. with `Resolution` and `AgentBrief`) actually correct?**
  _`AgentPopulation` has 56 INFERRED edges - model-reasoned connections that need verification._
- **What connects `$schema`, `type`, `action` to the rest of the system?**
  _1157 weakly-connected nodes found - possible documentation gaps or missing edges._