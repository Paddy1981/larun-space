-- ============================================================
-- Supabase Migration: ML Output Tables
-- Apply via: Supabase Dashboard → SQL Editor → Run
-- Project: mwmbcfcvnkwegrjlauis (larun-space, Tokyo)
-- Date: 2026-02-23
-- ============================================================

-- 1. maneuver_events — rule-based + ML-detected satellite maneuvers
--    Pushed from local ML DB via push_to_supabase.py --maneuvers
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS maneuver_events (
  id                    BIGSERIAL PRIMARY KEY,
  norad_id              INTEGER NOT NULL REFERENCES satellites(norad_id) ON DELETE CASCADE,
  detected_epoch        TIMESTAMPTZ NOT NULL,
  prior_epoch           TIMESTAMPTZ,
  delta_inclination     DOUBLE PRECISION,
  delta_mean_motion     DOUBLE PRECISION,
  delta_eccentricity    DOUBLE PRECISION,
  delta_v_proxy         DOUBLE PRECISION,
  maneuver_type         TEXT CHECK (maneuver_type IN (
                          'inclination','altitude','phasing',
                          'circularization','deorbit','unknown'
                        )),
  confidence            DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
  detection_method      TEXT CHECK (detection_method IN (
                          'rule_based','ml_model','confirmed'
                        )) DEFAULT 'rule_based',
  created_at            TIMESTAMPTZ DEFAULT NOW(),

  CONSTRAINT maneuver_events_norad_epoch_unique UNIQUE (norad_id, detected_epoch)
);

CREATE INDEX IF NOT EXISTS idx_maneuver_norad_time
  ON maneuver_events(norad_id, detected_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_maneuver_type
  ON maneuver_events(maneuver_type);
CREATE INDEX IF NOT EXISTS idx_maneuver_confidence
  ON maneuver_events(confidence DESC);


-- 2. decay_predictions — re-entry / orbital decay estimates
--    One row per satellite (latest prediction). Upserted by norad_id.
--    Pushed from local ML DB via push_to_supabase.py --decay
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decay_predictions (
  norad_id              INTEGER PRIMARY KEY REFERENCES satellites(norad_id) ON DELETE CASCADE,
  perigee_km            DOUBLE PRECISION,
  decay_rate_km_per_day DOUBLE PRECISION,
  predicted_reentry     DATE,
  confidence_days       INTEGER,   -- ± window in days
  model_version         TEXT,
  updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decay_reentry
  ON decay_predictions(predicted_reentry)
  WHERE predicted_reentry IS NOT NULL;


-- 3. satellite_enrichment — physical + operational metadata
--    Sourced from catalog.objects + catalog.debris_enrichment in local ML DB.
--    Pushed via push_to_supabase.py --enrichment
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS satellite_enrichment (
  norad_id              INTEGER PRIMARY KEY REFERENCES satellites(norad_id) ON DELETE CASCADE,
  -- Physical
  rcs_m2                DOUBLE PRECISION,
  rcs_size_class        TEXT,
  launch_mass_kg        DOUBLE PRECISION,
  dry_mass_kg           DOUBLE PRECISION,
  power_bol_w           DOUBLE PRECISION,
  -- Orbit
  orbit_type            TEXT,
  altitude_km           DOUBLE PRECISION,
  apogee_km             DOUBLE PRECISION,
  perigee_km            DOUBLE PRECISION,
  incl_deg              DOUBLE PRECISION,
  period_min            DOUBLE PRECISION,
  eccentricity          DOUBLE PRECISION,
  -- Operational
  constellation         TEXT,
  primary_purpose       TEXT,
  comm_bands_arr        TEXT[],
  throughput_gbps       DOUBLE PRECISION,
  propulsion_type       TEXT,
  design_life_yr        DOUBLE PRECISION,
  -- Debris / conjunction risk
  conjunction_risk      TEXT CHECK (conjunction_risk IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  parent_norad          INTEGER,
  parent_object         TEXT,
  frag_event_name       TEXT,
  constellations_at_risk TEXT[],

  updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_enrichment_constellation
  ON satellite_enrichment(constellation);
CREATE INDEX IF NOT EXISTS idx_enrichment_conjunction_risk
  ON satellite_enrichment(conjunction_risk);
CREATE INDEX IF NOT EXISTS idx_enrichment_propulsion
  ON satellite_enrichment(propulsion_type);
