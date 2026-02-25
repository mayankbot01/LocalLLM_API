# =============================================================================
# tests/test_api.py - LocalLLM_API test suite
# =============================================================================
# Run with: pytest tests/ -v
# Fixtures and env-var setup live in tests/conftest.py.
# All DB + Ollama calls are mocked so no real services are needed.
# =============================================================================
# FIXES APPLIED:
# BUG-8a: Removed duplicate `client` fixture and constant definitions -
#         they now live in conftest.py and are auto-injected by pytest.
# BUG-8b: Fixed mock target paths to match actual import locations.
#   - fetch_key_by_hash is imported into api_keys namespace, so patch
#     "api_keys.fetch_key_by_hash" (where it is *used*).
#   - list_models, chat_completion are imported into main namespace, so
#     patch "main.list_models", "main.chat_completion" etc.
#   - raw_generate and get_key_usage are local imports inside their
#     route functions, so patch at source "ollama_client.raw_generate"
#     and "database.get_key_usage".
# BUG-8c: Removed stale anyio_backend fixture (not needed with asyncio_mode=auto)
# =============================================================================
import pytest
from unittest.mock import AsyncMock, patch

# conftest.py exports these -- import for use in this module
from tests.conftest import TEST_API_KEY, TEST_ADMIN_SECRET, MOCK_KEY_DATA

# ---------------------------------------------------------------------------
# Public endpoint tests
# ---------------------------------------------------------------------------
async def test_health_check(client):
    """GET /health should return 200 with status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


async def test_root(client):
    """GET / should return 200 with service info."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "docs" in data


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------
async def test_no_api_key_returns_401(client):
    """Requests without API key should be rejected with 401."""
    response = await client.get("/v1/models")
    assert response.status_code == 401


async def test_invalid_api_key_returns_401(client):
    """Requests with an invalid (unknown) API key should be rejected."""
    # Patch where fetch_key_by_hash is used: in api_keys module namespace
    with patch("api_keys.fetch_key_by_hash", new_callable=AsyncMock, return_value=None):
        response = await client.get(
            "/v1/models",
            headers={"X-API-Key": "llm_invalid_key_xyz"},
        )
    assert response.status_code == 401


async def test_bearer_token_accepted(client):
    """Authorization: Bearer <key> header should be accepted as auth."""
    # list_models is imported at module level in main.py, so patch main.list_models
    with patch("api_keys.fetch_key_by_hash", new_callable=AsyncMock, return_value=MOCK_KEY_DATA), \
         patch("main.list_models", new_callable=AsyncMock, return_value=[]):
        response = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
    # Should succeed (200) -- not a 401 or 422
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Admin endpoint tests
# ---------------------------------------------------------------------------
async def test_admin_no_secret_returns_403(client):
    """Admin endpoints without X-Admin-Secret should return 403."""
    response = await client.post(
        "/admin/api-keys",
        json={"label": "test", "owner_email": "a@b.com"},
    )
    assert response.status_code == 403


async def test_admin_wrong_secret_returns_403(client):
    """Admin endpoints with wrong X-Admin-Secret should return 403."""
    response = await client.post(
        "/admin/api-keys",
        json={"label": "test", "owner_email": "a@b.com"},
        headers={"X-Admin-Secret": "wrongsecret"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Chat completion tests (Ollama mocked)
# ---------------------------------------------------------------------------
async def test_chat_completions_success(client):
    """POST /v1/chat/completions should return a valid OpenAI-style response."""
    from models import ChatResponse, ChatChoice, Message, UsageStats
    import time

    mock_chat_resp = ChatResponse(
        id="chatcmpl-test123",
        object="chat.completion",
        created=int(time.time()),
        model="llama3",
        choices=[
            ChatChoice(
                index=0,
                message=Message(role="assistant", content="Hello! How can I help?"),
                finish_reason="stop",
            )
        ],
        usage=UsageStats(prompt_tokens=10, completion_tokens=8, total_tokens=18),
    )
    # chat_completion is imported at module level in main.py => patch main.chat_completion
    with patch("api_keys.fetch_key_by_hash", new_callable=AsyncMock, return_value=MOCK_KEY_DATA), \
         patch("main.chat_completion", new_callable=AsyncMock, return_value=mock_chat_resp):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "llama3",
                "messages": [{"role": "user", "content": "Hello!"}],
                "stream": False,
            },
            headers={"X-API-Key": TEST_API_KEY},
        )
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert data["choices"][0]["message"]["content"] == "Hello! How can I help?"


async def test_chat_missing_messages_returns_422(client):
    """Missing required 'messages' field should return 422 Unprocessable Entity."""
    with patch("api_keys.fetch_key_by_hash", new_callable=AsyncMock, return_value=MOCK_KEY_DATA):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "llama3"},  # missing 'messages'
            headers={"X-API-Key": TEST_API_KEY},
        )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Generate endpoint tests
# ---------------------------------------------------------------------------
async def test_generate_success(client):
    """POST /v1/generate should return a generated text response."""
    from models import GenerateResponse, UsageStats
    import time

    mock_gen_resp = GenerateResponse(
        id="gen-test123",
        object="text_completion",
        created=int(time.time()),
        model="llama3",
        text="Python is a high-level programming language.",
        usage=UsageStats(prompt_tokens=5, completion_tokens=9, total_tokens=14),
    )
    # raw_generate is a local import inside the route => patch at source ollama_client.raw_generate
    with patch("api_keys.fetch_key_by_hash", new_callable=AsyncMock, return_value=MOCK_KEY_DATA), \
         patch("ollama_client.raw_generate", new_callable=AsyncMock, return_value=mock_gen_resp):
        response = await client.post(
            "/v1/generate",
            json={"model": "llama3", "prompt": "What is Python?"},
            headers={"X-API-Key": TEST_API_KEY},
        )
    assert response.status_code == 200
    data = response.json()
    assert "text" in data


# ---------------------------------------------------------------------------
# Usage endpoint tests
# ---------------------------------------------------------------------------
async def test_usage_endpoint(client):
    """GET /v1/usage should return usage stats for the authenticated key."""
    mock_usage = {
        "key_id": "uuid-test-1234",
        "label": "test-key",
        "tokens_used_this_month": 1500,
        "monthly_token_limit": 1_000_000,
        "month_resets_at": "2026-03-01T00:00:00+00:00",
        "last_used_at": None,
        "recent_requests": [],
    }
    # get_key_usage is a local import inside the route => patch at source database.get_key_usage
    with patch("api_keys.fetch_key_by_hash", new_callable=AsyncMock, return_value=MOCK_KEY_DATA), \
         patch("database.get_key_usage", new_callable=AsyncMock, return_value=mock_usage):
        response = await client.get(
            "/v1/usage",
            headers={"X-API-Key": TEST_API_KEY},
        )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Model listing tests
# ---------------------------------------------------------------------------
async def test_list_models(client):
    """GET /v1/models should return available Ollama models."""
    from models import ModelInfo

    mock_models = [
        ModelInfo(id="llama3", owned_by="ollama"),
        ModelInfo(id="qwen2.5:7b", owned_by="ollama"),
    ]
    # list_models is imported at module level in main.py => patch main.list_models
    with patch("api_keys.fetch_key_by_hash", new_callable=AsyncMock, return_value=MOCK_KEY_DATA), \
         patch("main.list_models", new_callable=AsyncMock, return_value=mock_models):
        response = await client.get(
            "/v1/models",
            headers={"X-API-Key": TEST_API_KEY},
        )
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    assert len(data["data"]) == 2
