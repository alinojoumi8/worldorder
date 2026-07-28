"""Crime, courts, penalties, and incarceration.

Revision ID: 0017_law
Revises: 0016_polity
"""

from alembic import op

revision = "0017_law"
down_revision = "0016_polity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE crimes (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            crime_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            type TEXT NOT NULL,
            perpetrator_id TEXT NOT NULL,
            victim_id TEXT,
            amount_cents BIGINT,
            place_id TEXT,
            district_id TEXT,
            source_action_id TEXT NOT NULL,
            concealment DOUBLE PRECISION NOT NULL,
            path TEXT NOT NULL,
            detected BOOLEAN NOT NULL DEFAULT FALSE,
            detected_tick BIGINT,
            reported_by TEXT,
            PRIMARY KEY (run_id,crime_id),
            UNIQUE (run_id,source_action_id)
        );
        CREATE INDEX crimes_detection_queue
            ON crimes(run_id,detected,tick,crime_id);

        CREATE TABLE court_cases (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            case_id TEXT NOT NULL,
            type TEXT NOT NULL,
            plaintiff_id TEXT,
            defendant_id TEXT NOT NULL,
            crime_id TEXT,
            cause_of_action TEXT NOT NULL,
            claim_cents BIGINT NOT NULL DEFAULT 0,
            filed_tick BIGINT NOT NULL,
            resolved_tick BIGINT,
            plaintiff_counsel_id TEXT,
            defence_counsel_id TEXT,
            judge_id TEXT,
            verdict TEXT,
            penalty_cents BIGINT,
            sentence_ticks BIGINT,
            damages_cents BIGINT NOT NULL DEFAULT 0,
            restitution_cents BIGINT NOT NULL DEFAULT 0,
            evidence_event_seqs BIGINT[] NOT NULL DEFAULT '{}',
            admitted_event_seqs BIGINT[] NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'open',
            PRIMARY KEY (run_id,case_id)
        );
        CREATE INDEX court_cases_docket
            ON court_cases(run_id,status,filed_tick,case_id);

        ALTER TABLE agents
            ADD COLUMN criminal_record INTEGER NOT NULL DEFAULT 0;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE agents DROP COLUMN IF EXISTS criminal_record;
        DROP TABLE IF EXISTS court_cases, crimes CASCADE;
        """
    )
