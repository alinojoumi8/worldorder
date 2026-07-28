# POLIS — Data Model

**Version:** 1.0
**Status:** Normative. Table and column names here are binding.
**Database:** PostgreSQL 17 + `pgvector` + `pg_trgm`. Migrations via Alembic in `migrations/`.

---

## 0. Conventions

| Rule | Detail |
|---|---|
| **Money** | Always `BIGINT`, always minor units (cents), always suffixed `_cents`. Never `NUMERIC`, never `FLOAT`. |
| **Prices on the exchange** | `BIGINT` in price ticks (1 tick = 1 cent by default). |
| **IDs** | Entity IDs are `TEXT` with a typed prefix: `ag_`, `fm_`, `bk_`, `hh_`, `pl_`, `st_`, `pt_`, `ol_`. External agents use the collision-resistant canonical identity `ag_<full_pubkey_hex>`, with the exact 32-byte Ed25519 public key encoded as 64 lowercase hexadecimal characters and no `0x` prefix. The separately verified public-key field must match it. A UI may shorten the ID for display, but persistence, routing, signatures, and authorization always use the full identity. Rationale: typed prefixes prevent entity-class confusion while the complete authenticated key avoids identity collisions. |
| **Time** | `tick BIGINT` is authoritative. `sim_time TIMESTAMP` is a convenience projection. Wall-clock time appears only in `llm_calls`, `runs`, and operational gateway metadata (`external_sessions`, `external_conformance_tokens`, `external_latency`); it never enters simulated state. |
| **Run scoping** | Every run-owned table carries `run_id UUID NOT NULL`; queries against them are run-scoped. Shared `completion_cache` entries and pre-admission `external_conformance_tokens` are explicit exceptions. There is no cross-run simulated state. |
| **Projections** | Simulation-state tables are **projections** rebuildable from the log. Non-replayable exceptions are `events`, `llm_calls`, `runs`, `checkpoints`, `completion_cache`, and the operational gateway tables `external_sessions`, `external_nonces`, `external_conformance_tokens`, and `external_latency`. Never store anything non-derived in a projection. |
| **Soft delete** | Entities are never deleted. `dissolved_at_tick`, `died_at_tick`, `closed_at_tick` mark the end of life. History is the product. |
| **JSONB** | Used for open-ended payloads (event payloads, trait vectors, LLM params). Never for anything that is queried in a hot path or joined on. |

---

## 1. Core: runs, events, LLM calls

### 1.1 `runs`

```sql
CREATE TABLE runs (
    run_id            UUID PRIMARY KEY,
    name              TEXT NOT NULL,
    config_yaml       TEXT NOT NULL,
    config_hash       TEXT NOT NULL,          -- sha256 of canonicalised config
    master_seed       BIGINT NOT NULL,
    prompt_manifest   JSONB NOT NULL,         -- {template_name: sha256}
    model_manifest    JSONB NOT NULL,         -- {purpose: {provider, model, version}}
    code_git_sha      TEXT NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    ended_at          TIMESTAMPTZ,
    last_tick         BIGINT NOT NULL DEFAULT 0,
    status            TEXT NOT NULL,          -- running|completed|halted|failed
    halt_reason       TEXT,
    total_llm_calls   BIGINT NOT NULL DEFAULT 0,
    total_tokens_in   BIGINT NOT NULL DEFAULT 0,
    total_tokens_out  BIGINT NOT NULL DEFAULT 0,
    total_cost_usd    NUMERIC(12,6) NOT NULL DEFAULT 0,
    parent_run_id     UUID REFERENCES runs(run_id),   -- for sweeps and re-runs
    sweep_id          UUID,
    tags              TEXT[] NOT NULL DEFAULT '{}',
    metric_manifest   JSONB NOT NULL DEFAULT '{}',    -- {metric_id: definition_hash}
    mechanism_manifest JSONB NOT NULL DEFAULT '{}',   -- {mechanism_id: entails_hash}
    completion_cache_manifest JSONB NOT NULL DEFAULT '{}',
                                                    -- {cache_key: completion_content_hash}
    completion_cache_manifest_hash CHAR(64) NOT NULL,
                                                    -- sha256(canonical manifest)
    ablations         JSONB NOT NULL DEFAULT '{}',    -- active ablation flags
    scale             INTEGER                         -- initial agent count, for the
                                                      --   finite-size ladder (threat T7)
);
```

`(config_hash, prompt_manifest, model_manifest, code_git_sha, master_seed,
completion_cache_manifest_hash)` is the **reproducibility tuple**. A result is only
comparable to another result with the same tuple (mitigates T4, T5). At launch, the manifest
maps every compatible cache entry already available in the run namespace to the canonical
hash of its persisted completion record (rendered prompt hash, provider response, and cost);
it is empty only for an explicitly cold `live` cache. The terminal manifest covers that
launch snapshot plus completions used or produced by the run. It is not bounded by the
in-process LRU. Its hash therefore identifies the run-specific cache snapshot rather than
a mutable shared cache.

### 1.2 `events` — the log

```sql
CREATE TABLE events (
    seq          BIGINT      NOT NULL,
    run_id       UUID        NOT NULL,
    tick         BIGINT      NOT NULL,
    sim_time     TIMESTAMP   NOT NULL,
    kind         INTEGER     NOT NULL,
    actor_id     TEXT,
    subject_ids  TEXT[]      NOT NULL DEFAULT '{}',
    cause_seq    BIGINT,
    payload      JSONB       NOT NULL,
    sig          TEXT,
    prev_hash    CHAR(64)    NOT NULL,
    hash         CHAR(64)    NOT NULL,
    PRIMARY KEY (run_id, seq)
) PARTITION BY LIST (run_id);

-- Per run, subpartitioned by tick bucket. Created by polis.store.partition.
-- CREATE TABLE events_<run> PARTITION OF events FOR VALUES IN ('<uuid>')
--     PARTITION BY RANGE (tick);
-- CREATE TABLE events_<run>_000 PARTITION OF events_<run> FOR VALUES FROM (0) TO (100000);

CREATE INDEX ev_kind_tick   ON events (run_id, kind, tick);
CREATE INDEX ev_actor_tick  ON events (run_id, actor_id, tick) WHERE actor_id IS NOT NULL;
CREATE INDEX ev_subjects    ON events USING GIN (subject_ids);
CREATE INDEX ev_cause       ON events (run_id, cause_seq) WHERE cause_seq IS NOT NULL;
CREATE INDEX ev_payload     ON events USING GIN (payload jsonb_path_ops);
CREATE INDEX ev_fts         ON events USING GIN (to_tsvector('english', payload->>'text'))
    WHERE payload ? 'text';
```

