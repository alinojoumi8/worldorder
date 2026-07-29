# C02 — Event log, kind registry, hash chain

**M0** · Owner module `polis/events` · Depends on: **C01** · Blocks: **C03 C04 C05 and every chunk that emits an event** · Size **M** (2–4 days)

---

## 1. Context

The log is the truth (`02 §1.1`). Every other chunk writes to it, every projection is
derived from it, and every research claim is ultimately a query over it. This chunk builds
the envelope, the byte-exact canonical serialisation and hash chain, the single file where
event kinds may be declared, per-kind payload validation, the per-tick batched writer, the
read/query surface, chain verification, and the causal walk over `cause_seq`. It contains
no storage code — it defines the `EventSink`/`EventReader` protocols that C03 implements —
and no simulation logic.

The hash chain is the one artefact in POLIS that must be **byte-identical across machines,
Python versions, and reimplementations**. Treat §3.1 of `02` as a wire format, not as a
suggestion.

---

## 2. Required reading

| Source | Why |
|---|---|
| `../docs/02-ARCHITECTURE.md` §3 (whole), §4.2, §4.6, §5 PHASE 6, §7.1, §12 | Binding. §3.1 is the serialisation. §3.2 is the range table. §3.3 is the sampling policy. |
| `../docs/03-DATA-MODEL.md` §1.2 (`events`), §0 (conventions), §12 (rebuild) | Column types, index shapes, and what a reader must support. |
| `../docs/10-RESEARCH-AND-OBSERVABILITY.md` §5.2 (`polis verify` contract) | The exact output contract for `polis verify`. |
| **C01** — `polis/config/canon.py`, `polis/config/errors.py`, `polis/cli/app.py` | The canonicaliser and error base you must use. |

---

## 3. Scope — in

1. `polis/events/types.py` — `Event`, `NewEvent`, `GENESIS_PREV_HASH`.
2. `polis/events/hashing.py` — canonical bytes, `event_hash`, `seal`, per-event verification.
3. `polis/events/kinds.py` — `KindSpec`, `KIND_REGISTRY`, `KIND_RANGES`, `register_kind`, reserved ranges including the new 4100–4199 and 10060–10069.
4. `polis/events/schemas.py` — per-kind JSON Schema (draft 2020-12) compilation, caching, validation, `schema_hash`.
5. `polis/events/log.py` — `EventLog` writer: stage → per-tick atomic commit, seq assignment, chain maintenance, ephemeral routing.
6. `polis/events/reader.py` — `EventQuery`, `EventReader` protocol, an in-memory implementation.
7. `polis/events/verify.py` — full-chain verification, `ChainReport`, signature checks.
8. `polis/events/causal.py` — ancestor/descendant walks over `cause_seq`.
9. `polis/events/sampling.py` — the `02 §3.3` cognition sampling gate.
10. `polis/cli/commands/verify.py` — a working `polis verify`.
11. `scripts/gen_kind_table.py` — regenerates the kind table for `11-GLOSSARY.md` from the registry.

## 4. Scope — out

| Not built here | Owner |
|---|---|
| Postgres `EventSink`/`EventReader` implementations, partitioning, COPY | C03 |
| Redis publication of ephemerals (you define `EphemeralSink`, C03/C23 implement) | C03 |
| ed25519 key management and signing; you **verify** signatures, you do not mint them | C22 |
| Kinds outside 1001–1006 and 90001 | the owning chunk |
| `polis replay`, `polis rebuild` | C24 / C03 |
| Full-text search over payloads | C03 |

---

## 5. Interfaces you provide

```python
# polis/events/types.py
GENESIS_PREV_HASH: Final[str] = "0" * 64
EPHEMERAL_SEQ: Final[int] = -1

@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    run_id: UUID
    tick: int
    sim_time: datetime            # UTC-naive, microsecond == 0
    kind: int
    actor_id: str | None
    subject_ids: tuple[str, ...]
    cause_seq: int | None
    payload: Mapping[str, Any]
    sig: str | None
    prev_hash: str
    hash: str

@dataclass(frozen=True, slots=True)
class NewEvent:
    """A draft. No seq, no hashes. What every other chunk constructs."""
    kind: int
    payload: Mapping[str, Any]
    actor_id: str | None = None
    subject_ids: tuple[str, ...] = ()
    cause_seq: int | None = None
    sig: str | None = None
```

