import sqlite3

import pytest
from pydantic import ValidationError

from market_assistant.agent.language import detect_dialect, detect_language, normalize_arabic
from market_assistant.agent.tools import TOOLS, call_tool, tool_specs
from market_assistant.core.errors import NotFoundError


def raw(db, sql, params=()):
    con = sqlite3.connect(db.path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_generated_data_is_consistent(db) -> None:
    assert raw(db, "SELECT COUNT(*) FROM companies")[0][0] == 90
    assert raw(db, "SELECT MIN(rsi14) >= 0 AND MAX(rsi14) <= 100 FROM indicators")[0][0] == 1
    assert raw(db, "SELECT COUNT(*) FROM daily_prices WHERE low > high OR close <= 0")[0][0] == 0
    # the stored 50-day average matches a fresh calculation
    closes = [r[0] for r in raw(db, "SELECT close FROM daily_prices WHERE ticker='ATLS' ORDER BY date DESC LIMIT 50")]
    stored = raw(db, "SELECT ma50 FROM indicators WHERE ticker='ATLS' ORDER BY date DESC LIMIT 1")[0][0]
    assert stored == pytest.approx(sum(closes) / 50, abs=0.01)


def test_quote_matches_the_database(db) -> None:
    row = call_tool(db, "quote", {"ticker": "atls"}).rows[0]
    last, prev = [r[0] for r in raw(db, "SELECT close FROM daily_prices WHERE ticker='ATLS' ORDER BY date DESC LIMIT 2")]
    assert row["close"] == last
    assert row["change_pct"] == round((last / prev - 1) * 100, 2)


def test_ticker_without_market_suffix_resolves(db) -> None:
    assert call_tool(db, "quote", {"ticker": "nltc"}).rows[0]["ticker"] == "NLTC.CA"


def test_top_movers_are_sorted_and_filtered(db) -> None:
    rows = call_tool(db, "top_movers", {"period": "1w", "country": "EG", "limit": 5}).rows
    assert len(rows) == 5 and all(r["country"] == "EG" for r in rows)
    assert [r["change_pct"] for r in rows] == sorted((r["change_pct"] for r in rows), reverse=True)


def test_list_companies_counts(db, bot) -> None:
    assert len(call_tool(db, "list_companies", {"country": "SA"}).rows) == 29
    everything = call_tool(db, "list_companies", {})
    assert everything.extra["total"] == 90 and len(everything.rows) == db.max_rows  # the table is capped, the count is not
    assert "**90**" in bot.ask("How many companies are there?").answer


def test_unknown_ticker_and_bad_args(db) -> None:
    with pytest.raises(NotFoundError):
        call_tool(db, "quote", {"ticker": "NOPE"})
    with pytest.raises(ValidationError):
        call_tool(db, "top_movers", {"period": "10y"})
    with pytest.raises(ValidationError):
        call_tool(db, "compare", {"tickers": ["ATLS"]})


def test_user_text_is_a_parameter_not_sql(db) -> None:
    with pytest.raises(NotFoundError):
        call_tool(db, "quote", {"ticker": "ATLS' OR '1'='1"})


def test_every_tool_has_a_schema() -> None:
    specs = {s["name"]: s for s in tool_specs()}
    assert set(specs) == set(TOOLS) and all("properties" in s["parameters"] for s in specs.values())


@pytest.mark.parametrize("text,lang,dialect", [
    ("سهم أطلس بكام النهارده؟", "ar", "egyptian"),
    ("شلون السوق الحين؟", "ar", "gulf"),
    ("شو صار بالسهم هلق؟", "ar", "levantine"),
    ("ما هو سعر السهم؟", "ar", "msa"),
    ("What's the price?", "en", "en"),
])
def test_language_and_dialect(text: str, lang: str, dialect: str) -> None:
    assert detect_language(text) == lang and detect_dialect(text) == dialect


def test_arabic_normalization() -> None:
    assert normalize_arabic("أداءُ الشركةِ إلى") == "اداء الشركه الي"
