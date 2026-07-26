# C03 — Postgres schema, migrations, repositories, partitioning

**M0** · Owner module `polis/store`, `migrations/` · Depends on: **C01, C02** · Blocks: **C04 C05 C08 C23 C24 and every chunk with a projection** · Size **L** (1–2 weeks)

---

## 1. Context

`03-DATA-MODEL.md` is a schema on paper. This chunk makes it a database: the full Alembic
migration set, run-scoped LIST partitioning with tick sub-partitions, the repository layer
every other chunk uses instead of writing SQL, the projection layer that turns events into
queryable state, and the `polis rebuild` path that proves projections are pure functions of
the log. It also owns the two operational guarantees the architecture depends on: only the
engine role may `INSERT` into `events`, and a whole tick's events are written in one
batched `COPY`.

The single hardest property in this chunk is **rebuild ≡ live**. If replaying the log
through the same handlers does not reproduce the live projections byte for byte, some
handler has a side effect that is not in the log, and every research claim built on
projections is unsound.

---

## 2. Required reading

| Source | Why |
|---|---|
| `../docs/03-DATA-MODEL.md` — **all of it** | Binding. Table and column names are normative. §12 is the rebuild contract. |
| `../docs/02-ARCHITECTURE.md` §2.1 (process model / roles), §5 PHASE 6, §7.1, §11 (150 ms COMMIT budget) | Who may write, when, and how fast. |
| `../docs/09-MODEL-ROUTING.md` §0.2, §1.3 columns, §5.2 (cache storage tiers) | The extra `llm_calls` and `completion_cache` columns. |
| `../docs/10-RESEARCH-AND-OBSERVABILITY.md` §5.3 (`polis rebuild`), §0.6 (`metric_manifest`) | Rebuild output contract; the manifest columns on `runs`. |
| **C01** `polis.config` (`Settings`, `StoreSettings`, `canon`) | DSNs, pool sizes, canonical hashing. |
| **C02** `polis.events` (`Event`, `EventSink`, `EventReader`, `EventQuery`, `KIND_REGISTRY`) | The protocols you implement and the registry projections dispatch on. |

---

## 3. Scope — in

1. Alembic migrations 0001–0012 covering **every** table in `03-DATA-MODEL.md`, plus the amendments in §7.2 below.
2. `polis/store/engine.py` — pooling, transaction context, `COPY` helper, health, pgvector/pg_trgm registration.
3. `polis/store/partition.py` — `PartitionManager` with DDL-injection-safe identifier validation.
4. `polis/store/repositories/` — one module per aggregate; full implementations for the M0 set, thin but correct implementations for the rest.
5. `polis/store/projections/` — `Projection` protocol, registry, router, and the M0 projections.
6. `polis/store/rebuild.py` — `polis rebuild`, projection diffing.
7. `polis/store/blobs.py` — `BlobStore` over local FS and S3/MinIO (checkpoints, cache blobs, exports).
8. `polis/store/roles.sql` + migration 0012 — `polis_engine` / `polis_reader` grants including future partitions.
9. `polis/cli/commands/db.py` — `polis db init|upgrade|downgrade|partitions|drop-run|grants`.
10. `polis/cli/commands/rebuild.py` — `polis rebuild`.

## 4. Scope — out

| Not built here | Owner |
|---|---|
| Domain projection handlers for tables whose events do not exist yet (labour, exchange, polity, law, …) | the owning chunk registers its `Projection`; C03 ships the registry, router, base class and one worked example per shape |
| `polis/economy/ledger.py` — the *only* writer of `ledger_accounts` / `ledger_entries` | C11/C14. C03 ships the tables and a `LedgerRepository` guarded so nothing outside `polis.economy.ledger` may call its write methods. |
| Redis pub/sub semantics beyond a bare `EphemeralSink` | C23 |
| Parquet export | C24 |
| Embedding *generation* (you store `vector(768)`, you do not compute it) | C05/C08 |
| `polis replay` | C24 |

---

## 5. Interfaces you provide

