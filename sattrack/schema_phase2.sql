-- SatTrack Phase 2 schema additions
-- Run in Supabase SQL editor after schema.sql

CREATE TABLE IF NOT EXISTS conjunctions (
  id                     BIGSERIAL PRIMARY KEY,
  norad_id_1             INTEGER NOT NULL REFERENCES satellites(norad_id),
  norad_id_2             INTEGER NOT NULL REFERENCES satellites(norad_id),
  tca_time               TIMESTAMPTZ NOT NULL,
  miss_distance_km       DOUBLE PRECISION NOT NULL,
  relative_velocity_km_s DOUBLE PRECISION NOT NULL,
  probability            DOUBLE PRECISION,           -- reserved for Phase 3
  screening_window_hrs   INTEGER DEFAULT 24,
  computed_at            TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT conjunctions_unique UNIQUE (norad_id_1, norad_id_2, tca_time)
);

CREATE INDEX IF NOT EXISTS idx_conj_tca_time  ON conjunctions(tca_time DESC);
CREATE INDEX IF NOT EXISTS idx_conj_miss_dist ON conjunctions(miss_distance_km ASC);
CREATE INDEX IF NOT EXISTS idx_conj_norad1    ON conjunctions(norad_id_1);
CREATE INDEX IF NOT EXISTS idx_conj_norad2    ON conjunctions(norad_id_2);
