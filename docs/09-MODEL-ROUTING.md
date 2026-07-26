# POLIS — Model Routing, Caching, and Cost Control

**Version:** 1.0
**Status:** Normative for `polis/llm/`. Binding on every chunk that issues an LLM call.
**Owner module:** `polis/llm/` — `router.py`, `providers/`, `cache.py`, `budget.py`, `structured.py`, `prompts.py`
**Depends on:** `02-ARCHITECTURE.md` (§4.4 completion cache, §5 tick loop, §7.1 dependency rules, §8 config), `03-DATA-MODEL.md` (§1.1 `runs`, §1.3 `llm_calls`, §1.4 `completion_cache`), `04-AGENT-SPEC.md` (§7 salience routing, §9 deliberate, §10 reflect, §13 prompt discipline)
**Consumed by:** `06-ECONOMY-SPEC.md` (`VC_EVAL`, optional `CREDIT_EVAL`), `07-SOCIETY-SPEC.md` (`POST_WRITE`, `NEWS_WRITE`, `JUDGE`, `IMPORTANCE`), `08-EXTERNAL-AGENT-PROTOCOL.md` (external budget line)

> No module outside `polis/llm/` decides which model runs. No module outside
> `polis/llm/providers/` imports a vendor SDK. Both rules are enforced in CI (§8.5).

---

## 0. Scope, and requests on the shared specification

### 0.1 What this document owns

The single entry point for every LLM call in the system:

```python
result = await router.call(purpose, agent_id, tick, variables, schema_name)
```

Everything between that call and the wire — prompt rendering, cache lookup, provider
selection, concurrency, retries, structured-output repair, budget accounting, and the
`llm_calls` row — is owned here. Callers supply a **purpose** and **variables**. They never
supply a model, a temperature, a base URL, or a token limit.

### 0.2 Requests on the shared specification

Following the convention of `06-ECONOMY-SPEC.md §0.2`, everything this document needs from
a shared, already-frozen spec is declared here rather than assumed.

| Item | Where it lives | Request | Justification |
|---|---|---|---|
| Kind range **4100–4199**, owner `polis.llm` | `02-ARCHITECTURE.md §3.2` | **Reserve a sub-range.** 4001–4099 is already assigned to perception/salience under `polis.agents`; 4100–4199 is unused. | `02-ARCHITECTURE.md §10` already names `LLM_CALL_FAILED` and `BUDGET_EXHAUSTED` as events, but no kind range owns them. Router failures must be in the log or degradation is invisible to replay. |
| `llm.budget.lines` sub-map | `02-ARCHITECTURE.md §8` | **Extend the config block.** Existing flat keys remain valid and become the `cognition` line's defaults. | `04-AGENT-SPEC.md §7` already states external agents draw "from a separate budget line". Ancillary purposes (`IMPORTANCE`, `POST_WRITE`, `EMBED`) must not compete with `DELIBERATE` for the same cap, or a busy news cycle silently reduces the deliberate rate and contaminates T8. |
| `llm.routing.EMBED` moves to a **local** provider | `02-ARCHITECTURE.md §8` | **Change the default** from `ollama / embeddinggemma:cloud` to `ollama_local / embeddinggemma`. | ~400 embed calls/tick (§3.2) against Ollama Cloud's 3–10 concurrent-run ceiling (§4.4) is infeasible by two orders of magnitude. Embeddings are the cheapest thing in the system to run locally. |
| `llm.max_concurrency` becomes **per-lane** | `04-AGENT-SPEC.md §9.3` | **Replace the scalar** with `providers.<name>.max_concurrency`. The scalar becomes the default for a lane that does not set one. | A single global value cannot express "90 concurrent to MiniMax, 3 to Ollama Cloud". §4.4 shows why this is not a tuning preference but a correctness constraint. |
| `CREDIT_EVAL` as an **optional eleventh purpose** | `06-ECONOMY-SPEC.md §0.2` | **Accept as declared.** Disabled by default; enabled by `banking.underwriting: llm`. | Already requested by the economy spec. Recorded in §3.1 so the enum has exactly one definition. |
| `llm_calls.provider_request_id TEXT`, `llm_calls.budget_line TEXT` | `03-DATA-MODEL.md §1.3` | **Add two nullable columns.** | `02-ARCHITECTURE.md §4.4` already requires the cache to store `provider_request_id`; without it on the call row you cannot reconcile a provider invoice against a run. `budget_line` is needed for per-line attribution (§9.2). |

### 0.3 Reconciliations

Three numeric inconsistencies exist across `01`–`03`. This document is the cost document,
so it resolves them normatively rather than inheriting them.

| # | Conflict | Resolution |
|---|---|---|
| **R1** | `02-ARCHITECTURE.md §5.2` gives **8,640 ticks/sim-year** in `microscope` and its config comments 43,200 ticks as *five* sim-years. `03-DATA-MODEL.md §10–11` uses **43,200 ticks** as *one* sim-year. | `02 §5.2` is authoritative for tick semantics: **microscope = 8,640 ticks/sim-year, chronicle = 360 ticks/sim-year.** `03 §11`'s 43,200 is the **reference run length** (5 microscope sim-years) and its storage table should be read as per-run, not per-sim-year. All costs below are given per sim-year *and* per 43,200-tick reference run. |
| **R2** | `02 §8` sets `tokens_per_tick: 120_000` and `calls_per_tick: 90`. At `max_prompt_tokens: 3000` (`04 §9.1`) plus ~300 completion tokens, the token cap binds at **36 calls/tick**, not 90 — a 3.6% deliberate rate, not the 7% in `01-PRD.md §6.2`. | Both caps stay. The router reports which one bound (`llm.budget.binding_constraint`). To realise a 7% deliberate rate at 1,000 agents and a 3,000-token prompt, `tokens_per_tick` must be **≥ 231,000**; the recommended default becomes **240,000** for `microscope`. The shipped `120_000` is retained as the *low-cost* profile and is what §7's $12/sim-year figure assumes. |
| **R3** | `01-PRD.md §7.1` targets **≤ $12 per simulated year**; `02 §8` sets `usd_per_run: 60.0`. | Both are correct only under `chronicle`. §7.3 shows the $12 figure is exactly the `chronicle` + `tokens_per_tick: 120_000` + MiniMax M2.7 combination. In `microscope` the same policy costs **~$313/sim-year**. The PRD target must be read as **profile-conditional** and is annotated as such in every cost report. |

---

## 1. Design goals

| # | Goal | Concrete test |
|---|---|---|
| **L1** | **Model-agnosticism (G7).** Changing the model behind any purpose is a config edit. | `grep -riE '(minimax\|ollama\|qwen\|gemma\|glm\|deepseek\|gpt-\|claude)' polis/ --exclude-dir=llm/providers` returns nothing. Same grep over `prompts/` returns nothing. |
| **L2** | **Hard cost bounds (G4).** A run cannot exceed its dollar budget. | Enforcement is in the router, before the wire, not in the agent layer. A run at 100% of `usd_per_run` degrades; at 120% it halts (`01-PRD.md §11`). |
| **L3** | **Determinism via cache (G2).** Two runs of `(config, seed, model_manifest, cache)` produce identical hash chains. | `tests/determinism/` runs 200 ticks against `StubProvider` and byte-compares chains; `tests/integration/` replays a recorded cache. |
| **L4** | **Graceful degradation.** Exhaustion and provider failure reduce fidelity; they never crash, truncate silently, or corrupt state. | Every degradation path emits an event and increments a reported statistic. `--llm-chaos` injects failures at a configured rate and the run must still complete. |
| **L5** | **Multi-family robustness (T5, V7).** A headline finding must survive a change of model family. | §10. Runs with mixed model versions are tagged and cannot be pooled. |
| **L6** | **Legibility (G6).** For any agent-tick a researcher can see the exact prompt, response, cost, latency, cache status, and which model produced it. | `llm_calls` + `runs.prompt_manifest` reconstruct the prompt without storing it (`03 §1.3`). |

L3 and L5 are in tension with L2: the cache is what makes replay free, but the cache key
contains the model and the seed, so **neither seed replication (V5) nor family replication
(V7) gets any cache benefit at all**. §5.4 and §7.5 quantify what that costs.

---

## 2. The provider abstraction

### 2.1 Protocol

```python
# polis/llm/providers/base.py
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Mapping, Protocol, Sequence

StructuredMode = Literal["schema", "json_mode", "none"]
Billing        = Literal["token", "gpu_time", "free"]

@dataclass(frozen=True, slots=True)
class Capabilities:
    context_window:            int
    max_output_tokens:         int
    structured_output:         StructuredMode
    prefix_caching:            bool
    max_concurrency:           int          # hard ceiling; the lane semaphore is sized from this
    rpm_limit:                 int | None
    tpm_limit:                 int | None
    supports_embeddings:       bool
    embedding_dim:             int | None   # MUST equal 768 for EMBED (03-DATA-MODEL §2.3)
    billing:                   Billing
    price_in_usd_per_mtok:     Decimal
    price_out_usd_per_mtok:    Decimal
    price_cached_in_usd_per_mtok: Decimal | None
    reports_model_version:     bool
    supports_call_seed:        bool

@dataclass(frozen=True, slots=True)
class SamplingParams:
    temperature: float
    top_p:       float = 1.0
    max_tokens:  int   = 512
    stop:        tuple[str, ...] = ()
    seed:        int | None = None          # forwarded iff capabilities.supports_call_seed

@dataclass(frozen=True, slots=True)
class CompletionRequest:
    purpose:    str                          # Purpose enum value
    system:     str
    user:       str
    schema:     Mapping[str, Any] | None     # JSON Schema; None for free-text purposes
    sampling:   SamplingParams
    call_seed:  int                          # rng.get("llm", agent_id, tick)
    timeout_s:  float

@dataclass(frozen=True, slots=True)
class CompletionResponse:
    text:                str
    tokens_in:           int
    tokens_out:          int
    tokens_cached_in:    int
    model_version:       str | None
    provider_request_id: str | None
    latency_ms:          int
    finish_reason:       Literal["stop", "length", "content_filter", "error"]

class Provider(Protocol):
    name:         str                        # lane name, e.g. "minimax", "ollama_cloud"
    model:        str
    capabilities: Capabilities

    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def health(self) -> HealthReport: ...
    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal: ...
```

**Rules.**

1. `price()` returns `Decimal`, never `float`. This is the **only** place USD appears in the
   codebase; it is API spend, not simulation currency, and must never touch `Money`
   (`02-ARCHITECTURE.md §4.6`). It lands in `llm_calls.cost_usd NUMERIC(12,8)`.
2. A provider raises exactly four exception classes: `ProviderTransient` (retryable),
   `ProviderPermanent` (do not retry — auth, bad model name, schema rejected),
   `ProviderRateLimited` (carries `retry_after_s`), `ProviderTimeout`. Anything else
   escaping a provider is a bug and propagates (`02-ARCHITECTURE.md §10`).
3. A provider **never** retries internally, never sleeps, never logs to `llm_calls`, and
   never reads config. It is a stateless wire adapter. Policy lives in the router.
