-- Migration: Enable RLS on all public tables missing Row Level Security
-- Fixes 5 Supabase security linter errors:
--   1. RLS disabled on public.users
--   2. RLS disabled on public.subscriptions
--   3. RLS disabled on public.analyses
--   4. RLS disabled on public.verification_tokens
--   5. Sensitive column (token) exposed on public.verification_tokens

-- ============================================
-- 1. public.users - Enable RLS
-- ============================================
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- Users can only read their own row
CREATE POLICY "Users can view own user record"
  ON public.users
  FOR SELECT
  USING (auth.uid() = id);

-- Users can update their own row
CREATE POLICY "Users can update own user record"
  ON public.users
  FOR UPDATE
  USING (auth.uid() = id);

-- ============================================
-- 2. public.subscriptions - Enable RLS
-- ============================================
-- RLS was defined in supabase-schema.sql but may not have been applied
ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

-- Policy may already exist from initial schema; use DO block to skip if so
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'subscriptions'
      AND policyname = 'Users can view their own subscriptions'
  ) THEN
    CREATE POLICY "Users can view their own subscriptions"
      ON public.subscriptions
      FOR SELECT
      USING (auth.uid() = user_id);
  END IF;
END
$$;

-- ============================================
-- 3. public.analyses - Enable RLS
-- ============================================
ALTER TABLE public.analyses ENABLE ROW LEVEL SECURITY;

-- Users can only read their own analyses
CREATE POLICY "Users can view own analyses"
  ON public.analyses
  FOR SELECT
  USING (auth.uid() = user_id);

-- Users can insert their own analyses
CREATE POLICY "Users can insert own analyses"
  ON public.analyses
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- Users can update their own analyses
CREATE POLICY "Users can update own analyses"
  ON public.analyses
  FOR UPDATE
  USING (auth.uid() = user_id);

-- Users can delete their own analyses
CREATE POLICY "Users can delete own analyses"
  ON public.analyses
  FOR DELETE
  USING (auth.uid() = user_id);

-- ============================================
-- 4. public.verification_tokens - Enable RLS
--    (Also fixes error #5: sensitive token column exposure)
-- ============================================
ALTER TABLE public.verification_tokens ENABLE ROW LEVEL SECURITY;

-- Only allow service_role access by default (no anon/authenticated access).
-- Verification tokens should be managed server-side only.
-- If user-facing access is needed, restrict by identifier/user_id:
CREATE POLICY "Service role only access to verification tokens"
  ON public.verification_tokens
  FOR ALL
  USING (auth.role() = 'service_role');