```python
# polis/store/engine.py
class Database:
    def __init__(self, pool: AsyncConnectionPool, *, role: Literal["engine", "reader"]) -> None: ...
    @classmethod
    async def open(cls, s: StoreSettings, *, role: Literal["engine", "reader"] = "engine",
                   application_name: str = "polis-engine") -> "Database": ...
    @asynccontextmanager
    def txn(self) -> AsyncIterator[AsyncConnection]: ...
    @asynccontextmanager
    def conn(self) -> AsyncIterator[AsyncConnection]: ...
    async def copy_rows(self, table: str, columns: Sequence[str],
                        rows: Iterable[Sequence[Any]], *,
                        conn: AsyncConnection | None = None) -> int: ...
    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> None: ...
    async def fetch(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]: ...
    async def health(self) -> "HealthReport": ...
    async def close(self) -> None: ...

@dataclass(frozen=True, slots=True)
class HealthReport:
    ok: bool; server_version: int; extensions: frozenset[str]
    alembic_head: str; pool_in_use: int; pool_size: int

class StoreError(PolisError): ...
class MigrationMismatch(StoreError): ...
class WriteForbidden(StoreError): ...
```

```python
# polis/store/partition.py
PARTITIONED_TABLES: Final[tuple[str, ...]] = (
    "events", "memories", "ledger_entries", "trades", "posts", "engagements")
TICK_BUCKET: Final[int] = 100_000
IDENT_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

def validate_ident(name: str) -> str:
    """Sole gate on every identifier interpolated into DDL. Raises StoreError otherwise."""
def run_suffix(run_id: UUID) -> str: ...              # run_id.hex — 32 chars, no dashes
def partition_name(table: str, run_id: UUID, bucket: int | None = None) -> str: ...

class PartitionManager:
    def __init__(self, db: Database) -> None: ...
    async def ensure_run_partitions(self, run_id: UUID) -> list[str]: ...
    async def ensure_tick_partition(self, run_id: UUID, tick: int) -> str | None: ...
    async def drop_run(self, run_id: UUID, *, cascade: bool = True) -> list[str]: ...
    async def list_partitions(self, run_id: UUID | None = None) -> list[str]: ...
    async def grant_reader(self, names: Sequence[str]) -> None: ...
```

```python
# polis/store/repositories/base.py
class Repository:
    def __init__(self, db: Database, run_id: UUID) -> None: ...
    db: Database; run_id: UUID

# polis/store/repositories/events.py
class EventRepository(Repository):                    # implements C02 EventSink + EventReader
    async def append(self, events: Sequence[Event]) -> None:
        """One COPY, one transaction, whole tick. Ensures the tick partition first."""
    async def get(self, run_id: UUID, seq: int) -> Event | None: ...
    def scan(self, q: EventQuery) -> AsyncIterator[Event]: ...      # server-side cursor
    async def count(self, q: EventQuery) -> int: ...
    async def last(self, run_id: UUID) -> Event | None: ...
    async def by_cause(self, run_id: UUID, cause_seq: int) -> list[Event]: ...
    async def max_seq(self) -> int: ...
    async def last_complete_tick(self) -> int:
        """Highest tick with a TICK_COMPLETED (1003). Torn-tick detection for C04 resume."""
    async def delete_after_seq(self, seq: int) -> int: ...           # torn-tick truncation
    async def search_text(self, query: str, *, limit: int = 100) -> list[Event]: ...

# polis/store/repositories/runs.py
@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: UUID; name: str; config_yaml: str; config_hash: str; master_seed: int
    prompt_manifest: Mapping[str, Any]; model_manifest: Mapping[str, Any]
    metric_manifest: Mapping[str, Any]; mechanism_manifest: Mapping[str, Any]
    ablations: Mapping[str, Any]; scale: int
    code_git_sha: str; started_at: datetime; status: str
    parent_run_id: UUID | None = None; sweep_id: UUID | None = None
    tags: tuple[str, ...] = ()

class RunRepository:
    def __init__(self, db: Database) -> None: ...
    async def create(self, rec: RunRecord) -> None: ...
    async def get(self, run_id: UUID) -> RunRecord | None: ...
    async def update_progress(self, run_id: UUID, *, last_tick: int, total_llm_calls: int,
                              total_tokens_in: int, total_tokens_out: int,
                              total_cost_usd: Decimal) -> None: ...
    async def finish(self, run_id: UUID, *, status: str, ended_at: datetime,
                     halt_reason: str | None = None) -> None: ...
    async def list(self, *, sweep_id: UUID | None = None,
                   tags: Sequence[str] = ()) -> list[RunRecord]: ...

# polis/store/repositories/checkpoints.py
class CheckpointRepository(Repository):
    async def put(self, *, tick: int, last_seq: int, chain_hash: str, uri: str,
                  bytes_: int, created_at: datetime) -> None: ...
    async def latest(self, *, at_or_before: int | None = None) -> Mapping[str, Any] | None: ...
    async def list(self) -> list[Mapping[str, Any]]: ...

# polis/store/repositories/llm.py
class LlmCallRepository(Repository):
    async def append(self, rows: Sequence[Mapping[str, Any]]) -> None: ...   # batched COPY
    async def by_actor(self, actor_id: str, *, from_tick: int = 0) -> list[Mapping[str, Any]]: ...
    async def cost_by_purpose(self) -> dict[str, Decimal]: ...
    async def cost_by_agent(self) -> dict[str, Decimal]: ...

class CompletionCacheRepository:
    def __init__(self, db: Database) -> None: ...                            # NOT run-scoped
    async def get(self, key: str) -> Mapping[str, Any] | None: ...
    async def put_many(self, rows: Sequence[Mapping[str, Any]]) -> None: ...  # ON CONFLICT DO NOTHING
    async def bump_hits(self, keys: Sequence[str]) -> None: ...               # out of band
    async def stats(self, keys: Sequence[str] | None = None) -> Mapping[str, Any]: ...

# polis/store/repositories/metrics.py
class MetricRepository(Repository):
    async def write(self, tick: int, values: Mapping[str, float]) -> None: ...   # one COPY
    async def series(self, metric: str, *, from_tick: int = 0,
                     to_tick: int | None = None) -> list[tuple[int, float]]: ...
    async def latest(self, metrics: Sequence[str]) -> dict[str, float]: ...
```