4. `embed()` on a provider whose `supports_embeddings` is `False` raises `NotImplementedError`
   at import time via a protocol check, not at call time.

### 2.2 Capability matrix (verified 2026-07-24)

| Lane | Model | Context | Structured | Prefix cache | Concurrency | Billing | $/M in | $/M out |
|---|---|---|---|---|---|---|---|---|
| `minimax` | `MiniMax-M2.7` | 204,800 | **`none`** (§6.1) | yes (cache read $0.06/M) | ~90 (RPM-bound) | token | 0.30 list / 0.24 promo | 1.20 list / 0.96 promo |
| `minimax` | `MiniMax-M2` | 204,800 | **`none`** | yes | ~90 | token | 0.26 | 1.00 |
| `ollama_cloud` | `*:cloud` | model-dependent | **`schema`** (native `/api/chat`) | no | **3 (Pro) / 10 (Max)** | gpu_time | flat | flat |
| `ollama_local` | any local tag | model-dependent | **`schema`** | n/a | `num_parallel` | free | 0 | 0 |
| `openai_compat` | configured | configured | configured | configured | configured | token | configured | configured |
| `stub` | `stub-v1` | 1,000,000 | **`schema`** | n/a | unbounded | free | 0 | 0 |

Two entries in this table are load-bearing and counter-intuitive; both are verified against
vendor documentation (Sources):

- **MiniMax M2.x does not support schema-constrained decoding on its OpenAI-compatible
  endpoint.** `response_format: {"type":"json_schema"}` is documented as supported only by
  `MiniMax-Text-01`, and is explicitly unsupported on the M2.x line. `stream` and
  `response_format` are additionally mutually exclusive. Consequence: **the highest-volume
  purpose in the system runs without grammar constraints** and depends entirely on the
  repair loop (§6.2). This is the single biggest reliability risk in `polis/llm/`.
- **Ollama's OpenAI-compatible `/v1/chat/completions` ignores `response_format.json_schema`.**
  Schema constraint works only through the native `/api/chat` `format` field, which compiles
  the schema to a GBNF grammar. `OllamaProvider` therefore speaks the **native** API, not the
  OpenAI-compatible one, despite both being served on port 11434.

### 2.3 `MiniMaxProvider`

```yaml
providers:
  minimax:
    kind: minimax
    base_url: "https://api.minimax.io/v1"        # OpenAI-compatible
    api_key_env: MINIMAX_API_KEY
    max_concurrency: 90
    rpm_limit: 500
    tpm_limit: 2_000_000
    timeout_s: 45
    model_version_pin: "MiniMax-M2.7-2026-03-18"
    structured_output: none                       # see §2.2
    prefix_cache_hint: true                       # emit stable-prefix ordering
```

- Speaks the OpenAI Chat Completions shape at `https://api.minimax.io/v1`. An
  Anthropic-shaped endpoint also exists at `/anthropic`; POLIS does not use it — one wire
  format per lane, and the OpenAI shape is what `OpenAICompatProvider` already implements.
- `structured_output: none` is not a configuration choice, it is a fact about the model line
  (§2.2). Setting it to `schema` is a config error and fails validation at startup.
- Never streams. Streaming buys nothing when the whole response is parsed before use, and it
  is mutually exclusive with `response_format` on this API.
- `model_version` is taken from the response body when present, otherwise from
  `model_version_pin`. If neither exists and `cache.strict_version: true`, startup fails —
  an unversioned model cannot be cached safely (T5, §12).

### 2.4 `OllamaProvider`

One class handles both cloud and local. The **only** difference is the model tag: a `:cloud`
suffix routes through `ollama.com`; anything else runs on the configured host. This is a
property of Ollama's design and is why one adapter suffices.

```yaml
providers:
  ollama_cloud:
    kind: ollama
    base_url: "https://ollama.com"
    api_key_env: OLLAMA_API_KEY
    max_concurrency: 3          # Pro tier. Max tier = 10. NOT a tuning knob — see §4.4.
    timeout_s: 90
    structured_output: schema
    billing: gpu_time
    tier: pro                   # free | pro | max
  ollama_local:
    kind: ollama
    base_url: "http://127.0.0.1:11434"
    max_concurrency: 8          # = OLLAMA_NUM_PARALLEL
    timeout_s: 30
    structured_output: schema
    billing: free
```

- Uses `POST /api/chat` with the `format` field carrying the JSON Schema object. Does **not**
  use `/v1/chat/completions` (§2.2).
- Uses `POST /api/embed` for `EMBED`. The returned dimension is asserted against
  `capabilities.embedding_dim` on first call and must be **768** to match
  `memories.embedding vector(768)` (`03-DATA-MODEL.md §2.3`). Changing the embedding model to
  one with a different dimension is a **schema migration**, not a config change, and the
  router refuses to start on a mismatch.
- `billing: gpu_time` means `price()` returns `Decimal(0)` and the call consumes the
  `tokens_per_tick` line but **not** `usd_per_run`. Subscription cost is amortised out of
  band and reported separately as `llm.cost.subscription_amortised_usd`.
- Cloud tier concurrency is read from config and cross-checked against observed 429 rate. A
  lane that sustains >5% rate-limit responses over 100 ticks emits a `PROVIDER_TIER_MISMATCH`
  warning naming the likely tier.

### 2.5 `OpenAICompatProvider`

Generic fallback for any endpoint speaking OpenAI Chat Completions: a self-hosted vLLM or
SGLang server, an aggregator, or a future vendor. It exists so that adding a provider is a
config entry rather than a code change, which is the mechanical guarantee behind G7.

```yaml
providers:
  vllm_local:
    kind: openai_compat
    base_url: "http://gpu-01:8000/v1"
    api_key_env: VLLM_API_KEY
    max_concurrency: 64          # = --max-num-seqs
    structured_output: schema    # vLLM guided decoding
    supports_call_seed: true
    billing: free
```

It carries no vendor quirks. If a provider needs a quirk, it gets its own subclass in
`polis/llm/providers/`; quirks never leak into the router.

### 2.6 `StubProvider` — mandatory test infrastructure

`02-ARCHITECTURE.md §12` declares this mandatory. It is specified here because it is the
only provider the entire test suite ever sees, and a weak stub silently disables the
determinism, invariant, and integration tiers.

**Contract.**

```python
# polis/llm/providers/stub.py
class StubProvider:
    """Deterministic fake. response = f(cache_key) and nothing else."""
    name = "stub"
    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        key  = stub_key(req)                       # sha256 over the same tuple as the cache key
        rng  = random.Random(int.from_bytes(key[:8], "big"))
        fault = self._fault_for(key)               # deterministic fault injection, §2.6.4
        if fault: raise fault
        text = synthesise(req.schema, req.user, rng) if req.schema else lorem(rng)
        return CompletionResponse(
            text=text,
            tokens_in=len(req.system) // 4 + len(req.user) // 4,
            tokens_out=len(text) // 4,
            tokens_cached_in=0,
            model_version="stub-v1",
            provider_request_id=key.hex()[:16],
            latency_ms=0,
            finish_reason="stop",
        )
```

**2.6.1 Determinism.** The response is a pure function of the request. No wall clock, no
`random` module global state, no network, no filesystem, no environment. Two processes on
two machines return byte-identical text for the same request. Latency is reported as `0`, so
`llm_calls.latency_ms` is constant and does not leak into any hash.

**2.6.2 Schema validity.** `synthesise()` walks the JSON Schema and emits a valid instance:
required properties in schema order, `enum` choices selected by `rng.choice`, strings drawn
from a fixed lorem corpus at the schema's `minLength`/`maxLength`, numbers uniform within
`minimum`/`maximum`, arrays at `minItems`. The result is validated against the schema before
return; a stub that emits an invalid instance is a test-suite bug and raises.

**2.6.3 Prompt-grounded choices.** This is the requirement that makes the stub useful rather
than decorative. For `DELIBERATE`, the `action.type` **must** be chosen from the legal-action
list rendered into the prompt, and `action.params` must reference IDs that appear in the
prompt. A stub that picks from the global `ActionType` enum produces actions that every
PHASE 4 gate rejects, and the integration suite then exercises nothing but the rejection
path. The stub therefore parses the `## What you can do` block out of the rendered prompt and
samples from it, and extracts `ag_`/`fm_`/`pl_`/`st_` prefixed IDs (`03-DATA-MODEL.md §0`) to
fill parameters. Behaviour is arbitrary but *legal*, which is exactly what a determinism or
invariant test needs.

**2.6.4 Fault injection.** Seeded from the key, so the same key always fails the same way and
a failure is reproducible:

```yaml
providers:
  stub:
    kind: stub
    failure_rate:      0.00   # ProviderTransient
    permanent_rate:    0.00   # ProviderPermanent
    timeout_rate:      0.00   # ProviderTimeout
    ratelimit_rate:    0.00   # ProviderRateLimited
    malformed_rate:    0.00   # returns text that is not JSON -> exercises the repair loop
    schema_drift_rate: 0.00   # returns valid JSON violating the schema -> exercises repair + reflex
    truncate_rate:     0.00   # finish_reason="length"
```

`--llm-chaos` sets all seven to 0.05. Every degradation path in §4 and §6 has a test that
runs under chaos and asserts the run completes, the invariants hold, and the counters match.

**2.6.5 Enforcement.** Under `POLIS_ENV=test`, the router refuses to instantiate any provider
other than `stub` unless the test is marked `@pytest.mark.live_llm`. CI additionally runs the
suite with outbound sockets blocked; a test that opens one fails.

---

## 3. Call purposes

### 3.1 The enum

```python
# polis/llm/purposes.py
class Purpose(StrEnum):
    DELIBERATE      = "DELIBERATE"
    REFLECT         = "REFLECT"
    IMPORTANCE      = "IMPORTANCE"
    POST_WRITE      = "POST_WRITE"
    NEWS_WRITE      = "NEWS_WRITE"
    VC_EVAL         = "VC_EVAL"
    JUDGE           = "JUDGE"
    EMBED           = "EMBED"
    SIM_AWARE_CHECK = "SIM_AWARE_CHECK"
    SUMMARISE       = "SUMMARISE"
    CREDIT_EVAL     = "CREDIT_EVAL"   # optional; enabled by banking.underwriting: llm
```

Closed. Adding a member requires a spec change, a routing entry, a prompt template, a JSON
schema, a paraphrase sibling, and a volume estimate in §3.2. `llm_calls.purpose` stores the
string; `runs.model_manifest` stores `{purpose: {provider, model, version}}` for all eleven
regardless of whether they fired, so a run is comparable to another run on the manifest alone.

### 3.2 The purpose table

Volumes are per tick at 1,000 agents in `microscope`. "Latency" is sensitivity to the
PHASE 3 budget of 3,000 ms (`02-ARCHITECTURE.md §11`); "Quality" is how much a worse model
degrades the research result.

