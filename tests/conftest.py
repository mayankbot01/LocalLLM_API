# =============================================================================
# tests/conftest.py - Shared pytest fixtures for LocalLLM_API test suite
# =============================================================================
# BUG-8 FIX: pytest-asyncio >= 0.21 changed how async fixtures work.
# The @pytest_asyncio.fixture decorator must be used for async fixtures
# (not @pytest.fixture). This conftest centralises the async HTTP client
# fixture and environment variable overrides so every test file benefits.
# =============================================================================

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# Override env vars BEFORE main.py / config.py are imported by the test
# These must be set at import time so pydantic-settings picks them up.
# ---------------------------------------------------------------------------
os.environ.setdefault("ADMIN_SECRET", "test_admin_secret_ci")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "eyJci_dummy_key_for_testing")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("API_KEY_PREFIX", "llm")
os.environ.setdefault("DEFAULT_RATE_LIMIT_PER_MIN", "20")
os.environ.setdefault("DEFAULT_MONTHLY_TOKEN_LIMIT", "1000000")


@pytest_asyncio.fixture
async def client():
    """
    Async HTTPX test client that talks directly to the FastAPI ASGI app.
    No real HTTP server is started — fully in-process and fast.

    Uses ASGITransport (httpx >= 0.20) which replaces the deprecated
    app= shortcut and avoids DeprecationWarnings in newer httpx versions.
    """
    # Import here so env vars above are set first
    from main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac


# Convenience constants re-exported for test modules
TEST_API_KEY = "llm_testkey123456789"
TEST_ADMIN_SECRET = "test_admin_secret_ci"
MOCK_KEY_DATA = {
    "id": "uuid-test-1234",
    "label": "test-key",
    "owner_email": "test@example.com",
    "rate_limit_per_min": 60,
    "monthly_token_limit": 1_000_000,
    "tokens_used_month": 0,
    "is_active": True,
}
