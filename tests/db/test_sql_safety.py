"""Red-team tests: SQL a model could be tricked into writing must never run."""
import sqlite3

import pytest

from market_assistant.core.errors import UnsafeQueryError
from market_assistant.db.sql_safety import validate_select

ATTACKS = [
    "DELETE FROM companies",
    "DROP TABLE daily_prices",
    "UPDATE daily_prices SET close = 0",
    "INSERT INTO companies (ticker) VALUES ('X')",
    "SELECT ticker FROM companies; DROP TABLE companies",
    "SELECT ticker FROM companies -- WHERE 1=0",
    "SELECT ticker /* hidden */ FROM companies",
    "SELECT name FROM sqlite_master",
    "SELECT ticker FROM companies UNION SELECT sql FROM sqlite_master",
    "SELECT load_extension('evil')",
    "SELECT randomblob(1000000000)",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA table_info(companies)",
    "WITH d AS (DELETE FROM companies RETURNING ticker) SELECT ticker FROM d",
    "SELECT * FROM companies",
    "SELECT ticker FROM main.companies",
    "sElEcT ticker FrOm users",
    "CREATE TABLE x (a INT)",
    "SELECT ticker INTO backup FROM companies",
    "VACUUM",
    "",
]


@pytest.mark.parametrize("sql", ATTACKS)
def test_attacks_are_blocked(sql: str) -> None:
    with pytest.raises(UnsafeQueryError):
        validate_select(sql)


def test_safe_select_gets_a_limit() -> None:
    out = validate_select("SELECT ticker, close FROM daily_prices WHERE close > 10", max_rows=20)
    assert out.upper().endswith("LIMIT 20")


def test_large_limit_is_clamped_and_small_one_kept() -> None:
    assert validate_select("SELECT ticker FROM companies LIMIT 10000", 50).upper().endswith("LIMIT 50")
    assert validate_select("SELECT ticker FROM companies LIMIT 5", 50).upper().endswith("LIMIT 5")


def test_ctes_joins_and_count_star_are_allowed() -> None:
    sql = ("WITH last AS (SELECT ticker, MAX(date) AS d FROM daily_prices GROUP BY ticker) "
           "SELECT c.sector, COUNT(*) AS n FROM companies c JOIN last l ON l.ticker = c.ticker GROUP BY c.sector")
    assert "LIMIT" in validate_select(sql).upper()


def test_union_is_wrapped_with_a_limit() -> None:
    out = validate_select("SELECT ticker FROM companies UNION SELECT ticker FROM signals", 10)
    assert out.upper().endswith("LIMIT 10")


def test_database_layer_blocks_on_its_own(db) -> None:
    """Even if the parser were bypassed, the read-only file and authorizer refuse writes and system tables."""
    with pytest.raises((UnsafeQueryError, sqlite3.DatabaseError)):
        db.fetch("DELETE FROM companies")
    with pytest.raises((UnsafeQueryError, sqlite3.DatabaseError)):
        db.fetch("SELECT name FROM sqlite_master")
    assert db.scalar("SELECT COUNT(ticker) FROM companies") == 30


def test_slow_query_is_stopped(db) -> None:
    from market_assistant.core.errors import QueryTimeoutError

    db_fast = type(db)(db.path, timeout_s=0.05, max_rows=5)
    heavy = ("SELECT COUNT(a.close) FROM daily_prices a, daily_prices b, daily_prices c")
    with pytest.raises(QueryTimeoutError):
        db_fast.fetch(heavy)
