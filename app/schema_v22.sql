-- Teeyooshun database schema, version 22 ("Mark again": one re-mark of a typed Revise recall).
-- Applied by app/db.py to a version 21 database, in one transaction. Two columns are added to recall_attempts; no
-- row changes. Back up the database first anyway.

BEGIN;

-- How many times the attempt has been marked again (the limit is one), and the first marking, kept when the second
-- one replaces the attempt's score and box move. NULL for an attempt that was never re-marked.
ALTER TABLE recall_attempts ADD COLUMN remark_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE recall_attempts ADD COLUMN first_result_json TEXT;

PRAGMA user_version = 22;

COMMIT;
