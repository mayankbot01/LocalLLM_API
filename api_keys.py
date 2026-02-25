# =============================================================================
# api_keys.py - API key generation, validation, rate limiting
# =============================================================================

import secrets
import string
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Annotated, Any, Dict, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from config import settings
from database import (
    insert_api_key,
    fetch_key_by_hash,
    delete_key,
    list_all_keys,
)
from models import APIKeyCreateResponse

logger = logging.getLogger("localllm_api.keys")

# ---------------------------------------------------------------------------
# FastAPI security scheme (reads Authorization: Bearer <key> or X-API-Key)
# ---------------------------------------------------------------------------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_header  = APIKeyHeader(name="Authorization", auto_error=False)


# ---------------------------------------------------------------------------
# In-memory rate limiter (per-key sliding window)
# ---------------------------------------------------------------------------
# Format: { key_id: [timestamp, timestamp, ...] }
_rate_windows: Dict[str, list] = defaultdict(list)


def _check_rate_limit(key_id: str, limit_per_min: int) -> None:
    now = time.time()
    window = _rate_windows[key_id]
    # Remove timestamps older than 60 seconds
    _rate_windows[key_id] = [t for t in window if now - t < 60]
    if len(_rate_windows[key_id]) >= limit_per_min:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit_per_min} requests/min. Try again later.",
        )
    _rate_windows[key_id].append(now)


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

ALPHABET = string.ascii_letters + string.digits


def _generate_raw_key(prefix: str = "llm") -> str:
    """Generate a secure random API key like  llm_<48-char-random>."""
    random_part = "".join(secrets.choice(ALPHABET) for _ in range(48))
    return f"{prefix}_{random_part}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_new_api_key(
    label: str,
    owner_email: Optional[str],
    rate_limit_per_min: int,
    monthly_token_limit: int,
) -> APIKeyCreateResponse:
    raw_key = _generate_raw_key(settings.API_KEY_PREFIX)
    row = await insert_api_key(
        raw_key=raw_key,
        label=label,
        owner_email=owner_email,
        rate_limit_per_min=rate_limit_per_min,
        monthly_token_limit=monthly_token_limit,
    )
    logger.info(f"Created API key id={row['id']} label={label}")
    return APIKeyCreateResponse(
        id=row["id"],
        key=raw_key,          # shown ONCE
        label=label,
        owner_email=owner_email,
        rate_limit_per_min=rate_limit_per_min,
        monthly_token_limit=monthly_token_limit,
        created_at=row["created_at"],
    )


async def delete_api_key(key_id: str) -> bool:
    return await delete_key(key_id)


async def get_all_keys():
    return await list_all_keys()


# ---------------------------------------------------------------------------
# Validation dependency
# ---------------------------------------------------------------------------

async def validate_api_key(
    x_api_key: Optional[str] = Security(api_key_header),
    authorization: Optional[str] = Security(bearer_header),
) -> Dict[str, Any]:
    """
    FastAPI dependency — extracts and validates the API key.
    Accepts:
      - Header  X-API-Key: llm_xxxxxxxx
      - Header  Authorization: Bearer llm_xxxxxxxx
    """
    raw_key: Optional[str] = None

    if x_api_key:
        raw_key = x_api_key.strip()
    elif authorization:
        parts = authorization.strip().split(" ")
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw_key = parts[1]

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Send it as  X-API-Key: <key>  or  Authorization: Bearer <key>",
        )

    # Lookup in DB
    key_data = await fetch_key_by_hash(raw_key)
    if not key_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )

    # Monthly token limit check
    if key_data["tokens_used_month"] >= key_data["monthly_token_limit"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Monthly token limit reached "
                f"({key_data['monthly_token_limit']:,} tokens). "
                "Resets next month."
            ),
        )

    # Per-minute rate limit
    _check_rate_limit(key_data["id"], key_data["rate_limit_per_min"])

    return key_data


# Type alias for use in route signatures
APIKeyDep = Dict[str, Any]
