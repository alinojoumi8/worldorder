"""Create M2 labour, offer, employment, and inventory projections."""

from alembic import op

revision = "0007_labour_firms"
down_revision = "0006_economy_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE firms
            ADD COLUMN liquid_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN target_headcount INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN cumulative_output_units BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN cumulative_revenue_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN cumulative_wage_cents BIGINT NOT NULL DEFAULT 0;

        CREATE TABLE vacancies (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            vacancy_id TEXT NOT NULL,
            firm_id TEXT NOT NULL,
            posted_tick BIGINT NOT NULL,
            closed_tick BIGINT,
            expires_tick BIGINT NOT NULL,
            district_id TEXT NOT NULL,
            occupation TEXT NOT NULL,
            skill_reqs JSONB NOT NULL,
            wage_offer_cents BIGINT NOT NULL CHECK(wage_offer_cents >= 0),
            headcount INTEGER NOT NULL CHECK(headcount >= 0),
            min_match_score_bp INTEGER NOT NULL,
            applicants_n INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            filled_by TEXT,
            PRIMARY KEY(run_id,vacancy_id),
            FOREIGN KEY(run_id,firm_id) REFERENCES firms(run_id,firm_id)
        );
        CREATE INDEX vacancies_open
            ON vacancies(run_id,firm_id) WHERE status='open';

        CREATE TABLE job_applications (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            application_id TEXT NOT NULL,
            vacancy_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            asked_wage_cents BIGINT NOT NULL CHECK(asked_wage_cents >= 0),
            outcome TEXT NOT NULL,
            match_score_bp INTEGER,
            rank INTEGER,
            PRIMARY KEY(run_id,application_id),
            FOREIGN KEY(run_id,vacancy_id) REFERENCES vacancies(run_id,vacancy_id)
        );
        CREATE INDEX applications_agent
            ON job_applications(run_id,agent_id,tick);

        CREATE TABLE job_offers (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            offer_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            vacancy_id TEXT NOT NULL,
            firm_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            wage_cents BIGINT NOT NULL CHECK(wage_cents >= 0),
            occupation TEXT NOT NULL,
            made_tick BIGINT NOT NULL,
            expires_tick BIGINT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(run_id,offer_id),
            FOREIGN KEY(run_id,application_id)
                REFERENCES job_applications(run_id,application_id),
            FOREIGN KEY(run_id,vacancy_id) REFERENCES vacancies(run_id,vacancy_id),
            FOREIGN KEY(run_id,firm_id) REFERENCES firms(run_id,firm_id)
        );

        CREATE TABLE employments (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            employment_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            firm_id TEXT NOT NULL,
            occupation TEXT NOT NULL,
            wage_cents BIGINT NOT NULL CHECK(wage_cents >= 0),
            started_tick BIGINT NOT NULL,
            ended_tick BIGINT,
            end_reason TEXT,
            match_score_bp INTEGER NOT NULL,
            hours_bp INTEGER NOT NULL,
            accrued_wage_cents BIGINT NOT NULL,
            accrual_remainder BIGINT NOT NULL,
            total_paid_cents BIGINT NOT NULL,
            last_worked_tick BIGINT,
            last_effective_labour_bp INTEGER NOT NULL,
            PRIMARY KEY(run_id,employment_id),
            FOREIGN KEY(run_id,firm_id) REFERENCES firms(run_id,firm_id)
        );
        CREATE INDEX emp_agent
            ON employments(run_id,agent_id) WHERE ended_tick IS NULL;
        CREATE INDEX emp_firm
            ON employments(run_id,firm_id) WHERE ended_tick IS NULL;

        CREATE TABLE inventory (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            firm_id TEXT NOT NULL,
            sku TEXT NOT NULL,
            qty INTEGER NOT NULL CHECK(qty >= 0),
            unit_cost_cents BIGINT NOT NULL CHECK(unit_cost_cents >= 0),
            price_cents BIGINT NOT NULL CHECK(price_cents >= 1),
            carry_micro INTEGER NOT NULL CHECK(carry_micro BETWEEN 0 AND 999999),
            markup_bp INTEGER NOT NULL,
            units_sold_28d INTEGER NOT NULL,
            updated_tick BIGINT NOT NULL,
            PRIMARY KEY(run_id,firm_id,sku),
            FOREIGN KEY(run_id,firm_id) REFERENCES firms(run_id,firm_id)
        );

        GRANT SELECT ON vacancies,job_applications,job_offers,employments,inventory
            TO polis_reader;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS inventory,employments,job_offers,job_applications,vacancies CASCADE;
        ALTER TABLE firms
            DROP COLUMN IF EXISTS cumulative_wage_cents,
            DROP COLUMN IF EXISTS cumulative_revenue_cents,
            DROP COLUMN IF EXISTS cumulative_output_units,
            DROP COLUMN IF EXISTS target_headcount,
            DROP COLUMN IF EXISTS liquid_cents;
        """
    )
