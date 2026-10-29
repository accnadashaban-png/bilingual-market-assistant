"""PostgreSQL backend: every tool must return the same answers as SQLite, and the role must stay read-only.

Runs against MA_TEST_PG_URL (CI uses a postgres:16 service) or a local `pgserver`; skipped otherwise.
"""
import os
import sys
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import load_postgres  # noqa: E402

from market_assistant.agent.tools import call_tool  # noqa: E402
from market_assistant.core.errors import QueryTimeoutError, UnsafeQueryError  # noqa: E402
from market_assistant.db.postgres import PostgresDatabase, to_pyformat  # noqa: E402


@pytest.fixture(scope="module")
def admin_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    url = os.environ.get("MA_TEST_PG_URL")
    if url:
        return url
    pgserver = pytest.importorskip("pgserver")
    try:
        server = pgserver.get_server(str(tmp_path_factory.mktemp("pg")), cleanup_mode="stop")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no local PostgreSQL: {exc}")
    return str(server.get_uri())


@pytest.fixture(scope="module")
def pg(admin_url: str, db_path: Path):
    ro_url = load_postgres.load(admin_url, db_path, "test_ro_pw", timeout_ms=1500)
    db = PostgresDatabase(ro_url, timeout_s=1.5, max_rows=50)
    yield db
    db.close()


PARITY = [
    ("quote", {"ticker": "ATLS"}),
    ("performance", {"ticker": "NLTC", "period": "ytd"}),
    ("top_movers", {"period": "1w", "country": "EG", "limit": 5}),
    ("top_movers", {"period": "1m", "direction": "down"}),
    ("sector_performance", {"period": "3m", "country": "SA"}),
    ("compare", {"tickers": ["ATLS", "NOVC", "TWQT.SR"], "period": "6m"}),
    ("screen", {"above_ma50": True, "rsi_max": 60, "country": "US"}),
    ("signals", {"signal_type": "52w_high_breakout", "days": 10}),
    ("company_profile", {"ticker": "FLHC.SR"}),
    ("fundamentals", {"ticker": "CRBK"}),
    ("market_overview", {"country": "EG"}),
    ("list_companies", {"sector": "Technology"}),
    ("run_sql", {"sql": "SELECT sector, COUNT(ticker) AS n FROM companies GROUP BY sector ORDER BY sector"}),
]


def normalize(rows):
    return [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]


@pytest.mark.parametrize("tool,args", PARITY)
def test_every_tool_matches_sqlite(pg, db, tool, args) -> None:
    pg_rows, lite_rows = normalize(call_tool(pg, tool, args).rows), normalize(call_tool(db, tool, args).rows)
    assert [list(r) for r in pg_rows] == [list(r) for r in lite_rows]  # same rows, same columns, same order
    for p, q in zip(pg_rows, lite_rows, strict=True):
        for k in p:
            if isinstance(p[k], float) or isinstance(q[k], float):
                # NUMERIC (PostgreSQL) and REAL (SQLite) can round a value ending in exactly .005 differently
                assert p[k] == pytest.approx(q[k], abs=0.011), (k, p, q)
            else:
                assert p[k] == q[k], (k, p, q)


def test_read_only_role_cannot_write(pg) -> None:
    for attack in ("DELETE FROM companies", "UPDATE daily_prices SET close = 0", "CREATE TABLE x (a int)"):
        with pytest.raises(UnsafeQueryError):
            pg.fetch(attack)
    assert pg.scalar("SELECT COUNT(ticker) FROM companies") == 90


def test_role_cannot_read_system_secrets(pg) -> None:
    with pytest.raises(UnsafeQueryError):  # permission denied by the role itself
        pg.fetch("SELECT rolpassword FROM pg_authid")


def test_server_side_timeout(pg) -> None:
    with pytest.raises(QueryTimeoutError):
        pg.fetch("SELECT COUNT(*) FROM daily_prices a, daily_prices b, daily_prices c")


def test_validator_blocks_postgres_tricks(pg) -> None:
    for sql in ("SELECT pg_sleep(10)", "SELECT pg_read_file('/etc/passwd')", "SELECT current_setting('data_directory')",
                "SELECT usename FROM pg_user", "SELECT ticker FROM public.companies"):
        with pytest.raises(UnsafeQueryError):
            call_tool(pg, "run_sql", {"sql": sql})


def test_param_conversion_keeps_casts() -> None:
    assert to_pyformat("SELECT :a::text, x FROM t WHERE y = :b") == "SELECT %(a)s::text, x FROM t WHERE y = %(b)s"
