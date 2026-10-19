-- Teeyooshun database schema, version 11 (session planner: Quick-fire step).
-- Applied by app/db.py to a version 10 database, in one transaction.

BEGIN;

-- A set whose cards are all recalled by self-tick (no typed check, no API call): the session's Quick-fire step.
ALTER TABLE revise_sets ADD COLUMN self_tick INTEGER NOT NULL DEFAULT 0;

PRAGMA user_version = 11;

COMMIT;
