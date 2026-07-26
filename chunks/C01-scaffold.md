# C01 — Repo scaffold, config system, CLI

**M0** · Owner module `polis/config`, `polis/cli` · Depends on: *nothing* · Blocks: **C02 C03 C04 C05 and every subsequent chunk** · Size **S** (≈ 1 day)

---

## 1. Context

Nothing else can be written until the repository has a shape, a config object, a dependency
firewall, and a CLI to hang commands on. This chunk is small in code and enormous in
consequence: every convention that the other 24 chunks obey — module paths, the `Settings`
tree, the `MECHANISM` registry, the canonical JSON serialiser, the import-linter contracts,
the ban on bare `random` — is established here. Get it wrong and 24 chunks inherit the
mistake. There is no simulation logic in this chunk at all.

---

## 2. Required reading

| Source | Why |
|---|---|
| `../docs/02-ARCHITECTURE.md` §7 (module layout), §7.1 (dependency rules), §8 (config), §8.1 (MECHANISM), §4 (determinism) | Binding. §7.1 is transcribed verbatim into `.importlinter`. |
| `../docs/03-DATA-MODEL.md` §0 (conventions), §1.1 (`runs`) | ID prefixes, money rules, the reproducibility tuple this chunk must be able to compute. |
| `../docs/09-MODEL-ROUTING.md` §3.4 (routing config), §5.1 (`canonical_json`), §8.5 (import-linter contract) | The `llm:` config sub-tree and the vendor-isolation contract. |
| `../docs/07-SOCIETY-SPEC.md` §7.1–§7.2 | `RuntimeConfig` API and the closed `POLICY_REGISTRY` parameter names it must accept. |
| `../docs/01-PRD.md` §7.1 | The determinism / throughput / cost targets the CI gate protects. |
| `chunks/README.md` §0, §5 | Ground rules and the handback contract. |

No chunk interfaces are consumed. This is the root.

---

## 3. Scope — in

1. Repository skeleton exactly as `02 §7` (every package directory with `__init__.py`, `py.typed`).
2. `pyproject.toml` (uv-managed, Python 3.12), lockfile, `uv` task recipes.
3. `ruff` (lint + format), `mypy --strict`, `import-linter` contracts, `pytest` config and markers.
4. `docker-compose.yml`: Postgres 17 + `pgvector`, Redis 7, MinIO. `.env.example`.
5. `alembic init` — `migrations/env.py`, `alembic.ini`, an empty base revision. **No table DDL** (C03).
6. `polis/config/`: `settings.py`, `canon.py`, `runtime.py`, `mechanisms.py`, `logging.py`, `paths.py`, `errors.py`, `profiles/`.
7. `polis/cli/`: Typer app with `run resume verify rebuild replay sweep gateway observe` plus stub namespaces.
8. `configs/baseline.yaml` and `configs/smoke.yaml` — complete, loadable, internally consistent.
9. `scripts/lint_determinism.py` and `scripts/lint_prompts.py` (harness + allowlist; C04/C05 extend the rule sets).
10. `.github/workflows/ci.yml`.

## 4. Scope — out

| Not built here | Owner |
|---|---|
| Any table DDL, any Alembic revision with content | C03 |
| `Event`, `kinds.py`, hashing | C02 |
| `Clock`, `RngRegistry`, `det.py`, `TickLoop` | C04 |
| Providers, router, prompt library | C05 |
| Real bodies for CLI commands (`run` prints a resolved plan and exits 0; the rest exit 2) | C02–C05, C24 |
| `POLICY_REGISTRY` contents and the policy loop that calls `RuntimeConfig.enact` | C18 |
| Prompt template files under `prompts/` | C05 |

---

## 5. Interfaces you provide

```python
# polis/config/errors.py
class PolisError(Exception): ...
class ConfigError(PolisError): ...
class ProfileNotFound(ConfigError): ...
class MechanismError(ConfigError): ...
class RuntimeOverlayError(PolisError): ...
```

