# =============================================================================
# api_keys.py - API key generation, validation, rate limiting
# =============================================================================
# BUG-6 FIX: _rate_windows (Dict[str, list]) grew unbounded -- every unique
#   key_id accumulated timestamps that were only pruned for keys that were
#   actively used.  Inactive keys leaked memory forever.
#   Fix: add a periodic sweep that removes entries for keys whose window is
#   completely empty and use a maxlen-aware deque for each window so appending
#   is O(1) and the data structure is bounded per key.
#   Additionally the old implementation mutated the list via reassignment
#   (_rate_windows[key_id] = [...]) while reading _rate_windows[key_id] -- in a
#   concurrent (asyncio) environment this could interleave.  Using deque +
#   in-place popleft is safer.
# =============================================================================

import secrets
import string
import logging
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Annotated, Any, Deque, Dict, Optional

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
# FastAPI security scheme (reads Authorization: Bearer or X-API-Key)
# ---------------------------------------------------------------------------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_header = APIKeyHeader(name="Authorization", auto_error=False)

# ---------------------------------------------------------------------------
# In-memory rate limiter (per-key sliding window)
# ---------------------------------------------------------------------------
# BUG-6 FIX: Use deque with a bounded maxlen to cap per-key memory usage.
# The maxlen is set to the maximum possible rate_limit_per_min (10 000).
# Empty deques are removed periodically by _purge_empty_windows().
_RATE_WINDOW_MAXLEN = 10_000
_rate_windows: Dict[str, Deque[float]] = defaultdict(
    lambda: deque(maxlen=_RATE_WINDOW_MAXLEN)
)
_last_purge: float = time.time()
_PURGE_INTERVAL: float = 300.0  # purge empty entries every 5 minutes


def _purge_empty_windows() -> None:
    """Remove entries for keys that have no timestamps in the last 60 s.

    BUG-6 FIX: Without this, every key that ever made a request permanently
    occupies memory in _rate_windows even after all its timestamps expire.
    """
    global _last_purge
    now = time.time()
    if now - _last_purge < _PURGE_INTERVAL:
        return
    _last_purge = now
    to_delete = [
        kid for kid, dq in _rate_windows.items()
        if not dq or (now - dq[-1]) >= 60
    ]
    for kid in to_delete:
        del _rate_windows[kid]


def _check_rate_limit(key_id: str, limit_per_min: int) -> None:
    _purge_empty_windows()
    now = time.time()
    dq = _rate_windows[key_id]
    # Remove timestamps older than 60 seconds from the left (oldest end)
    while dq and now - dq[0] >= 60:
        dq.popleft()
    if len(dq) >= limit_per_min:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {limit_per_min} requests/min. "
                "Try again later."
            ),
        )
    dq.append(now)


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

ALPHABET = string.ascii_letters + string.digits


def _generate_raw_key(prefix: str = "llm") -> str:
    """Generate a secure random API key like llm_<48-char-random>."""
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
        key=raw_key,  # shown ONCE
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
    """FastAPI dependency -- extracts and validates the API key.

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
            detail="API key required. Send it as X-API-Key: or Authorization: Bearer <key>",
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
