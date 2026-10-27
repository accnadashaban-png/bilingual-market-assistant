"""Provider failover: free tiers run out, rate limits hit, services go down. The assistant must keep answering."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from market_assistant.agent.llm import LLMClient, LLMRouter, build_llm, classify
from market_assistant.agent.pipeline import Assistant
from market_assistant.api.app import create_app
from market_assistant.core.errors import LLMUnavailableError
from market_assistant.core.settings import ProviderSpec


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def provider(name: str, behaviour, calls: list[str]) -> LLMClient:
    """A fake OpenAI-compatible provider. `behaviour(body)` returns an httpx.Response."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(name)
        return behaviour(json.loads(request.content))
    return LLMClient(transport=httpx.MockTransport(handler), base_url=f"https://{name}.test/v1", model=f"{name}-model", name=name)


def answer(text: str):
    def respond(body):
        if body.get("stream"):
            chunks = "".join(f'data: {json.dumps({"choices": [{"delta": {"content": w + " "}}]})}\n\n' for w in text.split())
            return httpx.Response(200, text=chunks + "data: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    return respond


def status(code: int, body: str = "", retry_after: str | None = None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return lambda _body: httpx.Response(code, text=body, headers=headers)


@pytest.mark.parametrize("code,body,kind", [
    (429, '{"error": "You exceeded your current quota"}', "quota"),
    (429, '{"error": "Too many requests"}', "rate_limit"),
    (402, "", "quota"),
    (401, "", "auth"),
    (503, "", "server"),
    (400, "bad param", "bad_request"),
])
def test_failures_are_classified(code, body, kind) -> None:
    assert classify(code, body, None).kind == kind


def test_quota_exhausted_fails_over_and_benches_the_provider() -> None:
    calls: list[str] = []
    clock = Clock()
    router = LLMRouter([provider("free-a", status(429, "quota exceeded"), calls), provider("free-b", answer("hi"), calls)],
                       clock=clock)
    assert router.complete([{"role": "user", "content": "x"}]) == "hi"
    assert router.complete([{"role": "user", "content": "x"}]) == "hi"
    assert calls == ["free-a", "free-b", "free-b"]  # free-a is benched after its quota error
    benched = router.status()[0]
    assert benched["state"].startswith("cooling down (quota)") and benched["retry_in_s"] == 3600
    clock.t += 3601
    router.complete([{"role": "user", "content": "x"}])
    assert calls[-2:] == ["free-a", "free-b"]  # retried after the cooldown


def test_retry_after_header_is_respected() -> None:
    calls: list[str] = []
    clock = Clock()
    router = LLMRouter([provider("a", status(429, "slow down", retry_after="7"), calls), provider("b", answer("ok"), calls)],
                       clock=clock)
    router.complete([{"role": "user", "content": "x"}])
    assert router.status()[0]["retry_in_s"] == 7


def test_outage_and_timeouts_fail_over() -> None:
    calls: list[str] = []

    def timeout(_body):
        raise httpx.ConnectTimeout("timed out")

    router = LLMRouter([provider("down", status(503), calls), provider("slow", timeout, calls), provider("up", answer("ok"), calls)])
    assert router.complete([{"role": "user", "content": "x"}]) == "ok"
    assert calls == ["down", "slow", "up"]


def test_all_providers_failing_raises_cleanly() -> None:
    calls: list[str] = []
    router = LLMRouter([provider("a", status(500), calls), provider("b", status(429, "quota"), calls)])
    with pytest.raises(LLMUnavailableError):
        router.complete([{"role": "user", "content": "x"}])
    with pytest.raises(LLMUnavailableError, match="cooling down"):
        router.complete([{"role": "user", "content": "x"}])  # both benched: fail fast, no calls
    assert calls == ["a", "b"]


def test_streaming_fails_over_before_the_first_token() -> None:
    calls: list[str] = []
    router = LLMRouter([provider("a", status(429, "quota"), calls), provider("b", answer("two words"), calls)])
    assert "".join(router.stream([{"role": "user", "content": "x"}])).split() == ["two", "words"]
    assert calls == ["a", "b"]


def test_keys_come_only_from_env(monkeypatch, settings) -> None:
    monkeypatch.setenv("TEST_KEY_B", "secret-b")
    s = settings.model_copy(update={"llm_providers": [
        ProviderSpec(name="no-key", base_url="https://a.test/v1", model="m", api_key_env="MISSING_KEY"),
        ProviderSpec(name="b", base_url="https://b.test/v1", model="m", api_key_env="TEST_KEY_B"),
        ProviderSpec(name="local", base_url="http://localhost:11434/v1", model="m"),
    ]})
    router = build_llm(s)
    assert [p["name"] for p in router.status()] == ["b", "local"]  # providers without a key are skipped
    assert "secret-b" not in json.dumps(router.status())


def test_assistant_answers_through_the_router_and_reports_status(settings) -> None:
    calls: list[str] = []
    plan = json.dumps({"route": "data", "tool_calls": [{"tool": "quote", "args": {"ticker": "ATLS"}}]})

    def b(body):
        return answer("Atlas Robotics closed at 137.88 USD.")(body) if body.get("stream") else answer(plan)(body)

    router = LLMRouter([provider("a", status(429, "quota exhausted"), calls), provider("b", b, calls)])
    bot = Assistant(settings, llm=router)
    result = bot.ask("price of atlas?")
    assert result.tool_calls[0]["tool"] == "quote" and "Atlas Robotics" in result.answer
    body = TestClient(create_app(settings, bot)).get("/v1/llm/providers").json()
    assert body["mode"] == "llm" and body["providers"][0]["state"].startswith("cooling down")
