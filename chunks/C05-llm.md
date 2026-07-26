# C05 — LLM router, providers, completion cache, budget, structured output

**M0** · Owner module `polis/llm` · Depends on: **C01, C03** · Blocks: **C04 (StubProvider), C08 C09 C17 C18 C19 C21 and every chunk that issues an LLM call** · Size **L** (1–2 weeks)

---

## 1. Context

Every LLM call in POLIS goes through one function. Callers supply a **purpose** and
**variables**; they never supply a model, a temperature, a base URL, or a token limit
(`09 §0.1`). Between that call and the wire sit prompt rendering, the content-addressed
completion cache that makes replay free and exact, budget admission, lane concurrency,
circuit breaking, the structured-output repair loop, fallback chains, and one `llm_calls`
row per attempt.

**The most important deliverable in this chunk is `StubProvider`.** It is the only provider
the entire test suite ever sees. Every determinism, invariant, and integration test in
every other chunk runs against it, and a weak stub silently disables all three tiers
(`02 §12`, `09 §2.6`). Build it first, build it properly, and treat "the stub picks a legal,
prompt-grounded action" as a hard requirement rather than a nicety.

---

## 2. Required reading

| Source | Why |
|---|---|
| `../docs/09-MODEL-ROUTING.md` — **all of it** | Binding and complete. §2 providers, §2.6 stub, §3 purposes, §4 routing, §5 cache, §6 structured output, §7 cost, §8 prompts, §9 telemetry, §12 failure modes. |
| `../docs/02-ARCHITECTURE.md` §4.4 (cache), §4.3 (concurrency), §5 PHASE 3/7, §7.1, §10, §11 | The determinism contract and the phase budget the router must live inside. |
| `../docs/03-DATA-MODEL.md` §1.3 `llm_calls`, §1.4 `completion_cache`, §1.1 `runs` | Every column you write. |
| `../docs/01-PRD.md` §9 T3, T5, T8, T9 | Why the telemetry in §9 is not optional. |
| **C01** `polis.config` | `Settings`, `LLMSettings`, `LaneSettings`, `RouteSpec`, `CacheSettings`, `canonical_json`, `PolisError`. |
| **C03** `polis.store` | `Database`, `LlmCallRepository`, `CompletionCacheRepository`, `BlobStore`. |

---

## 3. Scope — in

1. `polis/llm/purposes.py` — the closed `Purpose` enum (11 members incl. `CREDIT_EVAL`) and the budget-line map.
2. `polis/llm/providers/` — `base.py` (protocol + exceptions), `minimax.py`, `ollama.py`, `openai_compat.py`, **`stub.py`**.
3. `polis/llm/lanes.py` — `Lane`, `TokenBucket`, `CircuitBreaker`, lane construction and startup sizing validation.
4. `polis/llm/cache.py` — key construction, L0/L1/L2 tiers, `live`/`replay`/`hybrid` modes, render verification.
5. `polis/llm/budget.py` — `BudgetGuard`, admission, degradation ladder, per-line accounting.
6. `polis/llm/structured.py` — extract/validate/repair loop.
7. `polis/llm/prompts.py` — `PromptLibrary`, version headers, platform-stable template hashing, Jinja environment, paraphrase variants.
8. `polis/llm/router.py` — `LLMRouter`: resolution order, fallback chains, deferral, telemetry, `llm_calls` rows.
9. `polis/llm/telemetry.py` — `EventEmitter` protocol and the per-tick metric vector.
10. Kinds **4100–4199** registered in `polis/events/kinds.py`.
11. `prompts/` skeleton: one purpose directory per `Purpose`, each with `system`/`user` templates, ≥2 paraphrase siblings, and a JSON Schema.
12. `polis/cli/commands/cache.py` — `polis cache warm|pull|stats|export`; `scripts/lint_prompts.py` rules.

## 4. Scope — out

| Not built here | Owner |
|---|---|
| Prompt **content** for `DELIBERATE`/`REFLECT` (you ship a structurally correct placeholder that satisfies the schema and the lint) | C09 |
| Salience scoring and mode assignment (PHASE 2) | C09 |
| Memory embedding *use* (you provide `embed()`, retrieval is elsewhere) | C08 |
| `sim_aware_flag` regex prefilter content (you ship the column, the purpose, and a stub classifier) | C24 |
| `polis sweep`, `polis compare`, `polis report cost` | C24 |
| Anything about which agent gets a call | C09 |

---

## 5. Interfaces you provide

```python
# polis/llm/purposes.py
class Purpose(StrEnum):
    DELIBERATE = "DELIBERATE"; REFLECT = "REFLECT"; IMPORTANCE = "IMPORTANCE"
    POST_WRITE = "POST_WRITE"; NEWS_WRITE = "NEWS_WRITE"; VC_EVAL = "VC_EVAL"
    JUDGE = "JUDGE"; EMBED = "EMBED"; SIM_AWARE_CHECK = "SIM_AWARE_CHECK"
    SUMMARISE = "SUMMARISE"; CREDIT_EVAL = "CREDIT_EVAL"

BudgetLine: TypeAlias = Literal["cognition", "ancillary", "external", "free"]
PURPOSE_LINE: Final[Mapping[Purpose, BudgetLine]]      # 09 §3.2 'Line' column
DEFERRED_PURPOSES: Final[frozenset[Purpose]]           # {POST_WRITE, SUMMARISE} — 09 §4.4
```

