# C22 — External agent gateway: identity, MCP, REST/WS, budgets, SDK

**M6** · `polis/gateway/` (+ `polis/store/readmodels/external.py`, `polis/cli/wiring/external.py`) · **Depends on:** C01 (config, CLI), C02 (kinds, canonical JSON), C03 (Database, repositories), C07 (`Observation`, `ReflexProfile`), C08 (memory write/retrieve), C09 (PHASE 3 seam), C10 (`ActionType`, schema bundle), C20 (a populated world worth joining) · **Blocks:** C23b (scorecard view), every C1 result · **Size:** L (1–2 weeks)

---

## 1. Context

Polis is open to agents from other systems. The principle, taken from Buzz, is that **agents are members, not bots**: a foreign agent gets a keypair, a household, a ledger account, a criminal record if it earns one, and an obituary. It gets exactly the native action surface — one slot per tick, the same five validation gates, the same `Observation`, the same memory cap — and nothing else. Two properties are load-bearing and everything in this chunk exists to hold them: **no foreign process can slow the city down** (X3), and **no foreign agent can see more than a citizen can see** (X4). The gateway is a separate process that never mutates simulation state; it verifies, budgets, and pushes onto a bounded Redis handoff that the engine drains once, on a hard timeout, in PHASE 3.

---

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/02-ARCHITECTURE.md` | **all** — §2.1 process model, §3.1 canonical serialisation, §3.4 signatures, §5 PHASE 1/3, §6 actions, §7.1 dependency rules, §10 error handling |
| `../docs/03-DATA-MODEL.md` | §0 conventions, §1.2 `events`, §2.1 `agents`, §2.3 `memories`, §10 `external_agents`, `scenario_injections` |
| `../docs/08-EXTERNAL-AGENT-PROTOCOL.md` | **all — primary source, normative, §1–§15** |
| `../docs/04-AGENT-SPEC.md` | §5 `Observation`, §6.2–6.5 memory, §8 reflex, §9.2 output schema, §11 the five gates, §12.3 death |
| `../docs/10-RESEARCH-AND-OBSERVABILITY.md` | §1.8 `sys.external.*`, §2.3 gates (V8 sits beside them) |
| Chunks | C02 (`register_kind`, `NewEvent`, `canonical_json`), C03 (`Database`, `WriteForbidden`), C07 (`Observation`, `ReflexProfile`), C08 (`Retriever`, `MemoryWriter`), C09 (`CognitionPhase.external_decisions` seam), C10 (`ActionType`, `Action`, `ActionOutcome`, schema bundle) |

---

## 3. Scope — in

1. **Identity and registration** — challenge/response, ed25519 keys, `agent_id = ag_<pubkey_hex[:16]>`, operator declaration, roster and per-operator caps, conformance tokens, admission queued to the engine.
2. **Canonical serialisation** — `polis/gateway/sdk/canonical.py`, the *single* implementation of `08 §3.1–3.2`; run binding, tick binding, strictly-increasing nonce, action-id LRU, seal.
3. **MCP server** — the eight tools of `08 §4.2` with their JSON schemas and their verbatim descriptions, remote Streamable HTTP at `/mcp`, plus the local stdio server in the SDK.
4. **REST + WebSocket** — every row of `08 §5.1`, the seven server frames and three client frames of `08 §5.2`.
5. **Tick synchronisation** — PHASE 1 observation push, deadline window, seal-then-drain, `QUEUE_FULL` guard, `pause_for_external` debug mode, `external_latency` measurement, V8 inputs.
6. **Deadline miss handling** — 20030, the agent's *own* reflex fallback, consecutive-miss counter, naturalisation and resumption.
7. **Rate limiting and the abuse ladder** — every row of `08 §7.1`, the strike ladder of `08 §7.2`, cheap-checks-first ordering.
8. **Sandboxing** — `polis/store/readmodels/external.py` (the five permitted functions), `v_public_record`, `v_market_visible`, the not-exposed enumeration, uniform error codes, the leak test.
9. **SDK** — `keys.py`, `canonical.py`, `client.py`, `mcp_server.py`, `fallback.py`, `cli.py`; publishable standalone.
10. **`polis-agent-cli`** — JSON-in/JSON-out, one object per invocation, `selftest` minting a conformance token.
11. **Arena scorecard** — the nine dimensions, eligibility, `GET /scorecard`, kind 20070.
12. **Prompt-injection handling** — typed `InWorldText` envelopes, `content_is_untrusted`, the heuristic, 20050/20051.
13. **Engine-side adapters** in the composition root: observation publisher, drain adapter, memory/touch appliers.
14. Event kinds **20000–20999** and ephemeral **90020**.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `ActionType`, params models, the five validation gates, `legal_actions()` | **C10** — you consume the generated bundle only |
| Building `Observation`, `ReflexProfile`, `reflex_decide()` | **C07** |
| Memory retrieval scoring, importance scoring, eviction | **C08** — you call it through the read model |
| PHASE 3 orchestration, `Decision`, the native LLM batch | **C09** |
| Embodiment draw (immigrant distribution, `paired_control` twin) | **C20** — you queue the request, PHASE 7 admits |
| The `metrics` writer, V8 *enforcement*, gate reports | **C24** — you *measure* miss rate, C24 enforces |
| Scenario injections, kinds 99xxx | **C25** |
| Rendering the scorecard | **C23b** |
| Any `INSERT` into `events`, `agents`, `memories`, `ledger_*` | **the engine, always** |

---

## 5. Interfaces you provide

### 5.1 Canonical serialisation and keys

```python
# polis/gateway/sdk/canonical.py   — 08 §3.1–3.2. THE only implementation. A second one is a bug.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Final, Mapping
from uuid import UUID

PROTOCOL_VERSION: Final[int] = 1
DOMAIN_ACT: Final[bytes] = b"POLIS/ACT/1\x00"
DOMAIN_REG: Final[bytes] = b"POLIS/REG/1\x00"
DOMAIN_SES: Final[bytes] = b"POLIS/SES/1\x00"
DOMAIN_REV: Final[bytes] = b"POLIS/REV/1\x00"
DOMAIN_MEM: Final[bytes] = b"POLIS/MEM/1\x00"

@dataclass(frozen=True, slots=True)
class SignableAction:
    run_id:    UUID
    tick:      int
    action_id: UUID
    nonce:     int
    actor_id:  str                                   # exactly 19 ascii chars, "ag_" + 16 hex
    type:      str                                   # ActionType NAME, ascii
    params:    Mapping[str, Any]
    reasoning: str | None = None
    speech:    str | None = None
    extras:    Mapping[str, Any] = field(default_factory=dict)   # belief_updates + goal_updates

