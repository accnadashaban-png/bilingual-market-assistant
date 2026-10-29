"""Local app settings: data source and LLM providers, saved on this machine only.

The file lives in the user's profile (`~/.bilingual-market-assistant/settings.json` by default),
is written atomically, and on POSIX systems is readable by the owner only (0600).
Secrets (API keys, database passwords) are never returned by the API: only a masked hint is.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, SecretStr, field_validator

DEFAULT_DIR = Path.home() / ".bilingual-market-assistant"


class ProviderEntry(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    base_url: str = Field(min_length=8, max_length=300)
    model: str = Field(min_length=1, max_length=120)
    api_key: SecretStr = SecretStr("")

    @field_validator("base_url")
    @classmethod
    def http_url(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme not in ("https", "http") or not parts.netloc:
            raise ValueError("must be an http(s) URL")
        if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("plain http is only allowed for local servers; use https")
        return v.rstrip("/")


class DataSource(BaseModel):
    kind: Literal["demo", "live_demo", "csv", "sqlite", "postgres"] = "demo"
    sqlite_path: str | None = None       # for "sqlite"
    postgres_url: SecretStr | None = None  # for "postgres": a read-only role
    csv_folder: str | None = None        # last imported folder, for display


class LocalConfig(BaseModel):
    data: DataSource = Field(default_factory=DataSource)
    providers: list[ProviderEntry] = Field(default_factory=list, max_length=10)


def mask(secret: str) -> str:
    return "" if not secret else ("••••" + secret[-4:] if len(secret) > 8 else "••••")


def mask_url(url: str) -> str:
    """Hide the password in a database URL."""
    parts = urlsplit(url)
    if parts.password:
        host = parts.netloc.split("@")[-1]
        return urlunsplit((parts.scheme, f"{parts.username}:••••@{host}", parts.path, "", ""))
    return url


class ConfigStore:
    def __init__(self, folder: Path = DEFAULT_DIR):
        self.folder = Path(folder)
        self.path = self.folder / "settings.json"

    def load(self) -> LocalConfig:
        if not self.path.exists():
            return LocalConfig()
        return LocalConfig.model_validate(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, config: LocalConfig) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = config.model_dump(mode="json")
        # model_dump masks SecretStr; write the real values, only to this file
        data["providers"] = [{**p, "api_key": e.api_key.get_secret_value()} for p, e in zip(data["providers"], config.providers,
                                                                                            strict=True)]
        if config.data.postgres_url is not None:
            data["data"]["postgres_url"] = config.data.postgres_url.get_secret_value()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    @staticmethod
    def public_view(config: LocalConfig) -> dict[str, object]:
        """What the UI may see: everything except secrets, which become masked hints."""
        url = config.data.postgres_url.get_secret_value() if config.data.postgres_url else ""
        return {
            "data": {"kind": config.data.kind, "sqlite_path": config.data.sqlite_path, "csv_folder": config.data.csv_folder,
                     "postgres_url": mask_url(url) if url else None},
            "providers": [{"name": p.name, "base_url": p.base_url, "model": p.model,
                           "api_key": mask(p.api_key.get_secret_value()), "has_key": bool(p.api_key.get_secret_value())}
                          for p in config.providers],
        }
