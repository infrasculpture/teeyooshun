-- Teeyooshun database schema, version 4 (page images for questions).
-- Applied by app/db.py to a version 3 database, in one transaction.

BEGIN;

-- A sharper copy of each question page, shown on the question and review screens.
ALTER TABLE pages ADD COLUMN display_path TEXT;

-- text / table / draw_or_label. Only draw_or_label is left out of typed practice.
ALTER TABLE questions ADD COLUMN answer_format TEXT NOT NULL DEFAULT 'text';

-- Worked examples are left out of practice: their page shows the solution.
ALTER TABLE questions ADD COLUMN worked_example INTEGER NOT NULL DEFAULT 0;

PRAGMA user_version = 4;

COMMIT;
