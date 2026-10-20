-- Schema v17: indexes for the parent dashboard (no new columns).
-- api_calls already logs the feature in `purpose` and the cache-creation tokens in
-- cache_creation_input_tokens, so the cost figures need nothing else.
BEGIN;
-- First attempts only (the dashboard counts nothing else).
CREATE INDEX IF NOT EXISTS idx_attempts_first_created ON attempts (created_at) WHERE parent_attempt_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_recall_created ON recall_attempts (created_at);
CREATE INDEX IF NOT EXISTS idx_hint_uses_created ON hint_uses (created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON attempt_events (event, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions (started_at);
CREATE INDEX IF NOT EXISTS idx_api_calls_purpose ON api_calls (purpose, created_at);
PRAGMA user_version = 17;
COMMIT;
