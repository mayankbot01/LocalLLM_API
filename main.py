# =============================================================================
# main.py - LocalLLM_API : FastAPI Gateway for Ollama Models
# =============================================================================
# Author : mayankbot01
# Stack  : FastAPI + Ollama + Supabase (free) + Oracle Cloud A1 VM
# License: MIT
# =============================================================================

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from contextlib import asynccontextmanager
import time
import logging

from database import init_db
from api_keys import validate_api_key, APIKeyDep
from ollama_client import (
    chat_completion,
    stream_chat_completion,
    list_models,
    pull_model,
)
from models import (
    ChatRequest,
    ChatResponse,
    GenerateRequest,
    GenerateResponse,
    ModelListResponse,
    PullModelRequest,
    APIKeyCreateRequest,
    APIKeyCreateResponse,
    HealthResponse,
)
from config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("localllm_api")


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== LocalLLM API starting up ===")
    await init_db()
    logger.info("Database initialised")
    yield
    logger.info("=== LocalLLM API shutting down ===")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="LocalLLM API",
    description=(
        "OpenAI-compatible REST gateway for local Ollama models. "
        "Supports API-key auth, streaming, usage tracking via Supabase."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — tighten origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Process-Time-Ms"] = str(elapsed)
    return response


# ===========================================================================
# PUBLIC ROUTES
# ===========================================================================

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Liveness probe — no auth required."""
    return HealthResponse(status="ok", version="1.0.0")


@app.get("/", tags=["System"])
async def root():
    return {
        "service": "LocalLLM API",
        "docs": "/docs",
        "health": "/health",
    }


# ===========================================================================
# ADMIN ROUTES  (protected by ADMIN_SECRET header)
# ===========================================================================

def verify_admin(request: Request):
    secret = request.headers.get("X-Admin-Secret", "")
    if secret != settings.ADMIN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin secret",
        )


@app.post(
    "/admin/api-keys",
    response_model=APIKeyCreateResponse,
    tags=["Admin"],
    dependencies=[Depends(verify_admin)],
)
async def create_api_key(body: APIKeyCreateRequest):
    """
    Generate a new API key for a user / project.
    Pass  X-Admin-Secret: <your ADMIN_SECRET>  in the request header.
    """
    from api_keys import create_new_api_key
    result = await create_new_api_key(
        label=body.label,
        owner_email=body.owner_email,
        rate_limit_per_min=body.rate_limit_per_min,
        monthly_token_limit=body.monthly_token_limit,
    )
    return result


@app.delete(
    "/admin/api-keys/{key_id}",
    tags=["Admin"],
    dependencies=[Depends(verify_admin)],
)
async def revoke_api_key(key_id: str):
    from api_keys import delete_api_key
    deleted = await delete_api_key(key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": f"API key {key_id} revoked"}


@app.get(
    "/admin/api-keys",
    tags=["Admin"],
    dependencies=[Depends(verify_admin)],
)
async def list_api_keys():
    from api_keys import get_all_keys
    return await get_all_keys()


# ===========================================================================
# PROTECTED LLM ROUTES  (require valid API key)
# ===========================================================================

@app.get(
    "/v1/models",
    response_model=ModelListResponse,
    tags=["Models"],
)
async def get_models(key_data: APIKeyDep = Depends(validate_api_key)):
    """List all Ollama models available on the server."""
    models = await list_models()
    return ModelListResponse(data=models)


@app.post(
    "/v1/models/pull",
    tags=["Models"],
)
async def pull_ollama_model(
    body: PullModelRequest,
    key_data: APIKeyDep = Depends(validate_api_key),
):
    """Pull / download a model into Ollama."""
    result = await pull_model(body.model)
    return result


@app.post(
    "/v1/chat/completions",
    response_model=ChatResponse,
    tags=["Chat"],
)
async def chat_completions(
    body: ChatRequest,
    key_data: APIKeyDep = Depends(validate_api_key),
):
    """
    OpenAI-compatible chat completions endpoint.
    Supports streaming when  stream=true.
    """
    if body.stream:
        return StreamingResponse(
            stream_chat_completion(
                model=body.model,
                messages=body.messages,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                key_data=key_data,
            ),
            media_type="text/event-stream",
        )

    response = await chat_completion(
        model=body.model,
        messages=body.messages,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        key_data=key_data,
    )
    return response


@app.post(
    "/v1/generate",
    response_model=GenerateResponse,
    tags=["Generate"],
)
async def generate(
    body: GenerateRequest,
    key_data: APIKeyDep = Depends(validate_api_key),
):
    """Raw text-generation endpoint (no chat template)."""
    from ollama_client import raw_generate
    return await raw_generate(
        model=body.model,
        prompt=body.prompt,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        key_data=key_data,
    )


@app.get(
    "/v1/usage",
    tags=["Usage"],
)
async def get_usage(key_data: APIKeyDep = Depends(validate_api_key)):
    """Return usage stats for the authenticated API key."""
    from database import get_key_usage
    stats = await get_key_usage(key_data["id"])
    return stats


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again."},
    )


# ---------------------------------------------------------------------------
# Entry point (local dev)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=1,
    )
