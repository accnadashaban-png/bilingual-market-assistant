# Changelog

## 3.3.0

- **Bigger demo market:** 90 fictional companies (30 per market, 10 sectors) over about two years (520 trading days):
  46,800 daily prices and 5,122 technical signals. Names avoid real listings and everyday words.
- **Evaluation grows with the data:** 1,296 cases (from 536), still 100%, including every company in five registers.
- Fixed: "how many companies?" counted the rows shown (capped at 50) instead of the database; the count now comes from
  the database and the answer says when the table is shortened.
- PostgreSQL parity test allows the one-cent rounding difference between NUMERIC and REAL at exact .005 values.

## 3.2.0

- **Live data:** the database summary (companies, latest trading day, row count) refreshes every few seconds, and
  company names follow it, so new prices, days and companies are answered without a restart. Periods are always
  counted back from the same latest day, so a day added between two reads can never compare a day with itself.
- **Live price updates:** `append_prices` / `python -m market_assistant add-prices` insert or correct prices and
  recompute indicators and signals for the affected companies in one transaction. In the app: upload new prices, or
  switch on the demo feed (fills in trading days up to today, then moves today's prices; never a future date).
- **Saved conversations:** people, conversations and messages in SQLite (`MA_HISTORY_DB`); a sidebar by day with
  search across messages, rename, pin and delete; chats and follow-ups survive restarts; per-person access, admin
  endpoints behind `MA_ADMIN_KEY`, erase a person; emails and card numbers are masked before saving.
- **Richer answers:** price charts (several companies rebased to 100), follow-up questions in the user's language
  (each one tested to be answerable), copy and CSV export.
- **New look:** warm light and dark themes with a switch (light, system, dark), a serif heading face, and a phone
  layout with a slide-out sidebar. Same theme on the demo dashboard and the widget.
- `GET /v1/data/status`; Connections explains what a connection is and shows the data's freshness.
- 172 tests; evaluation still 536 cases at 100%.

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
