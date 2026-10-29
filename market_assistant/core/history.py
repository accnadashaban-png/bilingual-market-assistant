"""People, conversations and messages, saved like a modern AI assistant: a sidebar of past chats that survive
restarts, can be renamed, pinned, searched and deleted, and a person can be erased with everything they said.

SQLite, one file (the local app keeps it next to its settings; a server sets MA_HISTORY_DB).
A conversation also stores what it is about (last tool, arguments and companies), so a follow-up such as
"and its revenue?" still works after the app restarts.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_seen TEXT NOT NULL,
    conversations INTEGER NOT NULL DEFAULT 0, messages INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0, context TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')), content TEXT NOT NULL, route TEXT, language TEXT,
    tools TEXT NOT NULL DEFAULT '[]', at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
"""
ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def valid_id(value: str | None) -> bool:
    return bool(value and ID.match(value))


def mask(text: str) -> str:
    """Market questions rarely hold personal data; when they do, it is not kept."""
    return CARD.sub("[card]", EMAIL.sub("[email]", text))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ChatHistory:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._con:
            self._con.executescript(SCHEMA)

    def _q(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._lock, self._con:
            return self._con.execute(sql, params).fetchall()

    # ---------- writing ----------

    def owner(self, conversation_id: str) -> str | None:
        rows = self._q("SELECT user_id FROM conversations WHERE id = ?", (conversation_id,))
        return str(rows[0][0]) if rows else None

    def record(self, user_id: str, conversation_id: str, question: str, answer: str, route: str, language: str,
               tools: list[str], context: dict[str, Any]) -> None:
        now, q = _now(), mask(question)[:1000]
        self._q("""INSERT INTO users (id, created_at, last_seen) VALUES (?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET last_seen = excluded.last_seen""", (user_id, now, now))
        new = not self._q("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,))
        if new:
            title = " ".join(q.split())[:80]
            self._q("INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (conversation_id, user_id, title, now, now))
        self._q("UPDATE conversations SET updated_at = ?, messages = messages + 2, context = ? WHERE id = ?",
                (now, json.dumps(context, ensure_ascii=False), conversation_id))
        self._q("UPDATE users SET messages = messages + 2, conversations = conversations + ? WHERE id = ?",
                (1 if new else 0, user_id))
        self._q("INSERT INTO messages (conversation_id, role, content, route, language, tools, at) VALUES (?, 'user', ?, NULL, ?, '[]', ?)",
                (conversation_id, q, language, now))
        self._q("INSERT INTO messages (conversation_id, role, content, route, language, tools, at) VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
                (conversation_id, answer[:20000], route, language, json.dumps(tools), now))

    def rename(self, conversation_id: str, user_id: str, title: str) -> bool:
        title = " ".join(title.split())[:80]
        if not title or self.owner(conversation_id) != user_id:
            return False
        self._q("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
        return True

    def pin(self, conversation_id: str, user_id: str, pinned: bool) -> bool:
        if self.owner(conversation_id) != user_id:
            return False
        self._q("UPDATE conversations SET pinned = ? WHERE id = ?", (1 if pinned else 0, conversation_id))
        return True

    def delete(self, conversation_id: str, user_id: str) -> bool:
        if self.owner(conversation_id) != user_id:
            return False
        n = self._q("SELECT messages FROM conversations WHERE id = ?", (conversation_id,))[0][0]
        self._q("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self._q("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self._q("UPDATE users SET conversations = conversations - 1, messages = messages - ? WHERE id = ?", (n, user_id))
        return True

    def forget_user(self, user_id: str) -> dict[str, int]:
        """Erase a person and everything they said. Cannot be undone."""
        convs = [r[0] for r in self._q("SELECT id FROM conversations WHERE user_id = ?", (user_id,))]
        msgs = sum(self._q("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (c,))[0][0] for c in convs)
        for c in convs:
            self._q("DELETE FROM messages WHERE conversation_id = ?", (c,))
        self._q("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        users = self._q("SELECT COUNT(*) FROM users WHERE id = ?", (user_id,))[0][0]
        self._q("DELETE FROM users WHERE id = ?", (user_id,))
        return {"users": int(users), "conversations": len(convs), "messages": int(msgs)}

    # ---------- reading ----------

    def conversations(self, user_id: str, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Pinned first, then most recent. Search looks in titles and in every message."""
        like = f"%{search.strip()[:100]}%"
        rows = self._q("""SELECT c.id, c.title, c.created_at, c.updated_at, c.pinned, c.messages FROM conversations c
                          WHERE c.user_id = ? AND (? = '%%' OR c.title LIKE ? OR EXISTS (
                              SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.content LIKE ?))
                          ORDER BY c.pinned DESC, c.updated_at DESC, c.rowid DESC LIMIT ?""",
                       (user_id, like, like, like, max(1, min(limit, 500))))
        keys = ("id", "title", "created_at", "updated_at", "pinned", "messages")
        return [{**dict(zip(keys, r, strict=True)), "pinned": bool(r[4])} for r in rows]

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self._q("SELECT role, content, route, language, tools, at FROM messages WHERE conversation_id = ? ORDER BY id",
                       (conversation_id,))
        return [{"role": r[0], "content": r[1], "route": r[2], "language": r[3], "tools": json.loads(r[4]), "at": r[5]}
                for r in rows]

    def context(self, conversation_id: str) -> dict[str, Any]:
        rows = self._q("SELECT context FROM conversations WHERE id = ?", (conversation_id,))
        return dict(json.loads(rows[0][0])) if rows else {}

    def users(self, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        like = f"%{search.strip()[:100]}%"
        rows = self._q("""SELECT id, name, created_at, last_seen, conversations, messages FROM users
                          WHERE id LIKE ? OR name LIKE ? ORDER BY last_seen DESC LIMIT ?""",
                       (like, like, max(1, min(limit, 500))))
        keys = ("id", "name", "created_at", "last_seen", "conversations", "messages")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def stats(self) -> dict[str, int]:
        u, c, m = (self._q(f"SELECT COUNT(*) FROM {t}")[0][0] for t in ("users", "conversations", "messages"))  # noqa: S608
        return {"people": int(u), "conversations": int(c), "messages": int(m)}