| Purpose | What it does | Vol/tick | Latency | Quality | Lane / model | Temp | Max tok | Structured | Line |
|---|---|---|---|---|---|---|---|---|---|
| `DELIBERATE` | Observation + memory + goals → one typed action (`04 §9`) | **~70** | **critical** | **critical** | `minimax` / `MiniMax-M2.7` | 0.8 | 700 | required | cognition |
| `REFLECT` | Compress memory → insights, identity, beliefs, goals (`04 §10`) | **~10** | critical | **critical** | `minimax` / `MiniMax-M2.7` | 0.9 | 900 | required | cognition |
| `IMPORTANCE` | Score up to 20 candidate memories in one call (`04 §6.2`) | ~10 | low | low | `ollama_cloud` / `qwen3.5:cloud` | 0.0 | 200 | required | ancillary |
| `POST_WRITE` | Render a chosen `POST` action into platform-native text (`07 §3`) | ~8 | low | medium | `ollama_cloud` / `gemma4:cloud` | 1.0 | 200 | required | ancillary |
| `NEWS_WRITE` | Write an article from source events with an outlet's slant (`07 §4.3`) | ~0.5 | none | high | `minimax` / `MiniMax-M2` | 0.7 | 900 | required | ancillary |
| `VC_EVAL` | Evaluate a pitch → invest / pass / term sheet (`06 §8.5`) | ~0.2 | none | high | `minimax` / `MiniMax-M2.7` | 0.4 | 500 | required | ancillary |
| `JUDGE` | Render a verdict from evidence event seqs (`07 §8.7`) | ~0.1 | none | **critical** | `minimax` / `MiniMax-M2.7` | 0.2 | 600 | required | ancillary |
| `EMBED` | 768-d vector for every written memory (`04 §6.3`) | **~400** | medium | low | `ollama_local` / `embeddinggemma` | — | — | n/a | free |
| `SIM_AWARE_CHECK` | T3 classifier over responses that pass a regex prefilter (`03 §1.3`) | ~2 | none | low | `ollama_local` / `qwen3.5:4b` | 0.0 | 16 | required | free |
| `SUMMARISE` | Compress an over-long context block to fit `max_prompt_tokens` | ~1 | medium | medium | `ollama_cloud` / `qwen3.5:cloud` | 0.3 | 300 | none | ancillary |
| `CREDIT_EVAL` | Loan underwriting when `banking.underwriting: llm` (`06 §7.4`) | 0 / ~1 | none | high | `minimax` / `MiniMax-M2` | 0.2 | 400 | required | ancillary |

Cognition line total ≈ **80 calls/tick**, inside `calls_per_tick: 90` with headroom for
force-routed mandatory obligations (`04-AGENT-SPEC.md §7`). Ancillary ≈ **20 calls/tick**
(21 with `CREDIT_EVAL` enabled), inside `calls_per_tick: 24`. Free line ≈ **402 calls/tick**,
all local.

### 3.3 Why each recommendation

| Purpose | Justification |
|---|---|
| `DELIBERATE`, `REFLECT` | These *are* the research instrument. A cheap model here produces mode collapse (V4), schema drift (§6.4), and simulation-awareness leakage (T3) — three of the four ways the platform fails. They are also latency-critical: 70 calls must clear inside a 3 s phase, which requires ~90-way concurrency, which Ollama Cloud structurally cannot provide (§4.4). MiniMax M2.7 is the correct default because it is explicitly trained for long-horizon agentic tool-use workflows, has a 205K context (7× the 3,000-token prompt cap, so context is never the binding constraint), and at ~$0.001/call is ~3 orders of magnitude cheaper than a frontier US model at the same call volume. |
| `IMPORTANCE` | A bounded numeric scoring task over short text, batched 20:1, at temperature 0. Quality sensitivity is genuinely low: the heuristic tier already handles ~95% of writes and the LLM tier only adjudicates the ambiguous band `[0.4, 0.7]` (`04 §6.2`). It needs schema constraint, which Ollama has and MiniMax does not — so routing it to the cheap lane also improves reliability. |
| `POST_WRITE` | Short, high-temperature, stylistically varied text with no institutional consequence. `07-SOCIETY-SPEC.md §4.5` computes `posts.truthfulness` against the event log, so quality is measured downstream rather than assumed. Cheap lane, high temperature, schema-constrained for the stance fields. |
| `NEWS_WRITE` | Low volume (4 outlets, 1 story/reporter/cycle, daily) but high quality sensitivity: articles carry `slant_applied` and `accuracy` that feed B1/B2. `07 §11` already reports refusal patterns for this purpose — a model that declines to write slanted copy silently kills the polarisation experiment. MiniMax M2 rather than M2.7 because the task is generative rather than agentic and M2 is 20% cheaper; the same family keeps V7 arms clean. |
| `VC_EVAL`, `JUDGE`, `CREDIT_EVAL` | Rare, consequential, adversarially interesting, and the direct object of A6/B5. Low temperature, best available model, no cost pressure at 0.1–1 calls/tick. `JUDGE` is quality-critical because `07 §8.7` degrades to a mechanical bench rule on failure and `court.bench_share > 0.3` invalidates any judicial-bias finding. |
| `EMBED` | Volume dominates everything else (~400/tick, ~3.5M/sim-year) and quality barely matters — retrieval reranks the ANN top-100 with recency and importance anyway (`04 §6.3`). Any cloud lane at this volume is either rate-limited into the ground or expensive. Local, batched 64:1, ~768-d. |
| `SIM_AWARE_CHECK` | Off the critical path entirely: it runs in PHASE 9 over the tick's completed responses, after a regex prefilter that eliminates ~98%. A 4B local model is sufficient for a binary flag whose *rate* is the reported statistic. |
| `SUMMARISE` | Rare, mechanical, latency-tolerant by one tick. Its existence is what keeps `max_prompt_tokens` a hard cap rather than a suggestion (`04 §9.1`). |

### 3.4 Routing config

Extends `02-ARCHITECTURE.md §8`. Keys shown there remain valid.

```yaml
llm:
  budget:
    lines:
      cognition:  {calls_per_tick: 90,  tokens_per_tick: 240_000}   # see R2
      ancillary:  {calls_per_tick: 24,  tokens_per_tick:  40_000}
      external:   {calls_per_tick: 32,  tokens_per_tick: 100_000}   # 08-EXTERNAL-AGENT §5
      free:       {calls_per_tick: 512, tokens_per_tick: 0}          # local lanes only
    usd_per_run: 60.0
    usd_halt_multiple: 1.2
    on_exhaustion: degrade_to_reflex          # degrade_to_reflex | halt
  routing:
    DELIBERATE:
      lane: minimax
      model: MiniMax-M2.7
      temperature: 0.8
      max_tokens: 700
      structured: repair                       # constrain | repair | none
      schema: schemas/deliberate.schema.json
      template: deliberate
      fallback: [{lane: minimax, model: MiniMax-M2}]
      last_resort: reflex
    # ... one block per purpose
  fallback_policy: permissive                  # permissive | strict (headline runs)
  cache:
    mode: hybrid                               # live | replay | hybrid
    path: "s3://polis-cache/"
    schema_version: 1
    verify_render: true
    strict_version: true
    trust: verify                              # verify | trust
```

---

## 4. Routing policy

### 4.1 Resolution order

Per call, in order. The first step that produces a result wins.

```
1.  Resolve purpose -> RouteSpec (lane, model, params, schema, template)
2.  Render prompt from template + variables            -> prompt_template_hash, rendered_hash
3.  Compute cache_key (02-ARCHITECTURE.md §4.4, §5.1)
4.  L0 in-process LRU  -> hit? return
5.  Postgres completion_cache index -> hit? fetch blob, return
6.  mode == replay?  -> MISS is a HARD ERROR: emit 4106, HALT
7.  Budget admission (§4.6). Denied -> degrade per on_exhaustion
8.  Lane admission: circuit breaker (§4.3), semaphore (§4.4), rate limiter
9.  provider.complete()  -> on failure, fallback chain (§4.2)
10. Structured-output validate + repair loop (§6.2)
11. Write completion_cache; write llm_calls; charge budget; publish metrics
```

Steps 4–5 happen **before** budget admission: a cache hit is free and must never be
throttled. Steps 8–10 happen inside the lane semaphore so that repair retries count against
concurrency, which is how a repair storm is prevented from saturating a lane.

### 4.2 Fallback chains

```
primary  ->  fallback[0..n]  ->  last_resort  ->  degraded outcome
```

| Purpose | Primary | Fallback | Last resort | Degraded outcome |
|---|---|---|---|---|
| `DELIBERATE` | `minimax`/`M2.7` | `minimax`/`M2` | reflex | Agent uses its reflex policy for this tick (`04 §8`); action origin stays `deliberate` only if a model produced it, else `reflex` |
| `REFLECT` | `minimax`/`M2.7` | `minimax`/`M2` | skip | No reflection this trigger; the trigger re-arms next tick |
| `IMPORTANCE` | `ollama_cloud` | `ollama_local` | heuristic | Heuristic tier score is used (`04 §6.2`) — a designed, documented default, not a failure |
| `POST_WRITE` | `ollama_cloud` | `ollama_local` | drop | `POST` action is rejected with `reason: "llm_unavailable"` |
| `NEWS_WRITE` | `minimax`/`M2` | `ollama_cloud` | template | Mechanical headline from source events; `articles.slant_applied = 0`, flagged |
| `VC_EVAL` | `minimax`/`M2.7` | `minimax`/`M2` | pass | Investor declines; recorded as `mechanical_pass` |
| `JUDGE` | `minimax`/`M2.7` | `minimax`/`M2` | bench rule | `07 §8.7` bench rule; `court.bench_share` increments |
| `CREDIT_EVAL` | `minimax`/`M2` | — | scorecard | `06 §7.4` mechanical underwriting |
| `EMBED` | `ollama_local` | `ollama_cloud` | halt | Embeddings are not optional; retrieval is broken without them |
| `SIM_AWARE_CHECK` | `ollama_local` | — | regex only | Flag set from regex alone; run reports reduced detector fidelity |
| `SUMMARISE` | `ollama_cloud` | `ollama_local` | truncate | Hard truncation at the token cap, oldest-first |

**Three binding rules.**

1. **A fallback that changes the model changes the cache key.** The completion is stored
   under the *actual* `(provider, model, model_version, ...)` tuple, `llm_calls` records what
   actually ran, and the run is tagged `mixed_model`. `01-PRD.md §9` T5 already forbids
   pooling such runs; the router is what makes the tag automatic.
2. **`fallback_policy: strict` disables cross-model fallback entirely** and goes straight to
   `last_resort`. Headline runs and every V7 arm MUST use `strict`. A silent family switch
   mid-run is indistinguishable from the effect V7 is trying to measure.
3. **In `replay` mode there are no fallbacks and no network.** A cache miss is a halt (§5.3).

Retry policy before falling back: `ProviderTransient` and `ProviderTimeout` get 2 retries
with exponential backoff and full jitter, seeded from `call_seed` so backoff is reproducible.
`ProviderRateLimited` waits `retry_after_s` once, then falls back. `ProviderPermanent` never
retries.

### 4.3 Circuit breaker

One breaker per `(lane, model)`. States `closed → open → half_open → closed`.

