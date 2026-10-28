# Changelog

## 3.1.0

- **Local app** (`python -m market_assistant app`): a private desktop app with chat, data connections and AI
  provider settings. Listens on 127.0.0.1 only, checks the Host header (DNS rebinding) and Origin, protects settings
  with a per-launch token, and never returns saved keys or database passwords, only masked hints.
- **Bring your own data:** CSV import with row-level validation (file and line in every error), an atomic switch
  that keeps the previous data on failure, and indicators and technical signals computed on import; or connect a
  SQLite file or a read-only PostgreSQL user.
- Honest answers on short histories: a period longer than the data now says so instead of measuring from a shorter
  window.
- Unexpected tool failures always end with a clear message, in chat and in the stream.
- `company_profile` uses the latest fiscal year.

## 3.0.0

- **LLM provider failover:** `LLMRouter` over a configurable provider group, with failure-specific cooldowns,
  streaming failover before the first token, per-provider metrics and `GET /v1/llm/providers`.
- **PostgreSQL backend** with a SELECT-only role, READ ONLY sessions, server-side `statement_timeout`, a
  connection pool, a loader script and docker-compose; tool parity with SQLite is tested in CI.
- **New tools:** `signal_performance` (forward returns and hit rate after each signal type) and `risk_metrics`
  (annualized volatility, max drawdown, beta vs market).
- **MCP server** exposing all tools with the same validation.
- **Per-step timing trace** on every answer.
- **Evaluation:** 536 cases (generated coverage across companies, dialects, markets and periods), ordered ranking
  checks, independently recomputed risk numbers, dataset QA tests, run tracking and an optional LLM-as-judge.
- Deterministic tie-breakers on every ranking; precise empty-result and news answers.
- Package reorganized into `core`, `db`, `agent`, `api` and `eval`; tests mirror the package.

## 2.0.0

- LangGraph pipeline, LLM and rule planners, 12 typed tools, sqlglot validation, streaming widget, golden evaluation.
