# LocalLLM API

> **Run any Ollama model locally (or on Oracle Cloud Free VM) and expose it as a secure, key-authenticated REST API usable from any website or app — for free, forever.**

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![Ollama](https://img.shields.io/badge/Ollama-latest-black.svg)](https://ollama.com)

---

## What This Project Does

This project solves one problem: **"I want to run a local LLM (Ollama) and use it from anywhere on the internet with an API key — just like OpenAI."**

You get:
- An **OpenAI-compatible REST API** (`/v1/chat/completions`) wrapping any Ollama model
- **API key authentication** — generate keys, revoke them, set rate limits per key
- **Usage tracking** — token counts per request stored in Supabase (free)
- **Monthly token limits** per key with automatic reset
- **Streaming support** (SSE) for real-time responses
- **Docker Compose** for one-command deployment
- **Oracle Cloud Always Free** VM deployment instructions

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    YOUR WEBSITE / APP                           │
│  fetch('/v1/chat/completions', {                                 │
│    headers: { 'X-API-Key': 'llm_xxxx' },                        │
│    body: JSON.stringify({ model: 'qwen2.5:7b', messages: [...] })│
│  })                                                             │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTPS
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│           ORACLE CLOUD FREE VM (ARM 4 vCPU / 24 GB RAM)        │
│                                                                 │
│  ┌──────────────────────────────┐   ┌──────────────────────┐   │
│  │      FastAPI Gateway         │   │   Ollama Server      │   │
│  │      (main.py : 8000)        │──▶│   (port 11434)       │   │
│  │                              │   │                      │   │
│  │  1. Extract API key from     │   │  Runs model locally: │   │
│  │     header                   │   │  • qwen2.5:7b        │   │
│  │  2. SHA-256 hash key         │   │  • deepseek-r1:8b    │   │
│  │  3. Lookup hash in Supabase  │   │  • llama3.2:3b       │   │
│  │  4. Check rate limit         │   │  • mistral:7b        │   │
│  │  5. Forward to Ollama        │   │  • gemma2:9b         │   │
│  │  6. Return response          │   │  • phi3:mini         │   │
│  │  7. Log usage to Supabase    │   └──────────────────────┘   │
│  └──────────────────────────────┘                               │
└─────────────────────────────────────┬───────────────────────────┘
                                      │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              SUPABASE (Free Tier — always free)                 │
│                                                                 │
│  Table: api_keys            Table: usage_logs                   │
│  ─────────────────          ─────────────────────               │
│  id (UUID)                  id (UUID)                           │
│  key_hash (SHA-256)         api_key_id (FK)                     │
│  label                      model                               │
│  owner_email                prompt_tokens                       │
│  rate_limit_per_min         completion_tokens                   │
│  monthly_token_limit        total_tokens                        │
│  tokens_used_month          endpoint                            │
│  month_reset_at             response_time_ms                    │
│  is_active                  created_at                          │
│  created_at                                                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Request Data Flow (Step by Step)

```
Client Request
     │
     ▼
[1] FastAPI receives request on /v1/chat/completions
     │
     ▼
[2] Middleware: extract X-API-Key or Authorization: Bearer header
     │
     ▼
[3] SHA-256 hash the raw key (never store plain text)
     │
     ▼
[4] Supabase lookup: SELECT * FROM api_keys WHERE key_hash = ? AND is_active = TRUE
     │
     ├── Not found → 401 Unauthorized
     │
     ▼
[5] Monthly token limit check: tokens_used_month >= monthly_token_limit?
     │
     ├── Exceeded → 429 Too Many Requests
     │
     ▼
[6] In-memory sliding window rate limiter (per key, 60-second window)
     │
     ├── Exceeded → 429 Too Many Requests
     │
     ▼
[7] Forward request to Ollama: POST http://localhost:11434/api/chat
     │
     ├── stream=true  → SSE stream back to client
     └── stream=false → Wait for full response
     │
     ▼
[8] Build ChatResponse (OpenAI-compatible format)
     │
     ▼
[9] Fire-and-forget async task: log_usage() → INSERT INTO usage_logs
     │   Also: UPDATE api_keys SET tokens_used_month += total_tokens
     │
     ▼
[10] Return response to client
```

---

## Project Structure

```
LocalLLM_API/
├── main.py              # FastAPI app, all routes
├── config.py            # Settings from .env (Pydantic BaseSettings)
├── models.py            # Pydantic request/response schemas
├── api_keys.py          # Key generation, validation, rate limiting
├── ollama_client.py     # Async HTTP client for Ollama API
├── database.py          # Supabase client + all DB operations
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variables template
├── supabase_schema.sql  # SQL to run in Supabase SQL editor
├── Dockerfile           # Multi-stage production Docker image
├── docker-compose.yml   # Runs FastAPI + Ollama together
├── .gitignore
├── README.md            # This file
└── SETUP_GUIDE.md       # Full step-by-step deployment guide
```

---

## API Endpoints

### Public (no auth)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service info |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc UI |

### Admin (requires `X-Admin-Secret` header)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/api-keys` | Create new API key |
| GET | `/admin/api-keys` | List all keys |
| DELETE | `/admin/api-keys/{id}` | Revoke a key |

### Protected (requires `X-API-Key` or `Authorization: Bearer`)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | Chat completion (OpenAI compatible) |
| POST | `/v1/generate` | Raw text generation |
| GET | `/v1/models` | List available Ollama models |
| POST | `/v1/models/pull` | Pull/download a model |
| GET | `/v1/usage` | Usage stats for current key |

---

## Quick Start

### 1. Clone the repo
```bash
git clone https://github.com/mayankbot01/LocalLLM_API.git
cd LocalLLM_API
```

### 2. Set up environment
```bash
cp .env.example .env
# Edit .env — fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, ADMIN_SECRET
```

### 3. Set up Supabase (free)
1. Go to [supabase.com](https://supabase.com) → New Project (free)
2. Go to **SQL Editor** → paste contents of `supabase_schema.sql` → Run
3. Go to **Settings → API** → copy **URL** and **service_role key** → paste in `.env`

### 4. Install Ollama locally
```bash
# Linux / macOS
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b   # or deepseek-r1:8b, llama3.2:3b, etc.
```

### 5. Run with Docker Compose
```bash
docker compose up -d
# API is live at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### 6. Create your first API key
```bash
curl -X POST http://localhost:8000/admin/api-keys \
  -H "X-Admin-Secret: YOUR_ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"label": "my-website", "rate_limit_per_min": 20, "monthly_token_limit": 1000000}'
```
Response:
```json
{
  "id": "uuid-here",
  "key": "llm_AbCdEfGh...",
  "label": "my-website",
  "message": "Save this key — it will not be shown again."
}
```

### 7. Use the API from your website
```javascript
const response = await fetch('http://YOUR_SERVER_IP:8000/v1/chat/completions', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': 'llm_AbCdEfGh...'
  },
  body: JSON.stringify({
    model: 'qwen2.5:7b',
    messages: [
      { role: 'system', content: 'You are a helpful assistant.' },
      { role: 'user', content: 'What is machine learning?' }
    ],
    temperature: 0.7,
    stream: false
  })
});
const data = await response.json();
console.log(data.choices[0].message.content);
```

---

## Supported Ollama Models

| Model | RAM Needed | Best For |
|-------|-----------|---------|
| `qwen2.5:7b` | ~6 GB | General purpose, coding |
| `deepseek-r1:8b` | ~6 GB | Reasoning, math |
| `llama3.2:3b` | ~3 GB | Fast responses, low RAM |
| `mistral:7b` | ~6 GB | General purpose |
| `gemma2:9b` | ~8 GB | Google's model, strong |
| `phi3:mini` | ~2 GB | Ultra-light, fast |
| `codellama:7b` | ~6 GB | Code generation |

Pull a model: `ollama pull <model-name>`

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable debug mode |
| `ADMIN_SECRET` | *(required)* | Secret for admin endpoints |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL |
| `OLLAMA_DEFAULT_MODEL` | `qwen2.5:7b` | Default model |
| `OLLAMA_TIMEOUT` | `120` | Request timeout (seconds) |
| `SUPABASE_URL` | *(required)* | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | *(required)* | Supabase service role key |
| `DEFAULT_RATE_LIMIT_PER_MIN` | `20` | Default requests/minute per key |
| `DEFAULT_MONTHLY_TOKEN_LIMIT` | `1000000` | Default tokens/month per key |
| `API_KEY_PREFIX` | `llm` | Prefix for generated keys |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins |

---

## Security Notes

- API keys are **never stored in plain text** — only SHA-256 hash stored in DB
- Admin endpoints require a separate `X-Admin-Secret` header
- All Supabase tables have **Row Level Security** enabled — only the service_role key (used by the backend) can access them
- Use HTTPS in production (Nginx + Let's Encrypt or Cloudflare)
- Restrict `CORS_ORIGINS` to your actual domain in production

---

## Free Tier Costs

| Service | Cost |
|---------|------|
| Oracle Cloud ARM VM (4 vCPU, 24 GB RAM) | **$0 forever** |
| Supabase Free Tier (500 MB DB, 50K MAU) | **$0 forever** |
| Ollama | **$0 open source** |
| FastAPI / Python | **$0 open source** |
| **Total** | **$0** |

---

## License

MIT License — see [LICENSE](LICENSE)

---

## Author

Built by [mayankbot01](https://github.com/mayankbot01)