```python
# polis/store/repositories/ledger.py
_LEDGER_WRITER: Final[str] = "polis.economy.ledger"

class LedgerRepository(Repository):
    async def post(self, *, txn_id: UUID, tick: int, legs: Sequence[Mapping[str, Any]],
                   event_seq: int, _caller: str) -> None:
        """Raises WriteForbidden unless _caller == _LEDGER_WRITER. 03 §4 rule 4."""
    async def open_account(self, *, account_id: str, owner_id: str, owner_type: str,
                           account_type: str, opened_tick: int, _caller: str) -> None: ...
    async def balance(self, account_id: str) -> int: ...
    async def balances_by_owner_type(self) -> dict[str, int]: ...
    async def total_balance_cents(self) -> int: ...
    async def imbalance_cents(self) -> int:            # Σ(direction × amount) over all entries
    async def reconcile_balances(self) -> list[tuple[str, int, int]]:
        """(account_id, cached, computed) where they differ. Run every checkpoint (03 §4 rule 3)."""
```

```python
# polis/store/projections/base.py
class Projection(Protocol):
    name: str
    tables: tuple[str, ...]
    handles: frozenset[int]                           # event kinds
    async def apply(self, ctx: "ProjectionContext", event: Event) -> None: ...
    async def truncate(self, ctx: "ProjectionContext") -> None: ...

@dataclass(slots=True)
class ProjectionContext:
    db: Database; run_id: UUID; conn: AsyncConnection
    buffer: dict[str, list[Sequence[Any]]]            # table -> pending COPY rows

PROJECTION_REGISTRY: Final[dict[str, Projection]]
def register_projection(p: Projection) -> None: ...

class ProjectionRouter:
    def __init__(self, db: Database, run_id: UUID,
                 projections: Sequence[Projection] | None = None) -> None: ...
    async def apply_batch(self, events: Sequence[Event],
                          conn: AsyncConnection | None = None) -> None:
        """Dispatch by kind. Events applied in seq order. Unknown kinds ignored (02 §1.2)."""
    async def flush(self, ctx: ProjectionContext) -> None: ...
    async def truncate_all(self) -> list[str]: ...
```

```python
# polis/store/rebuild.py
@dataclass(frozen=True, slots=True)
class TableDiff:
    table: str; only_in_a: int; only_in_b: int; differing: int
    sample: tuple[Mapping[str, Any], ...]

@dataclass(frozen=True, slots=True)
class RebuildReport:
    run_id: UUID; events_replayed: int; from_tick: int; to_tick: int
    rows_written: Mapping[str, int]; duration_s: float; ok: bool

async def rebuild(db: Database, run_id: UUID, *, from_tick: int = 0, batch: int = 5_000,
                  progress: Callable[[int], None] | None = None) -> RebuildReport: ...
async def snapshot_projections(db: Database, run_id: UUID,
                               tables: Sequence[str] | None = None) -> dict[str, str]:
    """table -> sha256 over canonical-ordered rows. The rebuild≡live comparison primitive."""
async def diff_projections(db: Database, run_a: UUID, run_b: UUID, *,
                           tables: Sequence[str] | None = None) -> list[TableDiff]: ...
```

