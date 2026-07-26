# Graph Report - .  (2026-07-26)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1187 nodes · 3719 edges · 63 communities (51 shown, 12 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 558 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `431fa51b`
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
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 50
- Community 51
- Community 52
- Community 53
- Community 54
- Community 55
- Community 56
- Community 57
- Community 59

## God Nodes (most connected - your core abstractions)
1. `RngRegistry` - 77 edges
2. `Settings` - 66 edges
3. `World` - 62 edges
4. `load_settings()` - 57 edges
5. `Clock` - 57 edges
6. `Event` - 50 edges
7. `AgentPopulation` - 48 edges
8. `ConfigError` - 46 edges
9. `run_living_city()` - 45 edges
10. `Database` - 45 edges

## Surprising Connections (you probably didn't know these)
- `test_canonical_json_rejects_non_primitives()` --indirect_call--> `ConfigError`  [INFERRED]
  tests/unit/config/test_canon.py → polis/config/errors.py
- `test_unknown_key_is_rejected()` --indirect_call--> `ConfigError`  [INFERRED]
  tests/unit/config/test_settings.py → polis/config/errors.py
- `test_rng_has_a_stable_golden_sequence_and_namespaced_streams()` --calls--> `RngRegistry`  [EXTRACTED]
  tests/unit/kernel/test_clock_rng.py → polis/kernel/rng.py
- `test_blob_key_cannot_escape_root()` --indirect_call--> `StoreError`  [INFERRED]
  tests/unit/store/test_blobs.py → polis/store/engine.py
- `test_identifier_gate_rejects_unsafe_names()` --indirect_call--> `StoreError`  [INFERRED]
  tests/unit/store/test_partition.py → polis/store/engine.py

## Import Cycles
- None detected.

## Communities (63 total, 12 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (63): ancestors(), CausalNode, descendants(), explain(), has_ancestor_in_range(), Any, UUID, canonical_event_bytes() (+55 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (45): F, ConfigError, MechanismError, ProfileNotFound, A requested configuration profile does not exist., A mechanism registration is invalid., A runtime policy overlay violates temporal rules., Configuration cannot be loaded or validated. (+37 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (38): K, canonical_bytes(), canonical_json(), Any, T, round6(), round_floats(), sha256_hex() (+30 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (35): AgentsView(), buildingSeeds, CausalView(), ChartsView(), choroplethPalettes, choroplethRanges, CompareView(), DecisionPath() (+27 more)

