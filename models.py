# =============================================================================
# models.py - Pydantic request / response schemas
# =============================================================================
# BUG-3 FIX: datetime.utcnow() is deprecated in Python 3.12+ and returns a
#   naive datetime (no timezone info).  Replaced with
#   datetime.now(timezone.utc) which returns timezone-aware UTC datetime.
# =============================================================================
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal, Any, Dict
from datetime import datetime, timezone
import uuid


# ---------------------------------------------------------------------------
# Chat / Messages
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str = Field(default="qwen2.5:7b", description="Ollama model name")
    messages: List[Message]
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    stream: bool = False

    class Config:
        json_schema_extra = {
            "example": {
                "model": "qwen2.5:7b",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is the capital of India?"},
                ],
                "temperature": 0.7,
                "stream": False,
            }
        }


class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatChoice(BaseModel):
    index: int = 0
    message: Message
    finish_reason: str = "stop"


class ChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:8]}")
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatChoice]
    usage: UsageStats


# ---------------------------------------------------------------------------
# Raw generate
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    model: str = Field(default="qwen2.5:7b")
    prompt: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = None
    stream: bool = False


class GenerateResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"gen-{uuid.uuid4().hex[:8]}")
    object: str = "text_completion"
    created: int
    model: str
    text: str
    usage: UsageStats


# ---------------------------------------------------------------------------
# Models list
# ---------------------------------------------------------------------------

class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: Optional[int] = None
    owned_by: str = "local"
    details: Optional[Dict[str, Any]] = None


class ModelListResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]


# ---------------------------------------------------------------------------
# Pull model
# ---------------------------------------------------------------------------

class PullModelRequest(BaseModel):
    model: str = Field(description="Model tag, e.g. qwen2.5:7b or deepseek-r1:8b")


# ---------------------------------------------------------------------------
# API Key management
# ---------------------------------------------------------------------------

class APIKeyCreateRequest(BaseModel):
    label: str = Field(description="Human-readable label, e.g. my-website")
    owner_email: Optional[str] = Field(default=None, description="Owner email")
    rate_limit_per_min: int = Field(default=20, ge=1, le=10000)
    monthly_token_limit: int = Field(default=1_000_000, ge=1000)


class APIKeyCreateResponse(BaseModel):
    id: str
    key: str  # shown ONCE -- store it safely
    label: str
    owner_email: Optional[str]
    rate_limit_per_min: int
    monthly_token_limit: int
    created_at: datetime
    message: str = "Save this key -- it will not be shown again."


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str
    # BUG-3 FIX: datetime.utcnow() is deprecated (naive datetime, no tz info).
    # Use datetime.now(timezone.utc) for a timezone-aware UTC timestamp.
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
