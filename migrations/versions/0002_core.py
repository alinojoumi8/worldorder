"""Create runs, partitioned events, LLM calls, cache, and checkpoints."""

from alembic import op

revision = "0002_core"
down_revision = "0001_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE runs (
            run_id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            config_yaml TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            master_seed BIGINT NOT NULL,
            prompt_manifest JSONB NOT NULL DEFAULT '{}',
            model_manifest JSONB NOT NULL DEFAULT '{}',
            metric_manifest JSONB NOT NULL DEFAULT '{}',
            mechanism_manifest JSONB NOT NULL DEFAULT '{}',
            ablations JSONB NOT NULL DEFAULT '{}',
            scale INTEGER NOT NULL DEFAULT 0,
            code_git_sha TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            ended_at TIMESTAMP,
            status TEXT NOT NULL,
            last_tick BIGINT NOT NULL DEFAULT 0,
            terminal_hash TEXT,
            parent_run_id UUID REFERENCES runs(run_id),
            sweep_id UUID,
            tags TEXT[] NOT NULL DEFAULT '{}',
            halt_reason TEXT
        );

        CREATE TABLE events (
            seq BIGINT NOT NULL,
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            tick BIGINT NOT NULL,
            sim_time TIMESTAMP NOT NULL,
            kind INTEGER NOT NULL,
            actor_id TEXT,
            subject_ids TEXT[] NOT NULL DEFAULT '{}',
            cause_seq BIGINT,
            payload JSONB NOT NULL,
            sig TEXT,
            prev_hash CHAR(64) NOT NULL,
            hash CHAR(64) NOT NULL,
            PRIMARY KEY (run_id, tick, seq)
        ) PARTITION BY LIST (run_id);
        CREATE INDEX ev_seq ON events (run_id, seq);
        CREATE INDEX ev_kind_tick ON events (run_id, kind, tick);
        CREATE INDEX ev_actor ON events (run_id, actor_id, tick);
        CREATE INDEX ev_subjects ON events USING GIN (subject_ids);
        CREATE INDEX ev_cause ON events (run_id, cause_seq);

        CREATE TABLE llm_calls (
            call_id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            tick BIGINT NOT NULL,
            actor_id TEXT,
            purpose TEXT NOT NULL,
            lane TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            request JSONB NOT NULL,
            response JSONB NOT NULL,
            cache_hit BOOLEAN NOT NULL,
            cache_mode TEXT NOT NULL,
            tokens_in INTEGER NOT NULL,
            tokens_out INTEGER NOT NULL,
            cost_usd NUMERIC(14, 8) NOT NULL,
            latency_ms INTEGER NOT NULL,
            provider_request_id TEXT,
            budget_line TEXT NOT NULL
        );
        CREATE INDEX llm_run_tick ON llm_calls (run_id, tick);
        CREATE INDEX llm_actor_tick ON llm_calls (run_id, actor_id, tick);

        CREATE TABLE completion_cache (
            cache_key TEXT PRIMARY KEY,
            prompt_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            response JSONB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            hits BIGINT NOT NULL DEFAULT 0
        );

        CREATE TABLE checkpoints (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            tick BIGINT NOT NULL,
            last_seq BIGINT NOT NULL,
            chain_hash CHAR(64) NOT NULL,
            uri TEXT NOT NULL,
            bytes BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            PRIMARY KEY (run_id, tick)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS checkpoints, completion_cache, llm_calls, events, runs CASCADE"
    )
