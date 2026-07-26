"""Persist the exact loan close tick for windowed metrics.

Revision ID: 0011_loan_close_tick
Revises: 0010_loan_capitalised_interest
"""

from alembic import op

revision = "0011_loan_close_tick"
down_revision = "0010_loan_capitalised_interest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE loans
            ADD COLUMN closed_tick BIGINT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE loans
            DROP COLUMN IF EXISTS closed_tick;
        """
    )