Notes:
- `LIST (run_id)` partitioning means `DROP TABLE events_<run>` deletes a run instantly.
- `ev_cause` supports backward causal walks — the Observatory's "why did this happen?"
  query. `ev_subjects` supports "everything that ever happened to agent X".
- Writes are batched: one `COPY` per tick from PHASE 6. Never row-by-row inserts.
- Only the engine's DB role may `INSERT`. `polis_reader` has `SELECT` only.

### 1.3 `llm_calls`

```sql
CREATE TABLE llm_calls (
    call_id          UUID PRIMARY KEY,
    run_id           UUID NOT NULL,
    tick             BIGINT NOT NULL,
    actor_id         TEXT,
    purpose          TEXT NOT NULL,        -- DELIBERATE|REFLECT|IMPORTANCE|POST_WRITE|...
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    model_version    TEXT,
    prompt_template  TEXT NOT NULL,
    prompt_hash      CHAR(64) NOT NULL,
    cache_key        CHAR(64) NOT NULL,
    cache_hit        BOOLEAN NOT NULL,
    call_seed        BIGINT NOT NULL,
    sampling_params  JSONB NOT NULL,
    prompt_text      TEXT,                 -- retained iff runs.tags @> '{keep_prompts}'
    response_text    TEXT NOT NULL,
    parsed_ok        BOOLEAN NOT NULL,
    repair_attempts  SMALLINT NOT NULL DEFAULT 0,
    tokens_in        INTEGER NOT NULL,
    tokens_out       INTEGER NOT NULL,
    cost_usd         NUMERIC(12,8) NOT NULL,
    latency_ms       INTEGER NOT NULL,
    error            TEXT,
    sim_aware_flag   BOOLEAN NOT NULL DEFAULT FALSE,  -- T3 detector
    lane             TEXT NOT NULL,                   -- provider concurrency lane
    cache_mode       TEXT NOT NULL                    -- live|replay|hybrid at call time
);
CREATE INDEX llm_run_tick ON llm_calls (run_id, tick);
CREATE INDEX llm_actor    ON llm_calls (run_id, actor_id, tick);
CREATE INDEX llm_cache    ON llm_calls (cache_key);
CREATE INDEX llm_simaware ON llm_calls (run_id) WHERE sim_aware_flag;
```

`prompt_text` is off by default because it multiplies storage ~10×. The prompt is
reconstructible from `prompt_template` + the template hash in `runs.prompt_manifest` + the
event state at that tick, so nothing is lost.

`sim_aware_flag` is set by a cheap classifier over the response (regex + small-model check)
looking for the agent referring to itself as an AI, a language model, or being in a
simulation. Its rate per run is a reported statistic (threat T3).

### 1.4 `completion_cache`

Content-addressed, shared across runs. Lives in the object store; this table is the index.

```sql
CREATE TABLE completion_cache (
    cache_key      CHAR(64) PRIMARY KEY,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    model_version  TEXT,
    response_blob  TEXT NOT NULL,       -- or object-store URI if > 64 KB
    tokens_in      INTEGER NOT NULL,
    tokens_out     INTEGER NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL,
    hit_count      BIGINT NOT NULL DEFAULT 0
);
```

### 1.5 `checkpoints`

```sql
CREATE TABLE checkpoints (
    run_id     UUID NOT NULL,
    tick       BIGINT NOT NULL,
    last_seq   BIGINT NOT NULL,
    chain_hash CHAR(64) NOT NULL,
    uri        TEXT NOT NULL,           -- object-store location of the state blob
    bytes      BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, tick)
);
```

---

## 2. Agents

### 2.1 `agents`

```sql
CREATE TABLE agents (
    run_id            UUID NOT NULL,
    agent_id          TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    kind              TEXT NOT NULL,       -- native|external
    pubkey            TEXT,                -- ed25519 hex; NOT NULL iff kind='external'

    born_at_tick      BIGINT NOT NULL,
    died_at_tick      BIGINT,
    death_cause       TEXT,
    age_years         NUMERIC(6,3) NOT NULL,

    household_id      TEXT,
    mother_id         TEXT,
    father_id         TEXT,
    generation        INTEGER NOT NULL DEFAULT 0,

    traits            JSONB NOT NULL,      -- {openness,conscientiousness,extraversion,
                                           --  agreeableness,neuroticism,risk_tolerance,
                                           --  time_preference,altruism,ambition,honesty}
    needs             JSONB NOT NULL,      -- {hunger,energy,health,social,esteem,security}
    health            NUMERIC(5,4) NOT NULL,

    home_place_id     TEXT,
    current_place_id  TEXT,
    pos_x             SMALLINT,
    pos_y             SMALLINT,
    dest_place_id     TEXT,
    path_cursor       INTEGER,

    education_level   TEXT NOT NULL,       -- none|primary|secondary|tertiary|graduate
    employment_status TEXT NOT NULL,       -- child|student|employed|unemployed|
                                           -- self_employed|retired|dead
    employer_id       TEXT,
    occupation        TEXT,

    ledger_account_id TEXT NOT NULL,       -- FK to ledger_accounts
    wealth_cents      BIGINT NOT NULL DEFAULT 0,   -- denormalised; ledger is authoritative

    reputation        NUMERIC(5,4) NOT NULL DEFAULT 0.5,
    criminal_record   SMALLINT NOT NULL DEFAULT 0,

    reflex_profile    JSONB NOT NULL,      -- learned/assigned weights for the reflex policy
    goals             JSONB NOT NULL DEFAULT '[]',  -- current goal stack, LLM-maintained

    PRIMARY KEY (run_id, agent_id)
);
CREATE INDEX ag_alive    ON agents (run_id) WHERE died_at_tick IS NULL;
CREATE INDEX ag_place    ON agents (run_id, current_place_id);
CREATE INDEX ag_employer ON agents (run_id, employer_id) WHERE employer_id IS NOT NULL;
CREATE INDEX ag_household ON agents (run_id, household_id);
```

