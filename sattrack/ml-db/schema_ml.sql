-- SatTrack ML Local Database Schema
-- Apply with: psql -U sattrack_ml -d sattrack_ml -f schema_ml.sql
-- Or auto-applied on first docker compose up via initdb.d mount.

-- ============================================================
-- BASE TABLES  (mirrors Supabase schema.sql exactly)
-- ============================================================

-- satellites — master catalog
CREATE TABLE IF NOT EXISTS satellites (
  norad_id        INTEGER PRIMARY KEY,
  cospar_id       TEXT,
  name            TEXT NOT NULL,
  orbit_class     TEXT,
  object_type     TEXT,
  status          TEXT,
  launch_date     DATE,
  decay_date      DATE,
  operator        TEXT,
  country         TEXT,
  source_flags    JSONB DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_satellites_orbit_class ON satellites(orbit_class);
CREATE INDEX IF NOT EXISTS idx_satellites_status      ON satellites(status);

-- tle_history — every ingested TLE
CREATE TABLE IF NOT EXISTS tle_history (
  id              BIGSERIAL PRIMARY KEY,
  norad_id        INTEGER NOT NULL REFERENCES satellites(norad_id) ON DELETE CASCADE,
  epoch           TIMESTAMPTZ NOT NULL,
  source          TEXT NOT NULL,
  tle_line1       TEXT NOT NULL,
  tle_line2       TEXT NOT NULL,
  inclination     DOUBLE PRECISION,
  eccentricity    DOUBLE PRECISION,
  raan            DOUBLE PRECISION,
  arg_perigee     DOUBLE PRECISION,
  mean_anomaly    DOUBLE PRECISION,
  mean_motion     DOUBLE PRECISION,  -- rev/day
  bstar           DOUBLE PRECISION,
  quality_score   INTEGER DEFAULT 50 CHECK (quality_score BETWEEN 0 AND 100),
  is_current      BOOLEAN DEFAULT FALSE,
  ingested_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tle_norad_epoch   ON tle_history(norad_id, epoch DESC);
CREATE INDEX IF NOT EXISTS idx_tle_epoch         ON tle_history(epoch DESC);
CREATE INDEX IF NOT EXISTS idx_tle_source        ON tle_history(source);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tle_dedup  ON tle_history(norad_id, epoch, source);

-- space_weather — F10.7 and Kp time series
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


-- ============================================================
-- ML TABLES
-- ============================================================

-- tle_features — pre-computed feature vector, one row per TLE
CREATE TABLE IF NOT EXISTS tle_features (
  norad_id              INTEGER NOT NULL REFERENCES satellites(norad_id) ON DELETE CASCADE,
  epoch                 TIMESTAMPTZ NOT NULL,

  -- Derived orbital elements
  sma_km                DOUBLE PRECISION,  -- semi-major axis (km)
  perigee_km            DOUBLE PRECISION,  -- perigee altitude above WGS84 (km)
  apogee_km             DOUBLE PRECISION,  -- apogee altitude above WGS84 (km)
  period_min            DOUBLE PRECISION,  -- orbital period (minutes)

  -- Delta features (change from previous TLE, rate per hour)
  dt_hours              DOUBLE PRECISION,  -- time gap from prior TLE
  d_inclination         DOUBLE PRECISION,  -- deg/hr
  d_eccentricity        DOUBLE PRECISION,  -- per hour
  d_raan                DOUBLE PRECISION,  -- deg/hr
  d_arg_perigee         DOUBLE PRECISION,  -- deg/hr
  d_mean_motion         DOUBLE PRECISION,  -- rev/day per hour
  d_bstar               DOUBLE PRECISION,  -- per hour

  -- Space weather joined at epoch (nearest within ±1h)
  kp_at_epoch           DOUBLE PRECISION,
  f107_at_epoch         DOUBLE PRECISION,
  ap_at_epoch           DOUBLE PRECISION,

  -- ML labels
  is_maneuver           BOOLEAN,   -- NULL = unlabeled, TRUE = maneuver detected
  maneuver_confidence   DOUBLE PRECISION CHECK (maneuver_confidence IS NULL OR
                                                 maneuver_confidence BETWEEN 0 AND 1),

  computed_at           TIMESTAMPTZ DEFAULT NOW(),

  PRIMARY KEY (norad_id, epoch)
);

CREATE INDEX IF NOT EXISTS idx_tle_features_norad_epoch  ON tle_features(norad_id, epoch DESC);
CREATE INDEX IF NOT EXISTS idx_tle_features_is_maneuver  ON tle_features(is_maneuver)
  WHERE is_maneuver IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tle_features_epoch        ON tle_features(epoch DESC);


-- maneuver_events — one row per detected maneuver event
CREATE TABLE IF NOT EXISTS maneuver_events (
  id                    BIGSERIAL PRIMARY KEY,
  norad_id              INTEGER NOT NULL REFERENCES satellites(norad_id) ON DELETE CASCADE,
  detected_epoch        TIMESTAMPTZ NOT NULL,  -- TLE epoch where maneuver was detected
  prior_epoch           TIMESTAMPTZ,           -- previous TLE epoch
  delta_inclination     DOUBLE PRECISION,      -- magnitude of inclination change (deg)
  delta_mean_motion     DOUBLE PRECISION,      -- change in rev/day
  delta_eccentricity    DOUBLE PRECISION,
  delta_v_proxy         DOUBLE PRECISION,      -- estimated Δv proxy (sqrt sum of squares)
  maneuver_type         TEXT CHECK (maneuver_type IN (
                          'inclination', 'altitude', 'phasing',
                          'circularization', 'deorbit', 'unknown'
                        )),
  confidence            DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
  detection_method      TEXT CHECK (detection_method IN (
                          'rule_based', 'ml_model', 'confirmed'
                        )) DEFAULT 'rule_based',
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_maneuver_norad_epoch
  ON maneuver_events(norad_id, detected_epoch);
CREATE INDEX IF NOT EXISTS idx_maneuver_norad_time
  ON maneuver_events(norad_id, detected_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_maneuver_type
  ON maneuver_events(maneuver_type);


-- decay_predictions — re-entry prediction log
CREATE TABLE IF NOT EXISTS decay_predictions (
  id                    BIGSERIAL PRIMARY KEY,
  norad_id              INTEGER NOT NULL REFERENCES satellites(norad_id) ON DELETE CASCADE,
  prediction_date       DATE NOT NULL,
  perigee_km            DOUBLE PRECISION,
  decay_rate_km_per_day DOUBLE PRECISION,
  predicted_reentry     DATE,
  confidence_days       INTEGER,  -- ± window in days
  model_version         TEXT,
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decay_norad ON decay_predictions(norad_id, prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_decay_reentry ON decay_predictions(predicted_reentry)
  WHERE predicted_reentry IS NOT NULL;
