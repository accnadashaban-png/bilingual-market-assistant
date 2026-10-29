"""Local app mode: the assistant as a private app on your own machine.

    python -m market_assistant app            # opens in its own window (Edge/Chrome app mode) or your browser

Security model for a local app that holds API keys:
- listens on 127.0.0.1 only;
- every request must carry a Host header for this machine and port (blocks DNS-rebinding attacks);
- settings endpoints also need a secret token created at launch and embedded in the app page.
  Other websites cannot read that page (no CORS), so they cannot obtain the token (blocks CSRF);
- requests with a foreign Origin are rejected;
- keys are stored in the local settings file and never sent back to the browser, only masked hints.
"""
from __future__ import annotations

import hmac
import secrets
import tempfile
import threading
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, SecretStr, ValidationError

from market_assistant.agent.llm import LLMClient, LLMRouter, ProviderError
from market_assistant.agent.pipeline import Assistant
from market_assistant.api.app import create_app
from market_assistant.core.settings import ROOT, Settings
from market_assistant.db.builder import ImportError_, import_csv
from market_assistant.db.live import LiveFeed, append_csv
from market_assistant.desktop.store import ConfigStore, DataSource, LocalConfig, ProviderEntry

WEB = ROOT / "web"
MAX_IMPORT_BYTES = 50_000_000

Scope = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]], Awaitable[None]]


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    base_url: str
    model: str
    api_key: str = Field("", max_length=400, description="empty keeps the saved key for this provider name")


class DataIn(BaseModel):
    kind: str = Field(pattern="^(demo|live_demo|sqlite|postgres)$")
    sqlite_path: str | None = None
    postgres_url: str | None = Field(None, max_length=500)


class PricesIn(BaseModel):
    daily_prices: str


class LiveIn(BaseModel):
    on: bool
    every_s: float = Field(10.0, ge=2, le=3600)


class CsvIn(BaseModel):
    companies: str
    daily_prices: str
    fundamentals: str | None = None


class _Delegate:
    """ASGI app that forwards to whatever inner app is current, so settings changes apply without a restart."""

    def __init__(self, get: Callable[[], ASGIApp]):
        self.get = get

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        await self.get()(scope, receive, send)


def build_llm(config: LocalConfig, transport: httpx.BaseTransport | None = None) -> LLMRouter | None:
    clients = [LLMClient(transport=transport, base_url=p.base_url, api_key=p.api_key.get_secret_value(), model=p.model,
                         name=p.name) for p in config.providers]
    return LLMRouter(clients) if clients else None


