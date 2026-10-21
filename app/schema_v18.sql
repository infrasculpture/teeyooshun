-- Schema v18: session accountability (how a session ended, idle gaps, breaks, blank submits).
-- Applied by app/db.py to a version 17 database, in one transaction. New columns only; nothing is rewritten.
-- The page pings and the idle log write these columns on their own, never plan_json, so a ping can't
-- overwrite a step that another request has just saved.
BEGIN;

ALTER TABLE sessions ADD COLUMN end_state TEXT;                              -- finished / timed_out / continued / abandoned (NULL while running)
ALTER TABLE sessions ADD COLUMN last_active_at TEXT;                         -- the last sign of activity (a page request or a ping)
ALTER TABLE sessions ADD COLUMN idle_seconds INTEGER NOT NULL DEFAULT 0;     -- total of the gaps in idle_json
ALTER TABLE sessions ADD COLUMN idle_json TEXT NOT NULL DEFAULT '[]';        -- [{"from", "to", "seconds"}] for every gap over 3 minutes
ALTER TABLE sessions ADD COLUMN break_from TEXT;                             -- set while she is on the break screen between blocks
ALTER TABLE sessions ADD COLUMN break_seconds INTEGER NOT NULL DEFAULT 0;    -- finished breaks (not idle, not study time)
ALTER TABLE sessions ADD COLUMN blank_submits INTEGER NOT NULL DEFAULT 0;    -- times she pressed Submit with nothing written

-- Sessions that already exist: the ones that ended, ended normally; the last activity is the end (or the start).
UPDATE sessions SET end_state = 'finished' WHERE ended_at IS NOT NULL;
UPDATE sessions SET last_active_at = COALESCE(ended_at, started_at);

PRAGMA user_version = 18;

COMMIT;
