"""Incremental unique-view reach support.

Revision ID: 0014_post_viewers
Revises: 0013_society_core
"""

from alembic import op

revision = "0014_post_viewers"
down_revision = "0013_society_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE post_viewers (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            post_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            PRIMARY KEY (run_id,post_id,agent_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS post_viewers")