> `wealth_cents` is a **denormalised cache** of the ledger balance, refreshed each tick.
> `INV-MONEY` compares it against the ledger; a mismatch is a bug, not a rounding issue.

### 2.2 `agent_skills`

```sql
CREATE TABLE agent_skills (
    run_id      UUID NOT NULL,
    agent_id    TEXT NOT NULL,
    skill       TEXT NOT NULL,        -- see 04-AGENT-SPEC §3 for the closed skill list
    level       NUMERIC(6,4) NOT NULL,   -- 0..1
    last_used_tick BIGINT,
    PRIMARY KEY (run_id, agent_id, skill)
);
```

### 2.3 `memories`

```sql
CREATE TABLE memories (
    run_id        UUID NOT NULL,
    memory_id     BIGSERIAL,
    agent_id      TEXT NOT NULL,
    tick          BIGINT NOT NULL,
    type          TEXT NOT NULL,      -- observation|reflection|plan|semantic
    text          TEXT NOT NULL,
    importance    NUMERIC(4,3) NOT NULL,     -- 0..1
    embedding     vector(768),
    source_event_seq BIGINT,
    parent_memory_ids BIGINT[] NOT NULL DEFAULT '{}',   -- reflection provenance
    subject_ids   TEXT[] NOT NULL DEFAULT '{}',
    last_accessed_tick BIGINT NOT NULL,
    access_count  INTEGER NOT NULL DEFAULT 0,
    archived      BOOLEAN NOT NULL DEFAULT FALSE,   -- set on agent death
    PRIMARY KEY (run_id, memory_id)
) PARTITION BY LIST (run_id);

CREATE INDEX mem_agent_tick ON memories (run_id, agent_id, tick DESC);
CREATE INDEX mem_type       ON memories (run_id, agent_id, type);
CREATE INDEX mem_vec        ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX mem_subjects   ON memories USING GIN (subject_ids);
CREATE INDEX mem_fts        ON memories USING GIN (to_tsvector('english', text));
```

`parent_memory_ids` makes reflection provenance explicit — you can always trace an abstract
belief ("the market is rigged") back to the concrete observations that produced it. This is
required for G6 (legibility).

### 2.4 `beliefs`

Structured, queryable beliefs distinct from free-text memory. This is what politics and
misinformation research operates on.

```sql
CREATE TABLE beliefs (
    run_id      UUID NOT NULL,
    agent_id    TEXT NOT NULL,
    proposition TEXT NOT NULL,     -- closed vocabulary, e.g. 'tax.rate.should_rise',
                                   -- 'trust.outlet.ol_herald', 'fact.acme_is_fraudulent'
    value       NUMERIC(5,4) NOT NULL,   -- -1..1 for stances, 0..1 for factual credences
    confidence  NUMERIC(5,4) NOT NULL,
    updated_tick BIGINT NOT NULL,
    source      TEXT NOT NULL,     -- inherited|experience|social|media|reflection
    source_ref  TEXT,
    PRIMARY KEY (run_id, agent_id, proposition)
);
CREATE INDEX bel_prop ON beliefs (run_id, proposition);
```

### 2.5 `households`

```sql
CREATE TABLE households (
    run_id       UUID NOT NULL,
    household_id TEXT NOT NULL,
    formed_at_tick BIGINT NOT NULL,
    dissolved_at_tick BIGINT,
    home_place_id TEXT NOT NULL,
    member_ids   TEXT[] NOT NULL,
    head_agent_id TEXT,
    tenure       TEXT NOT NULL,       -- own|rent|shelter
    rent_cents   BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, household_id)
);
```

### 2.6 `relationships` — the social graph

```sql
CREATE TABLE relationships (
    run_id     UUID NOT NULL,
    a_id       TEXT NOT NULL,
    b_id       TEXT NOT NULL,        -- stored with a_id < b_id for symmetric types
    type       TEXT NOT NULL,        -- kin|partner|friend|colleague|rival|
                                     -- creditor|acquaintance
    strength   NUMERIC(5,4) NOT NULL,
    valence    NUMERIC(5,4) NOT NULL,   -- -1 hostile .. +1 warm
    trust      NUMERIC(5,4) NOT NULL,
    formed_tick BIGINT NOT NULL,
    ended_tick  BIGINT,
    last_interaction_tick BIGINT,
    PRIMARY KEY (run_id, a_id, b_id, type)
);
CREATE INDEX rel_a ON relationships (run_id, a_id) WHERE ended_tick IS NULL;
CREATE INDEX rel_b ON relationships (run_id, b_id) WHERE ended_tick IS NULL;
```

---

## 3. World

### 3.1 `districts`, `places`, `tiles`

