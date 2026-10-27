# Contributing

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,postgres,mcp]"
python scripts/generate_data.py
```

## Before opening a pull request

```bash
make lint        # ruff + mypy
make test        # pytest with coverage
make eval        # evaluation gate
```

- Add or update tests next to the code you change (`tests/` mirrors `market_assistant/`).
- A new tool needs: a Pydantic argument model, parameterized SQL that runs on SQLite and PostgreSQL,
  a deterministic ORDER BY, a writer template in both languages, planner routing, a parity case in
  `tests/db/test_postgres.py` and evaluation cases.
- A new evaluation case must compute its expected values from the database, never hard-code them.
- Never commit `.env`, keys or real data. CI runs gitleaks on every push.

## Commit messages

Short imperative subject (`Add risk_metrics tool`), with a body when the reason is not obvious.