```python
# polis/store/blobs.py
class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str = "application/json") -> str: ...
    async def get(self, key: str) -> bytes | None: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    def uri(self, key: str) -> str: ...

class LocalBlobStore(BlobStore): ...
class S3BlobStore(BlobStore): ...                     # MinIO-compatible
def open_blobs(url: str) -> BlobStore: ...            # file:// | s3://
```

---

## 6. Interfaces you consume

| From | What |
|---|---|
| C01 | `Settings`, `StoreSettings`, `canonical_json`, `sha256_hex`, `PolisError`, `get_logger`, `app` |
| C02 | `Event`, `EventQuery`, `EventSink`, `EventReader`, `EphemeralSink`, `KIND_REGISTRY`, `is_ephemeral` |

Third-party: `psycopg[binary,pool]>=3.2`, `pgvector`, `alembic`, `boto3` (S3 only).

---

## 7. Data model touched

**All of it.** `03-DATA-MODEL.md` §1–§10 verbatim, with the amendments below folded into
the initial migrations (not as follow-up revisions — there is no deployed database yet).

### 7.1 Migration plan

| Revision | Contents |
|---|---|
| `0001_extensions` | `CREATE EXTENSION vector, pg_trgm, pgcrypto`; `polis` schema; `search_path` |
| `0002_core` | `runs`, `events` (+ partitioning), `llm_calls`, `completion_cache`, `checkpoints` |
| `0003_agents` | `agents`, `agent_skills`, `memories` (+ partitioning, hnsw), `beliefs`, `households`, `relationships` |
| `0004_world` | `districts`, `places`, `tiles`, `place_paths` |
| `0005_ledger` | `ledger_accounts`, `ledger_entries` (+ partitioning) |
| `0006_economy` | `firms`, `vacancies`, `job_applications`, `employments`, `skus`, `inventory`, `goods_transactions` |
| `0007_exchange` | `securities`, `holdings`, `orders`, `trades` (+ partitioning), `ohlcv` |
| `0008_banking_ventures` | `banks`, `loans`, `loan_payments`, `startups`, `vc_funds`, `funding_rounds`, `cap_table`, `bankruptcies` |
| `0009_society` | `posts` (+ partitioning), `follows`, `engagements` (+ partitioning), `outlets`, `articles`, `parties`, `elections`, `candidacies`, `votes`, `policies`, `crimes`, `court_cases` |
| `0010_education` | `schools`, `enrolments` |
| `0011_research` | `metrics`, `external_agents`, `scenario_injections`, `sweeps` |
| `0012_roles_grants` | `polis_reader` role, grants, `ALTER DEFAULT PRIVILEGES`, revoke INSERT/UPDATE/DELETE on `events` |

### 7.2 Ratified amendments folded in

| Table | Change |
|---|---|
| `runs` | `+ metric_manifest JSONB NOT NULL DEFAULT '{}'`, `+ mechanism_manifest JSONB NOT NULL DEFAULT '{}'`, `+ ablations JSONB NOT NULL DEFAULT '{}'`, `+ scale INTEGER NOT NULL DEFAULT 0` |
| `llm_calls` | `+ lane TEXT`, `+ cache_mode TEXT`, `+ provider_request_id TEXT`, `+ budget_line TEXT` (09 §0.2) |
| `scenario_injections` | `+ step_id TEXT`, `+ event_seq BIGINT`, `+ scenario_hash TEXT` |
| `sweeps` | `+ preregistration TEXT`, `+ analysis_plan_hash TEXT`, `+ cost_estimate_usd NUMERIC(12,2)` |
| `ledger_accounts.account_type` | domain gains `issuance` → `cash\|deposit\|loan_receivable\|loan_payable\|equity\|reserve\|tax_receivable\|escrow\|issuance` |
| `ledger_entries.reason` | domain gains `write_off`, `escrow` |
| `places.type` | domain gains `shelter`, `prison` |
| `households.tenure` | domain gains `shelter` → `own\|rent\|shelter` |