```sql
CREATE TABLE districts (
    run_id       UUID NOT NULL,
    district_id  TEXT NOT NULL,
    name         TEXT NOT NULL,
    bbox         INT4RANGE[] NOT NULL,       -- [x_range, y_range]
    land_value_cents BIGINT NOT NULL,
    school_quality NUMERIC(4,3) NOT NULL,
    crime_rate     NUMERIC(6,5) NOT NULL DEFAULT 0,
    amenity_score  NUMERIC(4,3) NOT NULL,
    PRIMARY KEY (run_id, district_id)
);

CREATE TABLE places (
    run_id      UUID NOT NULL,
    place_id    TEXT NOT NULL,
    district_id TEXT NOT NULL,
    type        TEXT NOT NULL,     -- home|office|factory|shop|school|university|bank|
                                   -- exchange|town_hall|courthouse|police|prison|hospital|
                                   -- park|bar|newsroom|studio|shelter
    name        TEXT NOT NULL,
    x           SMALLINT NOT NULL,
    y           SMALLINT NOT NULL,
    capacity    INTEGER NOT NULL,
    owner_id    TEXT,              -- agent or firm
    rent_cents  BIGINT NOT NULL DEFAULT 0,
    open_hours  INT4RANGE,
    PRIMARY KEY (run_id, place_id)
);
CREATE INDEX pl_type     ON places (run_id, type);
CREATE INDEX pl_district ON places (run_id, district_id);

CREATE TABLE tiles (
    run_id   UUID NOT NULL,
    x        SMALLINT NOT NULL,
    y        SMALLINT NOT NULL,
    terrain  SMALLINT NOT NULL,    -- 0 walkable, 1 blocked, 2 road, 3 water
    place_id TEXT,
    PRIMARY KEY (run_id, x, y)
);
```

`tiles` is loaded into a NumPy array at startup and never read row-wise during a tick.
It exists so the grid is reproducible and inspectable, not as a hot-path store.

### 3.2 `place_paths` — precomputed routing

```sql
CREATE TABLE place_paths (
    run_id   UUID NOT NULL,
    from_place_id TEXT NOT NULL,
    to_place_id   TEXT NOT NULL,
    distance      INTEGER NOT NULL,      -- tiles
    travel_ticks  SMALLINT NOT NULL,
    path          SMALLINT[] NOT NULL,   -- flattened [x0,y0,x1,y1,...] for rendering
    PRIMARY KEY (run_id, from_place_id, to_place_id)
);
```

Computed once at world generation (all-pairs over ~400 places ≈ 160k rows). This is
decision D4: movement becomes an O(1) lookup instead of per-tick A*.

---

## 4. The ledger — money

**This is the most important table in the system.** `INV-MONEY` and `INV-LEDGER` are
computed from it, and V2 (accounting closure) is the primary correctness gate for M2.

### 4.1 `ledger_accounts`

```sql
CREATE TABLE ledger_accounts (
    run_id     UUID NOT NULL,
    account_id TEXT NOT NULL,
    owner_id   TEXT NOT NULL,        -- agent|firm|bank|government|market|external
    owner_type TEXT NOT NULL,
    account_type TEXT NOT NULL,      -- cash|deposit|loan_receivable|loan_payable|
                                     -- equity|reserve|tax_receivable|escrow|issuance
                                     -- `issuance` is the central bank's sole money-creation
                                     -- account. It is the ONLY account permitted to run an
                                     -- unbounded contra balance; see §4.2 rule 2.
    currency   TEXT NOT NULL DEFAULT 'POL',
    balance_cents BIGINT NOT NULL DEFAULT 0,
    opened_tick BIGINT NOT NULL,
    closed_tick BIGINT,
    PRIMARY KEY (run_id, account_id)
);
CREATE INDEX la_owner ON ledger_accounts (run_id, owner_id);
```

### 4.2 `ledger_entries` — double entry

```sql
CREATE TABLE ledger_entries (
    run_id      UUID NOT NULL,
    entry_id    BIGSERIAL,
    txn_id      UUID NOT NULL,        -- groups the legs of one transaction
    tick        BIGINT NOT NULL,
    account_id  TEXT NOT NULL,
    direction   SMALLINT NOT NULL,    -- +1 debit, -1 credit
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    reason      TEXT NOT NULL,        -- wage|purchase|trade|loan|interest|tax|rent|
                                      -- dividend|inheritance|fine|transfer|issuance|
                                      -- write_off|escrow|tuition|legal_fee|campaign|
                                      -- ad_revenue|welfare|damages
    event_seq   BIGINT NOT NULL,
    PRIMARY KEY (run_id, entry_id)
) PARTITION BY LIST (run_id);
CREATE INDEX le_txn     ON ledger_entries (run_id, txn_id);
CREATE INDEX le_account ON ledger_entries (run_id, account_id, tick);
```

**Rules, enforced in `polis/economy/ledger.py` and by `INV-LEDGER`:**

1. Money moves **only** through `post_transaction(legs)`, which asserts
   `sum(direction * amount) == 0` before writing.
2. There is exactly one account type that may be created from nothing: the central bank's
   `issuance` account, and only via an explicit `MONEY_ISSUED` event.
3. `balance_cents` on `ledger_accounts` is maintained incrementally and reconciled against
   the sum of entries every checkpoint.
4. No code outside `ledger.py` may write to either table. Enforced by `import-linter`.

If you take one thing from this document: **every economic feature must be expressed as
balanced ledger legs.** Wages, trades, dividends, taxes, inheritance, fines, and bankruptcy
write-offs all go through the same function. This is the only realistic way to make V2 hold.

---

## 5. Economy — labour, firms, goods