```python
# polis/llm/providers/base.py   (09 §2.1, verbatim)
StructuredMode = Literal["schema", "json_mode", "none"]
Billing = Literal["token", "gpu_time", "free"]

@dataclass(frozen=True, slots=True)
class Capabilities:
    context_window: int; max_output_tokens: int
    structured_output: StructuredMode; prefix_caching: bool
    max_concurrency: int; rpm_limit: int | None; tpm_limit: int | None
    supports_embeddings: bool; embedding_dim: int | None
    billing: Billing
    price_in_usd_per_mtok: Decimal; price_out_usd_per_mtok: Decimal
    price_cached_in_usd_per_mtok: Decimal | None
    reports_model_version: bool; supports_call_seed: bool

@dataclass(frozen=True, slots=True)
class SamplingParams:
    temperature: float; top_p: float = 1.0; max_tokens: int = 512
    stop: tuple[str, ...] = (); seed: int | None = None

@dataclass(frozen=True, slots=True)
class CompletionRequest:
    purpose: str; system: str; user: str
    schema: Mapping[str, Any] | None
    sampling: SamplingParams; call_seed: int; timeout_s: float

@dataclass(frozen=True, slots=True)
class CompletionResponse:
    text: str; tokens_in: int; tokens_out: int; tokens_cached_in: int
    model_version: str | None; provider_request_id: str | None; latency_ms: int
    finish_reason: Literal["stop", "length", "content_filter", "error"]

@dataclass(frozen=True, slots=True)
class HealthReport:
    ok: bool; lane: str; model: str; model_version: str | None
    latency_ms: int; detail: str = ""

class Provider(Protocol):
    name: str; model: str; capabilities: Capabilities
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def health(self) -> HealthReport: ...
    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal: ...

class ProviderError(PolisError): ...
class ProviderTransient(ProviderError): ...
class ProviderPermanent(ProviderError): ...
class ProviderRateLimited(ProviderError):
    retry_after_s: float
class ProviderTimeout(ProviderError): ...
```

```python
# polis/llm/providers/stub.py   — THE deliverable
class StubConfig(BaseModel):
    failure_rate: float = 0.0; permanent_rate: float = 0.0; timeout_rate: float = 0.0
    ratelimit_rate: float = 0.0; malformed_rate: float = 0.0
    schema_drift_rate: float = 0.0; truncate_rate: float = 0.0
    embedding_dim: int = 768

class StubProvider:
    name: str = "stub"; model: str = "stub-v1"; capabilities: Capabilities
    def __init__(self, cfg: StubConfig | None = None) -> None: ...
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def health(self) -> HealthReport: ...
    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal: ...   # always Decimal(0)

def stub_key(req: CompletionRequest) -> bytes:
    """sha256 over (purpose, system, user, canonical_json(schema), canonical_json(sampling),
    call_seed) — the same tuple shape as the cache key."""
def synthesise(schema: Mapping[str, Any], prompt: str, rng: random.Random) -> str: ...
def legal_actions_from_prompt(prompt: str) -> list[str]:
    """Parses the '## What you can do' block; returns [] when absent."""
def ids_from_prompt(prompt: str) -> dict[str, list[str]]:
    """{'ag': [...], 'fm': [...], 'pl': [...], 'st': [...], 'bk': [...],
        'hh': [...], 'pt': [...], 'ol': [...]} in first-appearance order."""
def stub_embedding(text: str, dim: int = 768) -> list[float]:
    """Deterministic feature-hashed, L2-normalised vector. Lexically similar texts
    have higher cosine similarity — retrieval tests are then meaningful, not arbitrary."""
class StubContractError(PolisError): ...
```

```python
# polis/llm/lanes.py
class TokenBucket:
    def __init__(self, rate_per_min: float | None, capacity: float | None = None) -> None: ...
    async def acquire(self, n: float = 1.0) -> float: ...    # -> waited ms
class CircuitBreaker:
    def __init__(self, *, error_window_calls: int = 50, error_rate_trip: float = 0.40,
                 consecutive_trip: int = 8, open_ticks: int = 20,
                 half_open_probes: int = 3) -> None: ...
    def state(self) -> Literal["closed", "open", "half_open"]: ...
    def allow(self, tick: int) -> bool: ...
    def record(self, ok: bool, tick: int, *, permanent: bool = False) -> bool: ...  # -> state changed

@dataclass
class Lane:
    name: str; provider: Provider
    sem: asyncio.Semaphore; rpm: TokenBucket; tpm: TokenBucket; breaker: CircuitBreaker
    inflight: int = 0; queue_wait_ms_p95: float = 0.0
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

def build_lanes(s: LLMSettings, *, http: httpx.AsyncClient | None = None) -> dict[str, Lane]: ...
def validate_sizing(s: LLMSettings, lanes: Mapping[str, Lane], *,
                    phase_budget_s: float = 3.0) -> None:
    """09 §4.4: max_calls_per_tick(lane) = floor(C * phase_budget_s / p50_latency_s).
    Raises ConfigError with the computed numbers when a routing table over-subscribes a lane."""
```

```python
# polis/llm/prompts.py
@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str; purpose: Purpose; version: int
    schema_name: str | None; paraphrases: tuple[str, ...]
    max_tokens: int; template_hash: str

@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    system: str; user: str; rendered_hash: str; template_hash: str; est_tokens: int

def template_hash(src: bytes) -> str:
    """09 §8.3 VERBATIM: normalise \\r\\n and \\r to \\n, rstrip each line,
    rstrip the whole, append one \\n, then sha256. Platform stability depends on this."""

class PromptLibrary:
    @classmethod
    def load(cls, root: Path, *, variant: str | None = None) -> "PromptLibrary": ...
    def get(self, purpose: Purpose) -> PromptTemplate: ...
    def render(self, purpose: Purpose, variables: Mapping[str, Any]) -> RenderedPrompt: ...
    def manifest(self) -> dict[str, str]: ...              # -> runs.prompt_manifest
    def schema(self, name: str) -> Mapping[str, Any]: ...
    def schema_hash(self, name: str) -> str: ...
class PromptError(PolisError): ...
```