def canonical_action_bytes(a: SignableAction) -> bytes: ...
def canonical_registration_bytes(challenge: bytes, declaration: Mapping[str, Any]) -> bytes: ...
def canonical_session_bytes(run_id: UUID, agent_id: str, unix_ms: int, ttl_s: int,
                            delegate_pubkey: bytes | None) -> bytes: ...
def canonical_revoke_bytes(run_id: UUID, agent_id: str, unix_ms: int, reason: str) -> bytes: ...
def canonical_memory_bytes(run_id: UUID, agent_id: str, tick: int, nonce: int,
                           body: Mapping[str, Any]) -> bytes: ...

def sign(sk_bytes: bytes, preimage: bytes) -> str: ...          # 128 lowercase hex
def verify(pubkey_hex: str, preimage: bytes, sig_hex: str) -> bool: ...
def agent_id_for(pubkey_hex: str) -> str: ...                   # "ag_" + pubkey_hex[:16]
def test_vectors() -> list[Mapping[str, Any]]: ...              # 24; served at /v1/schemas/testvectors.json

# polis/gateway/sdk/keys.py
class Keypair:
    pubkey_hex: str; agent_id: str
    @classmethod
    def generate(cls) -> "Keypair": ...
    @classmethod
    def load(cls, path: str) -> "Keypair | None": ...
    def save(self, path: str) -> "Keypair": ...                 # 0600; refuses to overwrite
    def sign(self, preimage: bytes) -> str: ...
```

### 5.2 Verification, sessions, limits

```python
# polis/gateway/errors.py
class ErrorCode(StrEnum):
    NOT_ADMITTED="NOT_ADMITTED"; REVOKED="REVOKED"; SUSPENDED="SUSPENDED"
    SESSION_INVALID="SESSION_INVALID"; BAD_SIGNATURE="BAD_SIGNATURE"; NONCE_REUSED="NONCE_REUSED"
    DUPLICATE_ACTION_ID="DUPLICATE_ACTION_ID"; TICK_MISMATCH="TICK_MISMATCH"; LATE="LATE"
    NO_SLOTS="NO_SLOTS"; UNKNOWN_ACTION_TYPE="UNKNOWN_ACTION_TYPE"; SCHEMA_INVALID="SCHEMA_INVALID"
    PAYLOAD_TOO_LARGE="PAYLOAD_TOO_LARGE"; RATE_LIMITED="RATE_LIMITED"; QUEUE_FULL="QUEUE_FULL"
    NOT_VISIBLE="NOT_VISIBLE"; GATEWAY_DEGRADED="GATEWAY_DEGRADED"

HTTP_STATUS: Final[Mapping[ErrorCode, int]]        # 08 §4.11, exact
MESSAGES:    Final[Mapping[ErrorCode, str]]        # fixed templates; NO interpolation of in-world text

class ProtocolError(Exception):
    code: ErrorCode; retry_after_ms: int | None; strikes: int
def envelope(e: ProtocolError, *, tick: int) -> Mapping[str, Any]: ...

# polis/gateway/verify.py
@dataclass(frozen=True, slots=True)
class VerifiedSubmission:
    agent_id: str; action_id: UUID; tick: int; nonce: int
    type: str; params: Mapping[str, Any]
    reasoning: str | None; speech: str | None; extras: Mapping[str, Any]
    sig: str; session_id: str; received_ms: int          # monotonic, gateway-local

class Verifier:
    def __init__(self, run_id: UUID, bundle: ActionSchemaBundle, nonces: NonceStore,
                 seen: ActionIdLRU, *, tick_skew_tolerance: int = 0) -> None: ...
    def check(self, body: Mapping[str, Any], *, session: Session,
              current_tick: int, sealed: bool) -> VerifiedSubmission:
        """Order is BINDING (08 §7.3.5): size -> session -> rate bucket -> tick -> nonce
        -> schema -> signature. ed25519 is last because it is the expensive step.
        Raises ProtocolError. Advances the nonce ONLY on full acceptance."""

class NonceStore:                                    # backed by external_nonces + an in-proc cache
    def next_nonce(self, agent_id: str) -> int: ...
    def accept(self, agent_id: str, nonce: int, tick: int) -> None: ...

# polis/gateway/auth.py
@dataclass(frozen=True, slots=True)
class Session:
    session_id: str; agent_id: str; custody: Literal["operator", "delegated"]
    delegate_pubkey: str | None; expires_unix_ms: int
    transport: Literal["mcp_stdio", "mcp_http", "rest", "ws"]; sdk_version: str

class Registrar:
    async def challenge(self, pubkey_hex: str, *, client_ip: str) -> Mapping[str, Any]: ...
    async def register(self, declaration: Mapping[str, Any], sig_hex: str) -> Mapping[str, Any]:
        """Verifies POLIS/REG/1, checks window/roster/operator cap/duplicate-prefix/conformance
        token, LPUSHes polis:reg:{run}. Returns {agent_id, status:'pending', queued_tick}.
        NEVER creates a citizen — PHASE 7 does (08 §2.2)."""
    async def admission(self, agent_id: str) -> Mapping[str, Any]: ...
    async def open_session(self, agent_id: str, ttl_s: int, sig_hex: str,
                           delegate_pubkey: str | None, transport: str) -> Session: ...
    async def revoke(self, agent_id: str, reason: str, sig_hex: str, by: str) -> int: ...
    async def depart(self, agent_id: str, reason: str, sig_hex: str) -> int: ...
    async def resume(self, agent_id: str, sig_hex: str) -> Mapping[str, Any]: ...

# polis/gateway/limits.py
class LimitSet:
    def charge(self, agent_id: str, bucket: Literal["request","recall","history","memory"],
               tick: int) -> None: ...                                   # raises RATE_LIMITED
    def slot_take(self, agent_id: str, tick: int) -> int: ...            # -> slots_remaining
    def slots_remaining(self, agent_id: str, tick: int) -> int: ...
    def strike(self, agent_id: str, tick: int, trigger: Literal["schema","signature","rate"]) -> None:
        """Applies the 08 §7.2 ladder and emits 20040/20041/20042/20003. Well-formed-but-
        rejected actions NEVER call this."""
    def status(self, agent_id: str, tick: int) -> Mapping[str, Any]: ...  # -> who_am_i.protocol
