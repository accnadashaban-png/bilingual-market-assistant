# Changelog

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
