"""Local app: settings store, CSV import and the security of the settings endpoints."""
import csv
import io
import json
import math
import os
import random
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from market_assistant.core.settings import Settings
from market_assistant.db.builder import ImportError_, import_csv
from market_assistant.desktop.server import create_desktop_app
from market_assistant.desktop.store import ConfigStore, LocalConfig, ProviderEntry, mask, mask_url

PORT = 8765
TOKEN = "test-app-token"
SECRET = "sk-test-SECRET-1234567890"


def provider_transport(status: int = 200, body: str = "ok") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text=body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path, db_path):
    base = Settings(db_path=db_path, _env_file=None)  # type: ignore[call-arg]
    app = create_desktop_app(tmp_path, PORT, base=base, transport=provider_transport(), token=TOKEN)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def admin(client, method, path, **kw):
    return client.request(method, path, headers={"X-App-Token": TOKEN, **kw.pop("headers", {})}, **kw)


# ---------- store ----------

def test_store_round_trip_masks_secrets(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    config = LocalConfig(providers=[ProviderEntry(name="groq", base_url="https://api.groq.com/openai/v1", model="m",
                                                  api_key=SecretStr(SECRET))])
    store.save(config)
    assert store.load().providers[0].api_key.get_secret_value() == SECRET
    public = json.dumps(ConfigStore.public_view(store.load()), ensure_ascii=False)
    assert SECRET not in public and "••••7890" in public
    if os.name == "posix":
        assert oct(store.path.stat().st_mode & 0o777) == "0o600"


def test_masks_and_url_rules() -> None:
    assert mask("abcdefghijkl") == "••••ijkl" and mask("short") == "••••" and mask("") == ""
    assert mask_url("postgresql://ro:pa55word@db:5432/market") == "postgresql://ro:••••@db:5432/market"
    with pytest.raises(ValidationError):
        ProviderEntry(name="x", base_url="http://evil.example.com/v1", model="m")  # plain http only for local servers
    ProviderEntry(name="local", base_url="http://localhost:11434/v1", model="m")


# ---------- security of the settings endpoints ----------

def test_settings_need_the_launch_token(client) -> None:
    assert client.get("/v1/app/settings").status_code == 401
    assert client.get("/v1/app/settings", headers={"X-App-Token": "guess"}).status_code == 401
    assert admin(client, "GET", "/v1/app/settings").status_code == 200


def test_other_websites_are_rejected(client) -> None:
    r = admin(client, "PUT", "/v1/app/providers", json=[], headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    r = client.post("/v1/ask", json={"message": "hi"}, headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_dns_rebinding_host_is_rejected(client) -> None:
    assert client.get("/app", headers={"Host": "evil.example.com"}).status_code == 421
    assert client.get("/health", headers={"Host": "attacker.test:8765"}).status_code == 421


def test_app_page_embeds_token_with_strict_policy(client) -> None:
    r = client.get("/app")
    assert TOKEN in r.text and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_keys_are_saved_but_never_returned(client) -> None:
    body = [{"name": "groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama", "api_key": SECRET}]
    r = admin(client, "PUT", "/v1/app/providers", json=body)
    assert r.status_code == 200 and SECRET not in r.text
    settings = admin(client, "GET", "/v1/app/settings")
    assert SECRET not in settings.text and settings.json()["providers"][0]["has_key"] is True
    # saving again with an empty key keeps the stored one
    body[0]["api_key"] = ""
    admin(client, "PUT", "/v1/app/providers", json=body)
    assert admin(client, "GET", "/v1/app/settings").json()["providers"][0]["api_key"] == "••••7890"
    # the key never shows up in any other response either
    assert SECRET not in client.post("/v1/ask", json={"message": "hello"}).text
    assert SECRET not in client.get("/v1/llm/providers").text


def test_invalid_providers_are_refused(client) -> None:
    dup = [{"name": "a", "base_url": "https://x.test/v1", "model": "m"}] * 2
    assert admin(client, "PUT", "/v1/app/providers", json=dup).status_code == 422
    insecure = [{"name": "a", "base_url": "http://x.example.com/v1", "model": "m"}]
    assert admin(client, "PUT", "/v1/app/providers", json=insecure).status_code == 422


def test_provider_test_reports_the_problem_kind_only(tmp_path, db_path) -> None:
    base = Settings(db_path=db_path, _env_file=None)  # type: ignore[call-arg]
    app = create_desktop_app(tmp_path, PORT, base=base, token=TOKEN,
                             transport=provider_transport(429, '{"error": "You exceeded your current quota"}'))
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    admin(c, "PUT", "/v1/app/providers", json=[{"name": "free", "base_url": "https://x.test/v1", "model": "m", "api_key": SECRET}])
    r = admin(c, "POST", "/v1/app/providers/free/test")
    assert r.json() == {"ok": False, "problem": "quota"}
    assert admin(client_for_ok(tmp_path, db_path), "POST", "/v1/app/providers/nope/test").status_code == 404


def client_for_ok(tmp_path, db_path):
    base = Settings(db_path=db_path, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_desktop_app(tmp_path / "ok", PORT, base=base, token=TOKEN, transport=provider_transport()),
                      base_url=f"http://127.0.0.1:{PORT}")


# ---------- data sources and CSV import ----------

def make_csvs(tickers=("AAA", "BBB"), days=260, seed=3) -> dict[str, str]:
    rng = random.Random(seed)
    comp, prices = io.StringIO(), io.StringIO()
    cw = csv.writer(comp)
    cw.writerow(["ticker", "name_en", "name_ar", "sector", "country", "exchange", "currency", "shares_outstanding_m"])
    pw = csv.writer(prices)
    pw.writerow(["ticker", "date", "open", "high", "low", "close", "volume"])
    for t in tickers:
        cw.writerow([t, f"{t} Holdings", "", "Technology", "us", "NYSE", "usd", 100])
        close, day = 50.0, date(2025, 1, 1)
        for _ in range(days):
            while day.weekday() >= 5:
                day += timedelta(days=1)
            new = round(max(1, close * math.exp(rng.gauss(0, 0.02))), 2)
            high, low = round(max(close, new) * 1.01, 2), round(min(close, new) * 0.99, 2)
            pw.writerow([t, day.isoformat(), close, high, low, new, rng.randint(1000, 9000)])
            close, day = new, day + timedelta(days=1)
    return {"companies": comp.getvalue(), "daily_prices": prices.getvalue()}


def test_csv_import_switches_data_live(client) -> None:
    r = admin(client, "POST", "/v1/app/import-csv", json=make_csvs())
    assert r.status_code == 200 and r.json()["imported"]["companies"] == 2
    settings = admin(client, "GET", "/v1/app/settings").json()
    assert settings["data"]["kind"] == "csv" and settings["companies"] == 2
    answer = client.post("/v1/ask", json={"message": "What's the price of AAA?"}).json()
    assert answer["route"] == "data" and "AAA Holdings" in answer["answer"]


def test_short_history_gets_an_honest_answer_not_a_crash(client) -> None:
    admin(client, "POST", "/v1/app/import-csv", json=make_csvs(days=120))
    for question in ("How risky is AAA Holdings?", "Top gainers this year", "How did AAA do over the past year?"):
        answer = client.post("/v1/ask", json={"message": question}).json()
        assert "doesn't go back far enough" in answer["answer"], question
    stream = client.post("/v1/ask/stream", json={"message": "How risky is AAA Holdings?"}).text
    assert "doesn't go back far enough" in stream
    # a period that fits still works
    answer = client.post("/v1/ask", json={"message": "How risky is AAA Holdings over 3 months?"}).json()
    assert "volatility" in answer["answer"].lower()


def test_unexpected_tool_bug_still_ends_with_a_message(client, monkeypatch) -> None:
    from market_assistant.agent import tools

    def broken(db, a):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")
    monkeypatch.setitem(tools.TOOLS, "top_movers", tools.TOOLS["top_movers"].__class__(
        **{**tools.TOOLS["top_movers"].__dict__, "fn": broken}))
    answer = client.post("/v1/ask", json={"message": "Top gainers this week"}).json()
    assert "went wrong" in answer["answer"] and "boom" not in answer["answer"]


def test_bad_csv_reports_the_line_and_keeps_old_data(client) -> None:
    files = make_csvs()
    files["daily_prices"] = files["daily_prices"].replace("\nAAA,", "\nAAA,2025-13-45-", 1)
    r = admin(client, "POST", "/v1/app/import-csv", json=files)
    assert r.status_code == 422 and "daily_prices.csv line 2" in r.json()["detail"]
    assert admin(client, "GET", "/v1/app/settings").json()["data"]["kind"] == "demo"


def test_bad_data_source_keeps_the_working_one(client, tmp_path) -> None:
    assert admin(client, "POST", "/v1/app/data", json={"kind": "sqlite", "sqlite_path": str(tmp_path / "missing.db")}).status_code == 422
    assert admin(client, "POST", "/v1/app/data", json={"kind": "postgres", "postgres_url": "mysql://x"}).status_code == 422
    assert client.post("/v1/ask", json={"message": "Top gainers this week"}).json()["route"] == "data"


def test_import_validates_and_computes_indicators(tmp_path) -> None:
    files = make_csvs(("ZZZ",), days=230)
    (tmp_path / "companies.csv").write_text(files["companies"], encoding="utf-8")
    (tmp_path / "daily_prices.csv").write_text(files["daily_prices"], encoding="utf-8")
    counts = import_csv(tmp_path, tmp_path / "out.db")
    assert counts["companies"] == 1 and counts["prices"] == 230
    import sqlite3

    con = sqlite3.connect(tmp_path / "out.db")
    closes = [r[0] for r in con.execute("SELECT close FROM daily_prices WHERE ticker='ZZZ' ORDER BY date DESC LIMIT 50")]
    assert con.execute("SELECT ma50 FROM indicators ORDER BY date DESC LIMIT 1").fetchone()[0] == pytest.approx(sum(closes) / 50, abs=0.01)
    assert con.execute("SELECT country, currency FROM companies").fetchone() == ("US", "USD")
    (tmp_path / "daily_prices.csv").write_text(files["daily_prices"].replace("ZZZ,", "QQQ,", 1), encoding="utf-8")
    with pytest.raises(ImportError_, match="not in companies.csv"):
        import_csv(tmp_path, tmp_path / "out2.db")