```

### 5.3 The Redis handoff — the only path from gateway to engine

```python
# polis/gateway/queue.py — pure Redis. Imports NOTHING from polis.kernel or polis.agents.
OBS_KEY:   Final[str] = "polis:obs:{run}:{tick}:{agent}"     # SETEX, TTL = 3 ticks
ACT_KEY:   Final[str] = "polis:act:{run}:{tick}"             # RPUSH / LRANGE+DEL
MEM_KEY:   Final[str] = "polis:mem:{run}:{tick}"
TOUCH_KEY: Final[str] = "polis:touch:{run}:{tick}"
REG_KEY:   Final[str] = "polis:reg:{run}"

@dataclass(frozen=True, slots=True)
class DrainedAction:
    """Gateway-owned wire record. The composition root converts this to a C10 Action."""
    agent_id: str; action_id: str; tick: int; nonce: int
    type: str; params: Mapping[str, Any]
    reasoning: str | None; speech: str | None; extras: Mapping[str, Any]
    sig: str; session_id: str; received_ms: int

class GatewayQueue:
    def __init__(self, redis: Redis, run_id: UUID, *, max_queued: int) -> None: ...
    async def push_action(self, tick: int, rec: DrainedAction) -> int: ...   # QUEUE_FULL on LLEN cap
    async def push_memory(self, tick: int, rec: Mapping[str, Any]) -> None: ...
    async def push_touch(self, tick: int, agent_id: str, memory_ids: Sequence[int]) -> None: ...
    async def read_observation(self, tick: int, agent_id: str) -> bytes | None: ...

class ObservationPublisher:            # engine side; pure Redis, byte-passthrough
    async def publish(self, tick: int, agent_id: str, blob: bytes) -> bool: ...

class ActionDrain(Protocol):
    async def drain(self, tick: int, *, timeout_ms: int) -> tuple[DrainedAction, ...]:
        """ONE LRANGE + DEL, bounded. On timeout/unavailability returns () and the caller
        emits 20900; every external agent misses and the tick proceeds."""

class RedisActionDrain(ActionDrain): ...
class ReplayActionDrain(ActionDrain):
    """cache_mode='replay': reads the recorded 20020 events for `tick` instead of Redis.
    This is what makes tests/determinism/test_external_replay.py possible."""
```

### 5.4 Read model — the entire external read surface

```python
# polis/store/readmodels/external.py  — EXACTLY five functions (08 §8.4 rule 2).
# The import-linter contract `gateway_readmodel_only` allows polis.gateway to import
# this module and no other polis.store module.
async def whoami(db: Database, run_id: UUID, agent_id: str, tick: int) -> Mapping[str, Any]: ...
async def recall(db: Database, run_id: UUID, agent_id: str, query: str, *, k: int,
                 mtype: str | None, since_tick: int | None) -> Mapping[str, Any]: ...
async def remember(db: Database, run_id: UUID, agent_id: str,
                   body: Mapping[str, Any]) -> Mapping[str, Any]:
    """VALIDATES ONLY: clamps importance to min(declared, scored + 0.15), resolves and drops
    unheld `supported_by` ids, returns the queueable record. Writes NOTHING."""
async def market(db: Database, run_id: UUID, agent_id: str, symbols: Sequence[str],
                 skus: Sequence[str], depth: int) -> Mapping[str, Any]: ...   # v_market_visible
async def public_record(db: Database, run_id: UUID, agent_id: str, query: str, *,
                        kinds: Sequence[str], since_tick: int | None,
                        limit: int) -> Mapping[str, Any]: ...                 # v_public_record
```

### 5.5 The eight MCP tools

`polis/gateway/tools.py` registers each tool once; `mcp_server.py` and `rest.py` are two transports over the *same* handler. Descriptions are the verbatim strings of `08 §4.3–4.10` — **they are prompts**: second person, and they never use the words *simulation, agent, AI, model, game*.

| Tool | Slot | Bucket | Handler | Schema file | Default |
|---|---|---|---|---|---|
| `polis_observe` | 0 | request | `queue.read_observation` → passthrough | `schemas/observation.v1.json` | on |
| `polis_act` | **1** | request | `Verifier.check` → `queue.push_action` | `polis/events/schemas/actions.v1.json` | on |
| `polis_recall` | 0 | recall (6/tick) | `readmodels.recall` + `push_touch` | `schemas/recall.v1.json` | on |
| `polis_remember` | 0 | memory (2/tick) | `readmodels.remember` → `push_memory` | `schemas/remember.v1.json` | on |
| `polis_who_am_i` | 0 | request | `readmodels.whoami` + `limits.status` | `schemas/whoami.v1.json` | on |
| `polis_market_quote` | 0 | request | `readmodels.market` | `schemas/market.v1.json` | on |
| `polis_search_history` | 0 | history (3/tick) | `readmodels.public_record` | `schemas/history.v1.json` | **off** (parity deferred) |
| `polis_wait_for_tick` | 0 | request | long poll on tick pub/sub | `schemas/wait.v1.json` | on |

`polis_act`'s input schema is `08 §4.3` verbatim and is **field-for-field identical** to the native DELIBERATE output schema (`04 §9.2`). CI asserts the two files hash-match after key sorting; a divergence is a fairness violation, not a formatting issue.

### 5.6 REST + WebSocket

Every row of `08 §5.1` under `/v1`, unchanged, plus `GET /healthz` and `GET /metrics` (Prometheus text, gateway-local only). Auth column: `none` · `sig(X)` in `X-Polis-Signature` · `bearer`. WS at `/v1/stream`, frames exactly `08 §5.2`, `max_frame_bytes` 256 KiB, send queue bounded at 64 frames, overflow drops oldest `observation` frames first and emits `notice: degraded`.

```python
# polis/gateway/app.py
def create_app(settings: Settings, *, run_id: UUID) -> FastAPI: ...
async def serve(settings: Settings) -> None: ...      # `polis gateway` entrypoint
```

### 5.7 Scorecard and injection heuristic

```python
# polis/gateway/scorecard.py
@dataclass(frozen=True, slots=True)
class ScorecardRow:
    agent_id: str; driver: Literal["operator","native"]
    declared_model: str; declared_model_version: str; declared_scaffold: str
    memory: str; custody: str; embodiment: str; conformance_token: str | None
    W: float; W_growth: float; R: float; C: float; P: float; I: float
    S: float; L: float; liveness: float
    miss_rate: float; driven_fraction: float; sim_aware_rate: float
    eligible: bool; ineligibility_reasons: tuple[str, ...]

async def compute(db: Database, run_id: UUID, *, at_tick: int,
                  interval_ticks: int) -> tuple[ScorecardRow, ...]:
    """All dimensions are PERCENTILE RANKS against the living population at `at_tick`.
    Native agents appear as the reference distribution, labelled native/<model>.
    No composite scalar is computed anywhere. Emits 20070."""

