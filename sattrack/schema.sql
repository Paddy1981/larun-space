-- LARUN SatTrack Phase 1 Schema
-- Run once in Supabase SQL Editor:
-- https://supabase.com/dashboard/project/mwmbcfcvnkwegrjlauis/sql/new

-- ============================================
-- satellites — master catalog
-- ============================================
CREATE TABLE IF NOT EXISTS satellites (
  norad_id        INTEGER PRIMARY KEY,
  cospar_id       TEXT,
  name            TEXT NOT NULL,
  orbit_class     TEXT,          -- LEO, MEO, GEO, HEO, DEEP
  object_type     TEXT,          -- PAYLOAD, ROCKET BODY, DEBRIS, UNKNOWN
  status          TEXT,          -- active, inactive, decayed, unknown
  launch_date     DATE,
  decay_date      DATE,
  operator        TEXT,
  country         TEXT,
  source_flags    JSONB DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_satellites_orbit_class ON satellites(orbit_class);
CREATE INDEX IF NOT EXISTS idx_satellites_status ON satellites(status);
CREATE INDEX IF NOT EXISTS idx_satellites_name ON satellites USING gin(to_tsvector('english', name));

-- ============================================
-- tle_history — every ingested TLE
-- ============================================
CREATE TABLE IF NOT EXISTS tle_history (
  id              BIGSERIAL PRIMARY KEY,
  norad_id        INTEGER NOT NULL REFERENCES satellites(norad_id) ON DELETE CASCADE,
  epoch           TIMESTAMPTZ NOT NULL,
  source          TEXT NOT NULL,         -- celestrak, amsat, supplemental
  tle_line1       TEXT NOT NULL,
  tle_line2       TEXT NOT NULL,
  -- Parsed Keplerian elements
  inclination     DOUBLE PRECISION,
  eccentricity    DOUBLE PRECISION,
  raan            DOUBLE PRECISION,      -- right ascension of ascending node
  arg_perigee     DOUBLE PRECISION,
  mean_anomaly    DOUBLE PRECISION,
  mean_motion     DOUBLE PRECISION,      -- revs/day
  bstar           DOUBLE PRECISION,      -- drag term
  -- Quality
  quality_score   INTEGER DEFAULT 50 CHECK (quality_score BETWEEN 0 AND 100),
  is_current      BOOLEAN DEFAULT FALSE,
  ingested_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tle_history_norad_current ON tle_history(norad_id, is_current);
CREATE INDEX IF NOT EXISTS idx_tle_history_epoch ON tle_history(epoch DESC);
CREATE INDEX IF NOT EXISTS idx_tle_history_source ON tle_history(source);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tle_history_dedup ON tle_history(norad_id, epoch, source);

-- ============================================
-- space_weather — F10.7 and Kp time series
-- ============================================
CREATE TABLE IF NOT EXISTS space_weather (
  id              BIGSERIAL PRIMARY KEY,
  observed_at     TIMESTAMPTZ NOT NULL UNIQUE,
  kp_index        DOUBLE PRECISION,
  ap_index        DOUBLE PRECISION,
  f107_flux       DOUBLE PRECISION,
  source          TEXT DEFAULT 'noaa',
  ingested_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_space_weather_time ON space_weather(observed_at DESC);

-- ============================================
-- source_health — per-source monitoring
-- ============================================
CREATE TABLE IF NOT EXISTS source_health (
  id              BIGSERIAL PRIMARY KEY,
  source          TEXT NOT NULL,
  checked_at      TIMESTAMPTZ DEFAULT NOW(),
  status          TEXT NOT NULL CHECK (status IN ('ok', 'error', 'timeout', 'empty')),
  response_time_ms INTEGER,
  objects_returned INTEGER DEFAULT 0,
  freshest_epoch  TIMESTAMPTZ,
  error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_health_source_time ON source_health(source, checked_at DESC);

-- ============================================
-- current_tle view — latest TLE per satellite
-- ============================================
CREATE OR REPLACE VIEW current_tle AS
SELECT DISTINCT ON (t.norad_id)
  t.*,
  s.name,
  s.orbit_class,
  s.status AS satellite_status
FROM tle_history t
JOIN satellites s ON s.norad_id = t.norad_id
WHERE t.is_current = TRUE
ORDER BY t.norad_id, t.epoch DESC;

-- ============================================
-- refresh_current_tle — called after every TLE batch upsert
-- Sets is_current=FALSE on old rows, TRUE on latest epoch per norad_id
-- ============================================
CREATE OR REPLACE FUNCTION refresh_current_tle(p_norad_ids INTEGER[])
RETURNS VOID AS $$
BEGIN
  -- Mark all rows for these satellites as not current
  UPDATE tle_history
  SET is_current = FALSE
  WHERE norad_id = ANY(p_norad_ids);

  -- Mark the latest epoch per satellite as current
  UPDATE tle_history t
  SET is_current = TRUE
  FROM (
    SELECT DISTINCT ON (norad_id) id
    FROM tle_history
    WHERE norad_id = ANY(p_norad_ids)
    ORDER BY norad_id, epoch DESC
  ) latest
  WHERE t.id = latest.id;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- get_latest_source_health — used by /v1/status/sources
-- ============================================
CREATE OR REPLACE FUNCTION get_latest_source_health()
RETURNS TABLE (
  source TEXT,
  checked_at TIMESTAMPTZ,
  status TEXT,
  response_time_ms INTEGER,
  objects_returned INTEGER,
  freshest_epoch TIMESTAMPTZ,
  error_message TEXT
) AS $$
  SELECT DISTINCT ON (source)
    source, checked_at, status, response_time_ms,
    objects_returned, freshest_epoch, error_message
  FROM source_health
  ORDER BY source, checked_at DESC;
$$ LANGUAGE SQL;

-- ============================================
-- Auto-update satellites.updated_at
-- ============================================
CREATE OR REPLACE FUNCTION update_satellites_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_satellites_updated_at ON satellites;
CREATE TRIGGER trg_satellites_updated_at
  BEFORE UPDATE ON satellites
  FOR EACH ROW EXECUTE FUNCTION update_satellites_updated_at();