```python
# polis/events/hashing.py
def canonical_event_bytes(*, seq: int, run_id: UUID, tick: int, sim_time: datetime,
                          kind: int, actor_id: str | None, subject_ids: Sequence[str],
                          cause_seq: int | None, payload: Mapping[str, Any],
                          sig: str | None, prev_hash: str) -> bytes:
    """02 §3.1, byte-exact. Concatenation, no separators, no length prefixes."""
def event_hash(**kwargs: Any) -> str: ...                     # sha256_hex(canonical_event_bytes(...))
def seal(draft: NewEvent, *, seq: int, run_id: UUID, tick: int,
         sim_time: datetime, prev_hash: str) -> Event: ...
def recompute(ev: Event) -> str: ...
def verify_event(ev: Event) -> bool: ...                      # recompute(ev) == ev.hash
def verify_signature(ev: Event, pubkey_hex: str | None = None) -> bool:
    """ed25519 over canonical_event_bytes with sig=None.

    pubkey defaults to actor_id[3:] only for a full ag_<64-lowercase-hex> id.
    Callers must supply pubkey_hex for legacy or truncated agent ids.
    """
```

```python
# polis/events/kinds.py
class Persistence(StrEnum):
    PERSISTED = "persisted"
    SAMPLED   = "sampled"          # 02 §3.3 cognition sampling
    EPHEMERAL = "ephemeral"        # 90000-90999; never stored, never chained

@dataclass(frozen=True, slots=True)
class KindSpec:
    kind: int
    name: str
    owner: str                      # dotted module, e.g. "polis.economy.labour"
    persistence: Persistence
    schema: Mapping[str, Any]       # JSON Schema draft 2020-12
    description: str = ""
    since: str = "1.0"

@dataclass(frozen=True, slots=True)
class KindRange:
    lo: int; hi: int; domain: str; owner: str; persistence: Persistence

KIND_RANGES: Final[tuple[KindRange, ...]]
KIND_REGISTRY: Final[dict[int, KindSpec]]
KIND_BY_NAME: Final[dict[str, int]]

def register_kind(kind: int, name: str, *, owner: str, persistence: Persistence,
                  schema: Mapping[str, Any], description: str = "") -> int:
    """Returns `kind` so call sites read `HIRED: Final[int] = register_kind(5010, ...)`.
    Raises KindError on: duplicate kind, duplicate name, kind outside any declared range,
    owner disagreeing with the range's owner, persistence disagreeing with the range."""
def spec(kind: int) -> KindSpec: ...
def range_for(kind: int) -> KindRange: ...
def is_ephemeral(kind: int) -> bool: ...
def is_known(kind: int) -> bool: ...
def registry_manifest() -> dict[str, Any]: ...   # {kind: {name, owner, persistence, schema_hash}}

class KindError(PolisError): ...
```

```python
# polis/events/schemas.py
def validate_payload(kind: int, payload: Mapping[str, Any]) -> None:  # raises PayloadSchemaError
def validator_for(kind: int) -> Callable[[Mapping[str, Any]], None]: ...   # compiled + cached
def schema_hash(kind: int) -> str: ...
def assert_json_safe(payload: Mapping[str, Any]) -> None:
    """Rejects datetime, Decimal, set, bytes, UUID, NaN, Infinity, non-str keys."""
class PayloadSchemaError(PolisError):
    kind: int; path: str; message: str
```

