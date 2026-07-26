"""Banking, credit, central-bank, and fiscal projections.

Revision ID: 0009_banking_fiscal
Revises: 0008_goods_cpi
"""

from alembic import op

revision = "0009_banking_fiscal"
down_revision = "0008_goods_cpi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE banks
            ADD COLUMN lending_frozen BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN capital_ratio_bp INTEGER NOT NULL DEFAULT 10000;

        CREATE TABLE loan_applications (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            application_id TEXT NOT NULL,
            borrower_id TEXT NOT NULL,
            lender_id TEXT NOT NULL,
            requested_cents BIGINT NOT NULL,
            purpose TEXT NOT NULL,
            term_ticks BIGINT NOT NULL,
            collateral JSONB NOT NULL,
            submitted_tick BIGINT NOT NULL,
            status TEXT NOT NULL,
            score_bp INTEGER,
            offered_cents BIGINT NOT NULL,
            offered_rate_bp INTEGER NOT NULL,
            reason_codes JSONB NOT NULL,
            PRIMARY KEY(run_id,application_id)
        );

        CREATE TABLE loans (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            loan_id TEXT NOT NULL,
            lender_id TEXT NOT NULL,
            borrower_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            principal_cents BIGINT NOT NULL,
            outstanding_cents BIGINT NOT NULL,
            annual_rate_bp INTEGER NOT NULL,
            term_ticks BIGINT NOT NULL,
            originated_tick BIGINT NOT NULL,
            matures_tick BIGINT NOT NULL,
            status TEXT NOT NULL,
            collateral JSONB NOT NULL,
            collateral_value_cents BIGINT NOT NULL,
            credit_score_at_origination_bp INTEGER NOT NULL,
            payment_cents BIGINT NOT NULL,
            payments_n INTEGER NOT NULL,
            next_payment_tick BIGINT NOT NULL,
            accrued_interest_cents BIGINT NOT NULL,
            total_interest_paid_cents BIGINT NOT NULL,
            missed_since_tick BIGINT,
            defaulted_tick BIGINT,
            PRIMARY KEY(run_id,loan_id)
        );
        CREATE INDEX ln_borrower
            ON loans(run_id,borrower_id)
            WHERE status NOT IN ('repaid','written_off');

        CREATE TABLE loan_payments (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            payment_id TEXT NOT NULL,
            loan_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            principal_cents BIGINT NOT NULL,
            interest_cents BIGINT NOT NULL,
            missed BOOLEAN NOT NULL,
            PRIMARY KEY(run_id,payment_id)
        );

        CREATE TABLE tax_assessments (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            assessment_id TEXT NOT NULL,
            taxpayer_id TEXT NOT NULL,
            tax_type TEXT NOT NULL,
            base_cents BIGINT NOT NULL,
            rate_bp INTEGER NOT NULL,
            assessed_cents BIGINT NOT NULL,
            assessed_tick BIGINT NOT NULL,
            due_tick BIGINT NOT NULL,
            paid_cents BIGINT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,assessment_id)
        );

        CREATE TABLE securities (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            issuer_firm_id TEXT NOT NULL,
            class TEXT NOT NULL,
            shares_outstanding BIGINT NOT NULL,
            listed_tick BIGINT NOT NULL,
            delisted_tick BIGINT,
            coupon_bp INTEGER,
            matures_tick BIGINT,
            PRIMARY KEY(run_id,symbol)
        );

        CREATE TABLE holdings (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            holder_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            qty BIGINT NOT NULL,
            avg_cost_cents BIGINT NOT NULL,
            reserved_qty BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY(run_id,holder_id,symbol)
        );
        CREATE INDEX hd_symbol ON holdings(run_id,symbol);

        GRANT SELECT ON loan_applications,loans,loan_payments,tax_assessments,
            securities,holdings TO polis_reader;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS holdings,securities,tax_assessments,loan_payments,
            loans,loan_applications CASCADE;
        ALTER TABLE banks
            DROP COLUMN IF EXISTS capital_ratio_bp,
            DROP COLUMN IF EXISTS lending_frozen;
        """
    )
