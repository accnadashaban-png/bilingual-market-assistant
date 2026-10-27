"""Read-only database access, shared by the SQLite and PostgreSQL backends.

SQLite (default, zero setup):
  Layer 2  read-only file:  opened with mode=ro
  Layer 3  authorizer:      SQLite refuses anything except reading the allowed tables
  Layer 4  time limit:      queries that run too long are interrupted
  Layer 5  row cap:         at most `max_rows` rows come back

PostgreSQL (see postgres.py): a SELECT-only role, READ ONLY transactions,
server-side statement_timeout and the same row cap.

Tools always pass user values as bound parameters, never as SQL text.
"""
from __future__ import annotations

import sqlite3
import time
from functools import cached_property
from pathlib import Path
from typing import Any

from market_assistant.core.errors import QueryTimeoutError, UnsafeQueryError
from market_assistant.db.sql_safety import ALLOWED_TABLES, BLOCKED_FUNCTIONS


class BaseDatabase:
    """What the tools need from a database. Subclasses implement `fetch`."""

    dialect = "sqlite"
    max_rows = 50

    def fetch(self, sql: str, params: dict[str, Any] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """Run a read-only query. `limit` lets internal calculations read more than the user-facing row cap."""
        raise NotImplementedError

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        rows = self.fetch(sql, params)
        return next(iter(rows[0].values())) if rows else None

    def is_ready(self) -> bool:
        try:
            return bool(self.scalar("SELECT COUNT(ticker) FROM companies"))
        except Exception:
            return False

    @cached_property
    def latest_date(self) -> str:
        return str(self.scalar("SELECT MAX(date) FROM daily_prices"))

    @cached_property
    def companies(self) -> list[dict[str, Any]]:
        return self.fetch("SELECT ticker, name_en, name_ar, sector, country, currency FROM companies ORDER BY ticker")

    @cached_property
    def sectors(self) -> list[str]:
        return sorted({c["sector"] for c in self.companies})

    def schema_text(self) -> str:
        """Short schema description for the planner prompt."""
        return (
            f"Database: {self.dialect}\n"
            "companies(ticker, name_en, name_ar, sector, country 'US'|'SA'|'EG', exchange, currency, shares_outstanding_m)\n"
            "daily_prices(ticker, date 'YYYY-MM-DD', open, high, low, close, volume)\n"
            "indicators(ticker, date, ma50, ma200, rsi14, high_52w, low_52w, avg_volume_20d)\n"
            "signals(ticker, date, signal_type, direction 'bullish'|'bearish', level, note)  -- signal_type: "
            "52w_high_breakout, 52w_low_breakdown, golden_cross, death_cross, rsi_oversold, rsi_overbought, volume_spike\n"
            "fundamentals(ticker, fiscal_year, revenue_m, net_income_m, eps)\n"
            f"Latest trading date: {self.latest_date}. Sectors: {', '.join(self.sectors)}."
        )


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    if action == sqlite3.SQLITE_SELECT:  # recursive CTEs (SQLITE_RECURSIVE) fall through and are denied
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if (arg1 or "").lower() in ALLOWED_TABLES else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() in BLOCKED_FUNCTIONS else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class Database(BaseDatabase):
    """SQLite backend."""

    def __init__(self, path: Path, timeout_s: float = 2.0, max_rows: int = 50):
        self.path = Path(path)
        self.timeout_s = timeout_s
        self.max_rows = max_rows

    def _connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} not found. Run: python scripts/generate_data.py")
        con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.set_authorizer(_authorizer)
        deadline = time.monotonic() + self.timeout_s
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 5_000)
        return con

    def fetch(self, sql: str, params: dict[str, Any] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            rows = con.execute(sql, params or {}).fetchmany(limit or self.max_rows)
            return [dict(r) for r in rows]
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise QueryTimeoutError(str(exc)) from exc
            raise
        except sqlite3.DatabaseError as exc:
            if "not authorized" in str(exc):
                raise UnsafeQueryError(str(exc)) from exc
            raise
        finally:
            con.close()


def open_database(settings: Any) -> BaseDatabase:
    """SQLite by default; PostgreSQL when MA_DATABASE_URL is set."""
    url = getattr(settings, "database_url", None)
    if url:
        from .postgres import PostgresDatabase

        return PostgresDatabase(url, settings.query_timeout_s, settings.max_rows)
    return Database(settings.db_path, settings.query_timeout_s, settings.max_rows)