```python
# polis/events/log.py
class EventSink(Protocol):
    async def append(self, events: Sequence[Event]) -> None: ...
class EphemeralSink(Protocol):
    async def publish(self, events: Sequence[Event]) -> None: ...

class MemoryEventSink(EventSink):       # ships here; used by every unit test
    events: list[Event]
class NullEphemeralSink(EphemeralSink): ...

@dataclass(frozen=True, slots=True)
class CommitResult:
    tick: int; persisted: int; ephemeral: int; dropped_sampled: int
    first_seq: int; last_seq: int; chain_hash: str

class EventLog:
    def __init__(self, run_id: UUID, sink: EventSink, *,
                 ephemeral_sink: EphemeralSink | None = None,
                 validate: bool = True,
                 start_seq: int = 0,
                 start_prev_hash: str = GENESIS_PREV_HASH,
                 sampler: "CognitionSampler | None" = None) -> None: ...
    @property
    def last_seq(self) -> int: ...
    @property
    def chain_hash(self) -> str: ...
    def stage(self, draft: NewEvent, *, tick: int, sim_time: datetime) -> Event:
        """Validates, seals, assigns seq, extends the in-memory chain. No I/O.
        Ephemeral kinds get seq=EPHEMERAL_SEQ, hash='', and never touch the chain."""
    def staged(self) -> tuple[Event, ...]: ...
    async def commit(self, tick: int) -> CommitResult:
        """One batched append for the whole tick. On sink failure: rollback to the
        pre-tick (seq, chain_hash) and re-raise. Never partially commits a tick."""
    def rollback(self) -> None: ...
```

```python
# polis/events/sampling.py
class CognitionSampler:
    def __init__(self, rate: float, seed_for: Callable[[str, str, int], int]) -> None: ...
    def keep(self, ev: Event, *, routed_mode: str) -> bool:
        """02 §3.3: always True when routed_mode in {deliberate, reflect};
        else a seeded draw at `rate`. Pure; never consults wall clock."""
```

```python
# polis/events/reader.py
@dataclass(frozen=True, slots=True)
class EventQuery:
    run_id: UUID
    kinds: frozenset[int] | None = None
    kind_range: tuple[int, int] | None = None
    actor_id: str | None = None
    subject_id: str | None = None
    from_tick: int | None = None
    to_tick: int | None = None
    from_seq: int | None = None
    to_seq: int | None = None
    order: Literal["seq", "seq_desc"] = "seq"
    limit: int | None = None

class EventReader(Protocol):
    async def get(self, run_id: UUID, seq: int) -> Event | None: ...
    def scan(self, q: EventQuery) -> AsyncIterator[Event]: ...
    async def count(self, q: EventQuery) -> int: ...
    async def last(self, run_id: UUID) -> Event | None: ...
    async def by_cause(self, run_id: UUID, cause_seq: int) -> list[Event]: ...

class MemoryEventReader(EventReader):
    def __init__(self, sink: MemoryEventSink) -> None: ...
```

```python
# polis/events/verify.py
Reason = Literal["hash_mismatch", "prev_hash_mismatch", "seq_gap", "seq_duplicate",
                 "bad_signature", "missing_signature", "schema_invalid", "unknown_kind",
                 "ephemeral_persisted", "tick_regression", "sim_time_regression"]

@dataclass(frozen=True, slots=True)
class ChainFailure:
    seq: int; kind: int; reason: Reason; expected: str; actual: str

@dataclass(frozen=True, slots=True)
class ChainReport:
    run_id: UUID; events_checked: int; first_seq: int; last_seq: int
    terminal_hash: str; ok: bool
    signatures_verified: int; unknown_kinds: tuple[int, ...]
    failures: tuple[ChainFailure, ...]

async def verify_run(reader: EventReader, run_id: UUID, *,
                     check_signatures: bool = True, check_schemas: bool = True,
                     from_seq: int = 0, stop_on_first: bool = False,
                     progress: Callable[[int], None] | None = None) -> ChainReport: ...
def verify_batch(events: Sequence[Event], *, start_prev_hash: str,
                 start_seq: int) -> ChainReport: ...
```

