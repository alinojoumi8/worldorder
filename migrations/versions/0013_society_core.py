"""Social graph and platform projections.

Revision ID: 0013_society_core
Revises: 0012_m3_capital
"""

from alembic import op

revision = "0013_society_core"
down_revision = "0012_m3_capital"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE relationships (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            a_id TEXT NOT NULL,
            b_id TEXT NOT NULL,
            type TEXT NOT NULL,
            strength DOUBLE PRECISION NOT NULL,
            valence DOUBLE PRECISION NOT NULL,
            trust DOUBLE PRECISION NOT NULL,
            formed_tick BIGINT NOT NULL,
            ended_tick BIGINT,
            last_interaction_tick BIGINT NOT NULL,
            PRIMARY KEY (run_id, a_id, b_id, type)
        );
        CREATE INDEX rel_a ON relationships(run_id,a_id) WHERE ended_tick IS NULL;
        CREATE INDEX rel_b ON relationships(run_id,b_id) WHERE ended_tick IS NULL;

        CREATE TABLE posts (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            post_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            text TEXT NOT NULL,
            topic TEXT,
            stance_proposition TEXT,
            stance_value DOUBLE PRECISION,
            in_reply_to TEXT,
            repost_of TEXT,
            root_post_id TEXT NOT NULL,
            claims JSONB NOT NULL DEFAULT '[]',
            truthfulness DOUBLE PRECISION,
            reach INTEGER NOT NULL DEFAULT 0,
            deleted_tick BIGINT,
            PRIMARY KEY (run_id, post_id)
        ) PARTITION BY LIST(run_id);
        CREATE TABLE posts_default PARTITION OF posts DEFAULT;
        CREATE INDEX po_author ON posts(run_id,author_id,tick DESC);
        CREATE INDEX po_topic ON posts(run_id,topic,tick DESC);
        CREATE INDEX po_fts ON posts USING GIN(to_tsvector('english',text));

        CREATE TABLE follows (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            follower_id TEXT NOT NULL,
            followee_id TEXT NOT NULL,
            started_tick BIGINT NOT NULL,
            ended_tick BIGINT,
            context TEXT NOT NULL,
            PRIMARY KEY (run_id, follower_id, followee_id)
        );
        CREATE INDEX fo_followee ON follows(run_id,followee_id);

        CREATE TABLE engagements (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            engagement_id BIGINT NOT NULL,
            post_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            type TEXT NOT NULL,
            PRIMARY KEY (run_id, engagement_id)
        ) PARTITION BY LIST(run_id);
        CREATE TABLE engagements_default PARTITION OF engagements DEFAULT;
        CREATE INDEX en_post ON engagements(run_id,post_id,tick);
        CREATE INDEX en_agent ON engagements(run_id,agent_id,tick);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS engagements, follows, posts, relationships CASCADE")
