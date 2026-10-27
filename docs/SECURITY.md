# Security model

The assistant turns natural-language questions, and sometimes model-written SQL, into database reads.
The threat model assumes that **both the user and the model can be adversarial**: a user may try prompt
injection, and a model that was successfully injected may return malicious plans or SQL.

## Layers

| # | Layer | Where | Stops |
|---|---|---|---|
| 1 | Typed tools with bound parameters | `agent/tools.py` | SQL injection through user text; invalid arguments (Pydantic) |
| 2 | sqlglot validation of free SQL | `db/sql_safety.py` | non-SELECT, stacked statements, comments, `SELECT *`, recursive CTEs, unknown or schema-qualified tables, dangerous functions (`load_extension`, `pg_sleep`, `pg_read_file`, `set_config`, `dblink`...) |
| 3 | Read-only access | SQLite `mode=ro`; PostgreSQL role with SELECT only, `default_transaction_read_only` and READ ONLY sessions | any write that slips past layer 2 |
| 4 | Database-side permission checks | SQLite authorizer; PostgreSQL grants | reads of tables outside the allow-list, system catalogs |
| 5 | Time limits and row caps | progress handler / `statement_timeout`; fetch caps | denial of service through heavy queries |
| 6 | Output gate | `agent/writer.py` | SQL, raw rows, IDs or code leaking into replies; missing advice disclaimer |
| 7 | API limits | `api/app.py` | oversized input, long session ids, request floods (token bucket per client) |
| 8 | Secrets hygiene | settings, `.env.example`, gitleaks in CI | keys in code or history; keys in API responses |

## Tested attacks

`tests/security/test_redteam.py`, `tests/db/test_sql_safety.py` and `tests/db/test_postgres.py` cover, among others:

- prompt injection in English and Arabic ("ignore your instructions", "تجاهل التعليمات")
- stacked queries, hidden comments, `UNION` into `sqlite_master`, `ATTACH`, `PRAGMA`, `load_extension`, `randomblob`
- a `DELETE` hidden inside a CTE, recursive CTEs, `SELECT ... INTO`
- PostgreSQL: `pg_sleep`, `pg_read_file`, `current_setting`, `pg_user`, schema-qualified tables, direct writes as the read-only role, reading `pg_authid`
- a **compromised model** that returns malicious SQL plans, leaks SQL in its answer, or invents a `shell` tool
- direct API abuse: raw SQL through `/v1/tools/run_sql`, injected tickers, oversized payloads, path traversal

After each attack the tests assert that the data is unchanged and that nothing internal reached the reply.

## Reporting

This is a portfolio project on fictional data. If you spot a weakness, please open an issue.
