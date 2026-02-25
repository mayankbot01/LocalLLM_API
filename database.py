# =============================================================================
# database.py - Supabase client + DB helpers
# =============================================================================
# Tables managed here:
#   api_keys        - stores hashed API keys + metadata
#   usage_logs      - per-request usage records
# =============================================================================

import logging
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client, Client
from config import settings

logger = logging.getLogger("localllm_api.db")

# ---------------------------------------------------------------------------
# Supabase client (lazy singleton)
# ---------------------------------------------------------------------------
_supabase: Optional[Client] = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env"
            )
        _supabase = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,
        )
    return _supabase


# ---------------------------------------------------------------------------
# Table bootstrap (idempotent — safe to call every startup)
# ---------------------------------------------------------------------------
CREATE_API_KEYS_TABLE = """
CREATE TABLE IF NOT EXISTS api_keys (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash            TEXT NOT NULL UNIQUE,
    label               TEXT NOT NULL,
    owner_email         TEXT,
    rate_limit_per_min  INTEGER NOT NULL DEFAULT 20,
    monthly_token_limit BIGINT NOT NULL DEFAULT 1000000,
    tokens_used_month   BIGINT NOT NULL DEFAULT 0,
    month_reset_at      TIMESTAMPTZ NOT NULL DEFAULT date_trunc('month', NOW()) + INTERVAL '1 month',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at        TIMESTAMPTZ
);
"""

CREATE_USAGE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS usage_logs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id        UUID REFERENCES api_keys(id) ON DELETE CASCADE,
    model             TEXT,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    endpoint          TEXT,
    response_time_ms  FLOAT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


async def init_db():
    """Run table creation SQL via Supabase RPC (or just log if Supabase not configured)."""
    try:
        sb = get_supabase()
        # Execute raw SQL via the Postgres REST interface
        sb.rpc("exec_sql", {"sql": CREATE_API_KEYS_TABLE}).execute()
        sb.rpc("exec_sql", {"sql": CREATE_USAGE_LOGS_TABLE}).execute()
        logger.info("Supabase tables verified / created")
    except Exception as exc:
        logger.warning(
            f"Supabase init skipped (tables may not exist yet): {exc}. "
            "Run the SQL in supabase_schema.sql manually in Supabase SQL editor."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw key — never store plain-text keys."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# API Key CRUD
# ---------------------------------------------------------------------------

async def insert_api_key(
    raw_key: str,
    label: str,
    owner_email: Optional[str],
    rate_limit_per_min: int,
    monthly_token_limit: int,
) -> Dict[str, Any]:
    sb = get_supabase()
    key_hash = _hash_key(raw_key)
    row = {
        "key_hash": key_hash,
        "label": label,
        "owner_email": owner_email,
        "rate_limit_per_min": rate_limit_per_min,
        "monthly_token_limit": monthly_token_limit,
    }
    result = sb.table("api_keys").insert(row).execute()
    return result.data[0]


async def fetch_key_by_hash(raw_key: str) -> Optional[Dict[str, Any]]:
    sb = get_supabase()
    key_hash = _hash_key(raw_key)
    result = (
        sb.table("api_keys")
        .select("*")
        .eq("key_hash", key_hash)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


async def update_last_used(key_id: str) -> None:
    sb = get_supabase()
    sb.table("api_keys").update(
        {"last_used_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", key_id).execute()


async def increment_token_usage(key_id: str, tokens: int) -> None:
    """Increment monthly token counter. Resets if month_reset_at has passed."""
    sb = get_supabase()
    # Read current
    row = (
        sb.table("api_keys")
        .select("tokens_used_month, month_reset_at")
        .eq("id", key_id)
        .single()
        .execute()
        .data
    )
    now = datetime.now(timezone.utc)
    reset_at = datetime.fromisoformat(row["month_reset_at"])
    if now >= reset_at:
        # New month — reset counter and update reset_at
        from dateutil.relativedelta import relativedelta
        new_reset = reset_at + relativedelta(months=1)
        sb.table("api_keys").update(
            {
                "tokens_used_month": tokens,
                "month_reset_at": new_reset.isoformat(),
            }
        ).eq("id", key_id).execute()
    else:
        sb.table("api_keys").update(
            {"tokens_used_month": row["tokens_used_month"] + tokens}
        ).eq("id", key_id).execute()


async def delete_key(key_id: str) -> bool:
    sb = get_supabase()
    result = (
        sb.table("api_keys").update({"is_active": False}).eq("id", key_id).execute()
    )
    return bool(result.data)


async def list_all_keys() -> List[Dict[str, Any]]:
    sb = get_supabase()
    result = (
        sb.table("api_keys")
        .select("id, label, owner_email, rate_limit_per_min, monthly_token_limit, tokens_used_month, is_active, created_at, last_used_at")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


# ---------------------------------------------------------------------------
# Usage logging
# ---------------------------------------------------------------------------

async def log_usage(
    key_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    endpoint: str,
    response_time_ms: float,
) -> None:
    sb = get_supabase()
    row = {
        "api_key_id": key_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "endpoint": endpoint,
        "response_time_ms": response_time_ms,
    }
    try:
        sb.table("usage_logs").insert(row).execute()
        await increment_token_usage(key_id, total_tokens)
        await update_last_used(key_id)
    except Exception as exc:
        logger.error(f"Failed to log usage: {exc}")


# ---------------------------------------------------------------------------
# Usage stats per key
# ---------------------------------------------------------------------------

async def get_key_usage(key_id: str) -> Dict[str, Any]:
    sb = get_supabase()
    key_row = (
        sb.table("api_keys")
        .select("label, monthly_token_limit, tokens_used_month, month_reset_at, last_used_at")
        .eq("id", key_id)
        .single()
        .execute()
        .data
    )
    logs = (
        sb.table("usage_logs")
        .select("total_tokens, created_at, model, endpoint")
        .eq("api_key_id", key_id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
        .data
    )
    return {
        "key_id": key_id,
        "label": key_row.get("label"),
        "monthly_token_limit": key_row.get("monthly_token_limit"),
        "tokens_used_this_month": key_row.get("tokens_used_month"),
        "month_resets_at": key_row.get("month_reset_at"),
        "last_used_at": key_row.get("last_used_at"),
        "recent_requests": logs,
    }
