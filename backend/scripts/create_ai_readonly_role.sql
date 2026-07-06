-- backend/scripts/create_ai_readonly_role.sql — Phase AI-5
-- ============================================================================
-- The NL→SQL feature executes LLM-generated SELECTs. The application-level
-- safety gate (backend/api/ai/safety.py) is the first wall; this role is the
-- second: a TRUE PostgreSQL read-only login the AI engine connects as, so
-- even a gate bypass physically cannot write, and a runaway query dies at
-- the role-level statement_timeout.
--
-- Run once per database (idempotent):
--   psql "$DATABASE_URL" -f backend/scripts/create_ai_readonly_role.sql
--
-- Local dev (trust auth): no password needed. Production: set one —
--   ALTER ROLE gi_ai_ro PASSWORD '...';
-- and point GI_AI_RO_URL at it (postgresql+asyncpg://gi_ai_ro:...@host/db).
-- ============================================================================

DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gi_ai_ro') THEN
      CREATE ROLE gi_ai_ro LOGIN;
   END IF;
END
$$;

-- Hard runtime caps for every connection this role makes.
ALTER ROLE gi_ai_ro SET statement_timeout = '5s';
ALTER ROLE gi_ai_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE gi_ai_ro SET default_transaction_read_only = 'on';

GRANT CONNECT ON DATABASE gihub TO gi_ai_ro;
GRANT USAGE ON SCHEMA public TO gi_ai_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO gi_ai_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO gi_ai_ro;

-- Defense-in-depth: the safety gate already blocks these table names in SQL
-- text; revoking makes the sensitive surfaces unreadable even on a bypass.
REVOKE SELECT ON users, pending_users, auth_sessions, ai_jobs FROM gi_ai_ro;
