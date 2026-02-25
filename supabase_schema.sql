-- ============================================================
-- supabase_schema.sql
-- Run this in Supabase SQL Editor (supabase.com → SQL Editor)
-- ============================================================

-- Enable UUID extension (already enabled in Supabase by default)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- TABLE: api_keys
-- Stores hashed API keys + limits + usage tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS public.api_keys (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash            TEXT        NOT NULL UNIQUE,          -- SHA-256 of raw key
    label               TEXT        NOT NULL,                 -- e.g. "my-website"
    owner_email         TEXT,
    rate_limit_per_min  INTEGER     NOT NULL DEFAULT 20,
    monthly_token_limit BIGINT      NOT NULL DEFAULT 1000000,
    tokens_used_month   BIGINT      NOT NULL DEFAULT 0,
    month_reset_at      TIMESTAMPTZ NOT NULL DEFAULT date_trunc('month', NOW()) + INTERVAL '1 month',
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at        TIMESTAMPTZ
);

-- Index for fast lookup by hash
CREATE INDEX IF NOT EXISTS idx_api_keys_hash
    ON public.api_keys (key_hash)
    WHERE is_active = TRUE;

-- ============================================================
-- TABLE: usage_logs
-- One row per API request — used for analytics
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
-- We use the service_role key in the backend (bypasses RLS)
-- Enable RLS so anon key can never read keys table
-- ============================================================
ALTER TABLE public.api_keys   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_logs ENABLE ROW LEVEL SECURITY;

-- No public policies — only service_role can access these tables

-- ============================================================
-- USEFUL VIEWS (optional, for Supabase dashboard)
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
    COUNT(l.id)         AS total_requests,
    SUM(l.total_tokens) AS lifetime_tokens
FROM public.api_keys k
LEFT JOIN public.usage_logs l ON l.api_key_id = k.id
GROUP BY k.id;