```python
# polis/llm/cache.py
def cache_key(*, provider: str, model: str, model_version: str | None,
              prompt_template_hash: str, prompt_variables: Mapping[str, Any],
              sampling: SamplingParams, schema_hash: str | None, call_seed: int) -> str:
    """09 §5.1. Fields joined by b"\\x1f"; canonical_json for the two mappings."""

@dataclass(frozen=True, slots=True)
class CacheRecord:
    schema_version: int; key: str; provider: str; model: str; model_version: str | None
    rendered_hash: str; response_text: str
    tokens_in: int; tokens_out: int; tokens_cached_in: int
    finish_reason: str; provider_request_id: str | None; created_at: datetime

@dataclass(frozen=True, slots=True)
class CacheStats:
    hits: int; misses: int; l0_hits: int; l1_hits: int; l2_hits: int
    writes: int; bytes_written: int
    @property
    def hit_rate(self) -> float: ...

class CompletionCache:
    def __init__(self, *, mode: Literal["live", "replay", "hybrid"],
                 index: CompletionCacheRepository, blobs: BlobStore,
                 l0_entries: int = 50_000, verify_render: bool = True,
                 trust: Literal["verify", "trust"] = "verify",
                 schema_version: int = 1, root: str = "") -> None: ...
    @property
    def mode(self) -> str: ...
    async def get(self, key: str, *, rendered_hash: str) -> CacheRecord | None: ...
    async def put(self, rec: CacheRecord) -> None: ...       # no-op in replay
    async def flush(self) -> None: ...
    def stats(self) -> CacheStats: ...
class CacheRenderMismatch(PolisError): ...
class CacheMissInReplay(PolisError): ...
```

```python
# polis/llm/budget.py
class Admission(StrEnum):
    PERMIT = "permit"; DEGRADE = "degrade"; HALT = "halt"

@dataclass(frozen=True, slots=True)
class BudgetReport:
    tick: int; per_line: Mapping[str, Mapping[str, float]]
    binding_constraint: str | None; degraded_calls: Mapping[str, int]
    cumulative_usd: Decimal

class BudgetGuard:
    def __init__(self, s: LLMBudgetSettings) -> None: ...
    def begin_tick(self, tick: int) -> None: ...
    def admit(self, line: str, est_in: int, est_out: int, est_usd: Decimal) -> Admission: ...
    def charge(self, line: str, *, purpose: str, tokens_in: int, tokens_out: int,
               usd: Decimal, billed: bool) -> None: ...
    def end_tick(self, tick: int) -> BudgetReport: ...
    @property
    def binding_constraint(self) -> str | None: ...
    @property
    def cumulative_usd(self) -> Decimal: ...
```

```python
# polis/llm/structured.py
@dataclass(frozen=True, slots=True)
class ParsedResult:
    obj: Mapping[str, Any]; repair_attempts: int; resp: CompletionResponse
async def call_structured(lane: Lane, req: CompletionRequest, schema: Mapping[str, Any], *,
                          max_repairs: int = 2,
                          on_attempt: Callable[[int, str | None, CompletionResponse | None], None] | None = None
                          ) -> ParsedResult: ...
def extract_and_validate(text: str, schema: Mapping[str, Any]
                         ) -> tuple[Mapping[str, Any] | None, str | None]: ...
def with_error(req: CompletionRequest, error: str) -> CompletionRequest: ...
class SchemaRepairExhausted(PolisError):
    errors: tuple[str, ...]
```

```python
# polis/llm/telemetry.py
class EventEmitter(Protocol):
    """Injected by the composition root. polis.llm does NOT import polis.events (02 §7.1)."""
    def emit(self, kind: int, payload: Mapping[str, Any], *,
             actor_id: str | None = None, cause_seq: int | None = None) -> None: ...
class NullEmitter(EventEmitter): ...
```

```python
# polis/llm/router.py
@dataclass(frozen=True, slots=True)
class CallResult:
    call_id: UUID; purpose: Purpose
    text: str; parsed: Mapping[str, Any] | None; parsed_ok: bool
    lane: str; model: str; model_version: str | None
    cache_key: str; cache_hit: bool; cache_mode: str
    tokens_in: int; tokens_out: int; tokens_cached_in: int
    cost_usd: Decimal; latency_ms: int; repair_attempts: int
    degraded: bool; fallback_used: bool; budget_line: str
    provider_request_id: str | None; error: str | None

@dataclass(frozen=True, slots=True)
class CallRequest:
    purpose: Purpose; agent_id: str; tick: int
    variables: Mapping[str, Any]; schema_name: str | None = None

class LLMRouter:
    def __init__(self, *, settings: LLMSettings, lanes: Mapping[str, Lane],
                 cache: CompletionCache, budget: BudgetGuard, prompts: PromptLibrary,
                 seeds: SeedSource, emitter: EventEmitter,
                 calls: LlmCallRepository, run_id: UUID) -> None: ...
    @classmethod
    def from_settings(cls, s: Settings, *, run_id: UUID, seeds: SeedSource,
                      emitter: EventEmitter, db: Database) -> "LLMRouter": ...
    async def start(self) -> None:
        """Health-check every lane, run the F3 canary fingerprint, validate sizing.
        A local lane that fails health is a HARD startup error (09 §11.3)."""
    async def call(self, purpose: Purpose, agent_id: str, tick: int,
                   variables: Mapping[str, Any], schema_name: str | None = None, *,
                   deferred: bool = False) -> CallResult: ...
    async def gather(self, requests: Sequence[CallRequest]) -> list[CallResult]:
        """Concurrent issue, results in REQUEST order. Budget charged under one lock in
        request order at the end, never as calls land (09 §4.5)."""
    async def flush_deferred(self, tick: int) -> list[CallResult]: ...   # PHASE 7
    async def embed(self, texts: Sequence[str], *, tick: int,
                    owner_id: str = "") -> list[list[float]]: ...
    def tick_metrics(self, tick: int) -> dict[str, float]: ...           # 09 §9.2
    def model_manifest(self) -> dict[str, dict[str, str | None]]: ...    # -> runs.model_manifest
    async def close(self) -> None: ...
class RouterError(PolisError): ...
```

