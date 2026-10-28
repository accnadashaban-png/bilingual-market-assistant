"""FastAPI service: JSON and streaming (SSE) chat, tools, health and metrics.

    uvicorn market_assistant.api.app:app --reload
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, ValidationError

from market_assistant.agent.language import detect_language
from market_assistant.agent.llm import LLMRouter
from market_assistant.agent.pipeline import Assistant
from market_assistant.agent.tools import TOOLS, call_tool, tool_specs
from market_assistant.core.errors import AssistantError, RateLimitedError
from market_assistant.core.observability import request_id, setup_logging
from market_assistant.core.settings import Settings, get_settings

logger = logging.getLogger("market_assistant")

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(None, max_length=64)


class RateLimiter:
    """Token bucket per client: `per_minute` requests, refilled continuously."""

    def __init__(self, per_minute: int):
        self.capacity, self.rate = per_minute, per_minute / 60
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            allowed = tokens >= 1
            self._buckets[key] = (tokens - 1 if allowed else tokens, now)
            return allowed


def create_app(settings: Settings | None = None, assistant: Assistant | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    bot = assistant or Assistant(settings)
    limiter = RateLimiter(settings.rate_limit_per_minute)

    app = FastAPI(title="Bilingual Market Assistant", version="2.0.0",
                  description="Arabic + English market-data assistant over fictional demo data.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "X-Request-ID"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("X-Request-ID", "")[:64] or uuid.uuid4().hex
        token = request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def check_rate(request: Request, message: str) -> None:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            err = RateLimitedError()
            raise HTTPException(429, detail={"code": err.code, "message": err.user_message(detect_language(message))})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        checks = {"database": bot.db.is_ready(), "llm": "configured" if bot.llm else "offline mode"}
        return JSONResponse(checks, status_code=200 if checks["database"] else 503)

    @app.post("/v1/ask")
    def ask(body: AskRequest, request: Request) -> dict[str, Any]:
        check_rate(request, body.message)
        return {**bot.ask(body.message, body.session_id).to_dict(), "request_id": request_id.get()}

    @app.post("/v1/ask/stream")
    def ask_stream(body: AskRequest, request: Request) -> StreamingResponse:
        check_rate(request, body.message)
        rid = request_id.get()

        def events() -> Iterator[str]:
            # the generator runs in a worker thread, so set the id again for the logs written while streaming
            request_id.set(rid)
            try:
                for event in bot.stream(body.message, body.session_id):
                    yield f"event: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001 - the stream must always end with a message, never silently
                if not isinstance(exc, AssistantError):
                    logger.exception("stream failed")
                    exc = AssistantError()
                payload = {"event": "error", "code": exc.code, "message": exc.user_message(detect_language(body.message))}
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/v1/llm/providers")
    def llm_providers() -> dict[str, Any]:
        """Which providers are configured and which are cooling down. Never returns keys."""
        status = bot.llm.status() if isinstance(bot.llm, LLMRouter) else []
        return {"mode": "llm" if bot.llm else "offline", "providers": status}

    @app.get("/v1/tools")
    def tools() -> list[dict[str, Any]]:
        return tool_specs()

    @app.post("/v1/tools/{name}")
    def run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in TOOLS:
            raise HTTPException(404, detail={"code": "unknown_tool", "message": f"No tool named {name}"})
        try:
            result = call_tool(bot.db, name, args)
        except ValidationError as exc:
            raise HTTPException(422, detail={"code": "bad_args", "message": exc.errors(include_url=False, include_context=False)}) from exc
        except AssistantError as exc:
            raise HTTPException(400, detail={"code": exc.code, "message": exc.message_en}) from exc
        return {"tool": result.tool, "args": result.args, "rows": result.rows}

    @app.get("/v1/market/snapshot")
    def snapshot(country: str | None = None) -> dict[str, Any]:
        """Latest quote for every company, used by the demo dashboard."""
        if country not in (None, "US", "SA", "EG"):
            raise HTTPException(422, detail={"code": "bad_country", "message": "country must be US, SA or EG"})
        rows = call_tool(bot.db, "top_movers", {"period": "1d", "country": country, "limit": 15}).rows
        rest = call_tool(bot.db, "top_movers", {"period": "1d", "direction": "down", "country": country, "limit": 15}).rows
        seen = {r["ticker"] for r in rows}
        rows += [r for r in reversed(rest) if r["ticker"] not in seen]
        return {"date": bot.db.latest_date, "rows": rows}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


app = create_app()
