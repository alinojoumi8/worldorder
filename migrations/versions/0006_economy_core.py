"""Create the M2 double-entry ledger and economy projections."""

from alembic import op

revision = "0006_economy_core"
down_revision = "0005_observatory_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ledger_accounts (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            account_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            owner_type TEXT NOT NULL,
            account_type TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'POL' CHECK(currency='POL'),
            balance_cents BIGINT NOT NULL DEFAULT 0,
            opened_tick BIGINT NOT NULL,
            closed_tick BIGINT,
            PRIMARY KEY(run_id,account_id)
        );
        CREATE INDEX la_owner ON ledger_accounts(run_id,owner_id);

        CREATE TABLE ledger_entries (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            entry_id BIGSERIAL,
            txn_id UUID NOT NULL,
            tick BIGINT NOT NULL,
            account_id TEXT NOT NULL,
            direction SMALLINT NOT NULL CHECK(direction IN (-1,1)),
            amount_cents BIGINT NOT NULL CHECK(amount_cents > 0),
            reason TEXT NOT NULL,
            event_seq BIGINT NOT NULL,
            PRIMARY KEY(run_id,entry_id),
            UNIQUE(run_id,txn_id,account_id,direction,reason),
            FOREIGN KEY(run_id,account_id)
                REFERENCES ledger_accounts(run_id,account_id)
        ) PARTITION BY LIST(run_id);
        CREATE INDEX le_txn ON ledger_entries(run_id,txn_id);
        CREATE INDEX le_account ON ledger_entries(run_id,account_id,tick);

        CREATE TABLE firms (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            firm_id TEXT NOT NULL,
            name TEXT NOT NULL,
            founded_tick BIGINT NOT NULL,
            dissolved_tick BIGINT,
            sector TEXT NOT NULL,
            place_id TEXT NOT NULL,
            founder_id TEXT,
            ledger_account_id TEXT NOT NULL,
            productivity_bp INTEGER NOT NULL,
            capital_cents BIGINT NOT NULL DEFAULT 0,
            headcount INTEGER NOT NULL DEFAULT 0,
            is_public BOOLEAN NOT NULL DEFAULT FALSE,
            symbol TEXT,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,firm_id)
        );

        CREATE TABLE banks (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            bank_id TEXT NOT NULL,
            name TEXT NOT NULL,
            place_id TEXT NOT NULL,
            ledger_account_id TEXT NOT NULL,
            reserve_account_id TEXT NOT NULL,
            deposit_liability_account_id TEXT NOT NULL,
            capital_cents BIGINT NOT NULL,
            reserve_ratio_bp INTEGER NOT NULL,
            is_central BOOLEAN NOT NULL DEFAULT FALSE,
            status TEXT NOT NULL,
            founded_tick BIGINT NOT NULL,
            failed_tick BIGINT,
            PRIMARY KEY(run_id,bank_id)
        );

        GRANT SELECT ON ledger_accounts,ledger_entries,firms,banks TO polis_reader;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS banks,firms,ledger_entries,ledger_accounts CASCADE")
