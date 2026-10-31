-- Patient Service: add the patient status / no-show policy (BR12) to an
-- existing patient_db.
--
-- The service creates missing tables on startup (Base.metadata.create_all),
-- but never alters a table that already exists. A patients table created
-- before BR12 therefore lacks status and no_show_count; this script adds them
-- and the patient_no_shows table. Safe to run more than once.
--
-- Usage:
--   psql -h localhost -U patient_svc -d patient_db -f scripts/migrate_patient_status.sql

BEGIN;

-- ACTIVE -> BLOCKED (automatically at the no-show limit), BLOCKED -> ACTIVE (unblock)
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';

-- No-shows since the patient was registered or last unblocked.
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS no_show_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE patients DROP CONSTRAINT IF EXISTS check_patient_status;
ALTER TABLE patients
    ADD CONSTRAINT check_patient_status CHECK (status IN ('ACTIVE', 'BLOCKED'));

ALTER TABLE patients DROP CONSTRAINT IF EXISTS check_no_show_count_nonnegative;
ALTER TABLE patients
    ADD CONSTRAINT check_no_show_count_nonnegative CHECK (no_show_count >= 0);

-- One row per missed appointment; the unique booking_id makes reporting it idempotent.
-- booking_id is owned by Booking Service, so there is no foreign key across databases.
CREATE TABLE IF NOT EXISTS patient_no_shows (
    id          SERIAL PRIMARY KEY,
    patient_id  INTEGER NOT NULL REFERENCES patients (id),
    booking_id  INTEGER NOT NULL UNIQUE,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_patient_no_shows_id ON patient_no_shows (id);
CREATE INDEX IF NOT EXISTS ix_patient_no_shows_patient_id ON patient_no_shows (patient_id);

COMMIT;
