-- Teeyooshun database schema, version 12 (tutor explanation on attempts).
-- Applied by app/db.py to a version 11 database, in one transaction.

BEGIN;

-- The tutor-explanation JSON for an attempt (app/marking/tutor.py): written once after marking, NULL until then.
-- It never feeds back into the score, the confidence or the calibration.
ALTER TABLE attempts ADD COLUMN tutor_json TEXT;
ALTER TABLE attempts ADD COLUMN tutor_model TEXT;
ALTER TABLE attempts ADD COLUMN tutor_at TEXT;

PRAGMA user_version = 12;

COMMIT;
