# =============================================================================
# ollama_client.py - Async HTTP client for Ollama REST API
# =============================================================================
# Ollama REST reference: https://github.com/ollama/ollama/blob/main/docs/api.md
# =============================================================================

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from config import settings
from models import (
    ChatChoice,
    ChatResponse,
    GenerateResponse,
    Message,
    ModelInfo,
    UsageStats,
)

logger = logging.getLogger("localllm_api.ollama")

# ---------------------------------------------------------------------------
# Async HTTP client (shared, keep-alive)
# ---------------------------------------------------------------------------
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.OLLAMA_BASE_URL,
            timeout=httpx.Timeout(settings.OLLAMA_TIMEOUT),
        )
    return _client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Very rough token count (≈ 4 chars/token) for usage stats."""
    return max(1, len(text) // 4)


def _build_usage(prompt: str, completion: str) -> UsageStats:
    pt = _estimate_tokens(prompt)
    ct = _estimate_tokens(completion)
    return UsageStats(
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=pt + ct,
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

async def list_models() -> List[ModelInfo]:
    """GET /api/tags — returns all locally available models."""
    client = _get_client()
    try:
        resp = await client.get("/api/tags")
        resp.raise_for_status()
        data = resp.json()
        models = []
        for m in data.get("models", []):
            models.append(
                ModelInfo(
                    id=m.aboriginal("name", m.get("model", "unknown")),
                    details=m,
                )
            )
        return models
    except Exception as exc:
        logger.error(f"Failed to list models: {exc}")
        return []


async def list_models() -> List[ModelInfo]:
    """GET /api/tags — returns all locally available models."""
    client = _get_client()
    try:
        resp = await client.get("/api/tags")
        resp.raise_for_status()
        data = resp.json()
        models = []
        for m in data.get("models", []):
            models.append(
                ModelInfo(
                    id=m.get("name", m.get("model", "unknown")),
                    details=m,
                )
            )
        return models
    except Exception as exc:
        logger.error(f"Failed to list models: {exc}")
        return []


async def pull_model(model: str) -> Dict[str, Any]:
    """POST /api/pull — download a model."""
    client = _get_client()
    try:
        resp = await client.post(
            "/api/pull",
            json={"model": model, "stream": False},
            timeout=600,        # pulling can take minutes
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error(f"Failed to pull model {model}: {exc}")
        raise


# ---------------------------------------------------------------------------
# Chat Completion (non-streaming)
# ---------------------------------------------------------------------------

async def chat_completion(
    model: str,
    messages: List[Message],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    key_data: Optional[Dict[str, Any]] = None,
) -> ChatResponse:
    client = _get_client()
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [m.model_dump() for m in messages],
        "stream": False,
        "options": {"temperature": temperature},
    }
    if max_tokens:
        payload["options"]["num_predict"] = max_tokens

    try:
        resp = await client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(f"Ollama HTTP error: {exc.response.text}")
        raise
    except Exception as exc:
        logger.error(f"Ollama request failed: {exc}")
        raise

    content = data.get("message", {}).get("content", "")
    prompt_text = " ".join(m.content for m in messages)
    usage = _build_usage(prompt_text, content)

    # Log usage to DB asynchronously (fire-and-forget)
    if key_data:
        asyncio.create_task(_log(key_data["id"], model, usage, "/v1/chat/completions"))

    return ChatResponse(
        created=int(time.time()),
        model=model,
        choices=[
            ChatChoice(
                message=Message(role="assistant", content=content),
                finish_reason=data.get("done_reason", "stop"),
            )
        ],
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Chat Completion (streaming — SSE)
# ---------------------------------------------------------------------------

async def stream_chat_completion(
    model: str,
    messages: List[Message],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    key_data: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    client = _get_client()
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [m.model_dump() for m in messages],
        "stream": True,
        "options": {"temperature": temperature},
    }
    if max_tokens:
        payload["options"]["num_predict"] = max_tokens

    full_content = ""
    try:
        async with client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("message", {}).get("content", "")
                full_content += delta
                # SSE format compatible with OpenAI clients
                sse_data = {
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": delta},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(sse_data)}\n\n"
                if chunk.get("done", False):
                    break
    except Exception as exc:
        logger.error(f"Stream error: {exc}")
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    yield "data: [DONE]\n\n"

    # Log usage
    if key_data:
        prompt_text = " ".join(m.content for m in messages)
        usage = _build_usage(prompt_text, full_content)
        asyncio.create_task(_log(key_data["id"], model, usage, "/v1/chat/completions"))


# ---------------------------------------------------------------------------
# Raw text generation
# ---------------------------------------------------------------------------

async def raw_generate(
    model: str,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    key_data: Optional[Dict[str, Any]] = None,
) -> GenerateResponse:
    client = _get_client()
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if max_tokens:
        payload["options"]["num_predict"] = max_tokens

    try:
        resp = await client.post("/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"Generate error: {exc}")
        raise

    text = data.get("response", "")
    usage = _build_usage(prompt, text)

    if key_data:
        asyncio.create_task(_log(key_data["id"], model, usage, "/v1/generate"))

    return GenerateResponse(
        created=int(time.time()),
        model=model,
        text=text,
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Internal: async usage logging (fire-and-forget)
# ---------------------------------------------------------------------------

async def _log(key_id: str, model: str, usage: UsageStats, endpoint: str) -> None:
    try:
        from database import log_usage
        await log_usage(
            key_id=key_id,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            endpoint=endpoint,
            response_time_ms=0.0,
        )
    except Exception as exc:
        logger.warning(f"Usage log failed (non-fatal): {exc}")