```python
# polis/config/canon.py
# THE canonicaliser. One implementation in the codebase. polis.kernel.det re-exports it
# (09 §5.1 names that path) and polis.events uses it directly (02 §7.1 forbids events -> kernel).
def canonical_json(obj: Any) -> str:
    """json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).
    Raises ConfigError on non-JSON-primitive leaves (datetime, Decimal, set, bytes, UUID)."""
def canonical_bytes(obj: Any) -> bytes: ...              # canonical_json(obj).encode("utf-8")
def sha256_hex(data: bytes) -> str: ...
def round6(x: float) -> float: ...                        # 02 §4.6
def round_floats(obj: T, dp: int = 6) -> T: ...           # deep; leaves ints untouched
```

```python
# polis/config/settings.py
class RunSettings(BaseModel):
    name: str; seed: int; ticks: int
    checkpoint_interval: int = 500
    retention: Literal["full", "metrics_only"] = "full"
    scale: int | None = None                              # 03 runs.scale; defaults to population.initial_agents
    tags: tuple[str, ...] = ()

class ClockSettings(BaseModel):
    profile: Literal["microscope", "chronicle"] = "microscope"
    ticks_per_sim_day: int = 24
    days_per_sim_year: int = 360
    demographic_acceleration: float = 1.0

class LLMBudgetLine(BaseModel):
    calls_per_tick: int
    tokens_per_tick: int

class LLMBudgetSettings(BaseModel):
    lines: dict[Literal["cognition", "ancillary", "external", "free"], LLMBudgetLine]
    usd_per_run: Decimal = Decimal("60.0")
    usd_halt_multiple: Decimal = Decimal("1.2")
    on_exhaustion: Literal["degrade_to_reflex", "halt"] = "degrade_to_reflex"

class RouteSpec(BaseModel):
    lane: str; model: str
    temperature: float = 0.0
    max_tokens: int = 512
    structured: Literal["constrain", "repair", "none"] = "repair"
    schema_: str | None = Field(default=None, alias="schema")
    template: str
    fallback: tuple[Mapping[str, str], ...] = ()
    last_resort: str = "reflex"

class LaneSettings(BaseModel):
    kind: Literal["minimax", "ollama", "openai_compat", "stub"]
    base_url: str | None = None
    api_key_env: str | None = None
    max_concurrency: int = 8
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    timeout_s: float = 45.0
    structured_output: Literal["schema", "json_mode", "none"] = "schema"
    billing: Literal["token", "gpu_time", "free"] = "token"
    model_version_pin: str | None = None
    price_in_usd_per_mtok: Decimal = Decimal(0)
    price_out_usd_per_mtok: Decimal = Decimal(0)
    price_cached_in_usd_per_mtok: Decimal | None = None
    extra: Mapping[str, Any] = {}                          # stub fault rates, tier, etc.

class CacheSettings(BaseModel):
    mode: Literal["live", "replay", "hybrid"] = "hybrid"
    path: str = "file://./.cache/completions"
    schema_version: int = 1
    verify_render: bool = True
    strict_version: bool = True
    trust: Literal["verify", "trust"] = "verify"
    l0_entries: int = 50_000

class LLMSettings(BaseModel):
    providers: dict[str, LaneSettings]
    budget: LLMBudgetSettings
    routing: dict[str, RouteSpec]                          # keys are Purpose values
    fallback_policy: Literal["permissive", "strict"] = "permissive"
    cache: CacheSettings
    prompt_variant: str | None = None

class StoreSettings(BaseModel):
    dsn: str; reader_dsn: str | None = None
    pool_min: int = 2; pool_max: int = 16
    redis_url: str = "redis://127.0.0.1:6379/0"
    blob_url: str = "file://./.blobs"

class Settings(BaseSettings):
    run: RunSettings
    clock: ClockSettings
    population: PopulationSettings
    world: WorldSettings
    llm: LLMSettings
    salience: SalienceSettings
    mechanisms: dict[str, str]                             # 02 §8.1; values are mechanism ids
    society: SocietySettings
    ablations: AblationSettings
    store: StoreSettings
    telemetry: TelemetrySettings
    model_config = SettingsConfigDict(
        env_prefix="POLIS_", env_nested_delimiter="__", extra="forbid", frozen=True,
    )

def load_settings(path: Path, *, profiles: Sequence[str] = (),
                  overrides: Mapping[str, Any] | None = None) -> Settings: ...
def config_yaml(s: Settings) -> str: ...                   # round-trippable, -> runs.config_yaml
def config_hash(s: Settings) -> str: ...                   # sha256_hex(canonical_bytes(s.model_dump(mode="json")))
def reproducibility_tuple(s: Settings, *, prompt_manifest: Mapping[str, str],
                          model_manifest: Mapping[str, Any]) -> dict[str, str]: ...
```

