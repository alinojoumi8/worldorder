"""Lock down the read-only Observatory role."""

from alembic import op

revision = "0004_roles"
down_revision = "0003_living_city"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        GRANT USAGE ON SCHEMA public TO polis_reader;
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO polis_reader;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM polis_reader;
        ALTER DEFAULT PRIVILEGES FOR ROLE polis_engine IN SCHEMA public
            GRANT SELECT ON TABLES TO polis_reader;
        REVOKE CREATE ON DATABASE polis FROM polis_engine;
        """
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM polis_reader")
