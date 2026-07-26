"""Persist capitalised loan interest.

Revision ID: 0010_loan_capitalised_interest
Revises: 0009_banking_fiscal
"""

from alembic import op

revision = "0010_loan_capitalised_interest"
down_revision = "0009_banking_fiscal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE loans
            ADD COLUMN IF NOT EXISTS capitalised_interest_cents
                BIGINT NOT NULL DEFAULT 0;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE loans
            DROP COLUMN IF EXISTS capitalised_interest_cents;
        """
    )