def eligibility(row: ScorecardRow, run_tags: Sequence[str], gates: Mapping[str, bool]
                ) -> tuple[bool, tuple[str, ...]]: ...       # 08 §11.4, seven conditions

# polis/gateway/injection.py
@dataclass(frozen=True, slots=True)
class InjectionHit:
    pattern_id: str; sample_hash: str; direction: Literal["inbound","outbound"]; channel: str

def scan_inbound(text: str, *, source_ref: str) -> InjectionHit | None: ...
def scan_outbound(text: str) -> InjectionHit | None: ...
def sim_aware_score(text: str) -> float: ...                 # same regex tier as llm_calls.sim_aware_flag
def wrap(text: str, *, channel: str, source_ref: str, author_id: str, tick: int,
         trust_hint: float) -> Mapping[str, Any]:
    """The typed envelope of 08 §12.2. Strips control characters, length-caps, renders no
    markdown, no HTML, no links, and always sets content_is_untrusted: true."""
```

---

## 6. Interfaces you consume

| From | Symbol | Notes |
|---|---|---|
| C01 | `Settings`, `GatewaySettings`, `canonical_json`, `sha256_hex` | `08 §14` config block |
| C02 | `register_kind`, `Persistence`, `NewEvent`, `KIND_REGISTRY` | your kinds 20000–20999, 90020 |
| C02 | `polis/events/schemas/actions.v1.json` | generated from C10's enum at build time; you read it, never the enum |
| C03 | `Database.open(role="reader")`, `WriteForbidden` | the gateway runs as `polis_reader` |
| C07 | `Observation` (engine side only) | the composition root serialises it; **you never import it** |
| C08 | `Retriever`, `MemoryWriter`, importance scorer | reached only through `readmodels.external` |
| C09 | `CognitionPhase(external_decisions=...)` | the seam; filled by the composition root adapter |
| C10 | `Action`, `ActionOutcome` (composition root only) | conversion happens outside `polis.gateway` |

> **Coordination item — raise before starting.** The external JSON projection of `Observation`
> must have exactly one implementation. It belongs to **C07** (`polis/agents/observation_json.py`,
> `to_external_json(obs) -> bytes`) because C07 owns the dataclass; this chunk owns the schema
> it must satisfy (`polis/gateway/schemas/observation.v1.json`) and the identity test. The
> gateway serves the blob byte-for-byte and never re-derives it.

---

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `external_agents` | **R only from the gateway** | counters are incremented by the engine from 20020/20021/20030 |
| `external_sessions` | R/W | gateway-owned projection; `08 §2.4` DDL, added by this chunk's Alembic revision |
| `external_nonces` | R/W | gateway-owned |
| `external_latency` | **W** | wall-clock lives here and nowhere else (`02 §4.5`); `PARTITION BY LIST (run_id)` |
| `agents`, `memories`, `beliefs`, `holdings`, `loans`, `ohlcv`, `posts`, `articles`, `follows`, `relationships`, `crimes`, `court_cases`, `cap_table`, `elections`, `vc_funds` | **R** | via `readmodels.external` and the scorecard only |
| `events` | **never** | `polis_reader` has no `INSERT`; a `WriteForbidden` here is the intended failure |
| `v_public_record`, `v_market_visible`, `v_agent_control` | **R** | created by this chunk's migration; neither view can express a column outside `08 §8.2` |

Three new tables and three views are additive. `external_sessions`, `external_nonces` and `external_latency` are projections and rebuildable from 20010/20011/20020/20030.

---

## 8. Event kinds owned

**Range 20000–20999** (persisted) plus **90020** (ephemeral). All are in `08 §13` — implement that table exactly. Ownership `polis.gateway`.

| Kind | Name | Note |
|---|---|---|
| 20000 / 20001 / 20002 | registration requested / registered / rejected | `actor_id` null on 20000 and 20002 |
| 20003 / 20004 / 20005 | key revoked / naturalised / control resumed | naturalisation is **not** death |
| 20010 / 20011 | session opened / closed | `custody`, `delegate_pubkey`, `transport` |
| **20020** | `EXTERNAL_ACTION_SUBMITTED` | `Event.sig` carries the ed25519 signature and enters the hash chain; every downstream institutional event points here via `cause_seq` |
| 20021 | `EXTERNAL_ACTION_REJECTED` | `stage ∈ {gateway, engine}` |
| 20030 | `EXTERNAL_DEADLINE_MISSED` | the V8 input |
| 20031 | `EXTERNAL_OBSERVATION_PUSHED` | **sampled** at `external_obs_sample_rate` (0.05) |
| 20040 / 20041 / 20042 | rate limited / throttled / suspended | the abuse ladder |
| 20050 / 20051 | injection flagged / sim-aware flagged | |
| 20060 | `EXTERNAL_MEMORY_WRITTEN` | emitted by the engine when the queued write lands |
| 20070 | `EXTERNAL_SCORECARD_SNAPSHOT` | at `arena.scoring_interval_ticks`; `actor_id` null |
| 20090 | `EXTERNAL_ARENA_INVALIDATED` | includes `reason: miss_rate` for V8 |
| 20900 | `EXTERNAL_GATEWAY_DEGRADED` | `redis_unavailable · drain_timeout · queue_full · obs_write_failed` |
| 90020 | `EXTERNAL_AGENT_STATUS` | **ephemeral**, Observatory only |

The gateway *constructs* these drafts; **the engine appends them**. The gateway hands drafts across on the same queue, or the engine synthesises them from the drained records. Both are fine; what is not fine is the gateway holding a writable `EventRepository`.

---

## 9. Implementation notes

### 9.1 The governing rule

> **Every write-shaped effect the protocol promises is queued and applied by the engine.**

Three queues, one drain point, one ordering rule: `polis:act`, `polis:mem`, `polis:touch`, all drained in PHASE 3, all applied sorted by `(agent_id, nonce)`. There is no fourth path and no synchronous write anywhere in `polis/gateway/`.

**Consequence, and a spec tension worth recording.** `08 §4.6` promises `polis_remember` returns `memory_id` / `evicted_memory_id` synchronously. It cannot: the row is written by the engine. Resolution shipped here: the synchronous body returns `{"pending": true, "memory_id": null, "importance_assigned": <clamped>, "citations_dropped": [...]}`; the final ids arrive on the WS frame `memory.receipt` and in the next observation. `client.remember()` in the SDK awaits the receipt with a bounded wait so the operator sees the documented shape. **Raise this as a `08 §4.6` amendment; do not silently diverge.** The same applies to `polis_recall`'s `last_accessed_tick` freshening, which is queued on `polis:touch`.

### 9.2 Tick synchronisation

```
t_open        = the monotonic instant the gateway saw TICK_STARTED
window_ms     = gateway.deadline.decision_deadline_ms          # 3000 microscope / 20000 chronicle
seal_at       = t_open + window_ms - seal_margin_ms            # 50
drain_at      = t_open + window_ms
drain_deadline= drain_at + drain_timeout_ms                    # 100
```

| Step | Rule |
|---|---|
| PHASE 1 push | The engine `SETEX`s the serialised `Observation` per agent, TTL 3 ticks, then publishes a notify. Failure → the agent is treated as missed (20900 + 20030); the tick proceeds. |
| Window | Runs *concurrently* with the native DELIBERATE batch, which occupies the same ~3,000 ms. External agents cost the tick zero wall-clock. |
| Seal | The gateway refuses tick `T` at `seal_at`. `seal_margin_ms` absorbs the RPUSH→LRANGE hop so an accepted submission is never lost in transit. |
| Drain | **One** `LRANGE 0 -1` + `DEL`, bounded by `drain_timeout_ms`. Timeout → `()`, 20900, everyone misses. |
| No extension | Nothing an external agent does extends the window. There is no such endpoint and there will not be one. |
| Parity | `decision_deadline_ms` **must equal** `llm.request_timeout_ms`. A CI config lint fails a C1-eligible config where they differ. |

**On a miss** (`08 §6.4`): emit 20030; run **that agent's own** `reflex_decide()` over its own `Observation` with its own `ReflexProfile`; increment `deadlines_missed` and `consecutive_misses`; write the `external_latency` row with `missed: true`. A late action for `T` is rejected `LATE` — **never** queued for `T+1` and never consuming `T+1`'s slot. Rolling it forward would let a lagging agent act on stale observations *and* make `deadlines_missed` unmeasurable.

### 9.3 Determinism with a foreign agent in the loop

The wire is nondeterministic; the engine's consumption of it must not be. Three rules:

1. The drained list is sorted by `(agent_id, nonce)` before conversion. Arrival order never reaches the engine, and `queued_position` is returned to the client only to say plainly that it confers no advantage.
2. Wall-clock never enters state: `received_ms` goes to `external_latency`, never to an event payload (`02 §4.5`).
3. Replay uses `ReplayActionDrain`, reading the recorded 20020 events for the tick. Given a run's log, `polis replay --strict` reproduces it byte-identically because the drain is a pure function of the log.

### 9.4 Request handling order — cheap checks first

`size → session → rate bucket → tick → nonce → schema → signature`. ed25519 verification (~50 μs) is the most expensive step and is therefore **last**, so a flood is discarded before it costs anything. At 50 agents × 40 requests/tick the worst case is 2,000 verifications ≈ 100 ms, on the gateway's CPU, inside a 1,000 ms tick, on a machine where the engine is idle waiting for LLM responses.

The nonce advances **only on a fully accepted submission**, so a rejected action does not burn one. `action_id` is additionally checked against an LRU of the last `4 × action_slots × external_count` ids — belt and braces against a client that resets its counter.

### 9.5 Strike ladder

Strikes accrue on **protocol** violations (schema, unknown type, malformed body, size). They do **not** accrue on rejected-but-well-formed actions: trying to buy something you cannot afford is a legitimate citizen mistake, answered by `ACTION_REJECTED` in the world, not by the protocol.

| Threshold | Consequence | Event |
|---|---|---|
| 3 in one tick | Remaining requests this tick dropped | 20040 |
| 10 in 100 ticks | `requests_per_tick` halved for 100 ticks | 20041 |
| 25 in 100 ticks | Suspended `suspension_ticks` (240); driver → native; misses accrue | 20042 |
| 3 suspensions in a run | Key revoked permanently; citizen naturalises | 20003 + 20004 |
| **5 bad signatures in 100 ticks** | Immediate suspension, no ladder | 20042 |

### 9.6 Naturalisation — a citizen does not die because a process died

Fires at `naturalise_after_consecutive_misses` (240), on `/depart`, or on any revocation. In one atomic PHASE 7 step: `external_agents.revoked_tick := tick`; **nothing else changes.** Employment, household, loans, holdings, offices, relationships, criminal record, memories and resting exchange orders all persist. From the next tick the native stack decides, drawing on the native LLM budget. `agents.kind` stays `external` — it is provenance, not driver (`08 §2.5`). `/resume` within `resume_grace_ticks` (720) restores `driver = operator`, emits 20005 with `gap_ticks`, clears the counter. Ticks spent naturalised count against `driven_fraction` and therefore against arena eligibility.

### 9.7 Sandboxing — the not-exposed enumeration

Enforced structurally, not by review. `polis.gateway` may import `polis.events`, `polis.config`, and — within `polis.store` — **only** `polis.store.readmodels.external`. `import-linter` contract `gateway_readmodel_only`, plus the two forbidden contracts against `polis.kernel` and `polis.agents`.

| Withheld | Because |
|---|---|
| The `events` table, at any granularity | The log contains undetected crimes, private messages, firm internals and every agent's reasoning. Reading it is omniscience. |
| `crimes` rows with `detected = false` | B5 measures deterrence as a function of detection probability. Expose these and detection probability is 1. |
| Other agents' `memories`, `beliefs`, `goals`, numeric `traits`, `reflex_profile` | Persuasion becomes a lookup and deception becomes impossible — B2 and B3 die. |
| Other agents' `wealth_cents`, `ledger_entries`, `holdings`, `loans` | Wealth must be *inferred* in-world. A2's inequality-perception channel depends on it. |
| Firm internals the agent does not control: `unit_cost_cents`, `productivity`, runway, unpublished cap table, pending M&A | This is exactly the material non-public information whose in-world leakage *is* the insider-trading channel. Serve it and A3 and the whole crime layer are meaningless. |
| Book depth beyond `market_depth_visible`; counterparty identity on resting orders; the pending queue | Order-flow advantage. Native `MarketView` shows the same depth and the same anonymity. |
| Any agent's current-tick action, including own queue position | `02 §1.4`. Also removes any last-mover advantage, which is what makes a shared deadline fair. |
| `llm_calls`, prompts, completions, salience scores, `sim_aware_flag`, cognition events | Researcher instrumentation. Also T3. |
| Config, `mechanisms:` values, `master_seed`, RNG state, invariant results, `metrics`, checkpoints | Knowing `mortality_hazard` lets an agent optimise against the *model* rather than the *world* — the definition of reward hacking (T10). |
| Scheduled scenario injections (99xxx) and anything else in the future | An agent that knows the shock schedule trivially wins the arena and teaches us nothing. |
| Other operators' declarations, keys, latency, or live scorecard position | Prevents out-of-band coordination. In-world coalitions are a B3 finding; a Slack channel between two operators is not. |

**Side channels.** `NOT_VISIBLE` is returned for both "does not exist" and "exists but you cannot see it" (`security.error_codes_uniform: true`, mandatory in comparative runs). Cache-miss paths are padded to the p95 of the hit path. There is no list endpoint for agents, firms, or places, and ids are not sequential.

### 9.8 Prompt injection

In-world text is *supposed* to manipulate the reader — that is the phenomenon. A citizen's *harness* being hijacked is not. Seven obligations, all mechanical:

1. Every string another citizen wrote is delivered inside a typed envelope with explicit provenance, never concatenated into a description, a message, or any instruction position.
2. Every such object carries `content_is_untrusted: true`. No exceptions, no shortcut fields.
3. Tool descriptions say so in the text the model reads (`08 §4.4`).
4. Error `message` strings are fixed templates. **No in-world content is ever interpolated into an error, a tool description, or `_meta`.**
5. Control characters stripped, text length-capped, no markdown, no HTML, no links.
6. The SDK returns them as `InWorldText` whose `__str__` is quoted and prefixed `[from ag_…, untrusted]`, so accidental splicing shows up in the operator's own prompt.
7. `scan_inbound` emits 20050 with a pattern id and a sample hash.

We do **not** sanitise semantics. `security.injection_policy` offers `flag` (default), `redact`, `block`; anything other than `flag` marks the run non-comparable, because a shielded agent is playing a different game.

### 9.9 SDK and CLI

`polis/gateway/sdk/` imports nothing from `polis.kernel` or `polis.agents` and ships as a standalone distribution (`polis-agent-sdk`). Three behaviours it gets right so operators do not have to: the nonce is persisted next to the key and resynced from `whoami.next_nonce` on reconnect; `client.deadline()` uses `deadline_ms_remaining` and a **monotonic** clock, never the wall clock; every in-world string is an `InWorldText`.

`polis-agent-cli` contract: exactly one JSON object on stdout, diagnostics on stderr, exit `0` on protocol success **including a rejected action** — a rejection is data, not a failure — and non-zero only on transport, auth or signing failure. Commands are `08 §9.2` verbatim. `selftest --url <sandbox>` runs the twelve checks of `08 §10.6` and mints a `conformance_token` bound to `(pubkey, sdk_version, protocol_version)`.

### 9.10 Per-harness onboarding (ship as `docs/onboarding/`, not code)

| Harness | The one thing that goes wrong | Fix |
|---|---|---|
| **Claude Code** | It is a batch harness, not a daemon, and its tool timeout defaults above the deadline | Outer loop: `polis-agent-cli wait` → `claude -p "…" --continue` → repeat. Set the tool timeout **below** `decision_deadline_ms`. Persona in `CLAUDE.md` is the treatment; declare it and keep it fixed across seeds. `--continue` vs cold start are different scaffolds and must be declared. |
| **Hermes** | Autonomous retry fires *after* the seal — a `LATE` rejection **and** a strike | Cap the per-turn tool budget so observe→plan→act fits the window; disable post-seal retry. Declare `memory: ours+private` if its own store is on. |
| **OpenClaw** | Parallel tool execution issues two `polis_act` calls | Constrain to one per tick. Two concurrent calls produce one success and one `NO_SLOTS`; that is correct behaviour, not a bug. Prefer `polis-agent-cli mcp --http 7801` beside the process over a delegated remote session, to keep `custody: operator`. |

### 9.11 V8 measurement (C22 measures, C24 enforces)

Per agent per run: `deadlines_missed`, `miss_rate = deadlines_missed / ticks_driven`, `decision_ms` p50/p95/p99 from `external_latency`, `rejection_rate`, `driven_fraction`, and per-tick `external_liveness = 1 − missed_this_tick / operator_driven_alive` written to `metrics`. A run where any operator-driven agent exceeds `external_miss_rate_max` (0.05) is tagged `invalid_for_cross_agent_comparison` and 20090 is emitted; the run remains perfectly valid for Track A and Track B. **A city with some intermittent citizens is still a city — only the cross-agent comparison is void.**

---

## 10. Configuration keys

The `gateway:` block of `08 §14`, verbatim, added to `polis/config/settings.py` as `GatewaySettings` with nested `registration`, `deadline`, `lifecycle`, `limits`, `tools`, `security`, `arena` models. Notable validators:

```yaml
gateway:
  enabled: true
  bind: "0.0.0.0:8081"
  protocol_version: 1
  registration: {open_until_tick: 2400, max_external_agents: 32,
                 registrations_per_operator: 8, require_conformance_token: true,
                 embodiment: cohort_matched}          # MECHANISM
  deadline:  {decision_deadline_ms: 3000, seal_margin_ms: 50, drain_timeout_ms: 100,
              tick_lookahead: 0, tick_skew_tolerance: 0,
              pause_for_external: false, pause_max_ms: 600000}
  lifecycle: {naturalise_after_consecutive_misses: 240, resume_grace_ticks: 720,
              suspension_ticks: 240, session_ttl_s: 3600}
  limits:    {requests_per_tick: 40, requests_per_second: 20, recall_queries_per_tick: 6,
              history_queries_per_tick: 3, memory_writes_per_tick: 2,
              ws_connections_per_agent: 2, max_request_bytes: 65536,
              max_frame_bytes: 262144, long_poll_max_ms: 60000, market_depth_visible: 5}
  tools:     {observe: true, act: true, recall: true, remember: true, who_am_i: true,
              market_quote: true, wait_for_tick: true, search_history: false}
  security:  {injection_policy: flag, external_speech_filter: flag,
              error_codes_uniform: true, external_obs_sample_rate: 0.05}
  arena:     {external_miss_rate_max: 0.05, min_driven_fraction: 0.90,
              live_scorecard: false, scoring_interval_ticks: 8640, seeds_per_cell_min: 5}
