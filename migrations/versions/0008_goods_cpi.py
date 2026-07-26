"""Create M2 SKU, goods transaction, CPI, and skill projections."""

from alembic import op

revision = "0008_goods_cpi"
down_revision = "0007_labour_firms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE skus (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            sku TEXT NOT NULL,
            category TEXT NOT NULL,
            is_necessity BOOLEAN NOT NULL,
            base_utility_bp INTEGER NOT NULL,
            perishable_bp_per_day INTEGER NOT NULL,
            durable_life_ticks BIGINT,
            is_service BOOLEAN NOT NULL,
            is_capital BOOLEAN NOT NULL,
            need_restore_bp JSONB NOT NULL,
            gamma_units_per_year INTEGER NOT NULL,
            beta_bp INTEGER NOT NULL,
            sectors TEXT[] NOT NULL,
            yield_units INTEGER NOT NULL,
            PRIMARY KEY(run_id,sku)
        );

        CREATE TABLE goods_transactions (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            txn_id TEXT NOT NULL,
            ledger_txn_id UUID NOT NULL,
            tick BIGINT NOT NULL,
            buyer_id TEXT NOT NULL,
            seller_firm_id TEXT NOT NULL,
            sku TEXT NOT NULL,
            qty INTEGER NOT NULL CHECK(qty > 0),
            unit_price_cents BIGINT NOT NULL CHECK(unit_price_cents > 0),
            gross_cents BIGINT NOT NULL CHECK(gross_cents > 0),
            sales_tax_cents BIGINT NOT NULL CHECK(sales_tax_cents >= 0),
            subsidy_cents BIGINT NOT NULL CHECK(subsidy_cents >= 0),
            PRIMARY KEY(run_id,txn_id),
            FOREIGN KEY(run_id,seller_firm_id) REFERENCES firms(run_id,firm_id),
            FOREIGN KEY(run_id,sku) REFERENCES skus(run_id,sku)
        );
        CREATE INDEX gt_tick ON goods_transactions(run_id,tick);
        CREATE INDEX gt_sku ON goods_transactions(run_id,sku,tick);

        CREATE TABLE cpi_baskets (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            fixed_tick BIGINT NOT NULL,
            quantities JSONB NOT NULL,
            base_prices_cents JSONB NOT NULL,
            PRIMARY KEY(run_id,version)
        );

        CREATE TABLE cpi_series (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            tick BIGINT NOT NULL,
            index_bp INTEGER NOT NULL,
            core_bp INTEGER NOT NULL,
            fisher_bp INTEGER NOT NULL,
            category_index_bp JSONB NOT NULL,
            PRIMARY KEY(run_id,tick)
        );

        CREATE TABLE agent_skills (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            skill TEXT NOT NULL,
            level_bp INTEGER NOT NULL CHECK(level_bp BETWEEN 0 AND 10000),
            last_used_tick BIGINT NOT NULL,
            PRIMARY KEY(run_id,agent_id,skill)
        );

        GRANT SELECT ON skus,goods_transactions,cpi_baskets,cpi_series,agent_skills
            TO polis_reader;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS agent_skills,cpi_series,cpi_baskets,goods_transactions,skus
            CASCADE;
        """
    )