```sql
CREATE TABLE firms (
    run_id        UUID NOT NULL,
    firm_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    founded_tick  BIGINT NOT NULL,
    dissolved_tick BIGINT,
    sector        TEXT NOT NULL,
    place_id      TEXT NOT NULL,
    founder_id    TEXT,
    ledger_account_id TEXT NOT NULL,
    productivity  NUMERIC(8,5) NOT NULL,
    capital_cents BIGINT NOT NULL DEFAULT 0,
    headcount     INTEGER NOT NULL DEFAULT 0,
    is_public     BOOLEAN NOT NULL DEFAULT FALSE,
    symbol        TEXT,                       -- set on IPO
    status        TEXT NOT NULL,              -- active|distressed|bankrupt|acquired|dissolved
    PRIMARY KEY (run_id, firm_id)
);

CREATE TABLE vacancies (
    run_id      UUID NOT NULL, vacancy_id TEXT NOT NULL,
    firm_id     TEXT NOT NULL, posted_tick BIGINT NOT NULL, closed_tick BIGINT,
    occupation  TEXT NOT NULL,
    skill_reqs  JSONB NOT NULL,               -- {skill: min_level}
    wage_offer_cents BIGINT NOT NULL,
    filled_by   TEXT,
    PRIMARY KEY (run_id, vacancy_id)
);

CREATE TABLE job_applications (
    run_id UUID NOT NULL, application_id TEXT NOT NULL,
    vacancy_id TEXT NOT NULL, agent_id TEXT NOT NULL, tick BIGINT NOT NULL,
    outcome TEXT,                             -- pending|offered|rejected|withdrawn|accepted
    match_score NUMERIC(6,5),
    PRIMARY KEY (run_id, application_id)
);

CREATE TABLE employments (
    run_id UUID NOT NULL, employment_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, firm_id TEXT NOT NULL, occupation TEXT NOT NULL,
    wage_cents BIGINT NOT NULL,
    started_tick BIGINT NOT NULL, ended_tick BIGINT, end_reason TEXT,
    PRIMARY KEY (run_id, employment_id)
);
CREATE INDEX emp_agent ON employments (run_id, agent_id) WHERE ended_tick IS NULL;
CREATE INDEX emp_firm  ON employments (run_id, firm_id)  WHERE ended_tick IS NULL;

CREATE TABLE skus (
    run_id UUID NOT NULL, sku TEXT NOT NULL,
    category TEXT NOT NULL,                   -- food|housing|goods|services|luxury|health
    is_necessity BOOLEAN NOT NULL,
    base_utility NUMERIC(6,4) NOT NULL,
    PRIMARY KEY (run_id, sku)
);

CREATE TABLE inventory (
    run_id UUID NOT NULL, firm_id TEXT NOT NULL, sku TEXT NOT NULL,
    qty INTEGER NOT NULL, unit_cost_cents BIGINT NOT NULL,
    price_cents BIGINT NOT NULL, updated_tick BIGINT NOT NULL,
    PRIMARY KEY (run_id, firm_id, sku)
);

CREATE TABLE goods_transactions (
    run_id UUID NOT NULL, txn_id UUID NOT NULL, tick BIGINT NOT NULL,
    buyer_id TEXT NOT NULL, seller_firm_id TEXT NOT NULL,
    sku TEXT NOT NULL, qty INTEGER NOT NULL, unit_price_cents BIGINT NOT NULL,
    PRIMARY KEY (run_id, txn_id)
);
CREATE INDEX gt_tick ON goods_transactions (run_id, tick);
CREATE INDEX gt_sku  ON goods_transactions (run_id, sku, tick);
```

---

## 6. Economy — exchange

```sql
CREATE TABLE securities (
    run_id UUID NOT NULL, symbol TEXT NOT NULL,
    issuer_firm_id TEXT NOT NULL, class TEXT NOT NULL,     -- common|preferred|bond
    shares_outstanding BIGINT NOT NULL,
    listed_tick BIGINT NOT NULL, delisted_tick BIGINT,
    PRIMARY KEY (run_id, symbol)
);

CREATE TABLE holdings (
    run_id UUID NOT NULL, holder_id TEXT NOT NULL, symbol TEXT NOT NULL,
    qty BIGINT NOT NULL, avg_cost_cents BIGINT NOT NULL,
    reserved_qty BIGINT NOT NULL DEFAULT 0,      -- locked by resting sell orders
    PRIMARY KEY (run_id, holder_id, symbol)
);
CREATE INDEX hd_symbol ON holdings (run_id, symbol);

CREATE TABLE orders (
    run_id UUID NOT NULL, order_id TEXT NOT NULL,
    symbol TEXT NOT NULL, trader_id TEXT NOT NULL,
    side TEXT NOT NULL,                          -- buy|sell
    order_type TEXT NOT NULL,                    -- limit|market
    limit_price_cents BIGINT,
    qty BIGINT NOT NULL, filled_qty BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL,                        -- open|partial|filled|cancelled|expired
    submitted_tick BIGINT NOT NULL, submitted_seq BIGINT NOT NULL,
    ended_tick BIGINT,
    PRIMARY KEY (run_id, order_id)
);
CREATE INDEX or_book ON orders (run_id, symbol, side, limit_price_cents, submitted_seq)
    WHERE status IN ('open','partial');

CREATE TABLE trades (
    run_id UUID NOT NULL, trade_id BIGSERIAL, tick BIGINT NOT NULL,
    symbol TEXT NOT NULL, price_cents BIGINT NOT NULL, qty BIGINT NOT NULL,
    buy_order_id TEXT NOT NULL, sell_order_id TEXT NOT NULL,
    aggressor TEXT NOT NULL,                     -- buy|sell
    PRIMARY KEY (run_id, trade_id)
) PARTITION BY LIST (run_id);
CREATE INDEX tr_sym_tick ON trades (run_id, symbol, tick);

CREATE TABLE ohlcv (
    run_id UUID NOT NULL, symbol TEXT NOT NULL, session_tick BIGINT NOT NULL,
    open_cents BIGINT, high_cents BIGINT, low_cents BIGINT, close_cents BIGINT,
    volume BIGINT NOT NULL, vwap_cents BIGINT,
    PRIMARY KEY (run_id, symbol, session_tick)
);
```

`orders.submitted_seq` is the tiebreaker for time priority. Using the event `seq` rather
than a timestamp makes matching deterministic (§`02-ARCHITECTURE.md §4`).