Enumerated domains are **`CHECK` constraints over `TEXT`**, not Postgres `ENUM` types.
Adding a value to a Postgres `ENUM` cannot be done inside a transaction with other DDL and
is not reversible; a `CHECK` is a one-line migration. Name every constraint explicitly
(`ck_places_type`, …) so later chunks can drop and recreate it.

---

## 8. Event kinds owned

None. C03 is a consumer of events, not a producer. Projection handlers read
`KIND_REGISTRY`; they never register kinds.

---

## 9. Implementation notes

**9.1 Partitioning.** `events` is `PARTITION BY LIST (run_id)`; each run's partition is
itself `PARTITION BY RANGE (tick)` with 100,000-tick buckets:

```sql
CREATE TABLE ev_<run_hex> PARTITION OF events FOR VALUES IN ('<uuid>') PARTITION BY RANGE (tick);
CREATE TABLE ev_<run_hex>_0 PARTITION OF ev_<run_hex> FOR VALUES FROM (0) TO (100000);
```

`memories`, `ledger_entries`, `trades`, `posts`, `engagements` are LIST-by-`run_id` only —
they do not need tick sub-partitions and adding them costs planning time.
`PartitionManager.ensure_tick_partition` is called from `EventRepository.append` and is a
no-op after the first call for a bucket (cache the set of known buckets in memory; a
cross-process race is handled by `CREATE TABLE IF NOT EXISTS` plus catching
`DuplicateTable`).

**9.2 Identifier safety.** Partition names are the only place a runtime value reaches DDL.
Route **every** interpolated identifier through `validate_ident`, then wrap with
`psycopg.sql.Identifier`. Never f-string a name into DDL, even one you just validated —
`sql.Identifier` also handles quoting. Name length: `ev_` + 32 hex + `_` + bucket = 39
chars, comfortably under the 63-byte limit; `validate_ident` enforces 63 anyway.

**9.3 Grants on future partitions.** `GRANT SELECT ON events TO polis_reader` does **not**
propagate to partitions created afterwards. Two mechanisms, both required:
`ALTER DEFAULT PRIVILEGES FOR ROLE polis_engine IN SCHEMA polis GRANT SELECT ON TABLES TO polis_reader;`
in migration 0012, **and** an explicit `grant_reader()` call in
`ensure_run_partitions`/`ensure_tick_partition`. Belt and braces, because the default-
privileges route silently fails if the partition is created by a different role.
`polis_reader` gets `REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES` and no
`CREATE` on the schema (`02 §2.1`).

**9.4 Batched COPY.** `EventRepository.append` uses `psycopg` binary `COPY` in one
transaction. Column order fixed at
`(seq, run_id, tick, sim_time, kind, actor_id, subject_ids, cause_seq, payload, sig, prev_hash, hash)`.
`subject_ids` is `TEXT[]` — binary COPY handles arrays natively; text COPY requires manual
`{}` escaping and is a source of silent corruption on ids containing a comma or brace. Use
binary. `payload` goes through `psycopg.types.json.Jsonb` with `canonical_json` as the
dumps function so what is stored is byte-identical to what was hashed.

**9.5 Connection pooling.** One `AsyncConnectionPool` per process. Engine: `min 2, max 16`,
`application_name=polis-engine`. Reader: separate pool on `reader_dsn` (falls back to `dsn`
with the `polis_reader` role). Register pgvector on every connection via the pool's
`configure` hook — registering once on the pool object is a classic bug: new connections
created after a reset lose the adapter and `vector` columns come back as strings.
`autocommit=False`; `txn()` is the only place a transaction begins.

**9.6 Projections and the router.** A `Projection` declares the kinds it handles and the
tables it owns. `ProjectionRouter.apply_batch` dispatches strictly in `seq` order and
buffers rows per table, flushing with `COPY` (inserts) or `execute_many` (updates) at the
end of the batch. Rules every projection must obey, enforced by review and by the rebuild
test:

- A handler reads **only** the event and the projection tables. Never wall-clock, never
  `random`, never a live in-memory object from the engine.
- A handler is **idempotent under replay from a truncated table**, not idempotent in
  general. Rebuild truncates first; handlers may assume an empty starting state at
  `from_tick=0`.
- Two projections may not write the same table. `register_projection` asserts disjoint
  `tables`.