---

## 6. Interfaces you consume

| From | What |
|---|---|
| C01 | `Settings`, `LLMSettings`, `LaneSettings`, `RouteSpec`, `CacheSettings`, `LLMBudgetSettings`, `canonical_json`, `sha256_hex`, `PolisError`, `get_logger`, `PROMPTS_DIR` |
| C03 | `Database`, `LlmCallRepository`, `CompletionCacheRepository`, `BlobStore` |
| C04 (structurally, not by import) | `SeedSource` — `RngRegistry` satisfies it. Declared as a Protocol here so `llm` never imports `kernel`. |

Third-party: `httpx`, `jinja2`, `jsonschema`. **`httpx` may be imported only inside
`polis/llm/providers/`** (`09 §8.5`).

---

## 7. Data model touched

| Table | Access |
|---|---|
| `llm_calls` | **write** — one row per attempt including cache hits and each repair. Columns: all of `03 §1.3` plus `lane`, `cache_mode`, `provider_request_id`, `budget_line`. Batched per tick via `LlmCallRepository.append`. |
| `completion_cache` | read/write (cross-run, no `run_id`). `hit_count` bumped out of band. |
| `runs` | read `tags` (`keep_prompts`); supplies `prompt_manifest` and `model_manifest` at run start. C04 writes the row. |
| object store | cache blobs > 64 KB at `<root>/v<schema_version>/<provider>/<model>/<k[0:2]>/<k[2:4]>/<k>.json` |

---

## 8. Event kinds owned

Range **4100–4199**, owner `polis.llm`, persisted. Registered in `polis/events/kinds.py`
(data only — no import of `polis.events` from `polis/llm`).

| Kind | Name | Payload (required) |
|---|---|---|
| 4100 | `LANE_HEALTH_CHECKED` | `lane`, `model`, `model_version`, `ok`, `latency_ms` |
| 4101 | `LLM_CALL_FAILED` | `purpose`, `lane`, `model`, `error_class`, `message`, `repair_attempts`, `fell_back_to` |
| 4102 | `BUDGET_EXHAUSTED` | `line`, `cap` (`calls`\|`tokens`\|`usd`), `degraded_calls`, `tick` |
| 4103 | `PROVIDER_CIRCUIT_OPENED` | `lane`, `model`, `window_errors`, `window_calls`, `trigger` |
| 4104 | `PROVIDER_CIRCUIT_CLOSED` | `lane`, `model`, `probes` |
| 4105 | `PROVIDER_TIER_MISMATCH` | `lane`, `observed_429_rate`, `configured_concurrency`, `likely_tier` |
| 4106 | `CACHE_MISS_IN_REPLAY` | `purpose`, `cache_key`, `agent_id`, `template_hash` |
| 4107 | `PARSE_REPAIR_ATTEMPTED` | `purpose`, `model`, `attempt`, `error` |
| 4108 | `MODEL_VERSION_CHANGED` | `lane`, `model`, `previous_fingerprint`, `current_fingerprint` |

4109–4199 reserved.

---

## 9. Implementation notes

**9.1 Build order.** `StubProvider` → `prompts` → `cache` → `structured` → `budget` →
`lanes` → `router` → real providers. Every layer below the router is testable against the
stub alone, and C04 is unblocked as soon as the stub and a minimal router exist.

**9.2 `StubProvider` in detail** (`09 §2.6`). Response is a pure function of the request:
no wall clock, no `random` global state, no network, no filesystem, no environment.

- `rng = random.Random(int.from_bytes(stub_key(req)[:8], "big"))`.
- Fault injection first, from the same key, in a fixed order:
  `permanent → transient → timeout → ratelimit → truncate → malformed → schema_drift`.
  Each is `rng_fault.random() < rate` on a *separate* stream derived from
  `stub_key + rate_name`, so raising one rate does not perturb the others' decisions.
- `synthesise(schema, prompt, rng)` walks the JSON Schema: required properties in schema
  order; `enum` via `rng.choice`; strings from a fixed lorem corpus sized to
  `minLength`/`maxLength`; numbers uniform in `[minimum, maximum]` rounded to 6 dp;
  integers uniform; arrays at `minItems`; `$ref` resolved within the document;
  `oneOf`/`anyOf` → first branch. The result is validated against the schema before return;
  failure raises `StubContractError` (a stub emitting an invalid instance is a test-suite
  bug, not a simulated model failure).