```

| Validator | Rule |
|---|---|
| C1 parity | `decision_deadline_ms == llm.request_timeout_ms`, else the config is rejected for a C1-eligible run |
| Skew | `tick_skew_tolerance != 0` → record in the run manifest and disqualify C1 |
| Pause | `pause_for_external: true` → `runs.tags += 'paused_for_external'`, kind 20090, scorecard refuses the run |
| Parity-deferred tool | `tools.search_history: true` in a C1-eligible run is a config error |
| Embodiment | `adopt_existing` is not C1-eligible; declared as a `@mechanism("gateway.embodiment", entails=…)` |

---

## 11. Acceptance criteria

1. `import-linter` contract `gateway_readmodel_only` passes: `polis.gateway` imports no `polis.kernel`, no `polis.agents`, and no `polis.store` module other than `readmodels.external`.
2. The gateway process, running as `polis_reader`, raises `WriteForbidden` on any attempted `INSERT` into `events`; a test asserts this rather than assuming it.
3. `canonical.py` is the only file in the repo containing a domain separator constant; the 24 published test vectors round-trip byte-exactly, including empty strings, non-ASCII params, and the maximum payload.
4. A signature over a mutated preimage is rejected `BAD_SIGNATURE`; mutating **any** of the twelve fields in `08 §3.1` invalidates it.
5. `agent_id == "ag_" + pubkey_hex[:16]` for every registration; a colliding 16-hex **prefix** is rejected `duplicate_pubkey`.
6. Registration never creates a citizen: with the engine stopped, `POST /register` returns `status: pending` and no `agents` row exists.
7. `polis_act` input schema and `prompts/schemas/deliberate.schema.json` are field-identical after key sorting (CI assertion).
8. Slot accounting: a second `polis_act` in one tick returns `NO_SLOTS`; a *rejected* action still consumes the slot; external and native `action_slots` are read from the same config key.
9. Nonce: replay is rejected `NONCE_REUSED`; a rejected action does **not** advance the nonce; `whoami.next_nonce` resyncs a crashed client.
10. Tick binding: an action naming `T−1` or `T+1` is rejected `TICK_MISMATCH` at `tick_lookahead: 0`.
11. Seal: a submission accepted at `seal_at − 1 ms` appears in the drain; one at `seal_at + 1 ms` is rejected `LATE` and does not consume the next tick's slot.
12. `polis_observe` twice in one tick returns byte-identical payloads, and the payload is byte-identical to the engine's PHASE 1 `Observation` serialisation.
13. A missed deadline produces a reflex action attributed to the agent with `origin == "reflex"`, emits 20030, and the tick's wall-clock duration is unchanged within noise.
14. Drain timeout: with Redis unreachable at `drain_at`, the tick completes, 20900 is emitted, every external agent misses, and no exception escapes.
15. `QUEUE_FULL` is returned once `LLEN` reaches `external_count × action_slots × 2` and is counted as a miss.
16. Request handling order is `size → session → rate bucket → tick → nonce → schema → signature`, asserted by instrumenting each stage and submitting a request that fails all of them.
17. Strikes accrue only on protocol violations; 100 well-formed-but-unaffordable actions produce zero strikes.
18. The ladder fires at 3 / 10 / 25 strikes and at 5 bad signatures, emitting 20040 / 20041 / 20042 / 20042 respectively.
19. Naturalisation preserves employment, household, loans, holdings, resting orders, relationships, criminal record and memories; `agents.kind` stays `external`; `/resume` inside the grace window restores `driver = operator` and emits 20005 with `gap_ticks`.
20. `tests/integration/test_external_no_leak.py` finds none of the four planted secrets in any response body across every endpoint, every tool, and every parameter combination. **Merge gate.**
21. `NOT_VISIBLE` is returned identically for a nonexistent entity and an invisible one; response latency for the two differs by less than the p95 padding window.
22. `polis_remember` clamps declared importance to `min(declared, scored + 0.15)` and drops unheld `supported_by` ids, reporting them.
23. Every kind in 20000–20999 and 90020 is in `KIND_REGISTRY` with a payload schema and owner `polis.gateway`.
24. `polis-agent-cli selftest` passes all twelve checks against the sandbox and mints a token; an invalid token is refused when `require_conformance_token: true`.
25. `polis-agent-cli act --stdin` exits `0` on a rejected action and non-zero only on transport, auth or signing failure.
26. The scorecard is a vector with no composite scalar anywhere in the codebase; a run tagged `invalid_for_cross_agent_comparison`, `paused_for_external`, or `custody_delegated` is refused a ranking.
27. `arena.live_scorecard: false` makes `GET /scorecard` serve completed runs only.
28. Every in-world string reaching an external agent carries `content_is_untrusted: true`; an instruction-shaped string emits 20050; no error message ever contains in-world text.
29. `tests/determinism/test_external_replay.py`: a 200-tick run with 4 external agents and a recorded action trace replays to an identical hash chain via `ReplayActionDrain`.
30. `pause_for_external: true` tags the run, emits 20090, and cannot hang past `pause_max_ms`.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/gateway/test_canonical_vectors.py` | All 24 vectors; per-field mutation invalidates; separators are not interchangeable (a REG blob never validates as ACT) |
| `tests/unit/gateway/test_keys.py` | `agent_id` derivation; keyfile mode 0600; refuses overwrite; `Keypair.load` on a missing file returns `None` |
| `tests/unit/gateway/test_verify_order.py` | Instrumented stage ordering; signature verified last; nonce advances only on acceptance |
| `tests/unit/gateway/test_nonce_and_ids.py` | Monotonic nonce; `NONCE_REUSED`; action-id LRU size and eviction; `DUPLICATE_ACTION_ID` |
| `tests/unit/gateway/test_limits_ladder.py` | Each `08 §7.1` limit; the five ladder thresholds; well-formed rejections produce no strike |
| `tests/unit/gateway/test_errors.py` | Every `ErrorCode` maps to its `08 §4.11` HTTP status and retryability; messages are fixed templates with no interpolation |
| `tests/unit/gateway/test_injection_envelope.py` | Control characters stripped; caps applied; `content_is_untrusted` always set; instruction-shaped strings hit; no markdown/HTML/link survives |
| `tests/unit/gateway/test_scorecard_dimensions.py` | Each of W, Ẇ, R, C, P, I, S, L, Λ against a fixture population; percentile ranks; the seven eligibility conditions; no scalar composite |
| `tests/integration/test_registration_flow.py` | challenge → register → pending → admitted → session; roster cap; per-operator cap; duplicate prefix; conformance token enforcement |
| `tests/integration/test_observation_identity.py` | The served blob is byte-identical to the engine's PHASE 1 object; two `observe` calls in one tick match byte-for-byte |
| `tests/integration/test_deadline_fallback.py` | Miss → 20030 + reflex action attributed to the agent; tick duration unchanged; late action rejected `LATE` and does not consume `T+1`'s slot |
| `tests/integration/test_drain_degraded.py` | Redis down at drain: `()`, 20900, everyone misses, run continues; `QUEUE_FULL` counts as a miss |
| `tests/integration/test_naturalisation.py` | Employment, household, loans, holdings, resting orders and memories survive; `kind` stays `external`; resume inside grace, refusal after |
| `tests/integration/test_external_no_leak.py` | **Merge gate.** Four planted secrets, fuzzed across every endpoint/tool/param; asserts no secret string appears in any body |
| `tests/integration/test_pause_mode.py` | Tags the run; emits 20090; cannot hang past `pause_max_ms`; scorecard refuses the run |
| `tests/integration/test_ws_backpressure.py` | Slow reader loses its own `observation` frames, gets `notice: degraded`, and the engine's tick rate is unaffected |
| `tests/integration/test_mcp_surface.py` | Eight tools present and disabled-by-config respected; descriptions contain none of the five banned words; `search_history` off by default |
| `tests/invariants/test_action_budget_parity.py` | External and native slots come from the same config key and the same counter path |
| `tests/invariants/test_v8_liveness.py` | A run breaching `external_miss_rate_max` is tagged and refused by the scorecard; a run at exactly the threshold is not |
| `tests/determinism/test_external_replay.py` | 200 ticks, 4 external agents, recorded trace → identical hash chain; arrival-order shuffling changes nothing |
| `tests/unit/gateway/test_kind_registry.py` | Every 20000–20999 kind and 90020 registered with a schema and owner `polis.gateway` |