| Parameter | Default | Meaning |
|---|---|---|
| `error_window_calls` | 50 | Rolling window |
| `error_rate_trip` | 0.40 | Open if the window's error rate exceeds this |
| `consecutive_trip` | 8 | Open immediately on this many consecutive failures |
| `open_ticks` | 20 | Sim-ticks before a probe |
| `half_open_probes` | 3 | Consecutive successes required to close |

Open → all traffic for that pair goes to the next chain entry, and `PROVIDER_CIRCUIT_OPENED`
(4103) is emitted with the lane, model, window statistics, and the tick. Closing emits 4104.
Both are in the log, so a replay of a run that experienced an outage reproduces the
degradation pattern rather than papering over it.

`ProviderPermanent` on the **first** call to a lane trips the breaker immediately: a wrong
model name or a dead API key should fail in the first tick, not after 8 retries per agent.

### 4.4 Concurrency, and why Ollama Cloud cannot carry the hot path

Each lane owns an `asyncio.Semaphore(capabilities.max_concurrency)` plus a token-bucket rate
limiter over RPM and TPM. `04-AGENT-SPEC.md §9.3`'s single `llm.max_concurrency` is replaced
by per-lane limits (§0.2).

```python
# polis/llm/lanes.py
@dataclass
class Lane:
    provider:  Provider
    sem:       asyncio.Semaphore      # sized from capabilities.max_concurrency
    rpm:       TokenBucket
    tpm:       TokenBucket
    breaker:   CircuitBreaker
    inflight:  int = 0
    queue_wait_ms_p95: float = 0.0
```

**The arithmetic.** PHASE 3 has a 3,000 ms p50 budget (`02-ARCHITECTURE.md §11`). Let *N* be
calls needed in the phase, *C* the lane's concurrency, *L* the per-call latency. Wall time is
approximately `ceil(N / C) × L`.

| Lane | C | L (300 out tokens) | N = 80 cognition calls | vs 3,000 ms budget |
|---|---|---|---|---|
| `minimax` | 90 | ~3.0 s | 1 wave × 3.0 s = **3.0 s** | at budget |
| `minimax` | 32 (old default) | ~3.0 s | 3 waves × 3.0 s = **9.0 s** | 3× over |
| `ollama_cloud` **Pro** | **3** | ~4.0 s | 27 waves × 4.0 s = **108 s** | **36× over** |
| `ollama_cloud` **Max** | **10** | ~4.0 s | 8 waves × 4.0 s = **32 s** | **11× over** |
| `ollama_local` (1 GPU) | 8 | ~6.0 s | 10 waves × 6.0 s = **60 s** | 20× over |

At 43,200 ticks, a 108 s tick is **54 wall-days** per reference run. This is not a tuning
problem. **Ollama Cloud's 3/10 concurrent-run ceiling is a hard architectural constraint that
excludes it from `DELIBERATE` and `REFLECT` at 1,000 agents**, and it is the reason MiniMax is
the primary reasoning provider rather than a cost preference.

The corresponding sizing rule, which the router enforces at startup:

```
max_calls_per_tick(lane) = floor(C × phase_budget_s / observed_p50_latency_s)
```

A routing table that assigns a purpose more calls/tick than its lane can carry fails
validation with the computed numbers in the error message. For `ollama_cloud` at Max tier
this yields ~7 calls/tick, which is why every ancillary purpose on that lane is either
batched (`IMPORTANCE` at 20:1) or sub-1/tick (`NEWS_WRITE`, `SUMMARISE`).

Two further consequences:

- **Ancillary calls are deferrable by one tick.** `POST_WRITE` and `SUMMARISE` are issued from
  PHASE 3 but awaited in PHASE 7, so their latency does not sit on the critical path. The
  deferral is deterministic (fixed one-tick delay), not opportunistic.
- **The 1 tick/s throughput target and the 90-call budget are jointly infeasible against
  published provider rate limits.** 90 calls/tick at 1 tick/s is 5,400 RPM and ~17.8 M TPM;
  documented MiniMax paid tiers are in the hundreds of RPM. Realistic sustained throughput is
  **~0.1–0.3 tick/s when LLM-bound**. The router records `llm.throughput.tick_seconds_llm_bound`
  every tick and `polis run` prints the projected wall-clock completion time at startup so the
  researcher learns this before, not after, launching a 43,200-tick run.

### 4.5 Ordering and determinism

Concurrency is I/O-only (`02-ARCHITECTURE.md §4.3`). The router returns results in an
arbitrary completion order; PHASE 3 sorts by `actor_id` before any mutation. The router
itself must not depend on completion order: budget charging is done under a single lock in
`stable()` order at the end of the phase, not as calls land, so that a run whose calls return
in a different order still exhausts the budget at the same call.

### 4.6 Budget admission and the degradation ladder

Budget is enforced **in the router**, never in the agent layer (`01-PRD.md §11`). PHASE 2
allocates *intent*; the router enforces *reality*, and they can disagree when repair retries
or fallbacks consume more than the allocation assumed.

```python
# polis/llm/budget.py
class BudgetGuard:
    def admit(self, line: str, est_in: int, est_out: int, est_usd: Decimal) -> Admission:
        """PERMIT | DEGRADE | HALT. Checked before every wire call, including retries."""
```

| Trigger | Action |
|---|---|
| Line `calls_per_tick` reached | Remaining calls on that line → `DEGRADE`. Emit `BUDGET_EXHAUSTED` (4102) once per line per tick with the count degraded. |
| Line `tokens_per_tick` reached | As above. `llm.budget.binding_constraint` records which cap bound (R2). |
| `usd_per_run` reached | Every token-billed lane → `DEGRADE` for the rest of the run. Free lanes continue, so embeddings and memory keep working and the run remains analysable. |
| `usd_per_run × usd_halt_multiple` reached | `HALT`, checkpoint, exit non-zero. Reaching this means accounting is wrong, since the previous rule should have stopped spend. |
| `on_exhaustion: halt` | The first `DEGRADE` becomes a `HALT` instead. For runs where a partial-fidelity result is worse than no result. |

`DEGRADE` semantics per purpose are exactly the `last_resort` column of §4.2. Degradation is
never silent: `llm.degraded_calls.{purpose}` and `llm.degraded_agent_share` are per-tick
metrics, and `01-PRD.md §9` T9 requires the LLM-attributable share of behaviour to be
reported — a run that spent 60% of its ticks degraded is a different experiment and the
report says so.

**Cost estimation before the call.** `est_usd` uses the rendered prompt's actual token count
and `max_tokens` as a pessimistic output estimate. Over-estimating output is deliberate: the
budget should bind early rather than overshoot.

---

## 5. The completion cache

`02-ARCHITECTURE.md §4.4` defines the mechanism. This section specifies it.

### 5.1 Key construction

```python
cache_key = sha256(
    provider.encode()                      + b"\x1f" +
    model.encode()                         + b"\x1f" +
    (model_version or "").encode()         + b"\x1f" +
    prompt_template_hash.encode()          + b"\x1f" +
    canonical_json(prompt_variables)       + b"\x1f" +
    canonical_json(sampling_params)        + b"\x1f" +
    str(call_seed).encode()
).hexdigest()
```

- `canonical_json` is `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)`
  — byte-identical to the event canonicalisation in `02-ARCHITECTURE.md §3.1`. One
  canonicaliser in the codebase, in `polis/kernel/det.py`.
- `prompt_template_hash` is the sha256 of the **template source**, taken from
  `runs.prompt_manifest`, not of the rendered text. Template + variables determines the render.
- `sampling_params` includes `temperature`, `top_p`, `max_tokens`, `stop`, and the schema hash
  when structured output is requested. It excludes timeouts and retry counts, which do not
  affect the distribution.
- `call_seed = rng.get("llm", agent_id, tick)` (`02-ARCHITECTURE.md §4.1`). For non-agent
  purposes the entity id is the institution: `rng.get("llm", outlet_id, tick)` for
  `NEWS_WRITE`, `rng.get("llm", case_id, tick)` for `JUDGE`.
- **`rendered_hash` is stored alongside but not in the key.** On a hit with
  `cache.verify_render: true` the router recomputes `sha256(rendered_prompt)` and compares. A
  mismatch means the renderer changed behaviour without the template changing (Jinja version,
  whitespace policy, locale) and raises `CacheRenderMismatch`. This is the practical defence
  against a whole class of silent reproducibility failures.

### 5.2 Storage layout

| Tier | Where | Contents | Eviction |
|---|---|---|---|
| **L0** | In-process `OrderedDict` LRU, 50,000 entries | Full response | LRU |
| **L1** | Postgres `completion_cache` (`03-DATA-MODEL.md §1.4`) | Index + inline blob ≤ 64 KB | Never automatic |
| **L2** | Object store | Blobs > 64 KB, and the published bundle | Manual, logged |

Object path: `<root>/v<schema_version>/<provider>/<model>/<key[0:2]>/<key[2:4]>/<key>.json`.
Two levels of fan-out keep any prefix under ~10k objects at 4M completions.

Record body:

```json
{"schema_version": 1, "key": "…", "provider": "minimax", "model": "MiniMax-M2.7",
 "model_version": "MiniMax-M2.7-2026-03-18", "rendered_hash": "…",
 "response_text": "…", "tokens_in": 3012, "tokens_out": 287, "tokens_cached_in": 812,
 "finish_reason": "stop", "provider_request_id": "…", "created_at": "…"}
```

Entries are **immutable**. `hit_count` on the index row is the only mutable field, and it is
incremented out of band so it never affects the key or the blob.

### 5.3 Modes

| Mode | Miss behaviour | Writes? | Use |
|---|---|---|---|
| `live` | Call the provider | yes | First run of a new config |
| `replay` | **HARD ERROR** — emit `CACHE_MISS_IN_REPLAY` (4106), checkpoint, halt | **no** | Reproducing a published figure; third-party verification. Zero API spend, zero network. |
| `hybrid` | Call the provider | yes | Sweeps, resumed runs, warm starts |

In `replay`, the router is constructed with **no provider instances at all**. There is no
code path from `replay` to a socket, which is what makes "reproduces the figures with zero
API spend" a structural guarantee rather than a promise.

### 5.4 Hit-rate expectations

The cache is **trajectory-fragile**: one different token changes the agent's action, which
changes state, which changes `prompt_variables` for every subsequent call. Hit rate is
therefore not a smooth fraction — it is ~1.0 until the first divergence and ~0 after it.

| Scenario | Expected hit rate | Why |
|---|---|---|
| Replay of the same run | 1.00 | By construction; a miss is a bug |
| Re-run after a code change that does not touch prompts or state | ~1.00 | Same keys throughout |
| Resume from checkpoint | 1.00 over the replayed prefix | Same keys |
| Sweep over a parameter that first bites at tick *T* | ≈ `T / total_ticks` | Cliff at *T* |
| Sweep over a parameter that changes prompts immediately (`feed_algorithm`, `outlets`) | ~0 for `DELIBERATE`; 0.3–0.6 for `IMPORTANCE`/`EMBED` | Ancillary prompts repeat over identical text |
| **Seed sweep (V5, ≥20 seeds)** | **0.00** | `call_seed` is in the key, and the trajectory differs anyway |
| **Model-family sweep (V7)** | **0.00** | `provider` and `model` are in the key |
| Paraphrase arm (V6) | 0.00 | Different template hash — by design |

