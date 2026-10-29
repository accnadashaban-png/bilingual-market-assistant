"""Live data, saved conversations, charts and follow-up suggestions."""
import random
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from market_assistant.agent.extras import COMPANY, MARKET, chart_for, suggestions
from market_assistant.agent.pipeline import Assistant
from market_assistant.agent.planner import Memory, RulePlanner
from market_assistant.agent.tools import call_tool
from market_assistant.api.app import create_app
from market_assistant.core.history import ChatHistory, mask
from market_assistant.db.builder import ImportError_, PriceRow
from market_assistant.db.database import Database
from market_assistant.db.live import LiveFeed, append_prices, next_trading_day, simulate_day

USER, OTHER = "person-one", "person-two"


@pytest.fixture
def live_db(tmp_path, db_path):
    path = tmp_path / "live.db"
    shutil.copyfile(db_path, path)
    return path


# ---------- live data ----------

def test_new_trading_day_is_answered_without_a_restart(live_db, settings) -> None:
    bot = Assistant(settings.model_copy(update={"db_path": live_db}))
    bot.db.refresh_s = 0
    before = bot.db.status()
    added = simulate_day(live_db, random.Random(7))
    after = bot.db.status()
    assert after["latest_date"] == added["latest_date"] > before["latest_date"]
    assert after["price_rows"] == before["price_rows"] + added["prices"]
    quote = call_tool(bot.db, "quote", {"ticker": "ATLS"}).rows[0]
    assert quote["date"] == added["latest_date"]


def test_append_recomputes_indicators_and_rejects_unknown_tickers(live_db) -> None:
    db = Database(live_db)
    latest = date.fromisoformat(db.latest_date)
    day = next_trading_day(latest)
    assert day.weekday() < 5
    row = PriceRow(ticker="ATLS", date=day, open=100, high=120, low=99, close=119, volume=10_000_000)
    result = append_prices(live_db, [row])
    assert result == {"prices": 1, "companies": 1, "signals": result["signals"], "latest_date": day.isoformat()}
    ind = db.fetch("SELECT ma50, rsi14 FROM indicators WHERE ticker = 'ATLS' AND date = :d", {"d": day.isoformat()})
    assert ind and ind[0]["ma50"] is not None and ind[0]["rsi14"] is not None
    spike = db.fetch("SELECT signal_type FROM signals WHERE ticker = 'ATLS' AND date = :d", {"d": day.isoformat()})
    assert {"volume_spike"} <= {s["signal_type"] for s in spike}  # 10M shares is far above its 20-day average
    with pytest.raises(ImportError_, match="unknown tickers"):
        append_prices(live_db, [row.model_copy(update={"ticker": "NOPE"})])
    corrected = append_prices(live_db, [row.model_copy(update={"close": 101.0, "high": 101.0})])  # same day: corrected
    assert corrected["prices"] == 1 and db.scalar("SELECT close FROM daily_prices WHERE ticker = 'ATLS' AND date = :d",
                                                  {"d": day.isoformat()}) == 101.0


def test_new_company_is_recognised_by_name_after_the_refresh(live_db, settings) -> None:
    import sqlite3

    bot = Assistant(settings.model_copy(update={"db_path": live_db}))
    bot.db.refresh_s = 0
    assert RulePlanner(bot.db).find_tickers("price of zephyr labs") == []
    con = sqlite3.connect(live_db)
    with con:
        con.execute("INSERT INTO companies VALUES ('ZPHR', 'Zephyr Labs', 'زفير لابز', 'Technology', 'US', 'NASDAQ', 'USD', 10)")
        con.execute("INSERT INTO daily_prices SELECT 'ZPHR', date, open, high, low, close, volume FROM daily_prices WHERE ticker = 'ATLS'")
    con.close()
    assert bot.rules.find_tickers("price of zephyr labs") == ["ZPHR"]


