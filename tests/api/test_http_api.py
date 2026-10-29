import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from market_assistant.agent.pipeline import Assistant
from market_assistant.api.app import create_app


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings, Assistant(settings)))


def test_health_ready_and_metrics(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["database"] is True
    client.post("/v1/ask", json={"message": "hello"})
    assert "assistant_requests_total" in client.get("/metrics").text


def test_ask_returns_answer_session_and_request_id(client: TestClient) -> None:
    r = client.post("/v1/ask", json={"message": "سهم النيل للاتصالات بكام؟"}, headers={"X-Request-ID": "abc123"})
    body = r.json()
    assert r.status_code == 200 and r.headers["X-Request-ID"] == "abc123" and body["request_id"] == "abc123"
    assert body["route"] == "data" and body["session_id"] and "النيل" in body["answer"]


def test_stream_is_server_sent_events(client: TestClient) -> None:
    with client.stream("POST", "/v1/ask/stream", json={"message": "Top gainers this week"}) as r:
        text = "".join(r.iter_text())
    events = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
    assert r.headers["content-type"].startswith("text/event-stream")
    assert events[0]["event"] == "meta" and events[-1]["event"] == "final" and "|" in events[-1]["answer"]


def test_input_validation(client: TestClient) -> None:
    assert client.post("/v1/ask", json={"message": ""}).status_code == 422
    assert client.post("/v1/ask", json={"message": "x" * 501}).status_code == 422
    assert client.post("/v1/tools/top_movers", json={"period": "10y"}).status_code == 422
    assert client.post("/v1/tools/nope", json={}).status_code == 404
    assert client.get("/v1/market/snapshot?country=XX").status_code == 422


def test_tools_endpoint_and_snapshot(client: TestClient) -> None:
    assert len(client.get("/v1/tools").json()) >= 10
    rows = client.post("/v1/tools/quote", json={"ticker": "ATLS"}).json()["rows"]
    assert rows[0]["ticker"] == "ATLS"
    snap = client.get("/v1/market/snapshot?country=EG").json()
    assert len(snap["rows"]) == 29 and len({r["ticker"] for r in snap["rows"]}) == 29


def test_rate_limit(settings) -> None:
    strict = settings.model_copy(update={"rate_limit_per_minute": 2})
    c = TestClient(create_app(strict, Assistant(strict)))
    codes = [c.post("/v1/ask", json={"message": "hello"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_dashboard_and_widget_are_served(client: TestClient) -> None:
    assert "widget.js" in client.get("/").text
    assert "attachShadow" in client.get("/static/widget.js").text


def test_offline_eval_passes(db_path: Path) -> None:
    env = {"MA_DB_PATH": str(db_path), "MA_LLM_API_KEY": "", "PYTHONIOENCODING": "utf-8", "SYSTEMROOT": "C:\\Windows"}
    proc = subprocess.run([sys.executable, "-m", "market_assistant.eval", "--min-score", "1.0",
                           "--report", str(db_path.parent / "report")], capture_output=True, text=True, env=env,
                          encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