**9.7 `polis rebuild`.** `polis rebuild --run <id> [--from-tick N] [--check]`.
Truncates the tables of every registered projection (never `events`, `runs`, `llm_calls`,
`checkpoints` — `03 §0`), then streams the log in `seq` order in batches of 5,000 through
`ProjectionRouter`, in one transaction per batch. `--check` snapshots projections first,
rebuilds into a temporary schema, and reports `TableDiff`s. `snapshot_projections` hashes
each table as `sha256(canonical_json(sorted rows by primary key))` — the sort is what makes
the comparison order-independent, and the PK is always available because every table has a
composite PK starting with `run_id`.

**9.8 Ledger write guard.** `03 §4` rule 4 says no code outside `ledger.py` may write the
ledger tables. `import-linter` cannot express "may call this method", so
`LedgerRepository` write methods take an explicit `_caller: str` and raise
`WriteForbidden` unless it equals `polis.economy.ledger`. Crude, cheap, and it catches the
case that matters: a well-meaning institution posting a one-sided entry.
`imbalance_cents()` is what `INV-LEDGER` calls; `total_balance_cents()` and
`balances_by_owner_type()` are what `INV-MONEY` calls.

**9.9 pgvector and FTS.** `memories.embedding vector(768)` with an `hnsw` index using
`vector_cosine_ops`. Build the index **per partition**, after bulk load where possible —
an hnsw index on an empty partitioned parent is created on each child automatically but
`CONCURRENTLY` is not permitted on a partitioned table, so index creation blocks. Set
`maintenance_work_mem` high in `docker-compose`. `pg_trgm` backs fuzzy id/name lookup in
the Observatory; FTS indexes are the `to_tsvector('english', …)` GIN indexes named in
`03 §1.2`, §2.3, §8.

**9.10 What is *not* a projection.** `events`, `runs`, `llm_calls`, `completion_cache`,
`checkpoints` (`03 §0`). `rebuild` must refuse to truncate them, and a projection that
declares one of them in `tables` fails registration. `completion_cache` is
**cross-run** — it has no `run_id` and is never truncated by a run-scoped operation.

**9.11 Storage failure policy.** `02 §10`: retry with backoff, then HALT. `Database.txn`
retries on `psycopg.errors.SerializationFailure`, `DeadlockDetected`, and connection loss,
with `store.retry_attempts` attempts and exponential backoff; every other error propagates.
The retry must be safe because the whole tick is one transaction — a retried tick re-COPYs
the identical rows, and the `(run_id, seq)` PK makes a double-commit a hard conflict rather
than a duplicate.

---

## 10. Configuration keys

```yaml
store:
  dsn: "postgresql://polis_engine:polis@127.0.0.1:5432/polis"
  reader_dsn: null                 # defaults to dsn with the polis_reader role
  pool_min: 2
  pool_max: 16
  statement_timeout_ms: 30000
  retry_attempts: 5
  retry_base_ms: 50
  copy_batch_rows: 50000
  tick_bucket: 100000              # RANGE sub-partition width for events
  redis_url: "redis://127.0.0.1:6379/0"
  blob_url: "file://./.blobs"      # or s3://polis/... for MinIO
  require_migration_head: true     # engine refuses to start on a stale schema
  hnsw:
    m: 16
    ef_construction: 64
    ef_search: 40
```

---

## 11. Acceptance criteria