- **Prompt-grounded choices (`09 §2.6.3`) are mandatory.** For `DELIBERATE`, `action.type`
  is drawn from `legal_actions_from_prompt(req.user)` when that block exists, and
  `action.params` values that look like entity references are filled from
  `ids_from_prompt(req.user)` matching the parameter's declared prefix. A stub that samples
  the global `ActionType` enum produces actions that PHASE 4 rejects, and then every
  integration test exercises nothing but the rejection path. When the prompt has no legal-
  action block, fall back to the schema `enum` and log once at DEBUG.
- `tokens_in = len(system)//4 + len(user)//4`, `tokens_out = len(text)//4`,
  `latency_ms = 0`, `model_version = "stub-v1"`,
  `provider_request_id = stub_key(req).hex()[:16]`, `price() == Decimal(0)`.
- `embed()` returns `stub_embedding(text, 768)`: lowercase, split on non-alphanumerics,
  hash each token and each adjacent bigram into one of 768 buckets with a signed
  contribution, L2-normalise. Deterministic, dimension-correct, and **monotone in lexical
  overlap**, so C08's retrieval tests measure something.
- **Enforcement (`09 §2.6.5`).** Under `POLIS_ENV=test`, `build_lanes` refuses to
  instantiate any provider whose `kind != "stub"` unless the running test is marked
  `live_llm`. CI additionally blocks outbound sockets.

**9.3 `MiniMaxProvider`.** OpenAI Chat Completions shape at `https://api.minimax.io/v1`.
`structured_output` is **`none`** — a fact about the M2.x line, not a preference
(`09 §2.2`, `§2.3`). Setting `schema` in config for a MiniMax lane is a startup error.
Never stream. `model_version` from the response body, else `model_version_pin`; if neither
and `cache.strict_version`, startup fails. Emit blocks in the `09 §7.6` order so the
provider-side prefix cache can hit block 1.

**9.4 `OllamaProvider`.** One class for cloud and local; the only difference is the `:cloud`
model-tag suffix, which routes to `ollama.com`. Uses **`POST /api/chat` with the native
`format` field** carrying the JSON Schema — `/v1/chat/completions` silently ignores
`response_format.json_schema` (`09 §2.2`). `POST /api/embed` for `EMBED`; the returned
dimension is asserted against `capabilities.embedding_dim` on first call and must be **768**
to match `memories.embedding vector(768)`; a mismatch is a refusal to start, not a warning.
`billing: gpu_time` ⇒ `price()` returns `Decimal(0)` and the call consumes
`tokens_per_tick` but not `usd_per_run`. Sustained >5% 429s over 100 ticks emits 4105.

**9.5 `OpenAICompatProvider`.** Generic, quirk-free, fully config-driven (base URL, key env,
concurrency, structured mode, seed support, prices). If an endpoint needs a quirk it gets
its own subclass; quirks never leak into the router.

**9.6 Router resolution order** (`09 §4.1`), exactly:

```
1 purpose -> RouteSpec       2 render (template_hash, rendered_hash)
3 cache_key                  4 L0 LRU hit? return
5 L1 index (+L2 blob) hit? return
6 mode == replay and miss -> emit 4106, raise CacheMissInReplay, HALT
7 budget admission          8 lane admission: breaker, semaphore, rpm/tpm buckets
9 provider.complete + fallback chain
10 structured validate + repair
11 write cache; write llm_calls; charge budget; update metrics
```

Steps 4–5 precede budget admission: **a cache hit is free and must never be throttled.**
Steps 8–10 happen inside the lane semaphore so repair retries count against concurrency.

**9.7 Fallbacks.** Per `09 §4.2`. Three binding rules: a fallback that changes the model
changes the cache key, `llm_calls` records what actually ran, and the run is tagged
`mixed_model`; `fallback_policy: strict` disables cross-model fallback and goes straight to
`last_resort`; in `replay` there are no fallbacks and **no provider instances are
constructed at all** — the absence of a code path to a socket is what makes "reproduces the
figures with zero API spend" structural. Retries before fallback: `ProviderTransient` and
`ProviderTimeout` get 2 retries with exponential backoff and full jitter seeded from
`call_seed`; `ProviderRateLimited` waits `retry_after_s` once; `ProviderPermanent` never
retries and trips the breaker immediately on a lane's first call.

**9.8 Repair loop** (`09 §6.2`). ≤2 repairs, ≤3 wire calls. `extract_and_validate` strips
code fences, takes the outermost balanced `{...}` when the model prefixes prose, validates
with `jsonschema` draft 2020-12. The repair message appends **only** the validator's error
path and message, never the whole schema. `with_error` preserves `call_seed`; each attempt
has a distinct cache key because the user message differs, so replay reproduces the whole
repair sequence including its failures. After 2 failed repairs: `parsed_ok = False`, emit
4101, take the purpose's `last_resort`, and set the action origin to `reflex` so downstream
analysis never misattributes a reflex action to deliberation.

**9.9 Budget** (`09 §4.6`). Enforced in the router, never in the agent layer. `est_usd` uses
the rendered prompt's actual input tokens and `max_tokens` as a pessimistic output estimate.
Ladder: line `calls_per_tick` → DEGRADE + 4102 once per line per tick; line
`tokens_per_tick` → same, recording which cap bound; `usd_per_run` → DEGRADE every
token-billed lane for the rest of the run while free lanes continue (so embeddings and
memory keep working and the run stays analysable); `usd_per_run × usd_halt_multiple` →
HALT. `on_exhaustion: halt` turns the first DEGRADE into a HALT. Charging happens under one
lock in request order at the end of `gather`, not as calls land, so a run whose calls return
in a different order exhausts the budget at the same call (`09 §4.5`).

