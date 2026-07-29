"""Require a matched live external row for operator control.

Revision ID: 0021_agent_control_view
Revises: 0020_cache_manifest
"""

from alembic import op

revision = "0021_agent_control_view"
down_revision = "0020_cache_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW v_agent_control AS
        SELECT a.run_id,a.agent_id,a.kind,
               CASE
                   WHEN a.kind = 'native' THEN 'native'
                   WHEN x.agent_id IS NOT NULL
                        AND x.revoked_tick IS NULL
                        AND x.naturalised_tick IS NULL THEN 'operator'
                   ELSE 'native'
               END AS driver
        FROM agents a
        LEFT JOIN external_agents x
          ON x.run_id=a.run_id AND x.agent_id=a.agent_id;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW v_agent_control AS
        SELECT a.run_id,a.agent_id,a.kind,
               CASE
                   WHEN a.kind = 'native' THEN 'native'
                   WHEN x.agent_id IS NOT NULL THEN 'operator'
                   ELSE 'native'
               END AS driver
        FROM agents a
        LEFT JOIN external_agents x
          ON x.run_id=a.run_id AND x.agent_id=a.agent_id;
        """
    )
