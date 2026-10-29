-- Schema v20: a question can be retired.
-- Applied by app/db.py to a version 19 database, in one transaction. One new column, default 0 (not retired),
-- so no existing row changes. Back up the database first anyway.
--
-- A retired question stays in the table, with its versions and every attempt at it (the dashboard and her
-- history still show it), but practice, sessions and the generator's style examples skip it. Set by
-- python -m app.ingest.unapprove; cleared when the corrected item is approved again (import_questions).
BEGIN;

ALTER TABLE questions ADD COLUMN retired INTEGER NOT NULL DEFAULT 0;

PRAGMA user_version = 20;

COMMIT;