**9.10 Cache.** Key exactly as `09 §5.1`. `rendered_hash` is stored beside the record but
**not** in the key; with `verify_render: true` a hit recomputes `sha256(rendered_prompt)`
and raises `CacheRenderMismatch` on disagreement — this catches a Jinja version or
whitespace-policy change that silently altered rendering without changing the template.
Tiers: L0 in-process `OrderedDict` LRU (50k), L1 `completion_cache` with inline blob ≤64 KB,
L2 object store above that. Entries are immutable; `hit_count` is the only mutable field and
is bumped out of band.

**9.11 Prompts.** `prompts/<purpose>/{system,user}.v<N>.jinja` plus
`paraphrase/*.p1.jinja`, `*.p2.jinja`, and `prompts/schemas/*.schema.json`. Version header
parsed at load; a malformed header, an unknown purpose, or a missing schema fails startup.
`template_hash` normalises line endings and trailing whitespace **before** hashing
(`09 §8.3`) — **this repository is developed on Windows and deployed on Linux; without
normalisation every template hash differs by platform and the entire cache is lost on
checkout.** Jinja env: `StrictUndefined`, `autoescape=False`, `trim_blocks`,
`lstrip_blocks`, `keep_trailing_newline`. Banned in templates: `now()`, `random`, unbounded
`range`, dict iteration without `|dictsort`. `prompt_variables` in the cache key is the
exact mapping handed to `render()`; nothing may be added inside the template from ambient
state.

**9.12 `scripts/lint_prompts.py`** (`09 §8.5`): provider/model denylist over `prompts/**`;
simulation-status denylist; ≥2 paraphrase siblings per purpose; every template hash present
in the manifest. Plus the `.importlinter` vendor-isolation contract from `09 §8.5` verbatim.

**9.13 Deferral.** `POST_WRITE` and `SUMMARISE` are issued from PHASE 3 with
`deferred=True` and awaited in PHASE 7 via `flush_deferred(tick)` — a **fixed** one-tick
delay, deterministic, not opportunistic (`09 §4.4`).

**9.14 Telemetry.** One `llm_calls` row per attempt including cache hits (`cache_hit=True`,
`cost_usd=0`, `latency_ms` = lookup time) — an unrecorded hit makes the hit rate
unmeasurable. `tick_metrics()` returns the full `09 §9.2` vector. `model_manifest()` covers
**all eleven purposes** whether or not they fired, so a run is comparable to another on the
manifest alone.

---

## 10. Configuration keys

Under `llm:` in `Settings` (C01 defines the models; C05 gives them meaning). Beyond
`09 §3.4`:

```yaml
llm:
  est_tokens_per_call: 3300          # used by the C01 token/call coherence check
  max_prompt_tokens: 3000
  max_repairs: 2
  retry_attempts: 2
  retry_base_ms: 200
  deferred_purposes: [POST_WRITE, SUMMARISE]
  canary:
    enabled: true
    prompts: 16
    on_change: tag                   # tag | halt
  breaker: {error_window_calls: 50, error_rate_trip: 0.40, consecutive_trip: 8,
            open_ticks: 20, half_open_probes: 3}
  providers:
    stub:
      kind: stub
      failure_rate: 0.0; permanent_rate: 0.0; timeout_rate: 0.0; ratelimit_rate: 0.0
      malformed_rate: 0.0; schema_drift_rate: 0.0; truncate_rate: 0.0
```

`--llm-chaos` sets all seven stub rates to 0.05. Every degradation path in §9.7–§9.9 has a
test that runs under chaos and asserts the run completes, invariants hold, and the counters
match.

Cost defaults must stay internally consistent: at `calls_per_tick: 90` and ~3,300
tokens/call the cognition line needs `tokens_per_tick: 300_000`. **The $12/sim-year target
holds only under `chronicle`**; `microscope` is ~$250–400/sim-year at the same policy, and
every cost report prints the profile beside the figure.

---

## 11. Acceptance criteria