```python
# polis/config/mechanisms.py
@dataclass(frozen=True, slots=True)
class MechanismSpec:
    id: str; entails: str; config_key: str | None; module: str; qualname: str

MECHANISM_REGISTRY: Final[dict[str, MechanismSpec]]

F = TypeVar("F", bound=Callable[..., Any])
def mechanism(id: str, *, entails: str, config_key: str | None = None) -> Callable[[F], F]:
    """Registers at import time. Duplicate id -> MechanismError. Does not wrap the callable."""
def active_mechanisms(s: Settings) -> dict[str, MechanismSpec]: ...
def mechanism_manifest(s: Settings) -> dict[str, dict[str, str | None]]:
    """-> runs.mechanism_manifest: {id: {entails, config_key, module, qualname}}"""
```

```python
# polis/config/runtime.py   (07 §7.1, spec-signature compatible)
@dataclass(frozen=True, slots=True)
class Enactment:
    parameter: str; value: Any
    enacted_tick: int; effective_tick: int
    policy_id: str; event_seq: int

class RuntimeConfig:
    def __init__(self, base: Settings) -> None: ...
    def get(self, parameter: str, tick: int) -> Any: ...
    def enact(self, parameter: str, value: Any, effective_tick: int,
              policy_id: str, event_seq: int, *, enacted_tick: int = 0) -> None: ...
    def history(self, parameter: str) -> tuple[Enactment, ...]: ...
    def snapshot(self, tick: int) -> Mapping[str, Any]: ...
    def dump(self) -> Mapping[str, Any]: ...               # Checkpointable (C04)
    def load(self, state: Mapping[str, Any]) -> None: ...
    name: ClassVar[str] = "runtime_config"
```

```python
# polis/config/logging.py
def configure_logging(level: str = "INFO", *, json_lines: bool = False,
                      run_id: UUID | None = None) -> None: ...
def get_logger(name: str) -> structlog.stdlib.BoundLogger: ...

# polis/config/paths.py
REPO_ROOT: Final[Path]; PROMPTS_DIR: Final[Path]; CONFIGS_DIR: Final[Path]
MIGRATIONS_DIR: Final[Path]; PROFILES_DIR: Final[Path]
def repo_git_sha() -> str: ...                             # -> runs.code_git_sha; "unknown" outside a repo

# polis/cli/app.py
app: Final[typer.Typer]
def main() -> None: ...                                    # console_scripts entry point "polis"
```

---

## 6. Interfaces you consume

None. Third-party only: `pydantic>=2.9`, `pydantic-settings`, `typer`, `structlog`,
`pyyaml`, `alembic`, `psycopg[binary,pool]`, `httpx`, `jinja2`, `jsonschema`, `numpy`.

---

## 7. Data model touched

None. This chunk writes no SQL and opens no connection. It defines `StoreSettings.dsn`
and the `alembic.ini` that C03 fills in.

---

## 8. Event kinds owned

None. `polis/events/kinds.py` is created by C02.

---

## 9. Implementation notes

**9.1 The canonicaliser lives in `config`, not `kernel`.** `09 §5.1` names
`polis/kernel/det.py` as the home of `canonical_json`, but `02 §7.1` states
`events → config` and nothing else, so `polis.events` cannot import `polis.kernel`.
Resolution: the single implementation is `polis/config/canon.py`; C04's
`polis/kernel/det.py` re-exports `canonical_json`, `canonical_bytes`, `sha256_hex` so the
path named in `09` resolves. **Flag this to the spec owner as a `09 §5.1` correction; do
not duplicate the function.**

**9.2 Config layering.** Precedence, lowest to highest: package defaults → profile
fragments in load order → the YAML file → `POLIS_*` env vars → `--set key=value` CLI
overrides. Layering is a deep dict merge over the *raw* mapping, then one `Settings`
validation at the end. Never merge validated model instances — defaults would leak into
the merged result and change `config_hash`.

**9.3 Profiles.** `polis/config/profiles/<group>/<name>.yaml`, groups `clock`, `llm`,
`env`. `--profile clock/chronicle --profile llm/stub`. Ship at minimum:
`clock/microscope`, `clock/chronicle`, `llm/hybrid`, `llm/stub`, `llm/all_local`,
`env/dev`, `env/test`, `env/ci`.