- [ ] `polis db init` on an empty Postgres 17 produces every table, index, constraint and role in `03-DATA-MODEL.md` plus §7.2's amendments; `alembic downgrade base` removes them cleanly.
- [ ] `alembic upgrade head` twice is a no-op; `alembic check` reports no pending autogenerate diff against `polis/store/metadata.py`.
- [ ] `Database.open` raises `MigrationMismatch` when `alembic_version` is not head and `require_migration_head` is true.
- [ ] `validate_ident` rejects `"; DROP TABLE events; --"`, names > 63 bytes, uppercase, and leading digits; a fuzz test over 10,000 random strings never produces an accepted name that is not `^[a-z_][a-z0-9_]{0,62}$`.
- [ ] `ensure_run_partitions` is idempotent and safe under concurrent callers; `ensure_tick_partition(run, 100_001)` creates bucket 1 and not bucket 0 twice.
- [ ] `drop_run` removes every partition for that run across all six partitioned tables and completes in < 1 s on a 1M-event run.
- [ ] Writing 20,000 events for one tick via `EventRepository.append` completes in < 150 ms locally and issues exactly one `COPY` and one transaction.
- [ ] Events round-trip: `append` then `scan` returns `Event`s that `verify_event` accepts and whose `payload` is byte-identical to the input under `canonical_json`.
- [ ] `subject_ids` containing `,`, `{`, `}`, `"` and a non-ASCII character round-trips exactly.
- [ ] `polis_reader` can `SELECT` from `events` including a partition created **after** the grants ran, and its `INSERT` fails with a permission error.
- [ ] `LedgerRepository.post` from any `_caller` other than `polis.economy.ledger` raises `WriteForbidden`.
- [ ] `imbalance_cents()` is 0 after a balanced posting and non-zero after an unbalanced one.
- [ ] `reconcile_balances()` returns empty when incremental balances match the entry sums.
- [ ] `rebuild` after a 500-tick live run reproduces every projection table with zero diffs (`03 §12`).
- [ ] `rebuild --from-tick N` produces the same terminal state as a full rebuild.
- [ ] `rebuild` refuses to truncate `events`, `runs`, `llm_calls`, `completion_cache`, `checkpoints`.
- [ ] `memories` accepts a 768-d vector, rejects a 767-d one, and a cosine ANN query returns deterministically ordered results for a fixed corpus.
- [ ] `BlobStore` round-trips a 10 MB blob on both `file://` and `s3://` (MinIO) and `uri()` is stable.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/store/test_ident_validation.py` | Injection strings, length, case, unicode; property test over random strings. |
| `tests/unit/store/test_partition_names.py` | `partition_name` determinism, length bound, bucket arithmetic at 0 / 99_999 / 100_000. |
| `tests/integration/store/test_migrations.py` | `upgrade head` → `downgrade base` → `upgrade head`; every table/column/constraint in `03-DATA-MODEL.md` exists with the right type, checked against a checked-in expected-schema JSON generated from `information_schema`. |
| `tests/integration/store/test_amendments.py` | The eight §7.2 amendments: each new column exists with the right type; each widened `CHECK` accepts the new value and still rejects garbage. |
| `tests/integration/store/test_partitioning.py` | Run partition + tick sub-partition creation, idempotence, concurrency (two tasks racing `ensure_tick_partition`), `drop_run` completeness and speed. |
| `tests/integration/store/test_event_repository.py` | COPY round-trip incl. hostile `subject_ids`, NULL `actor_id`/`cause_seq`, non-ASCII payload; `scan` filters by kind/actor/subject/tick/seq; `last_complete_tick`; `delete_after_seq`. |
| `tests/integration/store/test_roles.py` | `polis_reader` SELECT allowed on pre- and post-grant partitions; INSERT/UPDATE/DELETE/TRUNCATE denied; no `CREATE` on schema. |
| `tests/integration/store/test_ledger_guard.py` | `WriteForbidden` for wrong caller; balanced/unbalanced `imbalance_cents`; `reconcile_balances` detects a deliberately drifted cached balance. |
| `tests/integration/store/test_projection_router.py` | Dispatch by kind in seq order; unknown kinds ignored; disjoint-table registration assertion; buffered flush issues one COPY per table. |
| `tests/determinism/test_projection_rebuild.py` | **The headline test (`03 §12`).** 500 ticks live with `StubProvider`, snapshot every projection, `rebuild`, diff — zero differences on every table. Also asserts `snapshot_projections` is insensitive to physical row order. |
| `tests/integration/store/test_pgvector.py` | Dimension enforcement; hnsw index present per partition; deterministic ANN ordering for a fixed corpus and fixed `ef_search`. |
| `tests/integration/store/test_blobs.py` | `file://` and `s3://` parity; overwrite, missing key, large blob, `uri()` stability. |
| `tests/integration/store/test_copy_throughput.py` | 20,000-event tick under 150 ms; marked `slow`, skipped when `POLIS_SKIP_PERF` is set. |

Integration tests use a `testcontainers`-managed or compose-provided Postgres 17 +
pgvector; every test creates its own `run_id` and drops its partitions in teardown.

---

## 13. Definition of done

`chunks/README.md §5` items 1–9, plus: `tests/determinism/test_projection_rebuild.py`
passes; the expected-schema JSON is checked in and CI fails when the migrations drift from
it; `polis db partitions --run <id>` lists what was created; every deviation from
`03-DATA-MODEL.md` (there should be exactly the eight in §7.2) is listed in the handback.

