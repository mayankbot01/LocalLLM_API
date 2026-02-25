# Setup Guide - LocalLLM_API

This guide walks you through setting up the LocalLLM_API gateway that exposes your local Ollama LLMs via a secure REST API with API key authentication and usage tracking.

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai) installed and running locally
- At least one Ollama model pulled (e.g. `ollama pull llama3`)
- Docker (optional, for containerized deployment)

## Quick Start (Local)

### 1. Clone the repository

```bash
git clone https://github.com/mayankbot01/LocalLLM_API.git
cd LocalLLM_API
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and set your API keys and Ollama URL
```

### 4. Start Ollama

Make sure Ollama is running on your machine:

```bash
ollama serve
```

### 5. Run the API server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`

Swagger docs: `http://localhost:8000/docs`

## Quick Start (Docker)

```bash
cp .env.example .env
# Edit .env with your settings
docker-compose up --build
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | URL where Ollama is running | `http://localhost:11434` |
| `API_KEYS` | Comma-separated list of valid API keys | (required) |
| `USAGE_LOG_FILE` | Path to the usage log JSON file | `usage_log.json` |
| `DEFAULT_MODEL` | Default Ollama model to use | `llama3` |
| `MAX_TOKENS` | Max tokens per request | `2048` |
| `RATE_LIMIT` | Requests per minute per API key | `60` |

## API Key Authentication

All requests must include an API key in the header:

```
Authorization: Bearer YOUR_API_KEY
```

Or as a query parameter:

```
?api_key=YOUR_API_KEY
```

## Generating API Keys

Run the helper script to generate secure API keys:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add the generated key to the `API_KEYS` variable in your `.env` file.

## Cloud Deployment (Railway / Render / Fly.io)

### Deploy to Railway

1. Push your repo to GitHub
2. Connect your GitHub repo to [Railway](https://railway.app)
3. Set environment variables in the Railway dashboard
4. Set the start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Use a tunnel (e.g. [ngrok](https://ngrok.com) or [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)) to expose your local Ollama to the cloud server

### Expose Local Ollama via ngrok

```bash
# Install ngrok and run:
ngrok http 11434
# Copy the HTTPS URL and set it as OLLAMA_BASE_URL in your cloud env vars
```

### Deploy to Render

1. Create a new Web Service on [Render](https://render.com)
2. Connect your GitHub repo
3. Set Build Command: `pip install -r requirements.txt`
4. Set Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables in the Render dashboard

## Usage Examples

### Chat Completion

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

### Text Generation

```bash
curl -X POST http://localhost:8000/v1/generate \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3",
    "prompt": "Explain quantum computing in simple terms",
    "max_tokens": 500
  }'
```

### List Available Models

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Check Usage Stats

```bash
curl http://localhost:8000/v1/usage \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Health Check

```bash
curl http://localhost:8000/health
```

## Project Structure

```
LocalLLM_API/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app & routes
│   ├── auth.py          # API key authentication
│   ├── models.py        # Pydantic request/response models
│   ├── usage.py         # Usage tracking & logging
│   └── ollama_client.py # Ollama HTTP client
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
└── SETUP_GUIDE.md
```