def demo_database(base: Settings) -> Path:
    path = Path(base.db_path)
    if not path.exists():
        import importlib.util

        spec = importlib.util.spec_from_file_location("generate_data", ROOT / "scripts" / "generate_data.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("demo data generator not found; run: python scripts/generate_data.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.build(path)
    return path


def settings_for(config: LocalConfig, folder: Path, base: Settings) -> Settings:
    data = config.data
    update: dict[str, Any] = {"allowed_origins": [], "database_url": None, "history_db": folder / "history.db"}
    if data.kind == "demo":
        update["db_path"] = demo_database(base)
    elif data.kind == "live_demo":  # a private copy of the demo that the live feed can write to
        live = folder / "live-demo.db"
        if not live.exists():
            folder.mkdir(parents=True, exist_ok=True)
            live.write_bytes(demo_database(base).read_bytes())
        update["db_path"] = live
    elif data.kind == "csv":
        update["db_path"] = folder / "my-data.db"
    elif data.kind == "sqlite":
        update["db_path"] = Path(data.sqlite_path or "")
    else:
        update["database_url"] = data.postgres_url.get_secret_value() if data.postgres_url else None
    return base.model_copy(update=update)


def writable_path(config: LocalConfig, folder: Path) -> Path | None:
    """The SQLite file new prices can be added to, or None for read-only sources."""
    kind = config.data.kind
    if kind == "live_demo":
        return folder / "live-demo.db"
    if kind == "csv":
        return folder / "my-data.db"
    if kind == "sqlite" and config.data.sqlite_path:
        return Path(config.data.sqlite_path)
    return None


def create_desktop_app(config_dir: Path, port: int, base: Settings | None = None,
                       transport: httpx.BaseTransport | None = None, token: str | None = None) -> FastAPI:
    store = ConfigStore(config_dir)
    base = base or Settings(_env_file=None)  # type: ignore[call-arg]
    token = token or secrets.token_urlsafe(32)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    lock = threading.Lock()
    state: dict[str, Any] = {"feed": None}

    def rebuild(config: LocalConfig) -> None:
        settings = settings_for(config, store.folder, base)
        bot = Assistant(settings, llm=build_llm(config, transport))
        if not bot.db.is_ready():
            raise HTTPException(422, "the database has no companies table or no rows; check the source")
        if config.data.kind != "live_demo" and state.get("feed"):
            state["feed"].stop()  # the feed only writes to the live demo copy
            state["feed"] = None
        state.update(config=config, inner=create_app(settings, bot), bot=bot)

    rebuild(store.load())
    outer = FastAPI(title="Bilingual Market Assistant (local app)", docs_url=None, redoc_url=None, openapi_url=None)

    @outer.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "unexpected host"}, status_code=421)
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
            return JSONResponse({"detail": "cross-origin requests are not allowed"}, status_code=403)
        if request.url.path.startswith("/v1/app/") and not hmac.compare_digest(
                request.headers.get("x-app-token", "").encode(), token.encode()):
            return JSONResponse({"detail": "missing or invalid app token"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @outer.get("/app", include_in_schema=False)
    def app_page() -> HTMLResponse:
        html = (WEB / "app.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__APP_TOKEN__", token),
                            headers={"Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                     "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
                                     "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})

    @outer.get("/app/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")

    @outer.get("/app/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(WEB / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/app"})

    @outer.get("/app/icon.svg", include_in_schema=False)
    def icon() -> FileResponse:
        return FileResponse(WEB / "icon.svg", media_type="image/svg+xml")

    @outer.get("/v1/app/settings")
    def get_settings() -> dict[str, Any]:
        with lock:
            config: LocalConfig = state["config"]
            bot: Assistant = state["bot"]
            companies = bot.db.companies
            feed: LiveFeed | None = state["feed"]
            return {**ConfigStore.public_view(config), "companies": len(companies), "latest_date": bot.db.latest_date,
                    "example_company": companies[0]["name_en"] if companies else None,
                    "llm": "on" if bot.llm else "offline", "data_status": bot.db.status(),
                    "writable": writable_path(config, store.folder) is not None,
                    "live_feed": {"running": bool(feed and feed.running), "every_s": feed.every_s if feed else None,
                                  "days_added": feed.days_added if feed else 0}}

    @outer.post("/v1/app/add-prices")
    def add_prices(body: PricesIn) -> dict[str, Any]:
        """Append or correct daily prices in the current database; the assistant answers with them at once."""
        if len(body.daily_prices) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "file is too large (50 MB max)")
        with lock:
            path = writable_path(state["config"], store.folder)
            if path is None:
                raise HTTPException(422, "the bundled demo and PostgreSQL are read-only here: use the live demo, your "
                                         "imported CSV data or your own SQLite file")
            with tempfile.TemporaryDirectory() as tmp:
                csv_file = Path(tmp) / "daily_prices.csv"
                csv_file.write_text(body.daily_prices, encoding="utf-8")
                try:
                    result = append_csv(csv_file, path)
                except ImportError_ as exc:
                    raise HTTPException(422, str(exc)) from exc
            state["bot"].db.snapshot(force=True)
            return {"added": result, "data_status": state["bot"].db.status()}

    @outer.post("/v1/app/live")
    def live_feed(body: LiveIn) -> dict[str, Any]:
        """Switch the simulated live feed on or off. Turning it on moves the app to the live demo copy."""
        with lock:
            feed: LiveFeed | None = state["feed"]
            if feed:
                feed.stop()
                state["feed"] = None
            if body.on:
                if state["config"].data.kind != "live_demo":
                    config = state["config"].model_copy(update={"data": DataSource(kind="live_demo")})
                    rebuild(config)
                    store.save(config)
                feed = LiveFeed(store.folder / "live-demo.db", body.every_s)
                feed.start()
                state["feed"] = feed
            return {"running": bool(state["feed"]), "every_s": body.every_s if body.on else None}

    @outer.put("/v1/app/providers")
    def save_providers(body: list[ProviderIn]) -> dict[str, Any]:
        with lock:
            current = {p.name: p for p in state["config"].providers}
            try:
                providers = [ProviderEntry(name=p.name, base_url=p.base_url, model=p.model,
                                           api_key=SecretStr(p.api_key) if p.api_key else
                                           (current[p.name].api_key if p.name in current else SecretStr("")))
                             for p in body]
                config = state["config"].model_copy(update={"providers": providers})
                LocalConfig.model_validate(config.model_dump())
            except ValidationError as exc:
                raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
            if len({p.name for p in providers}) != len(providers):
                raise HTTPException(422, "provider names must be unique")
            rebuild(config)
            store.save(config)
            return ConfigStore.public_view(config)

    @outer.post("/v1/app/providers/{name}/test")
    def test_provider(name: str) -> dict[str, Any]:
        with lock:
            entry = next((p for p in state["config"].providers if p.name == name), None)
        if entry is None:
            raise HTTPException(404, "no provider with that name")
        client = LLMClient(transport=transport, base_url=entry.base_url, api_key=entry.api_key.get_secret_value(),
                           model=entry.model, name=entry.name, timeout_s=20)
        try:
            client.raw_complete([{"role": "user", "content": "Reply with the single word: ok"}], json_mode=False, temperature=0)
        except ProviderError as exc:
            return {"ok": False, "problem": exc.kind}  # the kind only, never the provider's raw response
        return {"ok": True}

    @outer.post("/v1/app/data")
    def save_data(body: DataIn) -> dict[str, Any]:
        with lock:
            if body.kind == "sqlite" and not (body.sqlite_path and Path(body.sqlite_path).is_file()):
                raise HTTPException(422, "SQLite file not found")
            if body.kind == "postgres" and not (body.postgres_url or "").startswith(("postgresql://", "postgres://")):
                raise HTTPException(422, "use a postgresql:// URL for a read-only role")
            source = DataSource(kind=body.kind, sqlite_path=body.sqlite_path if body.kind == "sqlite" else None,
                                postgres_url=SecretStr(body.postgres_url) if body.kind == "postgres" and body.postgres_url else None)
            config = state["config"].model_copy(update={"data": source})
            try:
                rebuild(config)
            except HTTPException:
                rebuild(state["config"])
                raise
            except Exception as exc:
                rebuild(state["config"])
                raise HTTPException(422, f"could not open the database: {type(exc).__name__}") from exc
            store.save(config)
            return ConfigStore.public_view(config)

    @outer.post("/v1/app/import-csv")
    async def import_files(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "files are too large (50 MB max)")
        try:
            body = CsvIn.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(422, "send companies and daily_prices as CSV text") from exc
        with tempfile.TemporaryDirectory() as tmp, lock:
            folder = Path(tmp)
            (folder / "companies.csv").write_text(body.companies, encoding="utf-8")
            (folder / "daily_prices.csv").write_text(body.daily_prices, encoding="utf-8")
            if body.fundamentals:
                (folder / "fundamentals.csv").write_text(body.fundamentals, encoding="utf-8")
            try:
                counts = import_csv(folder, store.folder / "my-data.db")
            except ImportError_ as exc:
                raise HTTPException(422, str(exc)) from exc
            config = state["config"].model_copy(update={"data": DataSource(kind="csv", csv_folder="uploaded files")})
            rebuild(config)
            store.save(config)
            return {"imported": counts, **ConfigStore.public_view(config)}

    outer.mount("/", _Delegate(lambda: state["inner"]))
    return outer


def run(port: int = 8765, open_window: bool = True, config_dir: Path | None = None) -> None:
    import shutil
    import subprocess
    import webbrowser

    import uvicorn

    from market_assistant.desktop.store import DEFAULT_DIR

    app = create_desktop_app(config_dir or DEFAULT_DIR, port)
    url = f"http://127.0.0.1:{port}/app"

    def launch() -> None:
        for browser in ("msedge", "chrome", "google-chrome", "chromium"):
            exe = shutil.which(browser)
            if exe:
                subprocess.Popen([exe, f"--app={url}"])  # noqa: S603 - our own URL, no shell
                return
        webbrowser.open(url)

    if open_window:
        threading.Timer(1.2, launch).start()
    print(f"Bilingual Market Assistant is running at {url} (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


__all__ = ["Response", "create_desktop_app", "run"]