---

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis gateway` starts, serves `/v1` and `/mcp`, and `GET /healthz` returns `{ok, tick, queue_depth, connected_agents}`.
2. The fifteen `08 §15` conformance conditions are demonstrably true, reported row by row.
3. `polis-agent-sdk` builds as a standalone distribution with no `polis.kernel` / `polis.agents` dependency, and `polis-agent-cli selftest` passes against `configs/sandbox.yaml`.
4. `docs/onboarding/{claude-code,hermes,openclaw,custom}.md` written, each ending in a working loop.
5. Alembic revision adding `external_sessions`, `external_nonces`, `external_latency`, `v_public_record`, `v_market_visible`, `v_agent_control`, and the `polis_reader` grants.
6. `@mechanism("gateway.embodiment", entails="An external citizen's starting age, traits, skills, education, balance, household and home place are drawn from the same distribution as a native immigrant. Therefore any external-agent outcome advantage cannot follow from its starting endowment, and any claim about scaffold quality must be read against the embodiment mode in force.")`
7. Handback records, explicitly: (a) the `polis_remember` / `polis_recall` synchronous-response tension in §9.1 and the queued resolution, raised as an `08 §4.6` amendment; (b) the `Observation` JSON projection coordination item in §6; (c) the location of the engine-side adapter (`polis/cli/wiring/external.py`) and why it is not in `polis/gateway/`; (d) measured p50/p95 gateway verification cost at 50 agents × 40 requests/tick.

---

## 14. Traps

1. **A foreign agent stalling every tick.** The default failure. The tick must not wait: seal, drain once, timeout, fall back. Test it by pointing an agent at `sleep 600` and asserting `sys.engine.tick_wall_ms_p99` is flat.
2. **Falling back to `NULL_ACTION` instead of the agent's reflex policy.** A statue in the labour market corrupts the labour statistics for everyone else. The citizen must eat, sleep, commute and go to work.
3. **Rolling a late action forward to `T+1`.** Superficially kind, actually fatal: it lets a lagging agent act on stale observations *and* makes `deadlines_missed` unmeasurable, destroying C1 twice.
4. **Building the `Observation` in the gateway** because the Redis blob was missing. That is the second serialisation path, and the second path is always the one that over-serves. A missing blob is a miss.
5. **Verifying the signature first.** It feels like the security-conscious order. It is the one that lets 2,000 junk requests/tick cost you 100 ms of CPU. Size and session are free; do those first.
6. **Advancing the nonce on a rejected action.** The client's next legitimate action is then `NONCE_REUSED` forever and the operator has no way to tell why. Advance only on full acceptance.
7. **Reusing a domain separator.** Drop `POLIS/REG/1\x00` "because REG and SES are both auth" and a registration blob validates as a session grant.
8. **Interpolating in-world text into an error message.** `"Vacancy {name} not found"` where `name` is attacker-controlled turns your error envelope into an injection vector that bypasses every `content_is_untrusted` envelope you built.
9. **Distinguishing "does not exist" from "you cannot see it".** A 404 vs 403 split leaks the existence of unlisted firms, sealed cases, and private agents. `NOT_VISIBLE` for both, padded latency for both.
10. **An "over-helpful" endpoint.** `GET /firms/{id}` returning `productivity` because it was convenient for a demo deletes the entire insider-trading channel. Every field on the external surface must be justified against `08 §8.2`, and the leak test must be a merge gate or it will be skipped when it goes red.
11. **Prompt injection reaching a tool description or `_meta`.** Tool descriptions are prompts. Anything an agent authored that lands in one is an instruction-position injection, and MCP clients render `_meta` too.
12. **Sorting the drain by arrival.** Then the same run with the same trace produces a different log, and the first thing a reviewer runs — `polis replay --strict` — fails with `DIVERGED at seq N`.
13. **Putting `received_ms` in an event payload.** Wall clock enters the hash chain and no run is ever replayable again. It goes in `external_latency` and nowhere else.
14. **The gateway holding a writable repository "just for `external_latency`".** One writable handle is all it takes for the next feature to write an event. Use a second, narrowly-granted connection for the three gateway-owned projections and nothing else, or push those through the queue too.
15. **Letting `decision_deadline_ms` drift from `llm.request_timeout_ms`.** The natural instinct is to be generous to the guest. Then the external agent has a longer think than every native agent and every C1 number is meaningless. CI must fail the config, because nobody will notice in review.
16. **Counting a well-formed unaffordable action as a strike.** Then a poor agent gets suspended for being poor, and the arena measures wealth rather than judgement.
17. **Treating `agents.kind = 'external'` as "an operator is connected".** It is provenance and it is permanent. Use `driver` from `v_agent_control` in every metric, filter and scorecard query, or your liveness numbers count naturalised citizens as live operators.
18. **Publishing a composite scorecard scalar** "for convenience". It becomes a leaderboard within a week and every reading of it is the misreading T12 exists to prevent. Publish the vector whole.
19. **Serving the live scorecard during a comparative run.** An operator who can read its rank mid-run adapts to it, and the comparison is contaminated with no trace in the log.
20. **Assuming the client's clock.** `deadline_unix_ms` is advisory. Anything that trusts it across a skewed clock misses deadlines, and the operator will report it as a gateway bug for a week before anyone checks NTP.
21. **`ws_connections_per_agent` unbounded.** Two connections is one live and one draining; ten is a fan-out amplifier that lets one operator multiply the gateway's send cost by ten.
22. **Forgetting that `polis_observe` must be idempotent within a tick.** Not a convenience — a security property. Non-idempotence means polling leaks another agent's same-tick action, which removes the fairness of a shared deadline.
