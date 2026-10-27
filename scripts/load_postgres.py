"""Load the demo data into PostgreSQL and create the assistant's read-only role.

    python scripts/generate_data.py
    python scripts/load_postgres.py --admin-url postgresql://postgres:postgres@localhost:5432/postgres

The assistant then connects as `market_ro`, which can only SELECT the five tables,
and every session is forced to READ ONLY with a statement timeout.
Set MA_RO_PASSWORD to choose the role's password (default is for local demos only).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

HERE = Path(__file__).parent
TABLES = {
    "companies": """ticker TEXT PRIMARY KEY, name_en TEXT NOT NULL, name_ar TEXT NOT NULL, sector TEXT NOT NULL,
                    country TEXT NOT NULL, exchange TEXT NOT NULL, currency TEXT NOT NULL,
                    shares_outstanding_m NUMERIC(14,1) NOT NULL""",
    "daily_prices": """ticker TEXT NOT NULL REFERENCES companies(ticker), date TEXT NOT NULL,
                       open NUMERIC(14,2), high NUMERIC(14,2), low NUMERIC(14,2), close NUMERIC(14,2) NOT NULL,
                       volume BIGINT NOT NULL, PRIMARY KEY (ticker, date)""",
    "indicators": """ticker TEXT NOT NULL, date TEXT NOT NULL, ma50 NUMERIC(14,2), ma200 NUMERIC(14,2),
                     rsi14 NUMERIC(6,2), high_52w NUMERIC(14,2), low_52w NUMERIC(14,2), avg_volume_20d BIGINT,
                     PRIMARY KEY (ticker, date)""",
    "signals": """ticker TEXT NOT NULL, date TEXT NOT NULL, signal_type TEXT NOT NULL, direction TEXT NOT NULL,
                  level NUMERIC(18,2), note TEXT""",
    "fundamentals": """ticker TEXT NOT NULL, fiscal_year INTEGER NOT NULL, revenue_m NUMERIC(18,1),
                       net_income_m NUMERIC(18,1), eps NUMERIC(10,2), PRIMARY KEY (ticker, fiscal_year)""",
}
RO_ROLE = "market_ro"


def load(admin_url: str, sqlite_path: Path, ro_password: str, timeout_ms: int = 2000) -> str:
    """Create tables, copy the rows, create the read-only role. Returns the role's connection URL."""
    src = sqlite3.connect(sqlite_path)
    with psycopg.connect(admin_url, autocommit=True) as con:
        for table in reversed(list(TABLES)):
            con.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table)))
        for table, columns in TABLES.items():
            con.execute(sql.SQL("CREATE TABLE {} (" + columns + ")").format(sql.Identifier(table)))
            rows = src.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - fixed table names
            if rows:
                marks = sql.SQL(", ").join(sql.Placeholder() * len(rows[0]))
                with con.cursor() as cur:
                    cur.executemany(sql.SQL("INSERT INTO {} VALUES ({})").format(sql.Identifier(table), marks), rows)
        con.execute("CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(date)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date)")

        exists = con.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_ROLE,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        con.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE")
                    .format(sql.Identifier(RO_ROLE), sql.Literal(ro_password)))
        db = con.execute("SELECT current_database()").fetchone()[0]  # type: ignore[index]
        con.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
        con.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(RO_ROLE)))
        con.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(RO_ROLE)))
        con.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(RO_ROLE)))
        for table in TABLES:
            con.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(table), sql.Identifier(RO_ROLE)))
        con.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(RO_ROLE)))
        con.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(sql.Identifier(RO_ROLE), sql.Literal(timeout_ms)))
    src.close()

    parts = urlsplit(admin_url)
    host = parts.netloc.split("@")[-1]
    return urlunsplit((parts.scheme, f"{RO_ROLE}:{ro_password}@{host}", parts.path, parts.query, ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-url", required=True, help="URL of a role that can create tables and roles")
    parser.add_argument("--sqlite", type=Path, default=HERE.parent / "data" / "market.db")
    args = parser.parse_args()
    ro_url = load(args.admin_url, args.sqlite, os.environ.get("MA_RO_PASSWORD", "market_ro_demo"))
    print(f"Loaded. Point the assistant at the read-only role:\n  MA_DATABASE_URL={ro_url.split('@')[0].split(':')[0]}://{RO_ROLE}:***@{ro_url.split('@')[-1]}")


if __name__ == "__main__":
    main()