**9.4 Startup consistency checks** (all raise `ConfigError` with the computed numbers):

| Check | Rule |
|---|---|
| Token/call coherence | `tokens_per_tick >= calls_per_tick × llm.est_tokens_per_call` (default 3,300). At `calls_per_tick: 90` this forces `tokens_per_tick >= 297_000`; the shipped cognition default is **300_000**. |
| Clock derivation | `ticks_per_sim_day` must equal 24 for `microscope`, 1 for `chronicle`, unless explicitly overridden with `clock.allow_nonstandard: true`. |
| Cost profile | `microscope` + `usd_per_run` implying fewer cognition calls than `run.ticks × calls_per_tick` requires `--accept-degradation` (09 §7.3). |
| Routing coverage | Every `Purpose` (C05) has a `routing` entry; every `routing.lane` exists in `llm.providers`. |
| MiniMax structured | `providers.<lane>.kind == "minimax"` implies `structured_output == "none"` (09 §2.2). |
| Mechanism keys | Every value in `mechanisms:` resolves to a `MECHANISM_REGISTRY` id after all packages import. |

**9.5 `RuntimeConfig` semantics.** Append-only list per parameter, kept sorted by
`(effective_tick, event_seq)`. `get(p, t)` returns the value of the last enactment with
`effective_tick <= t`, else the static value resolved by dotted path from `Settings`.
`enact` asserts `effective_tick > enacted_tick` (07 §7.1 rule 3) and rejects a parameter
absent from `POLICY_REGISTRY` once C18 registers one — until then it accepts any dotted
path resolvable in `Settings`. `dump()/load()` make it `Checkpointable`. It is a pure
projection of kind 12030, so `polis rebuild` reconstructs it.

**9.6 `.importlinter`.** Transcribe `02 §7.1` literally, one `forbidden` contract per row,
plus the vendor-isolation contract from `09 §8.5`. Add
`layers` contract with the order `research/cli > society/economy/agents > world > llm/store > kernel > events > config`.

**9.7 CLI shape.** Every command takes `--config`, `--profile` (repeatable), `--set`
(repeatable `key=value`), `--log-level`, `--json`. `polis run` in this chunk resolves the
config, prints the reproducibility tuple, the active mechanism manifest and the projected
cost/wall-clock, then exits 0. `resume verify rebuild replay sweep gateway observe` are
registered with correct signatures and exit **2** with `NotImplementedError("owner: Cxx")`.
Stub namespaces to register now so later chunks only add subcommands: `db`, `cache`,
`gate`, `export`, `report`, `scenario`, `mechanisms`, `compare`, `seeds`, `agent`,
`package`, `metrics`, `ledger`, `paper-check`.

**9.8 Engine entrypoint hygiene.** `polis/cli/app.py:main()` asserts
`os.environ.get("PYTHONHASHSEED") == "0"` and re-execs itself once with it set if not
(02 §4.2). Set `TZ=UTC`. Never call `datetime.now()` outside `polis/cli` and `runs`
metadata.

**9.9 Docker Compose.** `pgvector/pgvector:pg17`, `redis:7-alpine`, `minio/minio`.
Postgres with `shared_preload_libraries=pg_stat_statements`, `max_connections=200`,
volume-mounted `initdb/00-roles.sql` creating `polis_engine` and `polis_reader` (C03 grants).

---

## 10. Configuration keys

The whole tree. `configs/baseline.yaml` is normative and must match `02 §8` extended by
`09 §3.4`, with these amended defaults:

```yaml
run:   {name: baseline-1k, seed: 20260724, ticks: 43200, checkpoint_interval: 500,
        retention: full, scale: 1000, tags: []}
clock: {profile: microscope, ticks_per_sim_day: 24, days_per_sim_year: 360,
        demographic_acceleration: 4.0}
llm:
  est_tokens_per_call: 3300
  budget:
    lines:
      cognition: {calls_per_tick: 90,  tokens_per_tick: 300_000}   # 90 x 3,300 = 297,000
      ancillary: {calls_per_tick: 24,  tokens_per_tick:  40_000}
      external:  {calls_per_tick: 32,  tokens_per_tick: 100_000}
      free:      {calls_per_tick: 512, tokens_per_tick: 0}
    usd_per_run: 60.0
    usd_halt_multiple: 1.2
    on_exhaustion: degrade_to_reflex
  fallback_policy: permissive
  cache: {mode: hybrid, path: "file://./.cache/completions", schema_version: 1,
          verify_render: true, strict_version: true, trust: verify, l0_entries: 50000}
salience: {policy: weighted, exploration_epsilon: 0.02,
           weights: {surprise: 0.30, stakes: 0.35, novelty: 0.10, social: 0.15, scheduled: 0.10}}
mechanisms: {labour_matching: stochastic_skill_match, price_setting: markup_over_cost,
             fertility_hazard: income_conditional, mortality_hazard: gompertz_makeham}
ablations: {reflex_only: false, obfuscate_domain: false, disclose_simulation: false,
            salience_policy_override: null}
telemetry: {timing_sample_every: 25, phase_budget_warn: true, redis_publish: true}
store: {dsn: "postgresql://polis_engine:polis@127.0.0.1:5432/polis",
        reader_dsn: null, pool_min: 2, pool_max: 16,
        redis_url: "redis://127.0.0.1:6379/0", blob_url: "file://./.blobs"}
```

`configs/smoke.yaml` = 50 agents, 500 ticks, `clock/chronicle`, `llm/stub`,
`cache.mode: live`, `usd_per_run: 0`. It is what every integration test loads.

The **$12/sim-year cost target holds only under `chronicle`**. `microscope` is
~$250–400/sim-year at these defaults. `polis run` prints both the profile and the figure.

---

## 11. Acceptance criteria