### Community 4 - "Community 4"
Cohesion: 0.13
Nodes (29): dtype, EducationLevel, EmploymentStatus, PlaceType, _age(), _education(), _employment(), generate_agents() (+21 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (13): reflect_decide(), _candidates(), reflex_decide(), AgentState, ReflexProfile, compute_world_hash(), District, Place (+5 more)

### Community 6 - "Community 6"
Cohesion: 0.10
Nodes (15): _chain(), _entropy(), _FunctionInvariant, Invariant, _ledger(), _money(), NullWorldState, Ok (+7 more)

### Community 7 - "Community 7"
Cohesion: 0.14
Nodes (32): exists, Option, gateway(), main(), observe(), _parse_overrides(), Path, UUID (+24 more)

### Community 8 - "Community 8"
Cohesion: 0.11
Nodes (22): apply_client_message(), live_channel(), LiveClient, LiveHub, Any, UUID, RedisEphemeralPublisher, Redis (+14 more)

### Community 9 - "Community 9"
Cohesion: 0.14
Nodes (19): MemoryType, deliberate_decide(), render_prompt(), embed_text(), MemoryRecord, MemoryStore, _normalise(), Any (+11 more)

### Community 10 - "Community 10"
Cohesion: 0.06
Nodes (31): additionalProperties, properties, required, type, additionalProperties, maximum, minimum, type (+23 more)

### Community 11 - "Community 11"
Cohesion: 0.16
Nodes (19): Exception, PolisError, Base exception for expected POLIS failures., Capabilities, HealthReport, ProviderError, ProviderPermanent, ProviderRateLimited (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.18
Nodes (12): Provider-neutral LLM routing., Purpose, StrEnum, LLMRouter, main(), Any, Path, smoke() (+4 more)

### Community 13 - "Community 13"
Cohesion: 0.19
Nodes (21): ActionOrigin, GateResult, Action, ActionType, make_action(), null_action(), Any, StrEnum (+13 more)

### Community 14 - "Community 14"
Cohesion: 0.16
Nodes (12): AgentBrief, build_observations(), _legal_actions(), Observation, PlaceView, datetime, SelfView, _jaccard_distance() (+4 more)

### Community 15 - "Community 15"
Cohesion: 0.07
Nodes (26): lucide-react, react, react-dom, @types/react, @types/react-dom, typescript, vite, @vitejs/plugin-react (+18 more)

### Community 16 - "Community 16"
Cohesion: 0.15
Nodes (19): CompletionRequest, legal_actions_from_prompt(), _pick(), Any, BaseModel, Decimal, HealthReport, Deterministic mandatory test provider; never performs I/O or reads ambient state (+11 more)

### Community 17 - "Community 17"
Cohesion: 0.19
Nodes (19): IntEnum, Deliberation, Reflection, EphemeralSink, EventSink, Protocol, Phase, PhaseHandler (+11 more)

### Community 18 - "Community 18"
Cohesion: 0.19
Nodes (15): cache_key(), CacheMissInReplay, CacheRecord, CacheRenderMismatch, CacheVersionMismatch, CompletionCache, Any, Cached content was rendered from different prompt text. (+7 more)

### Community 19 - "Community 19"
Cohesion: 0.18
Nodes (12): UUID, run_id_for(), _model_manifest(), _prompt_manifest(), run_persistent(), verify_stored_run(), _event(), EventRepository (+4 more)

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
Cohesion: 0.20
Nodes (12): StoreSettings, HealthReport, MigrationMismatch, The database migration head does not match the application., A reader or unauthorized module attempted a write., WriteForbidden, datetime, Decimal (+4 more)

### Community 24 - "Community 24"
Cohesion: 0.19
Nodes (9): AsyncConnectionPool, Database, Any, AsyncConnection, Persistence and projection repositories., UUID, rebuild(), RebuildReport (+1 more)

### Community 25 - "Community 25"
Cohesion: 0.26
Nodes (15): calibrate(), main(), percentile(), ProgressSink, Any, Path, sample_rate(), write_json() (+7 more)

### Community 26 - "Community 26"
Cohesion: 0.34
Nodes (9): RuntimeConfig, Settings, InvariantRunner, UUID, RunReport, TickLoop, TickReport, run_empty() (+1 more)

### Community 27 - "Community 27"
Cohesion: 0.17
Nodes (3): Clock, Any, datetime

### Community 28 - "Community 28"
Cohesion: 0.41
Nodes (10): Resolution, resolve_actions(), ClockProfile, Location, BlockedMove, Movement, MovementResult, MoveRequest (+2 more)

### Community 29 - "Community 29"
Cohesion: 0.15
Nodes (16): Admission, BudgetGuard, Decimal, StrEnum, _Usage, build_lanes(), Lane, AsyncClient (+8 more)

### Community 30 - "Community 30"
Cohesion: 0.23
Nodes (9): Projection, ProjectionContext, ProjectionRouter, Any, AsyncConnection, Protocol, UUID, register_projection() (+1 more)

### Community 31 - "Community 31"
Cohesion: 0.18
Nodes (6): UUID, Repository, CheckpointRepository, Any, datetime, MetricRepository

### Community 32 - "Community 32"
Cohesion: 0.28
Nodes (5): RoutingResult, Metrics, replay, and research tooling., MetricCollector, MetricPoint, Any

### Community 33 - "Community 33"
Cohesion: 0.35
Nodes (12): Persistence layer failure., StoreError, _clear_projections(), Any, write_living_city_projections(), load_run_settings(), UUID, rebuild_stored_run() (+4 more)

### Community 34 - "Community 34"
Cohesion: 0.33
Nodes (8): partition_name(), PartitionManager, UUID, run_suffix(), validate_ident(), test_accepted_identifiers_match_the_closed_grammar(), test_identifier_gate_rejects_unsafe_names(), test_partition_names_are_stable_and_bounded()

### Community 35 - "Community 35"
Cohesion: 0.27
Nodes (10): FastAPI, datetime, Return wall time for operational metadata, never simulation state., utc_now_naive(), create_app(), _freshness(), _json_row(), Any (+2 more)

### Community 37 - "Community 37"
Cohesion: 0.27
Nodes (4): Agent state and cognition., Any, Needs, Traits

### Community 39 - "Community 39"
Cohesion: 0.20
Nodes (9): vite.config.ts, compilerOptions, allowImportingTsExtensions, composite, module, moduleResolution, noEmit, skipLibCheck (+1 more)

### Community 41 - "Community 41"
Cohesion: 0.25
Nodes (3): Provider, Decimal, Protocol

### Community 42 - "Community 42"
Cohesion: 0.60
Nodes (4): BoundLogger, configure_logging(), get_logger(), UUID

### Community 43 - "Community 43"
Cohesion: 0.83
Nodes (3): main(), Path, violations()

## Knowledge Gaps
- **89 isolated node(s):** `$schema`, `type`, `action`, `reasoning`, `confidence` (+84 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PolisError` connect `Community 11` to `Community 0`, `Community 1`, `Community 33`, `Community 41`, `Community 16`, `Community 18`, `Community 23`, `Community 24`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Why does `run_living_city()` connect `Community 17` to `Community 0`, `Community 1`, `Community 32`, `Community 33`, `Community 4`, `Community 36`, `Community 38`, `Community 7`, `Community 8`, `Community 9`, `Community 12`, `Community 18`, `Community 19`, `Community 25`, `Community 26`, `Community 27`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `Settings` connect `Community 26` to `Community 32`, `Community 1`, `Community 33`, `Community 35`, `Community 36`, `Community 7`, `Community 12`, `Community 14`, `Community 17`, `Community 19`, `Community 29`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `RngRegistry` (e.g. with `Resolution` and `Reflection`) actually correct?**
  _`RngRegistry` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `Settings` (e.g. with `RoutingResult` and `SalienceScore`) actually correct?**
  _`Settings` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `World` (e.g. with `Resolution` and `ActionBudget`) actually correct?**
  _`World` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `Clock` (e.g. with `ConfigError` and `ClockSettings`) actually correct?**
  _`Clock` has 24 INFERRED edges - model-reasoned connections that need verification._