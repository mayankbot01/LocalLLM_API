# =============================================================================
# database.py - Supabase client + DB helpers
# =============================================================================
# Tables managed here:
#   api_keys    - stores hashed API keys + metadata
#   usage_logs  - per-request usage records
# =============================================================================
# BUG-4 FIX: init_db() previously called sb.rpc("exec_sql", ...) which is a
#   non-existent Supabase RPC endpoint -- this caused a 404 error on startup.
#   Supabase does NOT expose a generic SQL-execution RPC by default.
#   The correct approach is to run supabase_schema.sql manually once in the
#   Supabase SQL Editor (as documented in README).  init_db() now just verifies
#   connectivity by doing a lightweight select on pg_tables.
#
# BUG-5 FIX: increment_token_usage() previously did a SELECT then a separate
#   UPDATE -- a classic TOCTOU race condition.  Under concurrent load two
#   requests could both read the same value and each increment by their own
#   tokens, causing one update to be lost.  Fixed by using Supabase's
#   rpc("increment_tokens") stored procedure for an atomic increment, with a
#   Python-side fallback for environments where the procedure isn't installed.
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
# Table bootstrap
# ---------------------------------------------------------------------------

async def init_db():
    """Verify Supabase connectivity on startup.

    BUG-4 FIX: The old code called sb.rpc('exec_sql', ...) which is NOT a
    built-in Supabase endpoint and always returned a 404.  Tables must be
    created manually by running supabase_schema.sql in the Supabase SQL
    Editor (see README).  This function now just checks connectivity.
    """
    try:
        sb = get_supabase()
        # Lightweight connectivity check -- just fetch one row from api_keys
        sb.table("api_keys").select("id").limit(1).execute()
        logger.info("Supabase connectivity verified")
    except Exception as exc:
        logger.warning(
            f"Supabase connectivity check failed: {exc}. "
            "Make sure you have run supabase_schema.sql in the Supabase SQL "
            "Editor and that SUPABASE_URL / SUPABASE_SERVICE_KEY are correct."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw key -- never store plain-text keys."""
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
    """Atomically increment monthly token counter.

    BUG-5 FIX: The old implementation did SELECT + Python arithmetic + UPDATE
    which is a TOCTOU race condition.  Two simultaneous requests would both
    read the same counter value and each overwrite it with their own increment,
    silently dropping one update.

    Fix: Use a Supabase RPC stored procedure for an atomic increment.
    If the procedure is not installed (e.g. fresh DB), fall back to the
    non-atomic approach with a warning so the app still runs.

    To install the atomic increment procedure, add this to supabase_schema.sql:

        CREATE OR REPLACE FUNCTION increment_tokens(key_id UUID, amount BIGINT)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
          now_utc TIMESTAMPTZ := NOW();
          reset_ts TIMESTAMPTZ;
        BEGIN
          SELECT month_reset_at INTO reset_ts FROM api_keys WHERE id = key_id;
          IF now_utc >= reset_ts THEN
            UPDATE api_keys
            SET tokens_used_month = amount,
                month_reset_at = date_trunc('month', now_utc) + INTERVAL '1 month'
            WHERE id = key_id;
          ELSE
            UPDATE api_keys
            SET tokens_used_month = tokens_used_month + amount
            WHERE id = key_id;
          END IF;
        END;
        $$;
    """
    sb = get_supabase()
    try:
        sb.rpc("increment_tokens", {"key_id": key_id, "amount": tokens}).execute()
    except Exception as rpc_exc:
        logger.warning(
            f"increment_tokens RPC not available ({rpc_exc}); "
            "falling back to non-atomic update. "
            "Install the procedure from supabase_schema.sql to fix BUG-5."
        )
        # Non-atomic fallback (race condition possible under high concurrency)
        row = (
            sb.table("api_keys")
            .select("tokens_used_month, month_reset_at")
            .eq("id", key_id)
            .single()
            .execute()
            .data
        )
        now = datetime.now(timezone.utc)
        reset_at_raw = row["month_reset_at"]
        # Supabase may return timezone offset like "+00:00" -- handle both
        if reset_at_raw.endswith("Z"):
            reset_at_raw = reset_at_raw[:-1] + "+00:00"
        reset_at = datetime.fromisoformat(reset_at_raw)
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)

        if now >= reset_at:
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
        .select(
            "id, label, owner_email, rate_limit_per_min, monthly_token_limit, "
            "tokens_used_month, is_active, created_at, last_used_at"
        )
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
        .select(
            "label, monthly_token_limit, tokens_used_month, "
            "month_reset_at, last_used_at"
        )
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
