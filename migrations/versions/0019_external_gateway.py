"""External-agent identity, protocol projections, and safe public views.

Revision ID: 0019_external_gateway
Revises: 0018_demography
"""

from alembic import op

revision = "0019_external_gateway"
down_revision = "0018_demography"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agents
            ADD COLUMN pubkey TEXT;
        CREATE UNIQUE INDEX agents_external_pubkey
            ON agents (run_id,pubkey) WHERE pubkey IS NOT NULL;

        CREATE TABLE external_agents (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            pubkey TEXT NOT NULL,
            operator TEXT NOT NULL,
            contact TEXT NOT NULL,
            display_name TEXT NOT NULL,
            declared_model TEXT NOT NULL,
            declared_model_version TEXT NOT NULL,
            declared_scaffold TEXT NOT NULL,
            scaffold_notes TEXT NOT NULL,
            memory TEXT NOT NULL CHECK (memory IN ('ours','ours+private')),
            sdk_version TEXT NOT NULL,
            protocol_version INTEGER NOT NULL,
            requested_embodiment TEXT
                CHECK (
                    requested_embodiment IN (
                        'cohort_matched','paired_control','adopt_existing'
                    )
                ),
            embodiment TEXT NOT NULL
                CHECK (
                    embodiment IN (
                        'cohort_matched','paired_control','adopt_existing'
                    )
                ),
            conformance_token TEXT,
            twin_agent_id TEXT,
            registered_tick BIGINT NOT NULL,
            admitted_tick BIGINT NOT NULL,
            revoked_tick BIGINT,
            naturalised_tick BIGINT,
            resume_grace_until_tick BIGINT,
            consecutive_misses INTEGER NOT NULL DEFAULT 0,
            ticks_driven BIGINT NOT NULL DEFAULT 0,
            actions_submitted BIGINT NOT NULL DEFAULT 0,
            actions_rejected BIGINT NOT NULL DEFAULT 0,
            deadlines_missed BIGINT NOT NULL DEFAULT 0,
            sim_aware_count BIGINT NOT NULL DEFAULT 0,
            strikes INTEGER NOT NULL DEFAULT 0,
            suspensions INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (run_id,agent_id),
            UNIQUE (run_id,pubkey),
            FOREIGN KEY (run_id,agent_id) REFERENCES agents(run_id,agent_id)
                ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX external_agents_pubkey_prefix
            ON external_agents (run_id,left(pubkey,16));
        CREATE INDEX external_agents_operator
            ON external_agents (run_id,operator,agent_id);

        CREATE TABLE external_sessions (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            session_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            custody TEXT NOT NULL CHECK (custody IN ('operator','delegated')),
            delegate_pubkey TEXT,
            client JSONB NOT NULL,
            opened_tick BIGINT NOT NULL,
            expires_unix_ms BIGINT NOT NULL,
            closed_tick BIGINT,
            close_reason TEXT,
            PRIMARY KEY (run_id,session_id),
            FOREIGN KEY (run_id,agent_id)
                REFERENCES external_agents(run_id,agent_id) ON DELETE CASCADE
        );
        CREATE INDEX external_sessions_agent
            ON external_sessions (run_id,agent_id,closed_tick);

        CREATE TABLE external_nonces (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            last_nonce BIGINT NOT NULL,
            updated_tick BIGINT NOT NULL,
            PRIMARY KEY (run_id,agent_id),
            FOREIGN KEY (run_id,agent_id)
                REFERENCES external_agents(run_id,agent_id) ON DELETE CASCADE
        );

        CREATE TABLE external_conformance_tokens (
            token_hash TEXT PRIMARY KEY,
            issued_unix_ms BIGINT NOT NULL,
            expires_unix_ms BIGINT NOT NULL,
            pubkey TEXT NOT NULL,
            sdk_version TEXT NOT NULL,
            protocol_version INTEGER NOT NULL CHECK (protocol_version = 1),
            checks JSONB NOT NULL,
            used_run_id UUID,
            used_agent_id TEXT
        );

        CREATE TABLE external_latency (
            run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            tick BIGINT NOT NULL,
            observation_pushed_ms BIGINT NOT NULL,
            action_received_ms BIGINT,
            decision_ms INTEGER,
            missed BOOLEAN NOT NULL,
            PRIMARY KEY (run_id,agent_id,tick)
        ) PARTITION BY LIST (run_id);
        CREATE TABLE external_latency_default
            PARTITION OF external_latency DEFAULT;

        CREATE VIEW v_agent_control AS
        SELECT a.run_id,a.agent_id,a.kind,
               CASE
                   WHEN a.kind = 'native' THEN 'native'
                   WHEN x.agent_id IS NOT NULL THEN 'operator'
                   ELSE 'native'
               END AS driver
        FROM agents a
        LEFT JOIN external_agents x
          ON x.run_id=a.run_id AND x.agent_id=a.agent_id;

        CREATE VIEW v_market_visible AS
        SELECT o.run_id,o.symbol,o.side,o.limit_price_cents AS price_cents,
               SUM(o.remaining_qty)::BIGINT AS qty,
               COUNT(*)::BIGINT AS orders_n,
               MAX(o.submitted_tick)::BIGINT AS as_of_tick
        FROM orders o
        WHERE o.status IN ('open','partial') AND o.limit_price_cents IS NOT NULL
        GROUP BY o.run_id,o.symbol,o.side,o.limit_price_cents;

        CREATE VIEW v_public_record AS
        SELECT p.run_id,p.post_id AS record_id,p.tick,'post'::TEXT AS kind,
               p.topic::TEXT AS title,p.text::TEXT AS body,p.author_id AS actor_id
        FROM posts p
        WHERE p.deleted_tick IS NULL
        UNION ALL
        SELECT a.run_id,a.article_id,a.tick,'article'::TEXT,
               a.headline::TEXT,a.body::TEXT,a.reporter_id
        FROM articles a
        WHERE a.retracted_tick IS NULL
        UNION ALL
        SELECT c.run_id,c.case_id,c.resolved_tick,'court_case'::TEXT,
               c.cause_of_action::TEXT,
               CONCAT('verdict: ',c.verdict,'; damages_cents: ',c.damages_cents)::TEXT,
               c.defendant_id
        FROM court_cases c
        WHERE c.resolved_tick IS NOT NULL AND c.verdict IS NOT NULL;

        GRANT SELECT ON external_agents,external_sessions,external_nonces,
            external_conformance_tokens,external_latency,v_agent_control,
            v_market_visible,v_public_record
            TO polis_reader;
        REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON external_agents,external_sessions,
            external_nonces,external_conformance_tokens,external_latency
            FROM polis_reader;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP VIEW IF EXISTS v_public_record;
        DROP VIEW IF EXISTS v_market_visible;
        DROP VIEW IF EXISTS v_agent_control;
        DROP TABLE IF EXISTS external_latency CASCADE;
        DROP TABLE IF EXISTS external_conformance_tokens;
        DROP TABLE IF EXISTS external_nonces;
        DROP TABLE IF EXISTS external_sessions;
        DROP TABLE IF EXISTS external_agents;
        DROP INDEX IF EXISTS agents_external_pubkey;
        ALTER TABLE agents DROP COLUMN IF EXISTS pubkey;
        """
    )