```python
# polis/events/causal.py
@dataclass(frozen=True, slots=True)
class CausalNode:
    event: Event; depth: int; children: tuple[int, ...]

async def ancestors(reader: EventReader, run_id: UUID, seq: int, *,
                    max_depth: int = 64) -> list[Event]:
    """Walk cause_seq backwards. Index 0 is the event itself. Cycle-safe."""
async def descendants(reader: EventReader, run_id: UUID, seq: int, *,
                      max_depth: int = 8, max_nodes: int = 5_000) -> list[CausalNode]:
    """Breadth-first over ev_cause. Deterministically ordered by (depth, seq)."""
async def explain(reader: EventReader, run_id: UUID, seq: int, *,
                  max_depth: int = 64) -> dict[str, Any]:
    """{'event':…, 'ancestors':[…], 'root':…, 'depth':n, 'truncated':bool} — the
    Observatory's 'why did this happen?' payload."""
def has_ancestor_in_range(chain: Sequence[Event], lo: int, hi: int) -> bool:
    """Shared 'organic vs injected' filter (10 §R9). lo/hi are kind bounds."""
```

---

## 6. Interfaces you consume

| From | What |
|---|---|
| C01 `polis.config.canon` | `canonical_json`, `canonical_bytes`, `sha256_hex`, `round_floats` |
| C01 `polis.config.errors` | `PolisError` base for `KindError`, `PayloadSchemaError` |
| C01 `polis.cli.app` | `app` to register `polis verify` |

Third-party: `jsonschema>=4.23` (draft 2020-12), `cryptography` or `pynacl` for ed25519.

---

## 7. Data model touched

| Table | Access |
|---|---|
| `events` | **Defined, not implemented.** This chunk produces the `Event` objects and the `EventSink`/`EventReader` protocols that C03's Postgres implementation satisfies. The column list in `03 §1.2` is the field list of `Event`, one to one. |

Nothing else. No connection is opened in this chunk.

---

## 8. Event kinds owned

`polis/events/kinds.py` is created here and owned here as a **file**; the ranges below are
declared here and the kinds inside them are registered by their owning chunks.

### 8.1 The range table (`KIND_RANGES`) — declared by C02

| Range | Domain | Owner | Persistence |
|---|---|---|---|
| 1000–1999 | Kernel & run lifecycle | `polis.kernel` | persisted |
| 2000–2999 | Agent lifecycle & vitals | `polis.agents` | persisted |
| 3000–3999 | World, movement, space | `polis.world` | persisted |
| 4000–4099 | Cognition, memory, salience | `polis.agents` | **sampled** |
| **4100–4199** | **LLM router** | **`polis.llm`** | persisted |
| 5000–5999 | Labour & employment | `polis.economy.labour` | persisted |
| 6000–6999 | Firms, production, goods | `polis.economy.firms` | persisted |
| 7000–7999 | Exchange, securities | `polis.economy.exchange` | persisted |
| 8000–8999 | Banking, credit, monetary policy | `polis.economy.banking` | persisted |
| 9000–9999 | Ventures, funding, M&A | `polis.economy.ventures` | persisted |
| 10000–10059 | Communication & social graph | `polis.society.comms` | persisted |
| **10060–10069** | **Belief updates** | **`polis.society.beliefs`** | persisted |
| 10070–10999 | Communication & social graph (cont.) | `polis.society.comms` | persisted |
| 11000–11999 | Social media & news | `polis.society.media` | persisted |
| 12000–12999 | Government, elections, policy | `polis.society.polity` | persisted |
| 13000–13999 | Crime, police, courts | `polis.society.law` | persisted |
| 14000–14999 | Education & skills | `polis.agents.education` | persisted |
| 15000–15999 | Households & demographics | `polis.agents.demography` | persisted |
| 20000–20999 | External agent protocol | `polis.gateway` | persisted |
| 90000–90999 | **Ephemeral** | any | ephemeral |
| 99000–99999 | Researcher injection & scenario DSL | `polis.research` | persisted |

### 8.2 Kinds registered by C02

C04 owns 1010–1099 (invariants, checkpoints, cadences). C02 owns only the log's own
framing events, because the writer emits and the verifier depends on them.

