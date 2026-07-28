"""Persist the immutable completion-cache snapshot for each run.

Revision ID: 0020_cache_manifest
Revises: 0019_external_gateway
"""

from alembic import op

revision = "0020_cache_manifest"
down_revision = "0019_external_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runs
            ADD COLUMN completion_cache_manifest JSONB NOT NULL
                DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(completion_cache_manifest) = 'object'),
            ADD COLUMN completion_cache_manifest_hash CHAR(64) NOT NULL
                DEFAULT '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'
                CHECK (completion_cache_manifest_hash ~ '^[0-9a-f]{64}$');
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE runs
            DROP COLUMN IF EXISTS completion_cache_manifest_hash,
            DROP COLUMN IF EXISTS completion_cache_manifest;
        """
    )
