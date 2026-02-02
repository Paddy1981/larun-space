-- Migration: User Statistics and Activity Tracking
-- Description: Creates tables for user-specific stats and activity logging
-- Run this in your Supabase SQL Editor

-- ============================================
-- USER STATISTICS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS public.user_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    objects_processed INTEGER DEFAULT 0,
    detections INTEGER DEFAULT 0,
    vetted_candidates INTEGER DEFAULT 0,
    high_confidence_detections INTEGER DEFAULT 0,
    total_inference_time_ms NUMERIC DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id)
);

-- Index for fast user lookups
CREATE INDEX IF NOT EXISTS idx_user_stats_user_id ON public.user_stats(user_id);

-- Enable Row Level Security
ALTER TABLE public.user_stats ENABLE ROW LEVEL SECURITY;

-- Users can only see and modify their own stats
CREATE POLICY "Users can view own stats" ON public.user_stats
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own stats" ON public.user_stats
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own stats" ON public.user_stats
    FOR UPDATE USING (auth.uid() = user_id);


-- ============================================
-- ACTIVITY LOG TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS public.activity_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    activity_type TEXT NOT NULL CHECK (activity_type IN ('detection', 'vetting', 'calibration', 'report', 'pipeline', 'export')),
    title TEXT NOT NULL,
    description TEXT,
    source TEXT DEFAULT 'Larun',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_activity_log_user_id ON public.activity_log(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_created_at ON public.activity_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_log_type ON public.activity_log(activity_type);

-- Enable Row Level Security
ALTER TABLE public.activity_log ENABLE ROW LEVEL SECURITY;

-- Users can only see and create their own activity
CREATE POLICY "Users can view own activity" ON public.activity_log
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own activity" ON public.activity_log
    FOR INSERT WITH CHECK (auth.uid() = user_id);


-- ============================================
-- HELPER FUNCTIONS
-- ============================================

-- Function to increment a user stat
CREATE OR REPLACE FUNCTION public.increment_user_stat(
    p_user_id UUID,
    p_stat_name TEXT,
    p_increment INTEGER DEFAULT 1
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- Insert or update user stats
    INSERT INTO public.user_stats (user_id, objects_processed, detections, vetted_candidates)
    VALUES (p_user_id, 0, 0, 0)
    ON CONFLICT (user_id) DO NOTHING;

    -- Update the specific stat
    IF p_stat_name = 'objects_processed' THEN
        UPDATE public.user_stats
        SET objects_processed = objects_processed + p_increment,
            updated_at = NOW()
        WHERE user_id = p_user_id;
    ELSIF p_stat_name = 'detections' THEN
        UPDATE public.user_stats
        SET detections = detections + p_increment,
            updated_at = NOW()
        WHERE user_id = p_user_id;
    ELSIF p_stat_name = 'vetted_candidates' THEN
        UPDATE public.user_stats
        SET vetted_candidates = vetted_candidates + p_increment,
            updated_at = NOW()
        WHERE user_id = p_user_id;
    ELSIF p_stat_name = 'high_confidence_detections' THEN
        UPDATE public.user_stats
        SET high_confidence_detections = high_confidence_detections + p_increment,
            updated_at = NOW()
        WHERE user_id = p_user_id;
    END IF;
END;
$$;

-- Function to get user stats (creates default if not exists)
CREATE OR REPLACE FUNCTION public.get_user_stats(p_user_id UUID)
RETURNS TABLE (
    objects_processed INTEGER,
    detections INTEGER,
    vetted_candidates INTEGER,
    high_confidence_detections INTEGER,
    total_inference_time_ms NUMERIC
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- Ensure user has a stats row
    INSERT INTO public.user_stats (user_id)
    VALUES (p_user_id)
    ON CONFLICT (user_id) DO NOTHING;

    -- Return the stats
    RETURN QUERY
    SELECT
        us.objects_processed,
        us.detections,
        us.vetted_candidates,
        us.high_confidence_detections,
        us.total_inference_time_ms
    FROM public.user_stats us
    WHERE us.user_id = p_user_id;
END;
$$;


-- ============================================
-- TRIGGER: Auto-update updated_at
-- ============================================
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER user_stats_updated_at
    BEFORE UPDATE ON public.user_stats
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at();


-- ============================================
-- GRANT PERMISSIONS
-- ============================================
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT ALL ON public.user_stats TO authenticated;
GRANT ALL ON public.activity_log TO authenticated;
GRANT EXECUTE ON FUNCTION public.increment_user_stat TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_user_stats TO authenticated;