---

## 7. Economy — banking and ventures

```sql
CREATE TABLE banks (
    run_id UUID NOT NULL, bank_id TEXT NOT NULL, name TEXT NOT NULL,
    place_id TEXT NOT NULL, ledger_account_id TEXT NOT NULL,
    reserve_account_id TEXT NOT NULL,
    capital_cents BIGINT NOT NULL, reserve_ratio NUMERIC(5,4) NOT NULL,
    is_central BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL, founded_tick BIGINT NOT NULL, failed_tick BIGINT,
    PRIMARY KEY (run_id, bank_id)
);

CREATE TABLE loans (
    run_id UUID NOT NULL, loan_id TEXT NOT NULL,
    lender_id TEXT NOT NULL, borrower_id TEXT NOT NULL,
    principal_cents BIGINT NOT NULL, outstanding_cents BIGINT NOT NULL,
    annual_rate NUMERIC(7,6) NOT NULL, term_ticks BIGINT NOT NULL,
    originated_tick BIGINT NOT NULL, matures_tick BIGINT NOT NULL,
    status TEXT NOT NULL,                    -- current|delinquent|default|repaid|written_off
    collateral JSONB, credit_score_at_origination NUMERIC(5,4),
    PRIMARY KEY (run_id, loan_id)
);
CREATE INDEX ln_borrower ON loans (run_id, borrower_id) WHERE status <> 'repaid';

CREATE TABLE loan_payments (
    run_id UUID NOT NULL, payment_id BIGSERIAL, loan_id TEXT NOT NULL, tick BIGINT NOT NULL,
    principal_cents BIGINT NOT NULL, interest_cents BIGINT NOT NULL, missed BOOLEAN NOT NULL,
    PRIMARY KEY (run_id, payment_id)
);

CREATE TABLE startups (
    run_id UUID NOT NULL, startup_id TEXT NOT NULL, firm_id TEXT NOT NULL,
    thesis TEXT NOT NULL, stage TEXT NOT NULL,      -- idea|preseed|seed|a|b|exited|dead
    burn_rate_cents BIGINT NOT NULL, runway_ticks INTEGER NOT NULL,
    founded_tick BIGINT NOT NULL, died_tick BIGINT, exit_tick BIGINT, exit_type TEXT,
    PRIMARY KEY (run_id, startup_id)
);

CREATE TABLE vc_funds (
    run_id UUID NOT NULL, fund_id TEXT NOT NULL, gp_agent_id TEXT NOT NULL,
    committed_cents BIGINT NOT NULL, deployed_cents BIGINT NOT NULL,
    ledger_account_id TEXT NOT NULL, vintage_tick BIGINT NOT NULL, thesis TEXT,
    PRIMARY KEY (run_id, fund_id)
);

CREATE TABLE funding_rounds (
    run_id UUID NOT NULL, round_id TEXT NOT NULL, startup_id TEXT NOT NULL,
    stage TEXT NOT NULL, tick BIGINT NOT NULL,
    pre_money_cents BIGINT NOT NULL, amount_cents BIGINT NOT NULL,
    lead_investor_id TEXT, participants JSONB NOT NULL,
    PRIMARY KEY (run_id, round_id)
);

CREATE TABLE cap_table (
    run_id UUID NOT NULL, firm_id TEXT NOT NULL, holder_id TEXT NOT NULL,
    shares BIGINT NOT NULL, share_class TEXT NOT NULL, acquired_tick BIGINT NOT NULL,
    PRIMARY KEY (run_id, firm_id, holder_id, share_class)
);

CREATE TABLE bankruptcies (
    run_id UUID NOT NULL, case_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL, filed_tick BIGINT NOT NULL, resolved_tick BIGINT,
    assets_cents BIGINT NOT NULL, liabilities_cents BIGINT NOT NULL,
    recovery_rate NUMERIC(5,4), outcome TEXT,
    PRIMARY KEY (run_id, case_id)
);
```

---

## 8. Society — media, polity, law

