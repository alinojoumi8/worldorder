"""Add complete M1 projections and Observatory trace storage."""

from alembic import op

revision = "0005_observatory_projections"
down_revision = "0004_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agents
            ADD COLUMN display_name TEXT NOT NULL DEFAULT '',
            ADD COLUMN kind TEXT NOT NULL DEFAULT 'native',
            ADD COLUMN traits JSONB NOT NULL DEFAULT '{}',
            ADD COLUMN needs JSONB NOT NULL DEFAULT '{}',
            ADD COLUMN health DOUBLE PRECISION NOT NULL DEFAULT 1,
            ADD COLUMN home_place_id TEXT,
            ADD COLUMN current_place_id TEXT,
            ADD COLUMN pos_x INTEGER,
            ADD COLUMN pos_y INTEGER,
            ADD COLUMN dest_place_id TEXT,
            ADD COLUMN path_cursor INTEGER,
            ADD COLUMN education_level TEXT NOT NULL DEFAULT 'none',
            ADD COLUMN employment_status TEXT NOT NULL DEFAULT 'unemployed',
            ADD COLUMN wealth_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN reputation DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            ADD COLUMN reflex_profile JSONB NOT NULL DEFAULT '{}',
            ADD COLUMN goals JSONB NOT NULL DEFAULT '[]',
            ADD COLUMN cognition_mode TEXT NOT NULL DEFAULT 'reflex';
        CREATE INDEX agents_place ON agents(run_id,current_place_id,agent_id);

        ALTER TABLE memories
            ADD COLUMN source_event_seq BIGINT,
            ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0;

        ALTER TABLE beliefs
            ADD COLUMN confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            ADD COLUMN source TEXT NOT NULL DEFAULT 'experience',
            ADD COLUMN source_ref TEXT;

        ALTER TABLE places
            ADD COLUMN name TEXT NOT NULL DEFAULT '',
            ADD COLUMN owner_id TEXT,
            ADD COLUMN rent_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN open_hours INTEGER[] NOT NULL DEFAULT ARRAY[0,24];

        CREATE TABLE tiles (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            terrain SMALLINT NOT NULL,
            place_id TEXT,
            PRIMARY KEY(run_id,x,y)
        );

        CREATE TABLE cognition_traces (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            trace JSONB NOT NULL,
            as_of_seq BIGINT NOT NULL,
            PRIMARY KEY(run_id,agent_id,tick)
        );
        CREATE INDEX cognition_traces_tick
            ON cognition_traces(run_id,tick,agent_id);

        CREATE TABLE engine_heartbeats (
            run_id UUID PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
            tick BIGINT NOT NULL,
            as_of_seq BIGINT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        GRANT SELECT ON tiles,cognition_traces,engine_heartbeats TO polis_reader;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS engine_heartbeats,cognition_traces,tiles CASCADE;
        DROP INDEX IF EXISTS agents_place;
        ALTER TABLE places
            DROP COLUMN IF EXISTS open_hours,
            DROP COLUMN IF EXISTS rent_cents,
            DROP COLUMN IF EXISTS owner_id,
            DROP COLUMN IF EXISTS name;
        ALTER TABLE beliefs
            DROP COLUMN IF EXISTS source_ref,
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS confidence;
        ALTER TABLE memories
            DROP COLUMN IF EXISTS access_count,
            DROP COLUMN IF EXISTS source_event_seq;
        ALTER TABLE agents
            DROP COLUMN IF EXISTS cognition_mode,
            DROP COLUMN IF EXISTS goals,
            DROP COLUMN IF EXISTS reflex_profile,
            DROP COLUMN IF EXISTS reputation,
            DROP COLUMN IF EXISTS wealth_cents,
            DROP COLUMN IF EXISTS employment_status,
            DROP COLUMN IF EXISTS education_level,
            DROP COLUMN IF EXISTS path_cursor,
            DROP COLUMN IF EXISTS dest_place_id,
            DROP COLUMN IF EXISTS pos_y,
            DROP COLUMN IF EXISTS pos_x,
            DROP COLUMN IF EXISTS current_place_id,
            DROP COLUMN IF EXISTS home_place_id,
            DROP COLUMN IF EXISTS health,
            DROP COLUMN IF EXISTS needs,
            DROP COLUMN IF EXISTS traits,
            DROP COLUMN IF EXISTS kind,
            DROP COLUMN IF EXISTS display_name;
        """
    )
