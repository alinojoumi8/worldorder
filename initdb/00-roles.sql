CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'polis_engine') THEN
        CREATE ROLE polis_engine LOGIN PASSWORD 'polis';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'polis_reader') THEN
        CREATE ROLE polis_reader LOGIN PASSWORD 'polis_reader';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE polis TO polis_engine, polis_reader;
GRANT CREATE ON SCHEMA public TO polis_engine;
GRANT USAGE ON SCHEMA public TO polis_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE polis_engine IN SCHEMA public
    GRANT SELECT ON TABLES TO polis_reader;