- [ ] `StubProvider.complete` is a pure function of the request: two processes, two machines, byte-identical text; `latency_ms == 0`; no `random` global, no clock, no socket, no file read.
- [ ] Every stub response validates against its request schema; a synthetic schema the stub cannot satisfy raises `StubContractError` rather than returning an invalid instance.
- [ ] For a `DELIBERATE` prompt containing a `## What you can do` block, the stub's `action.type` is always from that block and every `ag_`/`fm_`/`pl_`/`st_` parameter value appears in the prompt.
- [ ] Each of the seven stub fault rates produces the expected failure at the configured rate ±2% over 10,000 keys, and the same key always fails the same way; raising one rate does not change which keys the others select.
- [ ] `stub_embedding` returns 768 floats with ‖v‖ = 1 ± 1e-6; identical text → identical vector; `cos(a, a+" more words") > cos(a, unrelated)`.
- [ ] Under `POLIS_ENV=test`, constructing any non-stub lane raises unless the test is marked `live_llm`; the suite passes with outbound sockets blocked.
- [ ] `template_hash` is identical for a file checked out with `\n` and with `\r\n`, and for trailing-whitespace variants.
- [ ] `PromptLibrary.load` fails on a malformed header, an unknown purpose, a missing schema, or fewer than 2 paraphrase siblings.
- [ ] `cache_key` changes on each of: provider, model, model_version, template hash, any variable, any sampling param, the schema hash, `call_seed` — and on nothing else.
- [ ] `replay` mode constructs **zero** provider instances; a miss emits 4106 and raises `CacheMissInReplay`; `put()` is a no-op.
- [ ] `verify_render: true` raises `CacheRenderMismatch` when the stored `rendered_hash` disagrees.
- [ ] Blobs > 64 KB go to the object store and round-trip; the object path matches `09 §5.2`.
- [ ] The repair loop makes at most 3 wire calls; each attempt gets its own cache key; after exhaustion `parsed_ok=False`, 4101 is emitted, and the purpose's `last_resort` is taken.
- [ ] `extract_and_validate` recovers JSON from: fenced code blocks, leading prose, trailing prose, and a JSON object containing a nested `{`; it rejects a schema-valid-looking object with an extra property (`additionalProperties: false`).
- [ ] Budget: each of the five ladder triggers produces the right `Admission`; 4102 is emitted once per line per tick; `binding_constraint` names the cap that bound; free lanes keep running after `usd_per_run` is reached.
- [ ] `gather` returns results in **request** order and charges budget in request order regardless of completion order (test with shuffled seeded latencies).
- [ ] Circuit breaker: opens on rate and on consecutive failures, emits 4103; probes after `open_ticks` and closes with 4104; `ProviderPermanent` on a lane's first call opens it immediately.
- [ ] `validate_sizing` rejects a routing table assigning `ollama_cloud` (concurrency 3) the `DELIBERATE` volume, with the computed wave count in the message.
- [ ] `MiniMaxProvider` configured with `structured_output: schema` fails startup.
- [ ] `OllamaProvider` posts to `/api/chat` with `format`, never to `/v1/chat/completions`; an embedding dimension ≠ 768 refuses to start.
- [ ] One `llm_calls` row per attempt including cache hits and each repair; `lane`, `cache_mode`, `provider_request_id`, `budget_line` populated.
- [ ] A 200-tick run under `--llm-chaos` completes, invariants hold, and `llm.degraded_calls` + `llm.repair_exhausted_rate` are non-zero and recorded.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/llm/test_stub_determinism.py` | Purity across processes (subprocess comparison); no clock/socket/env; `latency_ms == 0`; identical text for identical requests; different `call_seed` → different text. |
| `tests/unit/llm/test_stub_schema_synthesis.py` | Every checked-in schema in `prompts/schemas/` is satisfied; enums, `minLength`/`maxLength`, numeric bounds, `minItems`, `$ref`, `additionalProperties: false`; `StubContractError` on an unsatisfiable schema. |
| `tests/unit/llm/test_stub_prompt_grounding.py` | Action type drawn from the prompt's legal-action block; params reference in-prompt ids by prefix; graceful fallback when the block is absent. |
| `tests/unit/llm/test_stub_faults.py` | Seven rates × observed frequency ±2% over 10k keys; per-key reproducibility; cross-rate independence; `finish_reason='length'` on truncate; non-JSON on malformed; schema-valid-but-wrong on drift. |
| `tests/unit/llm/test_stub_embeddings.py` | Dimension, unit norm, determinism, lexical-overlap monotonicity, batch equals per-item. |
| `tests/unit/llm/test_prompt_hashing.py` | `\r\n` vs `\n` vs trailing whitespace equality; header parsing; paraphrase discovery; manifest shape; `StrictUndefined` raises on a missing variable. |
| `tests/unit/llm/test_cache_key.py` | Sensitivity matrix (one test per key component); insensitivity to timeout/retry settings; canonical-JSON stability of `prompt_variables`. |
| `tests/unit/llm/test_cache_modes.py` | `live`/`hybrid`/`replay` behaviour; no providers constructed in replay; 4106 + `CacheMissInReplay`; `put()` no-op; L0/L1/L2 tiering and the 64 KB boundary; `CacheRenderMismatch`. |
| `tests/unit/llm/test_structured_repair.py` | Fence stripping, prose prefix/suffix, nested braces, `additionalProperties` rejection; ≤3 wire calls; distinct cache keys per attempt; `call_seed` preserved; 4107 per attempt; `SchemaRepairExhausted`. |
| `tests/unit/llm/test_budget.py` | Five ladder triggers; per-line isolation; `binding_constraint`; free lanes after `usd_per_run`; `on_exhaustion: halt`; `Decimal` arithmetic never touches `float`. |
| `tests/unit/llm/test_lanes.py` | Semaphore bounds inflight; token buckets throttle RPM/TPM; breaker open/half-open/close with 4103/4104; permanent-on-first-call immediate trip; `validate_sizing` error text contains the computed numbers. |
| `tests/unit/llm/test_router_order.py` | `gather` returns request order and charges in request order under shuffled latency; fallback chain per purpose; `strict` policy skips cross-model fallback; `mixed_model` tagging. |
| `tests/unit/llm/test_providers_wire.py` | Against a `httpx.MockTransport`: MiniMax request shape (no `stream`, no `response_format`); Ollama `/api/chat` with `format`; embed dimension assertion; the four exception classes mapped from status codes. |
| `tests/unit/llm/test_llm_calls_rows.py` | One row per attempt incl. hits and repairs; all `03 §1.3` + amendment columns populated; `cost_usd` is `Decimal`. |
| `tests/integration/llm/test_chaos_run.py` | 200 ticks with `--llm-chaos`: completes, invariants hold, degradation counters non-zero, chain verifies. |
| `tests/determinism/test_router_determinism.py` | Two 200-tick runs against the stub → identical chain and identical `cache_key` sequence. |
| `tests/unit/llm/test_prompt_lint.py` | Denylists fire on a provider name and on "simulation"; the ≥2-paraphrase rule; manifest-completeness assertion. |

---

## 13. Definition of done

`chunks/README.md §5` items 1–9, plus: `StubProvider` is documented in
`polis/llm/providers/stub.py`'s module docstring as **mandatory test infrastructure** with
the `09 §2.6` contract restated; `prompts/` contains a loadable, lint-passing skeleton for
all eleven purposes with ≥2 paraphrase siblings each; `polis cache stats --run <id>` works;
the vendor-isolation `.importlinter` contract passes; the `llm → events` decision in §14.1
is written up in the handback.

---

## 14. Traps

1. **`polis.llm` importing `polis.events`.** `02 §7.1` gives `llm → config, store`, but this
   chunk owns kinds 4100–4199. Resolution: register the `KindSpec`s as *data* in
   `polis/events/kinds.py` and emit through the injected `EventEmitter` protocol. Do not
   `import polis.events` from router code, and do not "simplify" by dropping the protocol —
   flag the alternative (amend `02 §7.1` to `llm → config, store, events`) to the spec
   owner rather than silently patching either side.
2. **`StubProvider` and the `random` ban.** C04's determinism linter bans `import random`
   outside `polis/kernel/rng.py`. The stub genuinely needs `random.Random`, seeded from the
   key. Coordinate the allowlist entry with C04 **before** CI turns red, and justify it in
   the allowlist file: "seeded from the request hash; no global state".
3. **A decorative stub.** The single most expensive failure available here. A stub that
   returns `{"action": {"type": "IDLE"}}` for everything makes the whole test suite green
   and meaningless: every integration test then exercises the rejection path or the idle
   path and nothing else. Prompt-grounded action selection is a requirement, and
   `test_stub_prompt_grounding.py` is the test that keeps it one.
4. **Stub fault rates sharing one RNG stream.** If all seven draws come from one stream,
   changing `failure_rate` shifts every subsequent draw and every key's fate — so a chaos
   run is not reproducible across config edits. Derive a separate stream per rate.
5. **`float` for money.** `price()` returns `Decimal`, `cost_usd` is
   `NUMERIC(12,8)`, and USD must never touch the `Money` cents type (`09 §2.1` rule 1).
   A single `float(cost)` for a metric value is fine; a `float` in the budget accumulator
   compounds and eventually mis-halts a run.
6. **Not recording cache hits in `llm_calls`.** The hit rate becomes unmeasurable, the
   cliff-detection dashboard is empty, and per-agent attribution (T8) is wrong. Hits are
   rows too.
7. **Charging budget as calls land.** Completion order varies. If budget is charged on
   arrival, two runs with the same seed exhaust the budget at *different* calls and diverge.
   Charge under one lock in request order at the end of the phase (`09 §4.5`).
8. **Template hash over raw bytes.** Windows checkout → different hash → 100% cache miss on
   a cache you paid thousands of dollars to build. `09 §8.3`'s normalisation is not
   optional and is the single most likely way this system silently loses its cache.
9. **Putting `rendered_hash` in the cache key.** It is stored *beside* the record for
   verification. Putting it in the key defeats the point — a whitespace change would become
   a silent cache miss instead of a loud `CacheRenderMismatch`.
10. **`jsonschema` draft default.** Without an explicit `Draft202012Validator`, the library
    picks a draft from `$schema` or defaults to an older one, and `prefixItems`,
    `unevaluatedProperties` and `additionalProperties: false` behave differently. Pin the
    validator class.
11. **Assuming MiniMax supports JSON schema.** It does not on the M2.x line, and `stream`
    and `response_format` are mutually exclusive anyway (`09 §2.2`). The hot path is
    `repair`, not `constrain`. Building for `constrain` and discovering this in M1 costs a
    rewrite of the highest-volume path in the system.
12. **Using Ollama's OpenAI-compatible endpoint.** `/v1/chat/completions` accepts
    `response_format.json_schema` and **ignores** it. The output looks fine most of the time
    and drifts under load. Use `/api/chat` with `format`.
13. **Embedding dimension drift.** `memories.embedding` is `vector(768)`. Changing the
    embedding model to a 1024-d one is a **schema migration**, not a config change. Assert
    on first call and refuse to start.
14. **Repair prompt bloat.** Appending the whole schema on each repair doubles the prompt
    and rarely helps. Append only the validator's error path and message.
15. **Provider retrying internally.** A provider that sleeps or retries makes the router's
    budget accounting, breaker statistics and latency percentiles all wrong. Providers are
    stateless wire adapters; policy lives in the router (`09 §2.1` rule 3).
16. **Deferred calls awaited opportunistically.** `POST_WRITE` must be awaited at a **fixed**
    one-tick delay. "Await when convenient" makes the tick at which a post appears depend on
    latency, which is nondeterminism entering through the back door.
17. **Sizing the semaphore from a config guess rather than `Capabilities`.** Ollama Cloud's
    3/10 concurrent-run ceiling is a hard architectural constraint, not a tuning knob
    (`09 §4.4`). A routing table that ignores it produces a 108-second tick and a 54-day
    run, discovered a week in. `validate_sizing` at startup is what prevents that.
18. **`usd_halt_multiple` reached without the DEGRADE having fired.** That means the
    accounting is wrong, not that the budget was tight. Log it as an accounting bug, not a
    routine halt, and include the per-line utilisation in the halt reason.
