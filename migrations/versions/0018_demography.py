"""Households, lifecycle, and inheritance projections.

Revision ID: 0018_demography
Revises: 0017_law
"""

from alembic import op

revision = "0018_demography"
down_revision = "0017_law"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE households (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            household_id TEXT NOT NULL,
            formed_at_tick BIGINT NOT NULL,
            dissolved_at_tick BIGINT,
            home_place_id TEXT NOT NULL,
            member_ids TEXT[] NOT NULL DEFAULT '{}',
            head_agent_id TEXT,
            tenure TEXT NOT NULL CHECK (tenure IN ('own','rent','shelter')),
            rent_cents BIGINT NOT NULL DEFAULT 0,
            joint_baseline_cents JSONB NOT NULL DEFAULT '{}',
            arrears_cents BIGINT NOT NULL DEFAULT 0,
            as_of_tick BIGINT NOT NULL,
            as_of_seq BIGINT NOT NULL,
            PRIMARY KEY (run_id, household_id)
        );
        CREATE INDEX households_member_ids
            ON households USING GIN (member_ids);

        ALTER TABLE agents
            ADD COLUMN household_id TEXT,
            ADD COLUMN mother_id TEXT,
            ADD COLUMN father_id TEXT,
            ADD COLUMN generation INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN died_at_tick BIGINT,
            ADD COLUMN death_cause TEXT;
        UPDATE agents SET home_place_id = place_id WHERE home_place_id IS NULL;
        ALTER TABLE agents ALTER COLUMN home_place_id SET NOT NULL;
        CREATE INDEX agents_household
            ON agents (run_id, household_id, agent_id);
        CREATE INDEX agents_parents
            ON agents (run_id, mother_id, father_id, agent_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS agents_parents;
        DROP INDEX IF EXISTS agents_household;
        ALTER TABLE agents ALTER COLUMN home_place_id DROP NOT NULL;
        ALTER TABLE agents
            DROP COLUMN IF EXISTS death_cause,
            DROP COLUMN IF EXISTS died_at_tick,
            DROP COLUMN IF EXISTS generation,
            DROP COLUMN IF EXISTS father_id,
            DROP COLUMN IF EXISTS mother_id,
            DROP COLUMN IF EXISTS household_id;
        DROP TABLE IF EXISTS households;
        """
    )
