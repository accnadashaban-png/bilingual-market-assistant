# Deployment

## Options

| Setup | Command | Database |
|---|---|---|
| Local, no services | `pip install -e . && python scripts/generate_data.py && uvicorn market_assistant.api.app:app` | SQLite file |
| Single container | `docker build -t bilingual-market-assistant . && docker run -p 8000:8000 bilingual-market-assistant` | SQLite inside the image |
| Full stack | `docker compose up --build` | PostgreSQL 16 + loader + API on the read-only role |

## Configuration

All settings are environment variables with the `MA_` prefix (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `MA_DATABASE_URL` | unset (SQLite) | PostgreSQL URL of the **read-only** role |
| `MA_DB_PATH` | `data/market.db` | SQLite file |
| `MA_LLM_PROVIDERS` | `[]` | JSON list of providers for failover |
| `MA_LLM_BASE_URL` / `MA_LLM_API_KEY` / `MA_LLM_MODEL` | Gemini endpoint, empty key | single-provider shortcut |
| `MA_QUERY_TIMEOUT_S` | `2.0` | per-query time limit |
| `MA_MAX_ROWS` | `50` | rows returned to the model or user |
| `MA_RATE_LIMIT_PER_MINUTE` | `30` | per-client token bucket |
| `MA_ALLOWED_ORIGINS` | `["*"]` | CORS origins allowed to embed the widget |

## Operations

- `GET /health`: liveness. `GET /ready`: database readable and LLM mode.
- `GET /metrics`: Prometheus metrics: answers by route and language, tool calls by status, blocked queries,
  LLM calls by provider and outcome, answer latency histogram.
- Logs are JSON lines with a `request_id` that is also returned in the `X-Request-ID` header.
- `GET /v1/llm/providers`: which providers are available or cooling down, with call and failure counts.

## Scaling notes

- The API is stateless except for conversation sessions (in-memory with TTL). Move them to Redis to run replicas.
- The PostgreSQL backend uses a connection pool (default 5 per process).
- Keep the read-only role's password in a secret store and rotate it; the loader can be re-run safely.