`02-ARCHITECTURE.md §4.4` claims sweeps get a 5–20× cost reduction. That holds for
**late-biting single-parameter sweeps at a fixed seed**. It does not hold for the three
sweeps the validity gates actually mandate. §7.5 prices that honestly.

### 5.5 Cache warming

```
polis cache warm --config baseline.yaml --ticks 2000     # live prefix, shared by all cells
polis sweep --base baseline.yaml --grid grid.yaml --cache-mode hybrid
polis cache pull  s3://polis-cache/published/<paper>/    # third-party reproduction
polis cache stats --run <id>                             # hit rate, cliff tick, bytes
```

A sweep run starts by warming a common prefix at the shared configuration, so every cell gets
the pre-divergence portion free. Warming is worth doing exactly when the swept parameter
bites late; `polis sweep` estimates the cliff tick from the parameter's declared
`first_effect_tick` in the grid file and prints the projected saving before spending anything.

### 5.6 Invalidation

There is no TTL. Entries are content-addressed, so "invalidation" only ever means "the key
changed".

| Change | New key? |
|---|---|
| Prompt template edited | **yes** — template hash |
| Paraphrase sibling selected (V6) | **yes** — different template |
| Provider, model, or `model_version` changed | **yes** |
| Any sampling parameter changed | **yes** |
| Output JSON Schema changed | **yes** — schema hash is in `sampling_params` |
| `master_seed` changed | **yes** — every `call_seed` changes |
| Code change altering `prompt_variables` | **yes**, from the divergence tick onward |
| Code change not touching prompts (refactor, index, projection) | no |
| `cache.schema_version` bumped | **yes**, globally — path prefix changes |
| Storage reclamation | Deletion only, logged, never overwrite |

### 5.7 Publication as a reproducibility artefact

```
polis cache export --run <id> --out polis-cache-<paper>.tar.zst
```

Bundle contents: the completions reachable from the run's `llm_calls.cache_key` set,
`manifest.json` (`config_hash`, `master_seed`, `prompt_manifest`, `model_manifest`,
`code_git_sha`, key count, total bytes, per-key sha256), and a detached signature over
`manifest.json` by the researcher key — the same key that signs scenario injections
(`02-ARCHITECTURE.md §3.4`).

Size, at ~1.5 KB compressed per completion:

| Run | Cognition completions | Bundle |
|---|---|---|
| 1 sim-year `microscope` at 36 calls/tick | 311,040 | ~0.5 GB |
| 1 sim-year `microscope` at 90 calls/tick | 777,600 | ~1.2 GB |
| 43,200-tick reference run at 90 calls/tick | 3,888,000 | ~5.8 GB |
| 1 sim-year `chronicle` at 36 calls/tick | 12,960 | ~20 MB |

Publishable at every size. Prompts are **not** in the bundle — only responses — so publishing
the cache does not publish prompt text; templates ship separately in `prompts/` under the
same manifest hashes. A third party runs `polis run --mode replay --cache <bundle>` and
reproduces every figure with no API key, no account, and no dependence on a model that may
since have been retired. That last clause is the concrete mitigation for T5.

---

## 6. Structured output

### 6.1 Three tiers

| Tier | Mechanism | Lanes | Expected parse failure |
|---|---|---|---|
| `constrain` | Grammar-constrained decoding from the JSON Schema | `ollama_cloud`, `ollama_local`, `openai_compat` (vLLM guided), `stub` | ~0 (schema violations are structurally impossible; *semantic* violations still occur) |
| `repair` | Schema rendered into the prompt; validate; retry with the error appended | `minimax` (all M2.x models) | 2–15%, model-dependent |
| `none` | Free text | `SUMMARISE` | n/a |

The routing table's `structured:` field must match the lane's `capabilities.structured_output`
or startup fails. **The hot path is `repair`, not `constrain`** (§2.2), which means the repair
loop is primary infrastructure for `DELIBERATE` and `REFLECT`, not an edge case.

### 6.2 The repair loop

```python
# polis/llm/structured.py
async def call_structured(lane, req, schema, max_repairs=2) -> ParsedResult:
    attempt, errors = 0, []
    while attempt <= max_repairs:
        resp = await lane.complete(req if attempt == 0 else with_error(req, errors[-1]))
        obj, err = extract_and_validate(resp.text, schema)
        if err is None:
            return ParsedResult(obj, repair_attempts=attempt, resp=resp)
        errors.append(err)
        attempt += 1
        emit(4107, PARSE_REPAIR_ATTEMPTED, purpose=req.purpose, attempt=attempt, error=err.short())
    raise SchemaRepairExhausted(errors)     # caller falls back per §4.2
```

- `extract_and_validate` strips code fences, takes the outermost balanced `{...}` when the
  model prefixes prose, then validates with `jsonschema` in draft 2020-12.
- The repair message appends **only** the validator's error path and message, never a
  restatement of the whole schema — that doubles the prompt and rarely helps.
- `with_error` preserves `call_seed`. Each attempt has a distinct cache key because the user
  message differs, so all three attempts are cached independently and a replay reproduces the
  entire repair sequence including the failures.
- Maximum 2 repairs, i.e. at most 3 wire calls. Each repair costs a full call against both
  the token line and the dollar budget.
- After 2 failed repairs the agent **falls back to reflex** for that tick (`04-AGENT-SPEC.md
  §9.2`), `LLM_CALL_FAILED` (4101) is emitted, `llm_calls.parsed_ok = FALSE`, and the counter
  increments. The action's `origin` becomes `reflex`, so downstream analysis never
  misattributes a reflex action to deliberation.

**Cost of repairs.** At parse-failure rate *p*, expected wire calls per decision is
`1 + p + p²`.

| *p* | Calls/decision | Cost inflation |
|---|---|---|
| 0.02 | 1.020 | +2.0% |
| 0.05 | 1.053 | +5.3% |
| 0.10 | 1.110 | +11.0% |
| 0.20 | 1.240 | +24.0% |
| 0.35 | 1.473 | +47.3%, and ~4.3% of agent-ticks fall to reflex |

### 6.3 Parse-failure accounting

Recorded per call in `llm_calls.parsed_ok` and `llm_calls.repair_attempts`
(`03-DATA-MODEL.md §1.3`). Reported per run, **per model**:

| Statistic | Definition |
|---|---|
| `llm.parse_failure_rate.{model}` | Calls with `repair_attempts > 0` ÷ calls |
| `llm.repair_exhausted_rate.{model}` | Calls with `parsed_ok = FALSE` ÷ calls |
| `llm.repair_call_overhead.{model}` | Extra wire calls ÷ decisions |
| `llm.reflex_fallback_share` | Agent-ticks routed to deliberate that ended in reflex |

`llm.repair_exhausted_rate` above **0.05** flags the run as low-fidelity in the report header.
Above **0.15** the run is unusable for any behavioural claim: more than one agent-tick in
seven that was *selected* for cognition did not get it, which is a non-random treatment
failure on top of the T8 selection effect that already exists.

### 6.4 Practical guidance

- **Smaller models drift on schema, and they drift structurally.** The characteristic failures
  are: emitting the enum's description instead of its value; nesting `params` one level too
  deep; adding an unrequested `explanation` field; returning a list where the schema wants an
  object; and, at high temperature, abandoning JSON entirely partway through. Only the last is
  caught by "is it JSON"; the rest need real schema validation, which is why `additionalProperties: false`
  and explicit `required` are mandatory on every POLIS schema.
- **Keep schemas shallow and small.** `DELIBERATE`'s schema (`04-AGENT-SPEC.md §9.2`) is 5 top-level
  keys and 2 levels deep. Every additional level measurably raises *p* on cheap models.
- **Put the schema last in the user message**, immediately before the instruction to respond.
  Recency dominates for schema adherence in the `repair` tier.
- **Never use structured output to carry free text you will parse.** `reasoning` is a string
  the code never branches on (`02-ARCHITECTURE.md §6.1`); `speech` is stored verbatim. Only
  `action.type`, `action.params`, `belief_updates`, and `goal_updates` are machine-consumed.
- **Parse failure rate is a first-class run statistic and a model-selection criterion.** A
  model that is 30% cheaper and has 3× the parse-failure rate is more expensive after repairs
  and produces a materially different society through the reflex-fallback channel. §10 requires
  reflex share to match within 2 percentage points across V7 arms for exactly this reason.

---

## 7. Cost model and budgeting

All figures use **MiniMax M2.7 promotional pricing, $0.24/M input and $0.96/M output**, which
is the aggregator rate observed 2026-07-24. Vendor list is $0.30/$1.20; multiply by 1.25 for
the list-price column. Prices are configuration, not code: `polis report cost` recomputes from
`llm.providers.<lane>.price_*` so a price change does not require a doc edit.

### 7.1 Unit cost of one deliberate call

Prompt 3,000 tokens (`max_prompt_tokens`, `04-AGENT-SPEC.md §9.1`), completion ~300 tokens
(the `max_tokens: 700` cap is rarely approached — reasoning is 1–3 sentences plus a small JSON body).

| Component | Tokens | Rate | Cost |
|---|---|---|---|
| Input | 3,000 | $0.24/M | $0.000720 |
| Output | 300 | $0.96/M | $0.000288 |
| **Total** | 3,300 | | **$0.001008** |

With provider-side prefix caching on the global invariant preamble (§7.6): **$0.000864**.

### 7.2 One sim-year, 1,000 agents

| Profile | Ticks/sim-year | Calls/tick | Cognition calls | Cost |
|---|---|---|---|---|
| `chronicle` | 360 | 36 (token-bound, `tokens_per_tick: 120_000`) | 12,960 | **$13.06** |
| `chronicle` | 360 | 90 (calls-bound, `tokens_per_tick: 240_000`) | 32,400 | $32.66 |
| `microscope` | 8,640 | 36 | 311,040 | **$313.53** |
| `microscope` | 8,640 | 90 | 777,600 | $783.82 |
| Reference run (43,200 ticks = 5 microscope sim-years) | 43,200 | 90 | 3,888,000 | $3,919.10 |

Ancillary purposes add ~$0.30/sim-year in `chronicle` and ~$7/sim-year in `microscope`
(`NEWS_WRITE` and `VC_EVAL` on MiniMax; everything else is flat-rate or free). Ollama Cloud
subscription amortises to well under $1/sim-year at any realistic run cadence. Embeddings and
`SIM_AWARE_CHECK` are free (local).

### 7.3 Where the $12/sim-year target comes from

`01-PRD.md §7.1` targets ≤ $12 per simulated year. The arithmetic lands on it exactly under
one specific reading:

```
chronicle profile                       360 ticks / sim-year
tokens_per_tick: 120_000  (as shipped)  ->  floor(120_000 / 3_300) = 36 calls/tick
36 × 360                                =  12,960 cognition calls / sim-year
12,960 × $0.001008                      =  $13.06
minus a ~10% hybrid-cache hit           =  $11.76   ->  the $12 target
```

