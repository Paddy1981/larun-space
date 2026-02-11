-- Migration: Fix function search_path and RLS policy warnings
-- Fixes 10 Supabase linter warnings:
--   1-9. Function search_path mutable on 9 public functions
--   10.  RLS policy always true on public.model_feedback INSERT

-- ============================================
-- Fix mutable search_path on all 9 functions
-- Setting search_path = '' prevents search_path hijacking attacks
-- ============================================

ALTER FUNCTION public.update_updated_at_column() SET search_path = '';
ALTER FUNCTION public.reset_monthly_usage() SET search_path = '';
ALTER FUNCTION public.increment_analysis_count() SET search_path = '';
ALTER FUNCTION public.can_user_analyze() SET search_path = '';
ALTER FUNCTION public.handle_new_user() SET search_path = '';
ALTER FUNCTION public.validate_api_key(text) SET search_path = '';
ALTER FUNCTION public.increment_user_stat(uuid, text, integer) SET search_path = '';
ALTER FUNCTION public.get_user_stats(uuid) SET search_path = '';
ALTER FUNCTION public.update_updated_at() SET search_path = '';

-- ============================================
-- Fix overly permissive RLS policy on model_feedback
-- Replace WITH CHECK (true) with proper user ownership check
-- ============================================

-- Drop the permissive policy
DROP POLICY IF EXISTS "Users can insert own feedback" ON public.model_feedback;

-- Recreate with proper ownership check
CREATE POLICY "Users can insert own feedback"
  ON public.model_feedback
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);
