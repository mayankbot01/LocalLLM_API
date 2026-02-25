-- ============================================================
-- supabase_schema.sql - LocalLLM_API
-- Run this ONCE in Supabase SQL Editor:
--   supabase.com → Your Project → SQL Editor → New query → paste → Run
-- ============================================================

-- Enable UUID extension (already enabled in Supabase by default)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- TABLE: api_keys
-- Stores hashed API keys + limits + monthly usage tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS public.api_keys (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash             TEXT        NOT NULL UNIQUE,   -- SHA-256 of raw key (never store plain text)
    label                TEXT        NOT NULL,           -- human-readable name, e.g. "my-website"
    owner_email          TEXT,
    rate_limit_per_min   INTEGER     NOT NULL DEFAULT 20,
    monthly_token_limit  BIGINT      NOT NULL DEFAULT 1000000,
    tokens_used_month    BIGINT      NOT NULL DEFAULT 0,
    month_reset_at       TIMESTAMPTZ NOT NULL DEFAULT date_trunc('month', NOW()) + INTERVAL '1 month',
    is_active            BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at         TIMESTAMPTZ
);

-- Index for fast lookup by hash (most common query)
CREATE INDEX IF NOT EXISTS idx_api_keys_hash
    ON public.api_keys (key_hash)
    WHERE is_active = TRUE;

-- ============================================================
-- TABLE: usage_logs
-- One row per API request — used for analytics + billing
-- ============================================================
CREATE TABLE IF NOT EXISTS public.usage_logs (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id        UUID        REFERENCES public.api_keys(id) ON DELETE CASCADE,
    model             TEXT,
    prompt_tokens     INTEGER     NOT NULL DEFAULT 0,
    completion_tokens INTEGER     NOT NULL DEFAULT 0,
    total_tokens      INTEGER     NOT NULL DEFAULT 0,
    endpoint          TEXT,
    response_time_ms  FLOAT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for per-key usage queries
CREATE INDEX IF NOT EXISTS idx_usage_logs_key_id
    ON public.usage_logs (api_key_id, created_at DESC);

-- ============================================================
-- ROW LEVEL SECURITY
-- Backend uses service_role key which bypasses RLS.
-- Enable RLS so the anon key can NEVER read api_keys.
-- ============================================================
ALTER TABLE public.api_keys  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_logs ENABLE ROW LEVEL SECURITY;
-- No public policies — only service_role can access these tables.

-- ============================================================
-- STORED PROCEDURE: increment_tokens   (BUG-5 FIX)
-- ============================================================
-- Called by database.py to atomically increment the monthly
-- token counter and handle month rollovers.
--
-- Why this matters:
--   The naive Python approach (SELECT → arithmetic → UPDATE)
--   has a TOCTOU race condition: two concurrent requests both
--   read the same counter, then each overwrites it with their
--   own increment, silently losing one update.
--   A single SQL UPDATE is atomic and avoids the race.
-- ============================================================
CREATE OR REPLACE FUNCTION public.increment_tokens(key_id UUID, amount BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER   -- runs as table owner, not the calling role
AS $$
DECLARE
    now_utc   TIMESTAMPTZ := NOW();
    reset_ts  TIMESTAMPTZ;
BEGIN
    -- Read the current reset timestamp
    SELECT month_reset_at INTO reset_ts
    FROM public.api_keys
    WHERE id = key_id;

    IF now_utc >= reset_ts THEN
        -- Month has rolled over: reset counter and advance reset date
        UPDATE public.api_keys
        SET tokens_used_month = amount,
            month_reset_at    = date_trunc('month', now_utc) + INTERVAL '1 month'
        WHERE id = key_id;
    ELSE
        -- Normal case: atomic increment (no read-modify-write race)
        UPDATE public.api_keys
        SET tokens_used_month = tokens_used_month + amount
        WHERE id = key_id;
    END IF;
END;
$$;

-- Grant execute to the service_role (used by the FastAPI backend)
GRANT EXECUTE ON FUNCTION public.increment_tokens(UUID, BIGINT) TO service_role;

-- ============================================================
-- OPTIONAL VIEW: key_usage_summary  (useful in Supabase dashboard)
-- ============================================================
CREATE OR REPLACE VIEW public.key_usage_summary AS
SELECT
    k.id,
    k.label,
    k.owner_email,
    k.rate_limit_per_min,
    k.monthly_token_limit,
    k.tokens_used_month,
    ROUND(k.tokens_used_month::NUMERIC / NULLIF(k.monthly_token_limit, 0) * 100, 2) AS usage_pct,
    k.month_reset_at,
    k.is_active,
    k.created_at,
    k.last_used_at,
    COUNT(l.id)          AS total_requests,
    SUM(l.total_tokens)  AS lifetime_tokens
FROM public.api_keys k
LEFT JOIN public.usage_logs l ON l.api_key_id = k.id
GROUP BY k.id;
