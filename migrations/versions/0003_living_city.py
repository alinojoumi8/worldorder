"""Create M1 agent, memory, world, and metric projections."""

from alembic import op

revision = "0003_living_city"
down_revision = "0002_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agents (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            born_tick BIGINT NOT NULL,
            died_tick BIGINT,
            age_years INTEGER NOT NULL,
            district_id TEXT NOT NULL,
            place_id TEXT NOT NULL,
            state JSONB NOT NULL,
            as_of_tick BIGINT NOT NULL,
            as_of_seq BIGINT NOT NULL,
            PRIMARY KEY (run_id, agent_id)
        );
        CREATE INDEX agents_district ON agents (run_id, district_id, agent_id);

        CREATE TABLE memories (
            memory_id TEXT NOT NULL,
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            type TEXT NOT NULL,
            text TEXT NOT NULL,
            importance DOUBLE PRECISION NOT NULL,
            last_accessed_tick BIGINT NOT NULL,
            parent_memory_ids TEXT[] NOT NULL DEFAULT '{}',
            subject_ids TEXT[] NOT NULL DEFAULT '{}',
            archived BOOLEAN NOT NULL DEFAULT FALSE,
            embedding vector(768),
            PRIMARY KEY (run_id, memory_id)
        ) PARTITION BY LIST (run_id);

        CREATE TABLE beliefs (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            proposition TEXT NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            updated_tick BIGINT NOT NULL,
            source_seq BIGINT,
            PRIMARY KEY (run_id, agent_id, proposition)
        );

        CREATE TABLE districts (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            district_id TEXT NOT NULL,
            name TEXT NOT NULL,
            polygon JSONB NOT NULL,
            properties JSONB NOT NULL,
            PRIMARY KEY (run_id, district_id)
        );

        CREATE TABLE places (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            place_id TEXT NOT NULL,
            district_id TEXT NOT NULL,
            type TEXT NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            capacity INTEGER NOT NULL,
            properties JSONB NOT NULL,
            PRIMARY KEY (run_id, place_id)
        );

        CREATE TABLE metrics (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            tick BIGINT NOT NULL,
            metric TEXT NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            as_of_seq BIGINT NOT NULL,
            PRIMARY KEY (run_id, tick, metric)
        );
        CREATE INDEX metrics_series ON metrics (run_id, metric, tick);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS metrics, places, districts, beliefs, memories, agents CASCADE")
