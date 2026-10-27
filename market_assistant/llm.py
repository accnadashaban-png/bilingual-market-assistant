"""Minimal client for any OpenAI-compatible chat endpoint, with streaming."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from .errors import LLMUnavailableError
from .settings import Settings


class LLMClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.model = settings.llm_model
        self._client = httpx.Client(
            base_url=settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"},
            timeout=settings.llm_timeout_s,
            transport=transport,
        )

    def complete(self, messages: list[dict[str, str]], json_mode: bool = False, temperature: float = 0.2) -> str:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"]).strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailableError(str(exc)) from exc

    def stream(self, messages: list[dict[str, str]], temperature: float = 0.4) -> Iterator[str]:
        body = {"model": self.model, "messages": messages, "temperature": temperature, "stream": True}
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailableError(str(exc)) from exc
