-- SatTrack ML Conjunction Scoring schema additions
-- Run in Supabase SQL editor AFTER schema_phase2.sql

ALTER TABLE conjunctions
    ADD COLUMN IF NOT EXISTS rcs_m2_primary        DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rcs_size_primary       TEXT,
    ADD COLUMN IF NOT EXISTS rcs_m2_secondary       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rcs_size_secondary     TEXT,
    ADD COLUMN IF NOT EXISTS conjunction_risk_label TEXT,
    ADD COLUMN IF NOT EXISTS risk_score             DOUBLE PRECISION;

ALTER TABLE conjunctions
    ADD COLUMN IF NOT EXISTS ml_conjunction_probability DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_conj_ml_prob
    ON conjunctions(ml_conjunction_probability DESC)
    WHERE ml_conjunction_probability IS NOT NULL;