```sql
CREATE TABLE posts (
    run_id UUID NOT NULL, post_id TEXT NOT NULL, author_id TEXT NOT NULL,
    tick BIGINT NOT NULL, text TEXT NOT NULL,
    topic TEXT, stance_proposition TEXT, stance_value NUMERIC(5,4),
    in_reply_to TEXT, repost_of TEXT,
    truthfulness NUMERIC(4,3),          -- ground truth vs the event log; NULL if not checkable
    reach INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, post_id)
) PARTITION BY LIST (run_id);
CREATE INDEX po_author ON posts (run_id, author_id, tick DESC);
CREATE INDEX po_topic  ON posts (run_id, topic, tick DESC);
CREATE INDEX po_fts    ON posts USING GIN (to_tsvector('english', text));

CREATE TABLE follows (
    run_id UUID NOT NULL, follower_id TEXT NOT NULL, followee_id TEXT NOT NULL,
    started_tick BIGINT NOT NULL, ended_tick BIGINT,
    PRIMARY KEY (run_id, follower_id, followee_id)
);

CREATE TABLE engagements (
    run_id UUID NOT NULL, engagement_id BIGSERIAL, post_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, tick BIGINT NOT NULL,
    type TEXT NOT NULL,                 -- view|like|repost|comment|report
    PRIMARY KEY (run_id, engagement_id)
) PARTITION BY LIST (run_id);

CREATE TABLE outlets (
    run_id UUID NOT NULL, outlet_id TEXT NOT NULL, name TEXT NOT NULL,
    firm_id TEXT, slant NUMERIC(5,4) NOT NULL, rigour NUMERIC(5,4) NOT NULL,
    reach INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, outlet_id)
);

CREATE TABLE articles (
    run_id UUID NOT NULL, article_id TEXT NOT NULL, outlet_id TEXT NOT NULL,
    reporter_id TEXT, tick BIGINT NOT NULL,
    headline TEXT NOT NULL, body TEXT NOT NULL,
    source_event_seqs BIGINT[] NOT NULL,
    accuracy NUMERIC(4,3),              -- computed against source events
    slant_applied NUMERIC(5,4), reach INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, article_id)
);

CREATE TABLE parties (
    run_id UUID NOT NULL, party_id TEXT NOT NULL, name TEXT NOT NULL,
    platform JSONB NOT NULL,            -- {proposition: stance}
    founded_tick BIGINT NOT NULL, dissolved_tick BIGINT,
    PRIMARY KEY (run_id, party_id)
);

CREATE TABLE elections (
    run_id UUID NOT NULL, election_id TEXT NOT NULL, office TEXT NOT NULL,
    called_tick BIGINT NOT NULL, voting_tick BIGINT NOT NULL,
    turnout NUMERIC(5,4), winner_id TEXT, method TEXT NOT NULL,
    PRIMARY KEY (run_id, election_id)
);

CREATE TABLE candidacies (
    run_id UUID NOT NULL, candidacy_id TEXT NOT NULL, election_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, party_id TEXT, platform JSONB NOT NULL,
    spend_cents BIGINT NOT NULL DEFAULT 0, votes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, candidacy_id)
);

CREATE TABLE votes (
    run_id UUID NOT NULL, election_id TEXT NOT NULL, voter_id TEXT NOT NULL,
    candidacy_id TEXT NOT NULL, tick BIGINT NOT NULL,
    PRIMARY KEY (run_id, election_id, voter_id)
);

CREATE TABLE policies (
    run_id UUID NOT NULL, policy_id TEXT NOT NULL, parameter TEXT NOT NULL,
    old_value JSONB NOT NULL, new_value JSONB NOT NULL,
    enacted_tick BIGINT NOT NULL, repealed_tick BIGINT,
    enacted_by TEXT NOT NULL, vote_margin NUMERIC(5,4),
    PRIMARY KEY (run_id, policy_id)
);

CREATE TABLE crimes (
    run_id UUID NOT NULL, crime_id TEXT NOT NULL, tick BIGINT NOT NULL,
    type TEXT NOT NULL,                 -- theft|fraud|insider_trading|assault|
                                        -- contract_breach|embezzlement|perjury
    perpetrator_id TEXT NOT NULL, victim_id TEXT,
    amount_cents BIGINT, detected BOOLEAN NOT NULL DEFAULT FALSE,
    detected_tick BIGINT, reported_by TEXT,
    PRIMARY KEY (run_id, crime_id)
);

CREATE TABLE court_cases (
    run_id UUID NOT NULL, case_id TEXT NOT NULL, type TEXT NOT NULL,   -- criminal|civil
    plaintiff_id TEXT, defendant_id TEXT NOT NULL, crime_id TEXT,
    filed_tick BIGINT NOT NULL, resolved_tick BIGINT,
    plaintiff_counsel_id TEXT, defence_counsel_id TEXT, judge_id TEXT,
    verdict TEXT, penalty_cents BIGINT, sentence_ticks BIGINT,
    evidence_event_seqs BIGINT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, case_id)
);
```

`posts.truthfulness` and `articles.accuracy` are computed by comparing the claim against
the event log — the log is ground truth, so misinformation is *measurable*, not merely
labelled. This is what makes B2 answerable.

---

## 9. Education

```sql
CREATE TABLE schools (
    run_id UUID NOT NULL, school_id TEXT NOT NULL, place_id TEXT NOT NULL,
    level TEXT NOT NULL,                -- primary|secondary|university|vocational
    quality NUMERIC(5,4) NOT NULL, tuition_cents BIGINT NOT NULL, capacity INTEGER NOT NULL,
    curriculum JSONB NOT NULL,          -- {skill: weight}
    PRIMARY KEY (run_id, school_id)
);

CREATE TABLE enrolments (
    run_id UUID NOT NULL, enrolment_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, school_id TEXT NOT NULL,
    started_tick BIGINT NOT NULL, ended_tick BIGINT,
    outcome TEXT,                       -- graduated|dropped_out|expelled|in_progress
    gpa NUMERIC(4,3),
    PRIMARY KEY (run_id, enrolment_id)
);
```

---

## 10. Research and observability

```sql
CREATE TABLE metrics (
    run_id UUID NOT NULL, tick BIGINT NOT NULL, metric TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, metric, tick)
);
CREATE INDEX me_tick ON metrics (run_id, tick);
```

Long/narrow rather than wide so new metrics need no migration. Exported to Parquet
(wide) for analysis. ~120 metrics × 43,200 ticks ≈ 5.2M rows per run — trivial for Postgres,
and the Parquet export is what analysis actually reads.

