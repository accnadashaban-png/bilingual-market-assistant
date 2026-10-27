"""LLM access: OpenAI-compatible clients plus a router that fails over across providers.

Configure a group of providers (for example several free tiers). The router tries
them in order and moves on when one is out of quota, rate-limited, down or
misconfigured. A failing provider is benched for a cooldown that depends on the
failure (quota: long, rate limit: Retry-After, outage: short), then retried.
API keys are read from environment variables only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from market_assistant.core.errors import LLMUnavailableError
from market_assistant.core.observability import LLM_CALLS
from market_assistant.core.settings import Settings


class ChatModel(Protocol):
    """Anything the planner and writer can talk to: a single client or a router."""

    model: str

    def complete(self, messages: list[dict[str, str]], json_mode: bool = False, temperature: float = 0.2) -> str: ...

    def stream(self, messages: list[dict[str, str]], temperature: float = 0.4) -> Iterator[str]: ...


class ProviderError(Exception):
    """A provider-level failure, classified so the router knows how long to bench it."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind  # quota | rate_limit | auth | server | bad_request
        self.retry_after = retry_after


QUOTA_HINTS = ("quota", "exhausted", "insufficient", "billing", "credit", "limit exceeded")


def classify(status: int, body: str, retry_after: str | None) -> ProviderError:
    wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None
    if status == 429:
        kind = "quota" if any(h in body.lower() for h in QUOTA_HINTS) else "rate_limit"
        return ProviderError(kind, f"HTTP 429 ({kind})", wait)
    if status == 402:
        return ProviderError("quota", "HTTP 402 (payment required)", wait)
    if status in (401, 403):
        return ProviderError("auth", f"HTTP {status} (check the API key)")
    if status >= 500:
        return ProviderError("server", f"HTTP {status}", wait)
    return ProviderError("bad_request", f"HTTP {status}: {body[:200]}")


class LLMClient:
    """One OpenAI-compatible endpoint."""

    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None, *,
                 base_url: str | None = None, api_key: str = "", model: str | None = None, timeout_s: float | None = None,
                 name: str = "default"):
        self.name = name
        self.model = model or (settings.llm_model if settings else "default")
        key = api_key or (settings.llm_api_key.get_secret_value() if settings else "")
        self._client = httpx.Client(
            base_url=(base_url or (settings.llm_base_url if settings else "")).rstrip("/"),
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=timeout_s or (settings.llm_timeout_s if settings else 30.0),
            transport=transport,
        )

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            resp = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError("server", f"timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("server", f"connection error: {exc}") from exc
        if resp.status_code >= 400:
            raise classify(resp.status_code, resp.text, resp.headers.get("retry-after"))
        return resp

    def complete(self, messages: list[dict[str, str]], json_mode: bool = False, temperature: float = 0.2) -> str:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            return str(self._post(body).json()["choices"][0]["message"]["content"]).strip()
        except ProviderError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailableError(f"unexpected response: {exc}") from exc

    def raw_complete(self, messages: list[dict[str, str]], json_mode: bool, temperature: float) -> str:
        """Like `complete`, but raises ProviderError so a router can react to the failure type."""
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            return str(self._post(body).json()["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError("server", f"unexpected response: {exc}") from exc

    def raw_stream(self, messages: list[dict[str, str]], temperature: float) -> Iterator[str]:
        body = {"model": self.model, "messages": messages, "temperature": temperature, "stream": True}
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise classify(resp.status_code, resp.text, resp.headers.get("retry-after"))
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise ProviderError("server", f"stream error: {exc}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError("server", f"bad stream chunk: {exc}") from exc

    def stream(self, messages: list[dict[str, str]], temperature: float = 0.4) -> Iterator[str]:
        try:
            yield from self.raw_stream(messages, temperature)
        except ProviderError as exc:
            raise LLMUnavailableError(str(exc)) from exc


COOLDOWNS = {"quota": 3600.0, "rate_limit": 20.0, "auth": 86400.0, "server": 30.0, "bad_request": 0.0}


@dataclass
class _ProviderState:
    client: LLMClient
    benched_until: float = 0.0
    calls: int = 0
    failures: int = 0
    last_error: str | None = None
    last_kind: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class LLMRouter:
    """Fail over across a group of providers; same interface as LLMClient."""

    def __init__(self, clients: list[LLMClient], clock: Callable[[], float] = time.monotonic,
                 cooldowns: dict[str, float] | None = None):
        if not clients:
            raise ValueError("at least one provider is required")
        self.providers = [_ProviderState(c) for c in clients]
        self.clock = clock
        self.cooldowns = {**COOLDOWNS, **(cooldowns or {})}
        self.model = clients[0].model

    def _available(self) -> list[_ProviderState]:
        now = self.clock()
        return [p for p in self.providers if p.benched_until <= now]

    def _fail(self, p: _ProviderState, exc: ProviderError) -> None:
        wait = exc.retry_after if exc.retry_after is not None and exc.kind in ("rate_limit", "quota", "server") \
            else self.cooldowns[exc.kind]
        with p.lock:
            p.failures += 1
            p.last_error, p.last_kind = str(exc), exc.kind
            p.benched_until = self.clock() + wait
        LLM_CALLS.labels(p.client.name, exc.kind).inc()

    def _ok(self, p: _ProviderState) -> None:
        with p.lock:
            p.calls += 1
            p.last_kind = None
        LLM_CALLS.labels(p.client.name, "ok").inc()

    def complete(self, messages: list[dict[str, str]], json_mode: bool = False, temperature: float = 0.2) -> str:
        errors = []
        for p in self._available():
            try:
                text = p.client.raw_complete(messages, json_mode, temperature)
            except ProviderError as exc:
                self._fail(p, exc)
                errors.append(f"{p.client.name}: {exc}")
                continue
            self._ok(p)
            return text
        raise LLMUnavailableError("; ".join(errors) or "all providers are cooling down")

    def stream(self, messages: list[dict[str, str]], temperature: float = 0.4) -> Iterator[str]:
        """Fail over until a provider produces its first token; after that, stay with it."""
        errors = []
        for p in self._available():
            chunks = p.client.raw_stream(messages, temperature)
            try:
                first = next(chunks)
            except StopIteration:
                first = ""
            except ProviderError as exc:
                self._fail(p, exc)
                errors.append(f"{p.client.name}: {exc}")
                continue
            self._ok(p)
            if first:
                yield first
            try:
                yield from chunks
            except ProviderError as exc:
                self._fail(p, exc)
                raise LLMUnavailableError(f"{p.client.name} failed mid-answer: {exc}") from exc
            return
        raise LLMUnavailableError("; ".join(errors) or "all providers are cooling down")

    def status(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [{"name": p.client.name, "model": p.client.model,
                 "state": "available" if p.benched_until <= now else f"cooling down ({p.last_kind})",
                 "retry_in_s": max(0, round(p.benched_until - now)), "successful_calls": p.calls,
                 "failures": p.failures, "last_error": p.last_error} for p in self.providers]


def build_llm(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLMRouter | None:
    """A router over MA_LLM_PROVIDERS, or over the single legacy MA_LLM_* provider. None means offline mode."""
    clients = []
    for cfg in settings.llm_providers:
        key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
        if cfg.api_key_env and not key:
            continue  # configured but no key on this machine: skip it
        clients.append(LLMClient(transport=transport, base_url=cfg.base_url, api_key=key, model=cfg.model,
                                 timeout_s=cfg.timeout_s, name=cfg.name))
    if not clients and settings.llm_enabled:
        clients.append(LLMClient(settings, transport=transport, name="default"))
    return LLMRouter(clients) if clients else None