| Kind | Name | Persistence | Payload (required keys) |
|---|---|---|---|
| 1001 | `RUN_STARTED` | persisted | `config_hash`, `master_seed`, `code_git_sha`, `prompt_manifest`, `model_manifest`, `completion_cache_manifest_hash`, `mechanism_manifest`, `metric_manifest`, `kind_registry_hash`, `clock_profile`, `scale` |
| 1002 | `TICK_STARTED` | persisted | `tick`, `sim_time`, `due_cadences` (sorted array of str) |
| 1003 | `TICK_COMPLETED` | persisted | `tick`, `event_count`, `llm_calls`, `cost_usd_micros` (int), `chain_hash` |
| 1004 | `RUN_COMPLETED` | persisted | `last_tick`, `last_seq`, `chain_hash`, `total_events` |
| 1005 | `RUN_HALTED` | persisted | `tick`, `reason`, `detail`, `last_seq`, `chain_hash` |
| 1006 | `RUN_RESUMED` | persisted | `from_tick`, `checkpoint_tick`, `last_seq`, `chain_hash` |
| 90001 | `TICK_HEARTBEAT` | **ephemeral** | `tick`, `sim_time`, `phase_ms` |

`RUN_STARTED.cost_usd_micros` convention: **USD never appears as a float in a payload.**
Costs in payloads are integer micro-dollars. `Decimal` is banned by `assert_json_safe`.

---

## 9. Implementation notes

**9.1 The serialisation, field by field.** Deviating on any line changes every hash.

| Field | Bytes |
|---|---|
| `seq` | `seq.to_bytes(8, "big")` — unsigned; `seq >= 0` asserted |
| `run_id` | `run_id.bytes` — 16 raw bytes, **not** the hex string |
| `tick` | `tick.to_bytes(8, "big")` |
| `sim_time` | `sim_time.isoformat().encode()` — UTC-naive, `microsecond == 0`, so `"2100-01-01T00:00:00"` |
| `kind` | `kind.to_bytes(4, "big")` |
| `actor_id` | `(actor_id or "").encode()` — UTF-8 |
| `subject_ids` | `"\x1f".join(sorted(subject_ids)).encode()` — sorted at hash time, **not** at construction |
| `cause_seq` | `(cause_seq if cause_seq is not None else -1).to_bytes(8, "big", signed=True)` |
| `payload` | `canonical_json(payload).encode("utf-8")` — from `polis.config.canon` |
| `sig` | `(sig or "").encode()` |
| `prev_hash` | `bytes.fromhex(prev_hash)` — 32 raw bytes, **not** the 64 ASCII characters |

Note the asymmetry: `prev_hash` is hex-decoded, everything else that is textual is
UTF-8 encoded. This is deliberate and is the single most commonly mis-implemented line.

**9.2 Payload normalisation before hashing.** `stage()` applies, in order:
`assert_json_safe` → `round_floats(payload, 6)` (02 §4.6) → `validate_payload` → seal. The
**rounded** payload is what is stored and hashed. Rounding after validation would let an
unrounded float into the hash; rounding before validation lets a schema `minimum` reject a
value the caller supplied — that is correct and intended.

**9.3 Ephemerals never chain.** `is_ephemeral(kind)` events are diverted in `stage()` to a
separate buffer with `seq = EPHEMERAL_SEQ`, `prev_hash = GENESIS_PREV_HASH`, `hash = ""`.
They do not increment `last_seq` and do not alter `chain_hash`. On `commit()` they go to
`ephemeral_sink.publish()` and nowhere else. `verify_run` raises `ephemeral_persisted` if
it ever finds one in the log.

**9.4 Sampled kinds.** 4000–4099 pass through `CognitionSampler.keep()`. A dropped event is
**never sealed** — it consumes no `seq`, so the chain is identical whether or not the
sampler drops it for a given agent. `CommitResult.dropped_sampled` counts them.

