"""PostgreSQL backend.

Defense in depth on the database side:
- connect as a SELECT-only role (see data/load_postgres.py), never as the owner
- every transaction is READ ONLY, so even a mistaken grant can't write
- statement_timeout stops runaway queries on the server
- a connection pool keeps latency low; rows are capped like the SQLite backend
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, cast

import psycopg
from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from market_assistant.core.errors import QueryTimeoutError, UnsafeQueryError
from market_assistant.db.database import BaseDatabase

NAMED_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def to_pyformat(sql: str) -> str:
    """Tools write SQLite-style `:name` parameters; psycopg wants `%(name)s`."""
    return NAMED_PARAM.sub(r"%(\1)s", sql)


def _plain(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


class PostgresDatabase(BaseDatabase):
    dialect = "postgres"

    def __init__(self, url: str, timeout_s: float = 2.0, max_rows: int = 50, pool_size: int = 5):
        self.max_rows = max_rows
        timeout_ms = int(timeout_s * 1000)
        self.pool = ConnectionPool(
            url, min_size=1, max_size=pool_size, open=True,
            kwargs={
                "row_factory": dict_row,
                "cursor_factory": psycopg.ClientCursor,  # typed NULL params work in `:x IS NULL` checks
                "options": f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on",
            },
            configure=self._configure,
        )

    @staticmethod
    def _configure(conn: psycopg.Connection) -> None:
        conn.read_only = True
        conn.autocommit = False

    def fetch(self, sql: str, params: dict[str, Any] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        query = to_pyformat(sql) if params else sql
        try:
            with self.pool.connection() as conn, conn.cursor() as cur:
                cur.execute(query, params or None)
                rows = cur.fetchmany(limit or self.max_rows)
                conn.rollback()  # read-only work: end the transaction without committing anything
        except errors.QueryCanceled as exc:
            raise QueryTimeoutError(str(exc)) from exc
        except (errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction) as exc:
            raise UnsafeQueryError(str(exc)) from exc
        return [{k: _plain(v) for k, v in cast(dict[str, Any], row).items()} for row in rows]

    def close(self) -> None:
        self.pool.close()
