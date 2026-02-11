-- Migration: Fix function search_path and RLS policy warnings
--
-- STEP 1: Run the discovery query below in the SQL Editor to get the
--         exact signatures for the 4 functions not defined in the repo.
-- STEP 2: Run the ALTER FUNCTION statements for all 9 functions.
-- STEP 3: Run the RLS policy fix at the bottom.
--
-- ============================================
-- STEP 1 - Discovery query (run this first, then update STEP 2)
-- ============================================
-- Copy-paste this into the SQL Editor to find the parameter signatures:
--
--   SELECT p.proname AS function_name,
--          pg_catalog.pg_get_function_identity_arguments(p.oid) AS params
--   FROM pg_catalog.pg_proc p
--   JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
--   WHERE n.nspname = 'public'
--     AND p.proname IN (
--       'update_updated_at_column',
--       'reset_monthly_usage',
--       'increment_analysis_count',
--       'can_user_analyze'
--     );
--
-- Then fill in the correct signatures in the 4 lines marked TODO below.

-- ============================================
-- STEP 2 - Fix mutable search_path on all 9 functions
-- Setting search_path = '' prevents search_path hijacking attacks
-- ============================================

-- These 5 have known signatures from the repo:
ALTER FUNCTION public.handle_new_user() SET search_path = '';
ALTER FUNCTION public.validate_api_key(text) SET search_path = '';
ALTER FUNCTION public.increment_user_stat(uuid, text, integer) SET search_path = '';
ALTER FUNCTION public.get_user_stats(uuid) SET search_path = '';
ALTER FUNCTION public.update_updated_at() SET search_path = '';

-- TODO: Update these 4 with the correct parameter types from STEP 1:
-- ALTER FUNCTION public.update_updated_at_column(/* params from discovery */) SET search_path = '';
-- ALTER FUNCTION public.reset_monthly_usage(/* params from discovery */) SET search_path = '';
-- ALTER FUNCTION public.increment_analysis_count(/* params from discovery */) SET search_path = '';
-- ALTER FUNCTION public.can_user_analyze(/* params from discovery */) SET search_path = '';

-- ============================================
-- STEP 3 - Fix overly permissive RLS policy on model_feedback
-- Replace WITH CHECK (true) with proper user ownership check
-- ============================================

DROP POLICY IF EXISTS "Users can insert own feedback" ON public.model_feedback;

CREATE POLICY "Users can insert own feedback"
  ON public.model_feedback
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);
