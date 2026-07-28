"""News outlets, articles and source-aware belief projections.

Revision ID: 0015_news_beliefs
Revises: 0014_post_viewers
"""

from alembic import op

revision = "0015_news_beliefs"
down_revision = "0014_post_viewers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE posts ADD COLUMN truthfulness_n INTEGER NOT NULL DEFAULT 0;

        CREATE TABLE outlets (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            outlet_id TEXT NOT NULL,
            name TEXT NOT NULL,
            firm_id TEXT,
            slant DOUBLE PRECISION NOT NULL,
            rigour DOUBLE PRECISION NOT NULL,
            reach INTEGER NOT NULL DEFAULT 0,
            founded_tick BIGINT NOT NULL,
            closed_tick BIGINT,
            PRIMARY KEY (run_id,outlet_id)
        );

        CREATE TABLE articles (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            article_id TEXT NOT NULL,
            outlet_id TEXT NOT NULL,
            reporter_id TEXT,
            tick BIGINT NOT NULL,
            headline TEXT NOT NULL,
            body TEXT NOT NULL,
            source_event_seqs BIGINT[] NOT NULL DEFAULT '{}',
            claims JSONB NOT NULL DEFAULT '[]',
            accuracy DOUBLE PRECISION,
            accuracy_n INTEGER NOT NULL DEFAULT 0,
            slant_applied DOUBLE PRECISION,
            stance_proposition TEXT,
            stance_value DOUBLE PRECISION,
            reach INTEGER NOT NULL DEFAULT 0,
            retracted_tick BIGINT,
            PRIMARY KEY (run_id,article_id)
        ) PARTITION BY LIST(run_id);
        CREATE TABLE articles_default PARTITION OF articles DEFAULT;
        CREATE INDEX ar_outlet ON articles(run_id,outlet_id,tick DESC);
        CREATE INDEX ar_fts ON articles USING GIN(to_tsvector('english',headline || ' ' || body));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS articles, outlets CASCADE;
        ALTER TABLE posts DROP COLUMN IF EXISTS truthfulness_n;
        """
    )
