-- SatTrack — Row Level Security for core tables
-- Run in Supabase SQL Editor (project mwmbcfcvnkwegrjlauis)
-- Safe to re-run: all statements are idempotent.
--
-- Strategy:
--   Public read-only tables (satellite catalogue, TLEs, weather, conjunctions,
--   enrichment, maneuver events, decay predictions) → anon + authenticated can SELECT.
--   Backend uses service_role key which bypasses RLS for all writes — no change needed.
--   source_health is internal → no public access.

-- ── satellites ────────────────────────────────────────────────────────────────
ALTER TABLE public.satellites ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_read_satellites" ON public.satellites;
CREATE POLICY "public_read_satellites" ON public.satellites
  FOR SELECT USING (true);

-- ── tle_history ───────────────────────────────────────────────────────────────
ALTER TABLE public.tle_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_read_tle_history" ON public.tle_history;
CREATE POLICY "public_read_tle_history" ON public.tle_history
  FOR SELECT USING (true);

-- ── space_weather ─────────────────────────────────────────────────────────────
ALTER TABLE public.space_weather ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_read_space_weather" ON public.space_weather;
CREATE POLICY "public_read_space_weather" ON public.space_weather
  FOR SELECT USING (true);

-- ── conjunctions ─────────────────────────────────────────────────────────────
ALTER TABLE public.conjunctions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_read_conjunctions" ON public.conjunctions;
CREATE POLICY "public_read_conjunctions" ON public.conjunctions
  FOR SELECT USING (true);

-- ── satellite_enrichment ──────────────────────────────────────────────────────
ALTER TABLE public.satellite_enrichment ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_read_satellite_enrichment" ON public.satellite_enrichment;
CREATE POLICY "public_read_satellite_enrichment" ON public.satellite_enrichment
  FOR SELECT USING (true);

-- ── maneuver_events ───────────────────────────────────────────────────────────
ALTER TABLE public.maneuver_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_read_maneuver_events" ON public.maneuver_events;
CREATE POLICY "public_read_maneuver_events" ON public.maneuver_events
  FOR SELECT USING (true);

-- ── decay_predictions ─────────────────────────────────────────────────────────
ALTER TABLE public.decay_predictions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_read_decay_predictions" ON public.decay_predictions;
CREATE POLICY "public_read_decay_predictions" ON public.decay_predictions
  FOR SELECT USING (true);

-- ── source_health (internal — no public access) ───────────────────────────────
ALTER TABLE public.source_health ENABLE ROW LEVEL SECURITY;
-- No SELECT policy → anon/authenticated users get no rows via direct Supabase client.
-- Backend service_role bypasses RLS and can still read/write freely.