**9.5 Atomic per-tick commit.** `stage()` mutates only in-memory state. `commit(tick)`
calls `sink.append(all_persisted_for_tick)` exactly once. On exception, restore
`(last_seq, chain_hash)` to the values captured at the first `stage()` of this tick, drop
the buffer, re-raise. `02 §10` requires a store failure to retry and then HALT; the retry
policy lives in C03's sink, not here. Two consequences the rest of the system relies on:
a tick is all-or-nothing, and a torn tick after a crash is detectable by a missing
`TICK_COMPLETED` (C04's resume logic uses exactly this).

**9.6 `seq` is global-monotonic within a run and assigned at stage time**, in the order
`stage()` is called. PHASE 3 sorts actions by `actor_id` before mutation (02 §4.3), so the
call order into `stage()` is already canonical. C02 does not re-sort; re-sorting would hide
an ordering bug in a caller.

**9.7 Schemas.** One JSON Schema object per kind, inline in `kinds.py` (not a separate file
tree — the registry is one file by `02 §3.2`). Every schema **must** carry
`"type": "object"`, an explicit `"required"` list, and `"additionalProperties": false`
(`09 §6.4`). `validator_for` compiles once per kind and caches the compiled validator;
naive per-call `jsonschema.validate` at ~20k events/tick is a measurable cost.
`schema_hash(kind) = sha256_hex(canonical_bytes(schema))`. `registry_manifest()` hashed as
a whole gives `kind_registry_hash` for `RUN_STARTED` — two runs with different registries
are not comparable.

**9.8 Unknown kinds are read-tolerated, write-rejected.** `stage()` on an unregistered kind
raises `KindError` (`02 §3.2`: adding a kind anywhere else is a bug). A reader that
encounters an unknown kind returns the `Event` unchanged, skips schema validation, and
records it in `ChainReport.unknown_kinds` — `02 §1.2` requires existing readers never to
break on new kinds.

**9.9 Signatures.** `sig` is mandatory iff the event originated from an external agent
(20000–20999 and any event whose `actor_id` names a registered external agent) or is a
99xxx injection (`02 §3.4`). Verification is ed25519 over
`canonical_event_bytes(..., sig=None)`. `agent_id` **is** the full public-key form
`ag_<pubkey_hex>`; the prior `ag_<pubkey_hex[:16]>` form truncated, so the full pubkey must
be supplied by the caller — `verify_signature` takes an explicit `pubkey_hex` and only
falls back to `actor_id[3:]` for full-length ids. C22 owns
the key registry; C02 owns the algorithm.

**9.10 `polis verify`.** `polis verify --run <id> [--from-seq N] [--no-signatures] [--json]`.
Streams via `reader.scan` in `seq` order with bounded memory (never materialise the log).
Output on success: `chain OK: <n> events, seq 0..N, terminal <hash>, <k> signatures verified`.
On failure: the first failing seq, reason, expected, actual, and exit code 1. `--json`
emits `ChainReport` as canonical JSON.

**9.11 Causal walk.** `ancestors` follows `cause_seq` with a visited set and `max_depth`;
`cause_seq` should be acyclic by construction but a bug must not hang the Observatory.
`descendants` needs `by_cause` (backed by `ev_cause` in C03) and is bounded by both depth
and node count, returning `truncated: true` rather than an unbounded walk.

---

## 10. Configuration keys

```yaml
events:
  validate_payloads: true          # false only for a measured throughput experiment; never in CI
  cognition_sample_rate: 0.02      # 02 §3.3
  ephemeral_enabled: true          # false disables Redis publication entirely
  max_subject_ids: 64              # guard against an unbounded subject list in a payload
  max_payload_bytes: 65536         # stage() raises above this; forces prompts out of payloads
verify:
  batch_size: 10000
  check_signatures: true
```

Added to `Settings` as `EventSettings`. `cognition_sample_rate` is read by C09; C02 only
provides the sampler.

---

## 11. Acceptance criteria

- [ ] `canonical_event_bytes` reproduces a hand-computed golden vector checked into `tests/unit/events/vectors/chain_v1.json` (10 events, all field variants: null actor, empty subjects, negative-encoded `cause_seq`, non-ASCII payload, float requiring rounding).
- [ ] Changing any single byte of any event's payload changes that event's hash **and** every subsequent event's hash.
- [ ] Genesis event has `prev_hash == "0" * 64` and `verify_event` returns True.
- [ ] `subject_ids` order at construction does not affect the hash; the stored tuple preserves caller order.
- [ ] A tz-aware `sim_time`, or one with `microsecond != 0`, raises rather than hashing.
- [ ] A payload containing `datetime`/`Decimal`/`set`/`bytes`/`NaN`/an int key raises `PayloadSchemaError` before sealing.
- [ ] Floats in payloads are rounded to 6 dp before hashing; `stage()` of `0.1 + 0.2` and of `0.3` produce identical hashes.
- [ ] `register_kind` rejects: duplicate kind, duplicate name, out-of-range kind, owner/range mismatch, persistence/range mismatch.
- [ ] Every kind in `KIND_REGISTRY` has a schema with `additionalProperties: false` and a non-empty `required`.
- [ ] Ephemeral kinds never appear in `sink.append`, never consume a `seq`, and never change `chain_hash`.
- [ ] Sampler-dropped 40xx events do not consume a `seq`; the chain over 200 ticks is identical at `rate=0.0` and `rate=1.0` **except** for the sampled events themselves.
- [ ] `commit()` raising from the sink leaves `last_seq` and `chain_hash` at their pre-tick values, and re-staging the same drafts yields the identical chain.
- [ ] `verify_run` detects each of the eleven `Reason` values on a purpose-built corrupted log.
- [ ] `polis verify --run <id>` exits 0 on a clean 1,000-event log and 1 with the first failing seq on a tampered one.
- [ ] `ancestors` terminates on a synthetic cycle; `descendants` returns `truncated` at `max_nodes`.
- [ ] Staging 20,000 events and committing takes < 150 ms with `MemoryEventSink` (the PHASE 6 budget, 02 §11).

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/events/test_canonical_bytes.py` | Field-by-field byte layout against the golden vector; `run_id.bytes` not hex; `prev_hash` hex-decoded; `cause_seq=None` → `-1` signed; `\x1f` join of sorted subjects; UTF-8 for non-ASCII actor and payload. |
| `tests/unit/events/test_hash_chain.py` | Genesis prev_hash; chain linkage; single-byte tamper propagates; `verify_batch` reports `hash_mismatch` at the right seq; re-sealing is idempotent. |
| `tests/unit/events/test_payload_normalisation.py` | `round_floats` applied pre-hash; `assert_json_safe` rejection table; `max_payload_bytes`; `max_subject_ids`. |
| `tests/unit/events/test_kind_registry.py` | Range table covers 1000–99999 without overlap; 4100–4199 owned by `polis.llm`; 10060–10069 owned by `polis.society.beliefs`; every registration error path; `registry_manifest` is stable and hashable. |
| `tests/unit/events/test_schemas.py` | Every registered schema validates its own example payload; `additionalProperties: false` enforced; compiled validators are cached (call count on a spy). |
| `tests/unit/events/test_event_log.py` | seq monotonicity; per-tick batching (one `append` call per tick); rollback on sink failure; ephemeral routing; `CommitResult` counts. |
| `tests/unit/events/test_sampling.py` | Deliberate/reflect always kept; reflex kept at the seeded rate; dropping consumes no seq; same seed → same keep set. |
| `tests/unit/events/test_verify.py` | One test per `Reason`; `stop_on_first`; `from_seq` resumption; signature verification with a known ed25519 vector; missing signature on a 99xxx event. |
| `tests/unit/events/test_causal.py` | Backward walk order; cycle safety; `max_depth`; `descendants` ordering by `(depth, seq)`; `has_ancestor_in_range` for the 99xxx organic filter. |
| `tests/determinism/test_chain_determinism.py` | Two processes stage the same 5,000 drafts and produce byte-identical `chain_hash`; run under `PYTHONHASHSEED` 0 and 1 to prove hash-seed independence. |
| `tests/unit/events/test_verify_cli.py` | Exit codes, human and `--json` output, first-failure reporting. |

---

## 13. Definition of done

`chunks/README.md §5` items 1–9, plus: the golden chain vector is checked in with a
comment explaining that changing it is a **wire-format change** requiring a spec amendment;
`scripts/gen_kind_table.py` regenerates the glossary table and CI fails if the checked-in
table is stale; `EventSink`/`EventReader`/`EphemeralSink` protocols are stable enough that
C03 implements them without changing this chunk.

---

## 14. Traps

1. **`run_id.bytes` vs `str(run_id).encode()`.** The spec says `run_id.bytes` — 16 raw
   bytes. Using the hex string silently produces a self-consistent but non-conforming
   chain that only fails when a second implementation verifies it.
2. **`prev_hash` encoded instead of decoded.** `bytes.fromhex(prev_hash)` is 32 bytes;
   `prev_hash.encode()` is 64. Both "work". Only one is the spec.
3. **`sim_time.isoformat()` variability.** A tz-aware datetime appends `+00:00`; a
   nonzero microsecond appends `.000001`; `datetime.date` has no `T`. Assert
   `tzinfo is None and microsecond == 0` in `seal()`, not in a comment.
4. **Sorting `subject_ids` at construction.** The spec sorts *at hash time*. If you also
   sort at construction, the stored order is lost and the Observatory shows subjects in
   the wrong order; if you sort *only* at construction and forget at hash time, two
   callers with different insertion orders produce different hashes.
5. **`seq.to_bytes(8, "big")` on a negative seq.** Ephemerals carry `seq = -1` and will
   raise `OverflowError` if they are ever routed through `seal()`. Divert them before
   sealing, not after.
6. **Floats.** `0.1 + 0.2 != 0.3` and a payload built two different ways then produces two
   hashes. `round_floats(…, 6)` before hashing is mandatory and must recurse into nested
   lists and dicts. Also reject `NaN`/`Infinity` outright — `json.dumps` happily emits
   `NaN`, which is not valid JSON and which Postgres `jsonb` will reject on insert,
   turning a hashing bug into a commit failure three phases later.
7. **`json.dumps(sort_keys=True)` with non-string keys.** It coerces `1` to `"1"` and then
   sorts lexicographically, so `{1: …, 10: …, 2: …}` round-trips in a different order than
   it was built. `assert_json_safe` must reject non-`str` keys.
8. **Validating before rounding.** Order is: json-safe → round → validate → seal. Any other
   order either hashes an unrounded float or validates a value that is not what gets stored.
9. **Per-event `jsonschema.validate`.** At 20k events/tick this dominates PHASE 6.
   Compile once per kind with `jsonschema.validators.validator_for(schema)(schema)` and
   cache. Measure it; the budget is 150 ms for the entire phase.
10. **Partial commit.** If `sink.append` is implemented (by C03) as several statements and
    one fails, half a tick is in the log and the chain is broken forever. C02's contract is
    "one call, all events"; state in the docstring that the sink **must** be transactional
    and add a `MemoryEventSink` fault-injection test that proves rollback.
11. **Reusing `EventLog` across runs.** `last_seq`/`chain_hash` are per-run. Constructing
    one `EventLog` and swapping `run_id` produces a chain that verifies against neither.
    Make `run_id` a constructor-only, read-only attribute.
12. **Kind range gaps.** The 10000–10999 band is now split by 10060–10069. If you write the
    range table as three tuples, an off-by-one leaves 10059 or 10070 unowned and
    `register_kind` rejects a legitimate kind six chunks later. Add a test that every
    integer in every declared band resolves to exactly one `KindRange`.
13. **Storing the LLM prompt in a payload.** `02 §3.3` forbids it; a 3,000-token prompt in
    a payload multiplies the log 10×. `max_payload_bytes` is the mechanical guard — do not
    raise it to make a caller's life easier; make the caller store an `llm_call_id`.
14. **`polis verify` materialising the log.** 150M events per sim-year. `verify_run` must
    stream and hold only the previous hash. Any `list(...)` over `scan()` is a bug.
