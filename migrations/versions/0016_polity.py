"""Parties, elections, ballots, candidacies, and enacted policies.

Revision ID: 0016_polity
Revises: 0015_news_beliefs
"""

from alembic import op

revision = "0016_polity"
down_revision = "0015_news_beliefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE parties (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            party_id TEXT NOT NULL,
            name TEXT NOT NULL,
            platform JSONB NOT NULL,
            founded_tick BIGINT NOT NULL,
            dissolved_tick BIGINT,
            PRIMARY KEY (run_id,party_id)
        );

        CREATE TABLE elections (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            election_id TEXT NOT NULL,
            office TEXT NOT NULL,
            seats INTEGER NOT NULL,
            called_tick BIGINT NOT NULL,
            voting_tick BIGINT NOT NULL,
            campaign_ends_tick BIGINT NOT NULL,
            electorate_size INTEGER NOT NULL,
            turnout DOUBLE PRECISION,
            winner_id TEXT,
            winner_ids JSONB NOT NULL DEFAULT '[]',
            method TEXT NOT NULL,
            margin DOUBLE PRECISION,
            diagnostics JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (run_id,election_id)
        );

        CREATE TABLE candidacies (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            candidacy_id TEXT NOT NULL,
            election_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            party_id TEXT,
            platform JSONB NOT NULL,
            spend_cents BIGINT NOT NULL DEFAULT 0,
            votes INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (run_id,candidacy_id)
        );
        CREATE INDEX ca_election ON candidacies(run_id,election_id);

        CREATE TABLE votes (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            election_id TEXT NOT NULL,
            voter_id TEXT NOT NULL,
            candidacy_id TEXT,
            tick BIGINT NOT NULL,
            ranking JSONB NOT NULL DEFAULT '[]',
            approvals JSONB NOT NULL DEFAULT '[]',
            origin TEXT NOT NULL,
            utility JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (run_id,election_id,voter_id)
        );

        CREATE TABLE policies (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            policy_id TEXT NOT NULL,
            parameter TEXT NOT NULL,
            old_value JSONB NOT NULL,
            new_value JSONB NOT NULL,
            enacted_tick BIGINT NOT NULL,
            effective_tick BIGINT NOT NULL,
            repealed_tick BIGINT,
            enacted_by TEXT NOT NULL,
            vote_margin DOUBLE PRECISION,
            proposal_seq BIGINT,
            PRIMARY KEY (run_id,policy_id)
        );
        CREATE INDEX po_parameter ON policies(run_id,parameter,enacted_tick);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS policies, votes, candidacies, elections, parties CASCADE;
        """
    )
