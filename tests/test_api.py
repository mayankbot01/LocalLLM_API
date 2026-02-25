# =============================================================================
# tests/test_api.py - LocalLLM_API test suite
# =============================================================================
# Run with: pytest tests/ -v
# For integration tests, ensure Ollama is running and .env is configured.
# =============================================================================

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_API_KEY = "llm_testkey123456789"
TEST_ADMIN_SECRET = "test_admin_secret"

MOCK_KEY_DATA = {
    "id": "uuid-test-1234",
    "label": "test-key",
    "owner_email": "test@example.com",
    "rate_limit_per_min": 60,
    "monthly_token_limit": 1_000_000,
    "tokens_used_month": 0,
    "is_active": True,
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

# ---------------------------------------------------------------------------
# Public endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check(client):
    """GET /health should return 200 with status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
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

@pytest.mark.asyncio
async def test_no_api_key_returns_401(client):
    """Requests without API key should be rejected with 401."""
    response = await client.get("/v1/models")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(client):
    """Requests with an invalid API key should be rejected."""
    response = await client.get(
        "/v1/models",
        headers={"X-API-Key": "llm_invalid_key_xyz"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_token_auth(client):
    """Authorization: Bearer header should also be accepted."""
    with patch("api_keys.validate_api_key", return_value=MOCK_KEY_DATA), \
         patch("ollama_client.list_models", new_callable=AsyncMock, return_value=[]):
        response = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        # Should not return 422 (validation error) - key was accepted
        assert response.status_code != 422

# ---------------------------------------------------------------------------
# Admin endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_no_secret_returns_403(client):
    """Admin endpoints without X-Admin-Secret should return 403."""
    response = await client.post(
        "/admin/api-keys",
        json={"label": "test", "owner_email": "a@b.com"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_wrong_secret_returns_403(client):
    """Admin endpoints with wrong X-Admin-Secret should return 403."""
    response = await client.post(
        "/admin/api-keys",
        json={"label": "test", "owner_email": "a@b.com"},
        headers={"X-Admin-Secret": "wrongsecret"}
    )
    assert response.status_code == 403

# ---------------------------------------------------------------------------
# Chat completion tests (mocked Ollama)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_completions_success(client):
    """POST /v1/chat/completions should return a valid chat response."""
    mock_response = {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "llama3",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }
    with patch("api_keys.validate_api_key", return_value=MOCK_KEY_DATA), \
         patch("ollama_client.chat_completion", new_callable=AsyncMock, return_value=mock_response):
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


@pytest.mark.asyncio
async def test_chat_missing_messages_returns_422(client):
    """Missing required fields should return 422 Unprocessable Entity."""
    with patch("api_keys.validate_api_key", return_value=MOCK_KEY_DATA):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "llama3"},  # missing 'messages'
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 422

# ---------------------------------------------------------------------------
# Generate endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_success(client):
    """POST /v1/generate should return a generated text response."""
    mock_response = {
        "id": "gen-test123",
        "object": "text_completion",
        "created": 1700000000,
        "model": "llama3",
        "text": "Python is a high-level programming language.",
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    }
    with patch("api_keys.validate_api_key", return_value=MOCK_KEY_DATA), \
         patch("ollama_client.raw_generate", new_callable=AsyncMock, return_value=mock_response):
        response = await client.post(
            "/v1/generate",
            json={"model": "llama3", "prompt": "What is Python?"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200

# ---------------------------------------------------------------------------
# Usage endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_usage_endpoint(client):
    """GET /v1/usage should return usage stats for the current key."""
    mock_usage = {
        "key_id": "uuid-test-1234",
        "tokens_used_month": 1500,
        "monthly_token_limit": 1_000_000,
        "recent_requests": [],
    }
    with patch("api_keys.validate_api_key", return_value=MOCK_KEY_DATA), \
         patch("database.get_key_usage", new_callable=AsyncMock, return_value=mock_usage):
        response = await client.get(
            "/v1/usage",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200

# ---------------------------------------------------------------------------
# Model listing tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_models(client):
    """GET /v1/models should return available Ollama models."""
    mock_models = [
        {"id": "llama3", "object": "model", "owned_by": "ollama"},
        {"id": "qwen2.5:7b", "object": "model", "owned_by": "ollama"},
    ]
    with patch("api_keys.validate_api_key", return_value=MOCK_KEY_DATA), \
         patch("ollama_client.list_models", new_callable=AsyncMock, return_value=mock_models):
        response = await client.get(
            "/v1/models",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