```sql
CREATE TABLE external_agents (
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    pubkey TEXT NOT NULL, operator TEXT NOT NULL,
    contact TEXT NOT NULL, display_name TEXT NOT NULL,
    declared_model TEXT NOT NULL, declared_model_version TEXT NOT NULL,
    declared_scaffold TEXT NOT NULL, scaffold_notes TEXT NOT NULL,
    memory TEXT NOT NULL, sdk_version TEXT NOT NULL, protocol_version INTEGER NOT NULL,
    requested_embodiment TEXT
        CHECK (requested_embodiment IN ('cohort_matched','paired_control','adopt_existing')),
    embodiment TEXT NOT NULL
        CHECK (embodiment IN ('cohort_matched','paired_control','adopt_existing')),
    conformance_token TEXT, twin_agent_id TEXT,
    registered_tick BIGINT NOT NULL, admitted_tick BIGINT NOT NULL,
    revoked_tick BIGINT, naturalised_tick BIGINT, resume_grace_until_tick BIGINT,
    consecutive_misses INTEGER NOT NULL DEFAULT 0,
    ticks_driven BIGINT NOT NULL DEFAULT 0,
    actions_submitted BIGINT NOT NULL DEFAULT 0,
    actions_rejected BIGINT NOT NULL DEFAULT 0,
    deadlines_missed BIGINT NOT NULL DEFAULT 0,
    sim_aware_count BIGINT NOT NULL DEFAULT 0,
    strikes INTEGER NOT NULL DEFAULT 0,
    suspensions INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, agent_id),
    UNIQUE (run_id, pubkey)
);

-- This is an admitted-agent projection, not a pending-registration table.
-- Kind 20001 creates the row, so embodiment and admitted_tick remain NOT NULL.
CREATE TABLE external_sessions (
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL, agent_id TEXT NOT NULL,
    custody TEXT NOT NULL, delegate_pubkey TEXT, client JSONB NOT NULL,
    opened_tick BIGINT NOT NULL, expires_unix_ms BIGINT NOT NULL,
    closed_tick BIGINT, close_reason TEXT,
    PRIMARY KEY (run_id, session_id)
);

CREATE TABLE external_nonces (
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    last_nonce BIGINT NOT NULL, updated_tick BIGINT NOT NULL,
    PRIMARY KEY (run_id, agent_id)
);

CREATE TABLE external_latency (
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL, tick BIGINT NOT NULL,
    observation_pushed_ms BIGINT NOT NULL, action_received_ms BIGINT,
    decision_ms INTEGER, missed BOOLEAN NOT NULL,
    PRIMARY KEY (run_id, agent_id, tick)
) PARTITION BY LIST (run_id);
```

Pending declarations remain in the gateway registration handoff until the engine records kind
20000. Kind 20001 creates the admitted `external_agents` row; kind 20002 records rejection
without creating one. Consequently, row existence itself proves admission, and a projection
rebuild creates `external_agents` rows only from kind 20001.

These three tables are **operational gateway records**, not replayable projections.
`external_sessions` is retained for the live run and expires sessions against the gateway
server's Unix clock; replay never re-opens a bearer session. `external_nonces` is retained
with the run so a restarted gateway cannot accept an already-used nonce; an offline rebuild
may recover its maximum accepted value from 20020 events, but replay itself never consults
the table. `external_latency` is retained with the original run for liveness audit and is
not rebuildable because its gateway-local timing measurements are deliberately absent from
the event log. Removing a run removes these operational records (or its latency partition).
`external_conformance_tokens` is likewise operational and non-replayable: it is a short-lived,
single-use pre-admission credential with no run ownership until redemption records
`used_run_id`.

The expiry and latency values are control-plane metadata only. They never enter an event
payload, simulated state, an RNG seed, or deterministic resolution. This preserves
`02-ARCHITECTURE.md §4.5`: wall-clock time may be recorded for run/LLM operations and
gateway operation, but never as world state.

```sql
CREATE TABLE scenario_injections (
    run_id UUID NOT NULL, injection_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
    tick BIGINT NOT NULL, kind TEXT NOT NULL,
    payload JSONB NOT NULL, researcher_pubkey TEXT NOT NULL, sig TEXT NOT NULL,
    step_id TEXT NOT NULL,            -- which step of the scenario produced this
    event_seq BIGINT,                 -- the event this injection generated
    scenario_hash TEXT NOT NULL,      -- sha256 of the scenario YAML
    PRIMARY KEY (run_id, injection_id)
);

CREATE TABLE sweeps (
    sweep_id UUID PRIMARY KEY, name TEXT NOT NULL,
    base_config_hash TEXT NOT NULL, grid JSONB NOT NULL,
    seeds INTEGER[] NOT NULL, created_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL,
    preregistration TEXT,                    -- the analysis plan, written BEFORE the run
    analysis_plan_hash TEXT,                 -- sha256, frozen at launch
    cost_estimate_usd NUMERIC(12,2)          -- pre-launch estimate; launch is refused
                                             --   above the cap without an explicit override
);
```

`v_agent_control` exposes only the current native/operator driver, `v_market_visible`
exposes aggregated top-of-book rows without counterparty identity, and
`v_public_record` is the closed public-history surface. The gateway connects as
`polis_reader`; it has `SELECT` on these objects and no mutation privilege. Engine-side
adapters apply queued actions, memory writes, touches, and registrations.

---

## 11. Storage estimates and retention

At 1,000 agents, `microscope`, **five sim-years (43,200 ticks — one sim-year is 8,640
ticks: 24 ticks/day × 360 days, per `02-ARCHITECTURE.md §5.2`)**:

| Table | Rows / 43,200 ticks | Approx size |
|---|---|---|
| `events` | ~150 M | ~90 GB |
| `ledger_entries` | ~40 M | ~4 GB |
| `memories` | ~8 M | ~30 GB (embeddings dominate) |
| `posts` + `engagements` | ~25 M | ~8 GB |
| `trades` | ~5 M | ~0.5 GB |
| `metrics` | ~5 M | ~0.3 GB |

**Retention policy:**

- `events` for the *headline* run of a study is kept in full and published.
- Sweep cells keep `metrics` + `runs` + the completion cache, and drop `events` after
  computing derived tables. Configured as `retention: metrics_only` per run.
- `memories.embedding` is dropped for completed runs after export (`VACUUM FULL`) — it is
  regenerable from `text`.
- Cognition-event sampling (`02-ARCHITECTURE.md §3.3`) is the main lever on `events` size.
  At `cognition_sample_rate: 0.02` the log is ~40% smaller than with full logging.

---

## 12. Projection rebuild

`polis rebuild --run <id> [--from-tick N]` truncates all projection tables and replays the
log through the same handlers used at runtime. This must produce state identical to the
live run — verified by `tests/determinism/test_projection_rebuild.py`, which runs 500 ticks
live, rebuilds, and diffs every projection table.

If rebuild and live diverge, a handler has a side effect that isn't in the log. That is
always a bug, and this test is how you find it.

---

*Next: `04-AGENT-SPEC.md`.*
