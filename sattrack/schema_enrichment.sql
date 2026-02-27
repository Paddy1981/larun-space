-- LARUN SatTrack ML Enrichment Schema
-- Applied in Supabase SQL Editor AFTER the base schema.sql has been run:
-- https://supabase.com/dashboard/project/mwmbcfcvnkwegrjlauis/sql/new
--
-- Creates 3 supplemental tables:
--   satellite_enrichment  — rich physical/mission/debris metadata from SATCAT 67K catalog
--   maneuver_events       — rule-based maneuver detections from ML DB tle_features delta analysis
--   decay_predictions     — orbital decay estimates for low-perigee objects

-- ============================================
-- satellite_enrichment — rich satellite metadata
-- ============================================
CREATE TABLE IF NOT EXISTS satellite_enrichment (
  norad_id              INTEGER PRIMARY KEY REFERENCES satellites(norad_id) ON DELETE CASCADE,
  -- Physical
  rcs_m2                DOUBLE PRECISION,
  rcs_size_class        TEXT,
  launch_mass_kg        DOUBLE PRECISION,
  dry_mass_kg           DOUBLE PRECISION,
  power_bol_w           DOUBLE PRECISION,
  -- Orbit (pre-computed)
  orbit_type            TEXT,
  altitude_km           DOUBLE PRECISION,
  apogee_km             DOUBLE PRECISION,
  perigee_km            DOUBLE PRECISION,
  incl_deg              DOUBLE PRECISION,
  period_min            DOUBLE PRECISION,
  eccentricity          DOUBLE PRECISION,
  -- Mission
  constellation         TEXT,
  primary_purpose       TEXT,
  comm_bands_arr        TEXT[],
  throughput_gbps       DOUBLE PRECISION,
  propulsion_type       TEXT,
  design_life_yr        DOUBLE PRECISION,
  -- Debris risk (populated for DEBRIS / ROCKET BODY objects only)
  conjunction_risk      TEXT CHECK (conjunction_risk IN ('CRITICAL','HIGH','MEDIUM','LOW') OR conjunction_risk IS NULL),
  parent_norad          INTEGER,
  parent_object         TEXT,
  frag_event_name       TEXT,
  constellations_at_risk TEXT[],
  updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_enrichment_risk ON satellite_enrichment(conjunction_risk) WHERE conjunction_risk IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_enrichment_orbit ON satellite_enrichment(orbit_type, altitude_km);
CREATE INDEX IF NOT EXISTS idx_enrichment_constellation ON satellite_enrichment(constellation) WHERE constellation IS NOT NULL;

-- ============================================
-- maneuver_events — detected orbital maneuvers
-- ============================================
CREATE TABLE IF NOT EXISTS maneuver_events (
  id                    BIGSERIAL PRIMARY KEY,
  norad_id              INTEGER NOT NULL REFERENCES satellites(norad_id) ON DELETE CASCADE,
  detected_epoch        TIMESTAMPTZ NOT NULL,
  prior_epoch           TIMESTAMPTZ,
  maneuver_type         TEXT CHECK (maneuver_type IN ('inclination','altitude','phasing','circularization','deorbit','unknown')),
  confidence            DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
  delta_v_proxy         DOUBLE PRECISION,
  delta_inclination     DOUBLE PRECISION,
  delta_mean_motion     DOUBLE PRECISION,
  delta_eccentricity    DOUBLE PRECISION,
  detection_method      TEXT DEFAULT 'rule_based',
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_maneuver_dedup ON maneuver_events(norad_id, detected_epoch);
CREATE INDEX IF NOT EXISTS idx_maneuver_norad_time ON maneuver_events(norad_id, detected_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_maneuver_type ON maneuver_events(maneuver_type);

-- ============================================
-- decay_predictions — reentry forecast
-- ============================================
CREATE TABLE IF NOT EXISTS decay_predictions (
  norad_id              INTEGER PRIMARY KEY REFERENCES satellites(norad_id) ON DELETE CASCADE,
  perigee_km            DOUBLE PRECISION,
  decay_rate_km_per_day DOUBLE PRECISION,
  predicted_reentry     DATE,
  confidence_days       INTEGER,
  model_version         TEXT DEFAULT 'rule_v1',
  computed_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decay_reentry ON decay_predictions(predicted_reentry) WHERE predicted_reentry IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_decay_perigee ON decay_predictions(perigee_km) WHERE perigee_km < 400;

-- ============================================
-- Table comments
-- ============================================
COMMENT ON TABLE satellite_enrichment IS 'Rich satellite metadata from SATCAT 67K catalog — physical specs, mission data, debris risk labels';
COMMENT ON TABLE maneuver_events IS 'Rule-based maneuver detections from ML DB tle_features delta analysis';
COMMENT ON TABLE decay_predictions IS 'Orbital decay estimates for low-perigee objects — reentry prediction';