def test_live_feed_thread_adds_days(live_db) -> None:
    feed = LiveFeed(live_db, every_s=0.05, seed=1)
    feed.start()
    try:
        import time
        deadline = time.monotonic() + 5
        while feed.days_added < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        feed.stop()
    assert feed.days_added >= 2 and not feed.running


# ---------- charts and suggestions ----------

def test_chart_is_the_closing_prices_behind_the_answer(bot) -> None:
    result = call_tool(bot.db, "compare", {"tickers": ["ATLS", "NLTC.CA"], "period": "3m"})
    c = chart_for(bot.db, [result])
    assert c and c["normalized"] and c["period"] == "3m" and [s["ticker"] for s in c["series"]] == ["ATLS", "NLTC.CA"]
    pts = c["series"][0]["points"]
    assert pts[-1][0] == bot.db.latest_date and len(pts) <= 121
    assert pts[-1][1] == bot.db.scalar("SELECT close FROM daily_prices WHERE ticker = 'ATLS' AND date = :d", {"d": bot.db.latest_date})
    assert chart_for(bot.db, [call_tool(bot.db, "top_movers", {})]) is None  # nothing single to plot


@pytest.mark.parametrize("language", ["en", "ar"])
def test_every_suggestion_is_a_question_the_assistant_answers(bot, language) -> None:
    rules = RulePlanner(bot.db)
    seen = set()
    for tool, args in [("quote", {"ticker": "ATLS"}), ("risk_metrics", {"ticker": "NLTC.CA"}), ("top_movers", {}),
                       ("sector_performance", {}), ("signals", {})]:
        for q in suggestions(bot.db, [call_tool(bot.db, tool, args)], language, limit=10):
            seen.add(q)
            plan = rules.plan(q, Memory())
            assert plan.route == "data", (q, plan)
    assert len(seen) >= len(COMPANY) + len(MARKET) - 1


def test_answers_carry_chart_and_suggestions(bot) -> None:
    a = bot.ask("How did Atlas Robotics do this year?")
    assert a.chart and a.chart["period"] == "ytd" and len(a.suggestions) == 3
    assert all("this year" not in s for s in a.suggestions)  # never suggests the question just answered


# ---------- saved conversations ----------

def test_history_round_trip_rename_pin_search_delete() -> None:
    h = ChatHistory(":memory:")
    h.record(USER, "conv-0001", "Price of Atlas, mail me at a@b.co", "Atlas closed at 100.", "data", "en", ["quote"],
             {"tool": "quote", "args": {"ticker": "ATLS"}, "tickers": ["ATLS"]})
    h.record(USER, "conv-0001", "and its risk?", "Volatility 30%.", "data", "en", ["risk_metrics"], {"tool": "risk_metrics"})
    h.record(USER, "conv-0002", "Top gainers", "Sinai Energy led.", "data", "en", ["top_movers"], {})
    assert [c["id"] for c in h.conversations(USER)] == ["conv-0002", "conv-0001"]
    assert "[email]" in h.messages("conv-0001")[0]["content"] and h.conversations(USER, "volatility")[0]["id"] == "conv-0001"
    assert h.pin("conv-0001", USER, True) and h.conversations(USER)[0]["id"] == "conv-0001"
    assert not h.rename("conv-0001", OTHER, "stolen") and h.rename("conv-0001", USER, "Atlas check")
    assert h.conversations(USER)[0]["title"] == "Atlas check" and h.conversations(OTHER) == []
    assert not h.delete("conv-0002", OTHER) and h.delete("conv-0002", USER)
    assert h.users()[0]["messages"] == 4 and h.forget_user(USER) == {"users": 1, "conversations": 1, "messages": 4}
    assert h.stats() == {"people": 0, "conversations": 0, "messages": 0}
    assert mask("card 4111 1111 1111 1111 ok") == "card [card] ok"