**The target is profile-conditional and this document pins it as such.** In `microscope` the
identical policy costs $313.53/sim-year — 24× more, exactly the ratio of ticks. Every cost
report prints the profile alongside the dollar figure, and `polis run` refuses to start a
`microscope` run whose `usd_per_run` implies fewer ticks than `run.ticks` without
`--accept-degradation`.

The `usd_per_run: 60.0` default in `02-ARCHITECTURE.md §8` buys **59,524 cognition calls**:
1,653 ticks at 36 calls/tick — 4.6 `chronicle` sim-years, or 69 `microscope` sim-days. That is
a calibration budget, not a headline-run budget, and it is correctly sized for M1.

### 7.4 Sensitivity

**To prompt length — two different effects.** Under a *calls* cap, cost per decision scales
with prompt length. Under a *tokens* cap, prompt length trades decisions for tokens at roughly
constant spend, and longer prompts are marginally *cheaper per tick* because input is 4×
cheaper than output.

*A — calls-bound (`calls_per_tick: 90` binding), microscope sim-year:*

| `max_prompt_tokens` | $/call | $/sim-year |
|---|---|---|
| 1,000 | $0.000528 | $410.57 |
| 2,000 | $0.000768 | $597.20 |
| **3,000** | **$0.001008** | **$783.82** |
| 4,500 | $0.001368 | $1,063.76 |
| 6,000 | $0.001728 | $1,343.70 |

*B — tokens-bound (`tokens_per_tick: 120_000` binding):*

| `max_prompt_tokens` | Tokens/call | Calls/tick | Deliberate rate | $/tick | $/microscope sim-year |
|---|---|---|---|---|---|
| 1,000 | 1,300 | 90 (call cap binds) | 9.0% | $0.04752 | $410.57 |
| 2,000 | 2,300 | 52 | 5.2% | $0.03994 | $345.05 |
| **3,000** | **3,300** | **36** | **3.6%** | **$0.03629** | **$313.53** |
| 4,500 | 4,800 | 25 | 2.5% | $0.03420 | $295.49 |
| 6,000 | 6,300 | 19 | 1.9% | $0.03283 | $283.67 |

Read together: **shortening the prompt does not save money, it buys more decisions.** The
lever that actually reduces spend is `tokens_per_tick`. The lever that improves the science is
prompt length *at fixed* `tokens_per_tick`, and it trades directly against the deliberate rate
and therefore against T8 and T9.

**To cache hit rate.** Live spend scales as `(1 − h)`:

| *h* | microscope sim-year @ 90 calls/tick |
|---|---|
| 0.00 | $783.82 |
| 0.30 | $548.67 |
| 0.60 | $313.53 |
| 0.90 | $78.38 |
| 0.99 | $7.84 |
| 1.00 (`replay`) | **$0.00** |

Because *h* is bimodal (§5.4), the expected value for a sweep cell is approximately
`t_divergence / total_ticks`, not a design parameter.

### 7.5 What each budget buys

At $0.001008/cognition call, `tokens_per_tick: 120_000`, cold cache.

| Budget | Cognition calls | `chronicle` | `microscope` | What it is |
|---|---|---|---|---|
| **$12** | 11,905 | 0.92 sim-years | 13.8 sim-days | The PRD unit. A single `chronicle` sim-year. |
| **$60** (default) | 59,524 | 4.6 sim-years | 69 sim-days | M1 calibration: salience cutoff, V4 entropy check, prompt iteration. |
| **$130** | 128,968 | 10 sim-years | 149 sim-days | One `chronicle` demographic run to three generations. |
| **$314** | 311,508 | 24 sim-years | **1.0 sim-year** | One `microscope` sim-year — the behavioural-resolution unit. |
| **$1,300** | 1,289,683 | 20 seeds × 5 sim-years | 4.1 sim-years | **V5 satisfied in `chronicle`.** |
| **$1,568** | 1,555,556 | — | 5.0 sim-years (the 43,200-tick reference run) | The headline `microscope` run. |
| **$2,600** | 2,579,365 | 20 seeds × 5 sim-years × 2 families | — | **V5 + V7 in `chronicle`.** |
| **$6,270** | 6,220,238 | — | 20 seeds × 1 `microscope` sim-year | V5 in `microscope`. |
| **$12,540** | 12,440,476 | — | 20 seeds × 1 sim-year × 2 families | V5 + V7 in `microscope`. |

**The operational conclusion.** `01-PRD.md §7.3` requires ≥20 seeds (V5) and two model
families (V7) for a reportable result, and §5.4 establishes that the cache contributes nothing
to either. Doing that in `microscope` costs ~$12.5k. Doing it in `chronicle` costs ~$2.6k.
**Seed replication and model-robustness work belong in `chronicle`; `microscope` is reserved
for the single headline run and for questions that genuinely need sub-daily resolution** —
which, of the twelve research questions, is B1, B2, and B5. This is the most consequential
cost finding in the document and it should drive experiment design, not be discovered late.

### 7.6 Prefix caching

MiniMax bills cached input at $0.06/M against $0.30/M list — an 80% reduction on the cached
portion. `04-AGENT-SPEC.md §9.3` already requires stable-prefix ordering. This document makes
the prompt's block structure normative so the saving is realisable:

| Block | Tokens | Stability | Cacheable |
|---|---|---|---|
| 1. Global invariant preamble — city description, rules of conduct, the action schemas | ~800 | Identical for all 1,000 agents, all ticks | **yes** |
| 2. Agent identity — name, age, trait narrative, `identity_summary`, goals | ~400 | Stable per agent between reflections | marginal |
| 3. Situation — place, needs, co-located, offers, feed, news, retrieved memories | ~1,800 | Changes every tick | no |

Block 1 must be emitted first, byte-identical, before any per-agent content. Block 2 is
per-agent and only hits if the same agent is called again within the provider's cache TTL; at
36 calls/tick over 1,000 agents an agent recurs roughly every 28 ticks, which at LLM-bound
tick rates is minutes — usually beyond TTL. So the realisable saving is block 1 alone:

```
800 / 3,000 = 26.7% of input at 80% off  ->  21.3% off input  ->  ~14% off total
$783.82  ->  ~$672 per microscope sim-year at 90 calls/tick
```

Modest, real, and free. `llm.prefix_cache_hit_rate` is reported per lane so the assumption is
checked rather than assumed.

---

## 8. Prompt management

### 8.1 Layout

```
prompts/
├── manifest.yaml                     # generated; template -> sha256, purpose, schema, paraphrases
├── deliberate/
│   ├── system.v3.jinja
│   ├── user.v3.jinja
│   └── paraphrase/
│       ├── system.v3.p1.jinja
│       ├── user.v3.p1.jinja
│       ├── system.v3.p2.jinja
│       └── user.v3.p2.jinja
├── reflect/ importance/ post_write/ news_write/ vc_eval/ judge/
├── summarise/ sim_aware_check/ credit_eval/
└── schemas/
    ├── deliberate.schema.json
    └── …
```

One directory per purpose. Templates never live in Python (`04-AGENT-SPEC.md §13`).

### 8.2 Version header

```jinja
{# polis-prompt
   purpose:     DELIBERATE
   version:     3
   schema:      schemas/deliberate.schema.json
   paraphrases: [p1, p2]
   max_tokens:  3000
   changelog:   v3 moved the legal-action block last; v2 removed second-person plural
#}
```

Parsed at load. A template without a well-formed header, or naming a purpose that is not in
the enum, or referencing a missing schema, fails startup.

### 8.3 Hashing