---

## 14. Traps

1. **`GRANT` does not reach future partitions.** The single most likely production
   surprise: everything works until the tick counter crosses 100,000 and the Observatory
   goes blank because the new sub-partition has no `SELECT` grant. Test it explicitly by
   creating a partition *after* running the grants.
2. **A unique index on a partitioned table must include every partition key column.**
   `events` PK is `(run_id, seq)` and `run_id` is the LIST key — fine. But `ev_cause`,
   `llm_cache` and any index you are tempted to make `UNIQUE` on a non-key column will be
   rejected. Read the error, do not "fix" it by dropping the partitioning.
3. **`BIGSERIAL` in partitioned tables.** `memories.memory_id`, `ledger_entries.entry_id`,
   `trades.trade_id`, `engagements.engagement_id` are `BIGSERIAL` in `03`. Use
   `GENERATED BY DEFAULT AS IDENTITY` on the partitioned parent; the sequence is shared
   across partitions, which is what you want. Declaring the serial on each partition gives
   you colliding ids across runs.
4. **Text-format COPY of `TEXT[]`.** Array literals need `{a,b}` with quoting and escaping
   for commas, braces, backslashes and quotes. Agent ids will not contain those today, but
   `posts.text` and place names will. Use **binary** COPY and stop thinking about it.
5. **`jsonb` reordering.** Postgres `jsonb` does not preserve key order or duplicate keys.
   Never re-hash a payload read back from the database and compare it to `events.hash` —
   hash the *canonical* form, which `canonical_json` produces deterministically from any
   key order. But **do** be aware that `jsonb` also normalises numeric representation:
   `1.0` may come back as `1.0` or `1`. This is why floats must be rounded and why the
   hash is computed before the write, never after the read.
6. **`NaN` / `Infinity` in a payload.** `json.dumps` emits them; `jsonb` rejects them. The
   failure lands in `COPY` at PHASE 6 with a useless message. C02's `assert_json_safe`
   catches it; do not disable it for speed.
7. **Registering pgvector once on the pool.** Adapters are per-connection. Use the pool's
   `configure=` callback. Symptom of getting it wrong: vectors work for the first N
   queries and then start coming back as `str`.
8. **Retrying a transaction that is not idempotent.** The COPY retry is safe only because
   the whole tick is one transaction and `(run_id, seq)` is a PK. If anyone later splits
   `append` into two statements, the retry silently duplicates half a tick. Assert
   single-statement in a test, not in a comment.
9. **`rebuild` truncating `events`.** One typo turns a diagnostic command into total data
   loss on the only non-derivable table in the system. Hard-code the deny list, assert it
   in a test, and make `truncate_all` return the list it truncated so the CLI can print it.
10. **Rebuild ≠ live because a handler read live state.** The characteristic bug: a
    handler resolves `agents.wealth_cents` "for convenience" from an in-memory object that
    the engine had but the rebuild does not. The diff will name a table but not the cause.
    Structure `ProjectionContext` so a handler has *no* access to anything except the
    event and a DB connection, and it becomes impossible.
11. **`ledger_accounts.balance_cents` drifting from the entry sum.** It is a cache
    (`03 §4` rule 3). Reconcile every checkpoint, not "eventually". A drift of one cent
    discovered at sim-year three is unattributable.
12. **hnsw index build time.** Building hnsw on 8M memories with default
    `maintenance_work_mem` takes hours and blocks. Create it early on an empty table, tune
    `m`/`ef_construction` from config, and never rebuild it inside a tick.
13. **Statement timeout killing a long `scan`.** `statement_timeout_ms: 30000` is right for
    the engine and wrong for `polis verify` over 150M events. Use a server-side named
    cursor with the timeout disabled on that connection only, and say so in the code.
14. **Postgres `ENUM` types.** Adding a value later requires `ALTER TYPE ... ADD VALUE`,
    which cannot run in the same transaction as other DDL and cannot be rolled back. The
    §7.2 amendments already prove the domains change. Use `CHECK` constraints.
15. **`runs.parent_run_id REFERENCES runs(run_id)` plus `events` FK to `runs`.** Do **not**
    add a foreign key from `events` to `runs`: `03 §1.2` requires `DROP TABLE ev_<run>` to
    delete a run instantly, and an FK turns that into a validated cascade.
