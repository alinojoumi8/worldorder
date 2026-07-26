"""Exchange, venture, acquisition, and bankruptcy projections.

Revision ID: 0012_m3_capital
Revises: 0011_loan_close_tick
"""

from alembic import op

revision = "0012_m3_capital"
down_revision = "0011_loan_close_tick"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE securities
            ADD COLUMN listing_price_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN last_price_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN reference_price_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN ipo_round_id TEXT,
            ADD COLUMN lockup_until_tick BIGINT,
            ADD COLUMN status TEXT NOT NULL DEFAULT 'listed',
            ADD COLUMN halt_until_tick BIGINT,
            ADD COLUMN breaker_count INTEGER NOT NULL DEFAULT 0;

        ALTER TABLE holdings
            ADD COLUMN locked_qty BIGINT NOT NULL DEFAULT 0;

        CREATE TABLE orders (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            trader_id TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            limit_price_cents BIGINT,
            qty BIGINT NOT NULL,
            remaining_qty BIGINT NOT NULL,
            filled_qty BIGINT NOT NULL,
            status TEXT NOT NULL,
            submitted_tick BIGINT NOT NULL,
            submitted_seq BIGINT NOT NULL,
            ended_tick BIGINT,
            arrival_ordinal INTEGER NOT NULL,
            reserved_cents BIGINT NOT NULL,
            reserved_qty BIGINT NOT NULL,
            filled_notional_cents BIGINT NOT NULL,
            commission_cents BIGINT NOT NULL,
            flags JSONB NOT NULL,
            PRIMARY KEY(run_id,order_id)
        );
        CREATE INDEX or_book
            ON orders(run_id,symbol,side,limit_price_cents,submitted_seq)
            WHERE status IN ('open','partial');

        CREATE TABLE trades (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            trade_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            symbol TEXT NOT NULL,
            price_cents BIGINT NOT NULL,
            qty BIGINT NOT NULL,
            buy_order_id TEXT NOT NULL,
            sell_order_id TEXT NOT NULL,
            buyer_id TEXT NOT NULL,
            seller_id TEXT NOT NULL,
            aggressor TEXT NOT NULL,
            commission_buy_cents BIGINT NOT NULL,
            commission_sell_cents BIGINT NOT NULL,
            ledger_txn_id UUID NOT NULL,
            PRIMARY KEY(run_id,trade_id)
        ) PARTITION BY LIST(run_id);
        CREATE TABLE trades_default PARTITION OF trades DEFAULT;
        CREATE INDEX tr_sym_tick ON trades(run_id,symbol,tick);

        CREATE TABLE ohlcv (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            session_tick BIGINT NOT NULL,
            open_cents BIGINT NOT NULL,
            high_cents BIGINT NOT NULL,
            low_cents BIGINT NOT NULL,
            close_cents BIGINT NOT NULL,
            volume BIGINT NOT NULL,
            vwap_cents BIGINT,
            trades_n INTEGER NOT NULL,
            PRIMARY KEY(run_id,symbol,session_tick)
        );

        CREATE TABLE short_positions (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            trader_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            qty BIGINT NOT NULL,
            entry_price_cents BIGINT NOT NULL,
            collateral_cents BIGINT NOT NULL,
            opened_tick BIGINT NOT NULL,
            borrow_fee_bp INTEGER NOT NULL,
            margin_deadline_tick BIGINT,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,trader_id,symbol)
        );

        CREATE TABLE ipos (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            ipo_id TEXT NOT NULL,
            firm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            shares_offered BIGINT NOT NULL,
            primary_shares BIGINT NOT NULL,
            secondary_shares BIGINT NOT NULL,
            price_low_cents BIGINT NOT NULL,
            price_high_cents BIGINT NOT NULL,
            underwriter_bank_id TEXT NOT NULL,
            announced_tick BIGINT NOT NULL,
            book_close_tick BIGINT NOT NULL,
            indications JSONB NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,ipo_id)
        );

        CREATE TABLE startups (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            startup_id TEXT NOT NULL,
            firm_id TEXT NOT NULL,
            founder_id TEXT NOT NULL,
            thesis TEXT NOT NULL,
            sector TEXT NOT NULL,
            founded_tick BIGINT NOT NULL,
            initial_capital_cents BIGINT NOT NULL,
            burn_rate_cents BIGINT NOT NULL,
            runway_ticks BIGINT NOT NULL,
            revenue_ttm_cents BIGINT NOT NULL,
            total_raised_cents BIGINT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,startup_id)
        );

        CREATE TABLE vc_funds (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            fund_id TEXT NOT NULL,
            firm_id TEXT NOT NULL,
            gp_agent_id TEXT NOT NULL,
            committed_cents BIGINT NOT NULL,
            called_cents BIGINT NOT NULL,
            deployed_cents BIGINT NOT NULL,
            vintage_tick BIGINT NOT NULL,
            thesis TEXT NOT NULL,
            management_fee_bp INTEGER NOT NULL,
            carry_bp INTEGER NOT NULL,
            hurdle_bp INTEGER NOT NULL,
            lps JSONB NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,fund_id)
        );

        CREATE TABLE funding_rounds (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            round_id TEXT NOT NULL,
            startup_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            pre_money_cents BIGINT NOT NULL,
            amount_cents BIGINT NOT NULL,
            post_money_cents BIGINT NOT NULL,
            price_per_share_cents BIGINT NOT NULL,
            new_shares BIGINT NOT NULL,
            lead_investor_id TEXT NOT NULL,
            participants JSONB NOT NULL,
            option_pool_shares BIGINT NOT NULL,
            liq_pref_bp INTEGER NOT NULL,
            participating BOOLEAN NOT NULL,
            closed_tick BIGINT NOT NULL,
            PRIMARY KEY(run_id,round_id)
        );

        CREATE TABLE cap_table (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            firm_id TEXT NOT NULL,
            holder_id TEXT NOT NULL,
            share_class TEXT NOT NULL,
            shares BIGINT NOT NULL,
            invested_cents BIGINT NOT NULL,
            round_id TEXT,
            liq_pref_bp INTEGER NOT NULL,
            participating BOOLEAN NOT NULL,
            pro_rata BOOLEAN NOT NULL,
            conversion_price_cents BIGINT NOT NULL,
            PRIMARY KEY(run_id,firm_id,holder_id,share_class)
        );

        CREATE TABLE pitches (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            pitch_id TEXT NOT NULL,
            startup_id TEXT NOT NULL,
            founder_id TEXT NOT NULL,
            investor_id TEXT NOT NULL,
            ask_cents BIGINT NOT NULL,
            pre_money_ask_cents BIGINT NOT NULL,
            deck_text TEXT NOT NULL,
            made_tick BIGINT NOT NULL,
            status TEXT NOT NULL,
            conviction_bp INTEGER,
            valuation_view_cents BIGINT,
            verdict TEXT,
            PRIMARY KEY(run_id,pitch_id)
        );

        CREATE TABLE term_sheets (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            term_sheet_id TEXT NOT NULL,
            startup_id TEXT NOT NULL,
            investor_id TEXT NOT NULL,
            pre_money_cents BIGINT NOT NULL,
            amount_cents BIGINT NOT NULL,
            security TEXT NOT NULL,
            liq_pref_bp INTEGER NOT NULL,
            participating BOOLEAN NOT NULL,
            pro_rata BOOLEAN NOT NULL,
            board_seat BOOLEAN NOT NULL,
            option_pool_bp INTEGER NOT NULL,
            anti_dilution TEXT NOT NULL,
            issued_tick BIGINT NOT NULL,
            expires_tick BIGINT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,term_sheet_id)
        );

        CREATE TABLE acquisitions (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            deal_id TEXT NOT NULL,
            acquirer_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            offer_cents BIGINT NOT NULL,
            per_share_cents BIGINT NOT NULL,
            consideration TEXT NOT NULL,
            stock_ratio_bp INTEGER NOT NULL,
            premium_bp INTEGER NOT NULL,
            integration_mode TEXT NOT NULL,
            financing TEXT NOT NULL,
            proposed_tick BIGINT NOT NULL,
            expires_tick BIGINT NOT NULL,
            accepting_holders JSONB NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,deal_id)
        );

        CREATE TABLE bankruptcies (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            case_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            trigger TEXT NOT NULL,
            assets_cents BIGINT NOT NULL,
            liabilities_cents BIGINT NOT NULL,
            filed_tick BIGINT NOT NULL,
            stay_until_tick BIGINT NOT NULL,
            status TEXT NOT NULL,
            liquidation_tick BIGINT,
            estate_cents BIGINT NOT NULL,
            resolved_tick BIGINT,
            PRIMARY KEY(run_id,case_id)
        );

        CREATE TABLE bankruptcy_claims (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            claim_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            creditor_id TEXT NOT NULL,
            claim_cents BIGINT NOT NULL,
            priority_class INTEGER NOT NULL,
            collateral_ref TEXT,
            loan_id TEXT,
            paid_cents BIGINT NOT NULL,
            PRIMARY KEY(run_id,claim_id)
        );

        GRANT SELECT ON orders,trades,ohlcv,short_positions,ipos,startups,
            vc_funds,funding_rounds,cap_table,pitches,term_sheets,acquisitions,
            bankruptcies,bankruptcy_claims TO polis_reader;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS bankruptcy_claims,bankruptcies,acquisitions,
            term_sheets,pitches,cap_table,funding_rounds,vc_funds,startups,
            ipos,short_positions,ohlcv,trades,orders CASCADE;
        ALTER TABLE holdings DROP COLUMN IF EXISTS locked_qty;
        ALTER TABLE securities
            DROP COLUMN IF EXISTS breaker_count,
            DROP COLUMN IF EXISTS halt_until_tick,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS lockup_until_tick,
            DROP COLUMN IF EXISTS ipo_round_id,
            DROP COLUMN IF EXISTS reference_price_cents,
            DROP COLUMN IF EXISTS last_price_cents,
            DROP COLUMN IF EXISTS listing_price_cents;
        """
    )