- [ ] `uv sync && uv run polis --help` works from a clean checkout on Linux and Windows.
- [ ] `polis run --config configs/baseline.yaml` exits 0 and prints `config_hash`, `code_git_sha`, active mechanisms, projected cost and projected wall-clock.
- [ ] `load_settings` is deterministic: the same file + profiles + env produce the same `config_hash` across processes and platforms.
- [ ] `config_hash` changes when any semantic key changes and does **not** change on YAML comment/key-order/line-ending edits.
- [ ] `Settings` is frozen; mutation raises. `extra="forbid"` rejects an unknown key with the key name in the error.
- [ ] All six §9.4 startup checks fire with actionable messages containing the computed numbers.
- [ ] `import-linter` passes and **fails** on a deliberately introduced `polis/events/foo.py: import polis.kernel`.
- [ ] `mypy --strict polis/` passes on the empty packages.
- [ ] `RuntimeConfig.get(p, t)` returns the static value before any enactment, the enacted value at and after `effective_tick`, and is unaffected by enactment insertion order.
- [ ] `RuntimeConfig.enact` with `effective_tick <= enacted_tick` raises `RuntimeOverlayError`.
- [ ] `@mechanism` populates `MECHANISM_REGISTRY`; a duplicate id raises `MechanismError` at import.
- [ ] `docker compose up -d && docker compose exec db psql -c 'select 1'` succeeds; `pgvector` and `pg_trgm` are installable.
- [ ] `alembic upgrade head` runs and produces only the empty base revision.
- [ ] `scripts/lint_determinism.py polis/` exits 0 and exits 1 on a file containing `import random`.
- [ ] CI is green on a clean checkout in under 5 minutes.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/config/test_canon.py` | `canonical_json` is byte-stable across key insertion order; rejects `datetime`/`Decimal`/`set`/`bytes`; `round_floats` is idempotent; non-ASCII survives unescaped as UTF-8. |
| `tests/unit/config/test_settings_load.py` | Layer precedence defaults < profile < file < env < `--set`; deep merge of nested maps; `extra="forbid"`; frozen. |
| `tests/unit/config/test_config_hash.py` | Hash stable across processes; invariant to comments, key order, and `\r\n` vs `\n`; changes on any semantic edit. Parametrised over `baseline.yaml` and `smoke.yaml`. |
| `tests/unit/config/test_startup_checks.py` | Each of the six §9.4 checks raises `ConfigError`; the token/call check passes at 90/300_000 and fails at 90/120_000. |
| `tests/unit/config/test_runtime_overlay.py` | `get` before/at/after `effective_tick`; out-of-order `enact` yields the same `get` results; retroactive enactment raises; `dump`/`load` round-trips exactly. |
| `tests/unit/config/test_mechanisms.py` | Registration, duplicate-id error, `active_mechanisms` filters on the `mechanisms:` block, `mechanism_manifest` shape matches `runs.mechanism_manifest`. |
| `tests/unit/cli/test_cli_surface.py` | All eight top-level commands exist with the documented options; unimplemented ones exit 2; `--json` output parses. |
| `tests/unit/test_import_contracts.py` | Runs `lint-imports` in-process and asserts exit 0; a temp module violating `events → kernel` makes it exit non-zero. |
| `tests/unit/test_determinism_lint.py` | The AST linter flags `import random`, `from random import x`, `np.random.seed`, `datetime.now()`, `time.time()`, `uuid.uuid4()`; honours the allowlist file. |

---

## 13. Definition of done

`chunks/README.md §5` items 1–9, plus: `configs/baseline.yaml` and `configs/smoke.yaml`
both load; `docker compose up` gives a working Postgres 17 + pgvector, Redis 7 and MinIO;
CI runs `ruff check`, `ruff format --check`, `mypy --strict polis`, `lint-imports`,
`scripts/lint_determinism.py`, `scripts/lint_prompts.py`, `pytest -q` with outbound
sockets blocked; the §9.1 spec correction is written up in the handback.

---

## 14. Traps

1. **`config_hash` instability across platforms.** This repo is developed on Windows and
   run on Linux. If the hash is taken over the raw YAML bytes, every hash differs by line
   ending and the completion cache is destroyed on checkout. Hash the **validated,
   `mode="json"` model dump** through `canonical_bytes`, never the file.
2. **`Decimal` in the model dump.** `usd_per_run` is `Decimal`. `model_dump(mode="json")`
   renders it as a string — good — but `model_dump()` leaves a `Decimal` object that
   `canonical_json` must reject rather than coerce. A silent `float()` coercion here makes
   the hash depend on repr precision.
3. **Merging validated models instead of raw dicts.** Merging two `Settings` instances
   injects defaults into the "override" layer, so a key the user never set participates in
   the hash. Merge raw mappings, validate once.
4. **`env_nested_delimiter` collisions.** `POLIS_LLM__ROUTING__DELIBERATE__MAX_TOKENS`
   works; `POLIS_LLM_ROUTING` silently does not. Test the env path explicitly or
   researchers will set env vars that do nothing.
5. **`extra="forbid"` on `mechanisms:` and `llm.routing:`.** These are open dicts by
   design; forbidding extras there breaks C18 and C05. Forbid extras on *structured*
   models only.
6. **Putting `canonical_json` in `kernel`.** See §9.1. If you follow `09 §5.1` literally,
   `polis.events` cannot import it and someone writes a second copy. Two canonicalisers is
   the single fastest way to break the hash chain.
7. **`uuid.uuid4()` anywhere.** It is nondeterministic and IDs reach event payloads. Ban it
   in the determinism linter now, before code exists that uses it. `run_id` generation in
   `polis run` is the one allowlisted site.
8. **Typer + `from __future__ import annotations`.** Typer resolves parameter types at
   runtime; stringised annotations break option parsing on some versions. Either pin a
   Typer that handles it or omit the future import in `polis/cli/*` only, and say which.
9. **The `env/test` profile not actually being applied under pytest.** If `POLIS_ENV` is
   not set in `conftest.py`, C05's "only `StubProvider` under test" guard never engages and
   a test suite quietly makes network calls. Set it in `conftest.py`, not in CI only.
10. **Over-modelling `Settings` now.** Sub-models for chunks that do not exist yet
    (`WorldSettings`, `PopulationSettings`, `SocietySettings`) should be minimal and
    permissive. Every field you guess wrong is a migration for someone else. Ship the
    fields `02 §8` actually names and nothing more.
11. **Alembic autogenerate pointed at nothing.** `migrations/env.py` must import a
    `metadata` object that exists. Create an empty `polis/store/metadata.py` with
    `metadata = MetaData()` so C03 has a target and `alembic revision --autogenerate` does
    not crash on import.
12. **CI without socket blocking.** Add the socket guard in `conftest.py` from day one.
    Retrofitting it after C05 lands means discovering which tests were secretly live.