```python
def template_hash(src: bytes) -> str:
    text = src.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).rstrip() + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

Line endings are normalised to `\n` and trailing whitespace stripped **before** hashing. This
repository is developed on Windows and deployed on Linux; without normalisation every template
hash differs by platform, `runs.prompt_manifest` diverges, and every cache key changes on
checkout. This is not hypothetical — it is the single most likely way this system silently
loses its cache.

`PromptLibrary.load()` builds `{template_name: sha256}` at run start and writes it to
`runs.prompt_manifest` and into the `RUN_STARTED` (1001) payload.

### 8.4 Rendering determinism

```python
Environment(
    loader=FileSystemLoader("prompts/"),
    undefined=StrictUndefined,       # a missing variable is an error, never an empty string
    autoescape=False,
    trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
)
```

Banned in templates: `now()`, `random`, `range` without bounds, and iteration over a dict
without `|dictsort`. Any list rendered into a prompt must be pre-sorted by the caller — the
template does not sort, because sorting decisions belong where the ranking is specified
(`04-AGENT-SPEC.md §5`, all lists capped and ranked).

`prompt_variables` passed to the cache key is the exact mapping handed to `render()`, after
`canonical_json`. Nothing may be added inside the template from ambient state.

### 8.5 Prohibitions, enforced in CI

| Rule | Source | Enforcement |
|---|---|---|
| No template names a provider or model | G7, `04 §13` | `scripts/lint_prompts.py` denylist over `prompts/**`: `minimax, ollama, qwen, gemma, glm, deepseek, kimi, gpt, claude, llama, mistral, openai, anthropic` |
| No template reveals simulation status | T3, `04 §9.1` | Same lint, denylist: `simulation, simulated, agent, AI, artificial intelligence, language model, LLM, model, game, prompt, token, roleplay, scenario` — with an allowlist for the deliberate exceptions (`04 §9.1` permits none) |
| No code outside `polis/llm/providers/` imports a vendor SDK | G7, `02 §7.1` | `import-linter` contract below |
| Every purpose has ≥2 paraphrase siblings | V6, `04 §13` | `scripts/lint_prompts.py` fails on a purpose with fewer |
| Every template hash appears in `runs.prompt_manifest` | `03 §1.1` | Startup assertion |

```ini
# .importlinter
[importlinter:contract:llm-vendor-isolation]
name = Vendor SDKs are confined to polis.llm.providers
type = forbidden
source_modules = polis.kernel, polis.events, polis.store, polis.agents, polis.world,
                 polis.economy, polis.society, polis.gateway, polis.observatory,
                 polis.research, polis.cli, polis.llm.router, polis.llm.cache,
                 polis.llm.budget, polis.llm.structured, polis.llm.prompts
forbidden_modules = openai, anthropic, ollama, httpx_minimax, minimax
```

`httpx` itself is permitted only inside `polis.llm.providers`; everywhere else network I/O is
a design error.

### 8.6 Paraphrase siblings (V6)

A paraphrase **must preserve**: the output schema, the legal-action list and its ordering, every
number, every ID, every enumerated value, all caps, and the information content of each block.
It **must change**: sentence structure, section phrasing, and lexical choice. It **may change**:
the order of blocks whose order is semantically irrelevant.

`polis run --prompt-variant p1` selects the sibling set. Because the template hash changes,
V6 arms get **zero** cache reuse and cost full price (§5.4). Budget for V6 as a full extra arm,
not as a cheap check.

---

## 9. Token accounting and telemetry

### 9.1 Per call

Every call writes one `llm_calls` row (`03-DATA-MODEL.md §1.3`), including cache hits
(`cache_hit = TRUE`, `cost_usd = 0`, `latency_ms` = lookup time) and including each repair
attempt. A cache hit that is not recorded makes the hit rate unmeasurable and breaks
attribution.

Columns of note: `purpose`, `provider`, `model`, `model_version`, `prompt_template`,
`prompt_hash`, `cache_key`, `cache_hit`, `call_seed`, `sampling_params`, `parsed_ok`,
`repair_attempts`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `error`,
`sim_aware_flag`, plus the two requested in §0.2 (`provider_request_id`, `budget_line`).

`prompt_text` remains off by default; the prompt is reconstructible from
`prompt_template` + `runs.prompt_manifest` + the tick's committed state. `--keep-prompts` sets
the `keep_prompts` tag for a small diagnostic run.

### 9.2 Per tick

Written to `metrics` (`03-DATA-MODEL.md §10`) and published to Redis for the Observatory.

| Metric | Notes |
|---|---|
| `llm.calls.total`, `llm.calls.{purpose}` | Includes repairs |
| `llm.cache.hit_rate`, `llm.cache.hits`, `llm.cache.misses` | Cliff detection |
| `llm.tokens.in`, `llm.tokens.out`, `llm.tokens.cached_in` | |
| `llm.cost_usd.tick`, `llm.cost_usd.cumulative` | `Decimal`, 8 dp |
| `llm.latency.{lane}.p50/p95/p99` | Wall clock; never enters a hash |
| `llm.concurrency.{lane}.saturation` | Mean `inflight / max_concurrency` |
| `llm.queue_wait_ms.{lane}.p95` | Time blocked on the semaphore — the Ollama Cloud symptom |
| `llm.breaker.{lane}.state` | 0 closed, 1 half-open, 2 open |
| `llm.parse_failure_rate.{model}`, `llm.repair_exhausted_rate.{model}` | §6.3 |
| `llm.fallback_rate`, `llm.degraded_calls.{purpose}`, `llm.degraded_agent_share` | §4.6 |
| `llm.budget.{line}.utilisation`, `llm.budget.binding_constraint` | R2 |
| `llm.simaware.rate` | T3; `03 §1.3` |
| `llm.throughput.tick_seconds_llm_bound` | §4.4 |

### 9.3 Per run

`runs.total_llm_calls`, `total_tokens_in`, `total_tokens_out`, `total_cost_usd` are updated at
each checkpoint and finalised at run end. `polis report cost --run <id>` produces:

- Cost by purpose, with the share of total and calls per sim-day.
- Cost by lane and model, reconcilable against the provider invoice via `provider_request_id`.
- Cost by agent: total spend, call count, and the **Gini coefficient of LLM spend across
  agents**. This is the direct measurement of T8 (budget-induced selection). A near-zero Gini
  means salience routing is doing nothing; a very high one means a handful of agents consumed
  the cognition budget and the other 990 are effectively a classical ABM (T9).
- Correlation of an agent's cumulative call count with its terminal wealth, employment
  duration, and posting volume. Any positive correlation is a confound that must be reported
  alongside any result about those outcomes.
- Realised vs budgeted: which cap bound, in which ticks, and how many agents degraded.

### 9.4 Dashboards

Six panels in the Observatory's LLM tab (`10-RESEARCH-AND-OBSERVABILITY.md`):

1. **Spend** — cumulative $ vs the `usd_per_run` line and the halt multiple, with projected
   completion cost extrapolated from the current rate.
2. **Budget pressure** — per-line utilisation, degraded-agent share, and which cap bound, per tick.
3. **Lane health** — latency percentiles, semaphore saturation, queue wait, breaker state,
   error rate by class.
4. **Cache** — hit rate over ticks with the divergence cliff marked; bytes stored; L0/L1/L2 mix.
5. **Reliability** — parse-failure and repair-exhausted rates per model, reflex-fallback share,
   `sim_aware` rate.
6. **Attribution** — cost by purpose (stacked area over sim-time) and the agent-spend Lorenz curve.

Panels 2, 5, and 6 are not operational conveniences. They are the evidence for T8, T9, and the
fidelity caveats that any paper from this platform must state.

---

## 10. Model-robustness protocol (V7)

`01-PRD.md §7.2` V7: *headline effects replicate across at least two model families.*

### 10.1 What counts as a family

A **family** is a distinct pretraining lineage. `MiniMax-M2` and `MiniMax-M2.7` are the **same
family** and their agreement satisfies nothing. Valid second arms from the Ollama Cloud
catalogue: Qwen 3.5, Gemma 4, GLM-5.1, DeepSeek-V4. Prefer a second arm whose developer,
corpus, and post-training are as unrelated as available.

The arm's identity is recorded in `runs.model_manifest` and the arm label in `runs.tags`.

### 10.2 Held fixed

| Held fixed | Why |
|---|---|
| `config_hash` except `llm.routing` | Everything else must be identical or you are not replicating |
| `master_seed` set — the same ≥20 seeds | Paired comparison across arms |
| `prompt_manifest` | Same templates, same version, no per-model prompt tuning. Tuning the prompt per model is a legitimate engineering choice and an illegitimate scientific one. |
| `code_git_sha` | |
| All `mechanisms:` and `salience:` blocks | |
| **`calls_per_tick` and `tokens_per_tick`** | **Equalise the call budget, never the dollar budget.** A cheaper family under a fixed `usd_per_run` gets more cognition, and you measure budget rather than model. `usd_per_run` is set per arm to whatever the fixed call budget costs. |
| Ancillary and free-line routing | Only the cognition path varies. Swapping eight purposes at once means a null result identifies nothing. |

### 10.3 Varied

`llm.routing.DELIBERATE.{lane,model}` and `llm.routing.REFLECT.{lane,model}`. Nothing else.

A secondary **full-swap** arm, where every purpose moves to the second family, is run once as a
robustness check on the cognition-only design. If cognition-only and full-swap disagree, the
effect lives in an ancillary purpose and the finding must be restated.

### 10.4 Validity preconditions

A V7 comparison is **invalid** unless all of these hold. They are checked automatically by
`polis compare --gate v7`:

| Precondition | Threshold |
|---|---|
| Reflex share matches across arms | within **2 percentage points** |
| `llm.repair_exhausted_rate` in every arm | < 0.05 |
| `llm.degraded_agent_share` matches across arms | within 2 pp |
| Cognition calls per arm | within 1% |
| V1–V4 pass in every arm | mandatory |
| `llm.simaware.rate` reported per arm | no threshold; reported |
| Mean completion length reported per arm | no threshold; reported |

The reflex-share rule is the important one. A family with 3× the parse-failure rate silently
runs 3× more reflex agent-ticks, and every behavioural difference between the arms is then
confounded with how much LLM cognition each society actually received.

### 10.5 Comparison

```
polis sweep --base headline.yaml --axis model_family --arms minimax,qwen --seeds 20 \
            --fallback-policy strict --cache-mode live
polis compare --sweep <id> --effect <metric> --by model_family
```

**V7 passes** when the sign of the headline effect agrees across families and each family's
across-seed confidence interval excludes zero. The report additionally states the magnitude
ratio and the CI of the between-family difference. A sign agreement with a 5× magnitude
difference is a pass on V7 and a caveat in the paper, and both are printed.

**Cost.** Two arms × 20 seeds, zero cache benefit (§5.4): $2,600 in `chronicle`, $12,540 in
`microscope` (§7.5). Plan the arm in `chronicle` unless the research question requires
sub-daily resolution.

---

## 11. Local versus cloud

### 11.1 Deployment modes

| Mode | Local | Cloud | Hardware | When |
|---|---|---|---|---|
| `all_cloud` | nothing | everything incl. `EMBED` | laptop | Never for a real run — `EMBED` at ~400 calls/tick violates every cloud concurrency ceiling (§4.4) |
| **`hybrid`** (default) | `EMBED`, `SIM_AWARE_CHECK` | cognition on MiniMax; ancillary on Ollama Cloud | 1 consumer GPU, 8–12 GB, or CPU with a slower tick | The specified default |
| `local_heavy` | + `IMPORTANCE`, `POST_WRITE`, `SUMMARISE` | cognition only | 1 GPU, 24–32 GB, a ~30B MoE at 4-bit | Removes the Ollama Cloud concurrency ceiling from the ancillary line entirely |
| `all_local` | everything incl. cognition | nothing | 230B-class MoE at ~10B active: ~4×80 GB accelerators, or a 512 GB unified-memory host at 4-bit | Lab deployment. Zero marginal cost, no rate limits, perfect version pinning. |

### 11.2 Why `all_local` is attractive and what it costs

Attractive because it kills three threats outright: T5 (model drift — you own the weights),
rate limits (§12), and cost runaway (§12). And because a locally served model can be pinned to
greedy decoding with a fixed seed, which is closer to reproducible than any hosted API.

It costs throughput. At 80 cognition calls/tick you need continuous batching (vLLM or SGLang)
with `--max-num-seqs ≥ 80` and enough KV cache for 80 × 3,300 tokens ≈ 264k tokens resident.
`OpenAICompatProvider` addresses it (§2.5).

**A locally served model is still not bitwise deterministic.** Kernel reduction order in
batched matmuls depends on batch composition, so the same prompt in a different batch can
produce a different token. The completion cache remains mandatory even at `all_local`. Anyone
who proposes removing it because "we control the server" is wrong, and this sentence exists so
that conversation is short.

### 11.3 Mixed deployment in the router

Lanes are addresses, not tiers. A local lane differs only in its `Capabilities`:
`billing: free`, `price_* = 0`, `max_concurrency = server max_num_seqs`,
`supports_call_seed: true`.

| Concern | Handling |
|---|---|
| Budget | Local calls consume `tokens_per_tick` but not `usd_per_run`. Context bloat stays bounded even when inference is free. |
| GPU accounting | `llm.gpu_seconds.{lane}` recorded per tick; local capacity is a scheduling resource even when it is not a dollar cost. |
| Health at startup | Every configured lane is health-checked before tick 0. A local lane that fails is a **hard startup error**, never a silent fallback: a headline run that quietly switched from local to cloud has a different `model_manifest` and is not the run you think it is. |
| Model identity | Local `model_version` is the model digest reported by the server, recorded in `runs.model_manifest` exactly like a hosted version string. |
| Mixed-family accident | If cognition ends up split across lanes for any reason, the run is tagged `mixed_model` and cannot be pooled (§4.2 rule 1). |

---

## 12. Threats and failure modes

| # | Failure | Detection | Handling | Residual risk |
|---|---|---|---|---|
| **F1** | **Provider outage mid-run** | Error rate in the breaker window; 5xx class | Breaker opens (§4.3) → fallback chain → `last_resort`. `mixed_model` tag if the model changed. Run continues at reduced fidelity; `PROVIDER_CIRCUIT_OPENED` is in the log so replay reproduces the degradation. | A long outage degrades a large fraction of ticks. `llm.degraded_agent_share` over 0.2 flags the run unusable for behavioural claims. |
| **F2** | **Model deprecated between runs** | `ProviderPermanent` on the first call; startup health check | Startup fails loudly. Historic runs remain fully reproducible in `replay` from the published cache with no provider at all (§5.3). | New runs cannot extend an old study on the retired model. This is why the cache is published (T5). |
| **F3** | **Silent model update** — same name, different weights | **Canary fingerprint.** At run start, 16 fixed prompts at temperature 0 are run and their response hash is compared to the value stored with the last run using that `(provider, model, model_version)`. Divergence emits `MODEL_VERSION_CHANGED` (4108). | Run is tagged `model_drift`, `runs.model_manifest` records both fingerprints, and the run cannot be pooled with the earlier ones. `cache.strict_version: true` additionally refuses to start. | A provider that changes weights without changing the version string and passes 16 canaries is undetected. Increase canary count for long studies. |
| **F4** | **Rate limiting** | 429 rate per lane; `llm.queue_wait_ms` climbing | Token-bucket limiter pre-throttles from `rpm_limit`/`tpm_limit`. On 429, wait `retry_after_s` once, then fall back. Sustained >5% 429s emits `PROVIDER_TIER_MISMATCH`. | Throughput collapses long before quality does. §4.4's projected wall-clock estimate at startup is the early warning. |
| **F5** | **Cost runaway** | `llm.cost_usd.cumulative` vs `usd_per_run`, checked before every wire call | Degrade at 100%, halt at 120% (§4.6). Estimation is pessimistic (uses `max_tokens`), so overshoot is bounded by one in-flight batch. | A pricing change makes the configured `price_*` wrong and the guard under-counts. Reconcile against `provider_request_id` and the invoice after every headline run. |
| **F6** | **Cache poisoning** — an entry whose key does not match its content | `cache.trust: verify` recomputes the key from the stored tuple; `verify_render` compares `rendered_hash`; bundle manifest is signed | Mismatch raises and halts. `replay` never writes. Published bundles carry a detached researcher signature (§5.7). | A bug in the writer that corrupts key and content consistently. Mitigated by the golden test (`02 §12`) whose event-log hash is checked into the repo. |
| **F7** | **Non-determinism leaking through provider-side change** | Determinism suite; golden run; canary fingerprint (F3) | The cache is the answer: once a completion is cached, provider behaviour is irrelevant. Exposure is limited to `live`/`hybrid` misses. | A `hybrid` sweep run mixes cached and fresh completions from different weights. `--cache-mode replay` for anything published. |
| **F8** | **Schema drift on a cheaper model** | `llm.parse_failure_rate.{model}` per run | Repair loop (§6.2), then reflex. Rates above 0.05 flag the run; above 0.15 it is unusable. | Silent semantic drift — schema-valid but nonsensical output — is not caught here. It surfaces as V3/V4 invariant warnings. |
| **F9** | **Provider or model name leaks into a prompt** (G7) | `scripts/lint_prompts.py` in CI | Build fails. | A variable rendered into a prompt containing a model name at runtime. `StrictUndefined` plus a runtime denylist scan on `--keep-prompts` diagnostic runs. |
| **F10** | **Simulation status leaks into a prompt** (T3) | Same lint; plus `SIM_AWARE_CHECK` over responses | Build fails on the template. At runtime, `llm.simaware.rate` is a reported per-run statistic. | An agent infers simulation status from the structure of its situation rather than the words. This is why C2 is run as an explicit experiment (`01-PRD.md §3`). |
| **F11** | **Concurrency starvation** — a lane's semaphore is the tick's bottleneck | `llm.queue_wait_ms.{lane}.p95`, `llm.concurrency.{lane}.saturation` | Startup validation rejects a routing table that assigns a lane more calls/tick than §4.4's sizing rule allows. Ancillary purposes are deferred one tick to leave the critical path clear. | Latency drifts upward mid-run as prompts grow, silently violating the sizing rule. The metric is on dashboard panel 3 for this reason. |
| **F12** | **Ollama Cloud quota exhaustion** — session or weekly limit on a tier | 402/429 from the cloud lane; `PROVIDER_TIER_MISMATCH` | Fall back to `ollama_local`; if absent, take the ancillary `last_resort` (heuristic importance, dropped posts, template news). Emit `BUDGET_EXHAUSTED` with `line: ancillary`. | A run that loses `POST_WRITE` mid-way has a discontinuity in posting volume. `07-SOCIETY-SPEC.md` metrics show it; the run is annotated. |
| **F13** | **Context overflow** | `finish_reason == "length"` on input, or rendered tokens > `context_window` | `SUMMARISE` compresses the retrieved-memory and feed blocks, in that order (`04 §9.1`). Hard truncation only if `SUMMARISE` itself is unavailable. | At a 205K context window and a 3,000-token cap this is a non-issue on the primary lane and a real one on a small local model. Validated at startup against `capabilities.context_window`. |
| **F14** | **Refusal / safety filter** on slanted or adversarial content | `finish_reason == "content_filter"`; refusal-pattern regex on `NEWS_WRITE` and `POST_WRITE` (`07 §11`) | Counted as a parse failure, one repair attempt, then the purpose's `last_resort`. Refusal rate is reported per purpose per model. | A family that systematically refuses slanted copy makes B1/B2 unanswerable on that arm. Report it; do not prompt around it, because prompting around a safety filter is a per-model prompt change and breaks §10.2. |

---

*Next: `10-RESEARCH-AND-OBSERVABILITY.md`.*

---

## Sources

Provider facts, prices, limits, and capability claims in §2, §4.4, §6.1, and §7 were verified
on 2026-07-24 against the following. Prices and rate limits are volatile; they live in
`llm.providers.*` config and this document's arithmetic is reproduced by `polis report cost`
from whatever is configured at run time.

**MiniMax**

- [MiniMax M2 — API Pricing & Benchmarks (OpenRouter)](https://openrouter.ai/minimax/minimax-m2) — M2 at ~$0.255/M input, ~$1.02/M output; 204,800-token context, 131,072 max output.
- [MiniMax M2.7 — API Pricing & Benchmarks (OpenRouter)](https://openrouter.ai/minimax/minimax-m2.7) — M2.7 at $0.24/M input, $0.96/M output.
- [MiniMax M2.7 API Pricing 2026 (pricepertoken)](https://pricepertoken.com/pricing-page/model/minimax-minimax-m2.7) — vendor list $0.30/$1.20; cached input $0.06/M, cache write $0.375/M; release 2026-03-18; 204,800-token context.
- [MiniMax-M2.7 — Intelligence, Performance & Price Analysis (Artificial Analysis)](https://artificialanalysis.ai/models/minimax-m2-7) — 230B total / 10B active MoE; release date; benchmark position.
- [MiniMax M2.7 Advances Scalable Agentic Workflows (NVIDIA Technical Blog)](https://developer.nvidia.com/blog/minimax-m2-7-advances-scalable-agentic-workflows-on-nvidia-platforms-for-complex-ai-applications/) — agentic-workflow training objective; open weights.
- [MiniMaxAI/MiniMax-M2.7 (vLLM recipes)](https://recipes.vllm.ai/MiniMaxAI/MiniMax-M2.7) — 230B/10B active, 256 experts, 62 layers; self-hosting configuration.
- [MiniMax releases M2 open-source model (TechNode, 2025-10-28)](https://technode.com/2025/10/28/minimax-releases-m2-open-source-model-offering-double-speed-at-8-of-claude-sonnets-price/) — M2 release date and positioning.
- [MiniMax M2 Benchmarks, Pricing & Context Window (llm-stats)](https://llm-stats.com/models/minimax-m2) — 230B parameters, context window, licence.
- [Model Invocation — MiniMax API Docs](https://platform.minimax.io/docs/guides/text-generation) — OpenAI-compatible endpoint at `https://api.minimax.io/v1`.
- [Text Generation — MiniMax API Reference](https://platform.minimax.io/docs/api-reference/text-post) — `response_format` documented as supported only by `MiniMax-Text-01`; `stream` and `response_format` mutually exclusive.
- [Feature request: `response_format` support for MiniMax M2.5 via OpenAI-compatible API (GitHub)](https://github.com/MiniMax-AI/MiniMax-M2.5/issues/4) — confirms M2.x line does not support `json_object` or `json_schema` on the OpenAI-compatible endpoint. **This is the basis for `structured: repair` on the hot path (§6.1).**
- [JSON mode / JSON object output support for MiniMax M2.x (MiniMax community)](https://www.minimax.io/community/m/1512023552507773089) — same limitation from the vendor's forum.
- [MiniMax — liteLLM provider docs](https://docs.litellm.ai/docs/providers/minimax) — OpenAI-compatible and Anthropic-compatible base URLs.
- [MiniMax API Rate Limits: RPM, TPM & Concurrency](https://minimax-ai.chat/docs/minimax-api-rate-limits/) — RPM/TPM tiering; paid burst in the hundreds of RPM. Basis for §4.4's throughput conclusion.
- [fix: correct MiniMax context window from 192K to 204,800 tokens (cline PR #10007)](https://github.com/cline/cline/pull/10007) — resolves the 192K/197K/205K discrepancy across third-party listings in favour of 204,800.

**Ollama**

- [Pricing — Ollama](https://ollama.com/pricing) — Free / Pro / Max tiers; GPU-time-based usage rather than per-token billing; session and weekly limits.
- [Cloud — Ollama Docs](https://docs.ollama.com/cloud) — `:cloud` suffix, identical CLI and API surface to local models, `ollama signin` requirement.
- [Ollama Cloud Free vs Pro — Usage Limits, Pricing & What You Actually Get (2026)](https://dev.to/amareswer/ollama-cloud-free-vs-pro-usage-limits-pricing-what-you-actually-get-2026-3ieo) — **1 / 3 / 10 concurrent models on Free / Pro / Max.** This is the constraint in §4.4.
- [Ollama Cloud Pricing 2026, $0 Free to $200 Pro Max](https://pooyagolchian.com/blog/ollama-cloud-pricing-hardware-requirements-2026/) — tier prices; model usage levels 1–4 by model weight.
- [Ollama in 2026: From Local Runner to AI Platform](https://angelo-lima.fr/en/ollama-2026-state-of-the-art-en/) — cloud-only tags including `qwen3-coder-480b:cloud`, `kimi-k2.6:cloud`, `minimax-m3:cloud`; identical local/cloud interface.
- [Structured Outputs — Ollama Docs](https://docs.ollama.com/capabilities/structured-outputs) — JSON Schema via the `format` field, compiled to a grammar.
- [Structured outputs (Ollama Blog)](https://ollama.com/blog/structured-outputs) — schema-to-GBNF constraint mechanism.
- [Improve compatibility with OpenAI structured outputs json_schema response format (ollama/ollama #10001)](https://github.com/ollama/ollama/issues/10001) — **`/v1/chat/completions` ignores `response_format.json_schema`; only the native `/api/chat` `format` field constrains output.** Basis for `OllamaProvider` speaking the native API (§2.4).
- [Best Ollama Embedding Models 2026 (MTEB, VRAM, dimensions)](https://www.morphllm.com/ollama-embedding-models) — `embeddinggemma` and `nomic-embed-text` both at 768 dimensions, matching `memories.embedding vector(768)`.