def test_conversation_survives_a_restart_and_follow_ups_still_work(tmp_path, settings) -> None:
    s = settings.model_copy(update={"history_db": tmp_path / "history.db"})
    first = Assistant(s)
    a = first.ask("What's the price of Atlas Robotics?", user_id=USER)
    second = Assistant(s)  # a new process: nothing in memory
    b = second.ask("and its revenue?", a.session_id, USER)
    assert b.session_id == a.session_id and b.route == "data" and b.tool_calls[0]["args"]["ticker"] == "ATLS"
    stolen = second.ask("and its revenue?", a.session_id, OTHER)  # someone else's id starts a new chat
    assert stolen.session_id != a.session_id and second.history and second.history.owner(a.session_id) == USER


def test_conversation_api_is_per_person_and_admin_needs_a_key(tmp_path, settings) -> None:
    s = settings.model_copy(update={"history_db": tmp_path / "history.db", "admin_key": SecretStr("admin-key-123")})
    client = TestClient(create_app(s))
    r = client.post("/v1/ask", json={"message": "Top gainers this week", "user_id": USER}).json()
    conv = r["session_id"]
    assert client.get(f"/v1/conversations?user_id={USER}").json()["conversations"][0]["id"] == conv
    assert client.get(f"/v1/conversations/{conv}?user_id={OTHER}").status_code == 404
    assert client.patch(f"/v1/conversations/{conv}?user_id={USER}", json={"title": "Movers"}).status_code == 200
    assert client.get(f"/v1/conversations?user_id={USER}").json()["conversations"][0]["title"] == "Movers"
    assert client.get("/v1/conversations?user_id=x").status_code == 422  # ids are 6-64 safe characters
    assert client.get("/v1/admin/people").status_code == 401
    people = client.get("/v1/admin/people", headers={"X-Admin-Key": "admin-key-123"}).json()
    assert people["people"][0]["id"] == USER and people["messages"] == 2
    assert client.delete(f"/v1/admin/people/{USER}", headers={"X-Admin-Key": "admin-key-123"}).json()["deleted"]["messages"] == 2
    assert client.delete(f"/v1/conversations/{conv}?user_id={USER}").status_code == 404
    assert client.get("/v1/data/status").json()["companies"] > 0


def test_history_endpoints_are_off_by_default(settings) -> None:
    client = TestClient(create_app(settings))
    assert client.get(f"/v1/conversations?user_id={USER}").status_code == 404
    assert client.get("/v1/admin/people", headers={"X-Admin-Key": ""}).status_code in (403, 404)


def test_a_day_added_between_reads_never_compares_a_day_with_itself(live_db, settings) -> None:
    bot = Assistant(settings.model_copy(update={"db_path": live_db}))
    bot.db.refresh_s = 3600  # the summary is stale on purpose: a new day arrives after it was read
    seen = bot.db.latest_date
    simulate_day(live_db, random.Random(3))
    rows = call_tool(bot.db, "top_movers", {"period": "1d", "limit": 15}).rows
    assert bot.db.latest_date == seen and any(r["change_pct"] != 0 for r in rows)
    bot.db.snapshot(force=True)
    assert bot.db.latest_date > seen  # and the next refresh picks the new day up


def test_demo_feed_never_writes_a_future_date(live_db) -> None:
    db = Database(live_db)
    db.refresh_s = 0
    latest = date.fromisoformat(db.latest_date)
    rows_before = db.status()["price_rows"]
    tick = simulate_day(live_db, random.Random(5), today=latest)  # already up to date: prices move within the day
    assert tick["mode"] == "tick" and tick["latest_date"] == latest.isoformat() and db.status()["price_rows"] == rows_before
    row = db.fetch("SELECT high, low, close FROM daily_prices WHERE ticker = 'ATLS' AND date = :d", {"d": latest.isoformat()})[0]
    assert row["low"] <= row["close"] <= row["high"]
    assert simulate_day(live_db, random.Random(5), today=next_trading_day(latest))["mode"] == "new_day"
