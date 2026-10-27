"""The assistant: a LangGraph state machine plus conversation sessions.

    understand -> plan -> (data? execute) -> write -> finalize

`ask` runs the whole graph. `stream` runs up to `execute`, then streams the reply
token by token (from the LLM, or chunked template text offline) and finishes with
the gated final text.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from .database import Database
from .errors import AssistantError, LLMUnavailableError, NotFoundError, PlanningError, UnsafeQueryError
from .language import detect_dialect, detect_language
from .llm import LLMClient
from .observability import BLOCKED, LATENCY, REQUESTS, TOOL_CALLS, log
from .planner import LLMPlanner, Memory, Plan, RulePlanner
from .settings import Settings, get_settings
from .tools import ToolResult, call_tool
from .writer import LLMWriter, TemplateWriter, chunk_text, finalize

logger = logging.getLogger("market_assistant")


class State(TypedDict, total=False):
    question: str
    memory: Memory
    language: str
    dialect: str
    plan: Plan
    results: list[ToolResult]
    error: AssistantError | None
    answer: str


@dataclass
class Answer:
    answer: str
    route: str
    language: str
    dialect: str
    session_id: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class SessionStore:
    """In-memory conversations with a time-to-live (swap for Redis in a multi-server deployment)."""

    def __init__(self, ttl_s: int = 1800, max_sessions: int = 5000):
        self.ttl_s, self.max_sessions = ttl_s, max_sessions
        self._data: dict[str, tuple[float, Memory]] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str | None) -> tuple[str, Memory]:
        now = time.monotonic()
        with self._lock:
            self._data = {k: v for k, v in self._data.items() if now - v[0] < self.ttl_s}
            if session_id and session_id in self._data:
                memory = self._data[session_id][1]
            else:
                session_id = session_id if session_id and len(session_id) <= 64 else uuid.uuid4().hex
                memory = Memory()
                if len(self._data) >= self.max_sessions:
                    self._data.pop(min(self._data, key=lambda k: self._data[k][0]))
            self._data[session_id] = (now, memory)
            return session_id, memory


class Assistant:
    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None):
        self.settings = settings or get_settings()
        self.db = Database(self.settings.db_path, self.settings.query_timeout_s, self.settings.max_rows)
        self.rules = RulePlanner(self.db)
        self.llm = llm or (LLMClient(self.settings) if self.settings.llm_enabled else None)
        self.planner = LLMPlanner(self.db, self.llm, self.rules) if self.llm else self.rules
        self.template_writer = TemplateWriter()
        self.llm_writer = LLMWriter(self.llm) if self.llm else None
        self.sessions = SessionStore(self.settings.session_ttl_s)
        self.graph = self._build(full=True)
        self.prepare_graph = self._build(full=False)

    # ---------- graph nodes ----------

    def _understand(self, s: State) -> State:
        q = s["question"]
        return {"language": detect_language(q), "dialect": detect_dialect(q), "error": None, "results": []}

    def _plan(self, s: State) -> State:
        try:
            return {"plan": self.planner.plan(s["question"], s["memory"])}
        except LLMUnavailableError:
            return {"plan": self.rules.plan(s["question"], s["memory"])}  # degrade gracefully
        except Exception as exc:  # noqa: BLE001 - never let a planning bug reach the user
            logger.exception("planning failed")
            return {"plan": Plan(route="clarify", note="not_understood"), "error": PlanningError(str(exc))}

    def _execute(self, s: State) -> State:
        results: list[ToolResult] = []
        for c in s["plan"].tool_calls:
            try:
                results.append(self._run_tool(s["question"], c.tool, c.args))
                TOOL_CALLS.labels(c.tool, "ok").inc()
            except AssistantError as exc:
                TOOL_CALLS.labels(c.tool, exc.code).inc()
                if isinstance(exc, UnsafeQueryError):
                    BLOCKED.inc()
                return {"results": results, "error": exc}
            except (ValidationError, KeyError) as exc:
                TOOL_CALLS.labels(c.tool, "bad_args").inc()
                return {"results": results, "error": PlanningError(str(exc))}
        return {"results": results}

    def _run_tool(self, question: str, tool: str, args: dict[str, Any]) -> ToolResult:
        try:
            return call_tool(self.db, tool, args)
        except sqlite3.Error as exc:
            # self-heal once: let the model fix SQL that failed in the database (e.g. a wrong column)
            if tool == "run_sql" and isinstance(self.planner, LLMPlanner):
                fixed = self.planner.repair_sql(question, args.get("sql", ""), str(exc))
                return call_tool(self.db, "run_sql", {"sql": fixed})
            raise PlanningError(str(exc)) from exc

    def _write(self, s: State) -> State:
        if s.get("error"):
            return {"answer": s["error"].user_message(s["language"])}  # type: ignore[union-attr]
        args = (s["question"], s["plan"], s.get("results", []), s["language"], s["dialect"])
        if self.llm_writer and s["plan"].route in ("data", "advice"):
            try:
                return {"answer": "".join(self.llm_writer.stream(*args, s["memory"].history))}
            except LLMUnavailableError:
                pass
        return {"answer": self.template_writer.write(*args)}

    def _finalize(self, s: State) -> State:
        return {"answer": finalize(s["answer"], s["plan"].route, s["language"])}

    def _build(self, full: bool):  # type: ignore[no-untyped-def]
        g = StateGraph(State)
        g.add_node("understand", self._understand)
        g.add_node("plan", self._plan)
        g.add_node("execute", self._execute)
        g.set_entry_point("understand")
        g.add_edge("understand", "plan")
        after_execute = "write" if full else END
        g.add_conditional_edges("plan", lambda s: "execute" if s["plan"].route in ("data", "advice") and not s.get("error")
                                else after_execute)
        g.add_edge("execute", after_execute)
        if full:
            g.add_node("write", self._write)
            g.add_node("finalize", self._finalize)
            g.add_edge("write", "finalize")
            g.add_edge("finalize", END)
        return g.compile()

    # ---------- public API ----------

    def ask(self, question: str, session_id: str | None = None) -> Answer:
        start = time.perf_counter()
        session_id, memory = self.sessions.get(session_id)
        state: State = self.graph.invoke({"question": question.strip(), "memory": memory})
        return self._done(state, session_id, memory, start)

    def stream(self, question: str, session_id: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield events: meta (route + tool calls), delta (text chunks), final (gated answer)."""
        start = time.perf_counter()
        session_id, memory = self.sessions.get(session_id)
        state: State = self.prepare_graph.invoke({"question": question.strip(), "memory": memory})
        yield {"event": "meta", "session_id": session_id, "route": state["plan"].route,
               "language": state["language"], "tool_calls": _calls(state)}

        text = ""
        streamed = False
        if not state.get("error") and self.llm_writer and state["plan"].route in ("data", "advice"):
            try:
                for delta in self.llm_writer.stream(state["question"], state["plan"], state.get("results", []),
                                                    state["language"], state["dialect"], memory.history):
                    text += delta
                    yield {"event": "delta", "text": delta}
                streamed = True
            except LLMUnavailableError:
                text = ""
        if not streamed:
            text = self._write(state)["answer"]
            for piece in chunk_text(text):
                yield {"event": "delta", "text": piece}
        state["answer"] = text
        state = {**state, **self._finalize(state)}
        yield {"event": "final", **self._done(state, session_id, memory, start).to_dict()}

    def _done(self, state: State, session_id: str, memory: Memory, start: float) -> Answer:
        plan, lang = state["plan"], state["language"]
        memory.remember(plan, state["question"], state["answer"], self.settings.history_turns)
        elapsed = time.perf_counter() - start
        REQUESTS.labels(plan.route, lang).inc()
        LATENCY.observe(elapsed)
        error = state.get("error")
        log(logger, "answered", route=plan.route, language=lang, dialect=state["dialect"],
            tools=[c.tool for c in plan.tool_calls], error=error.code if error else None, ms=int(elapsed * 1000))
        return Answer(answer=state["answer"], route=plan.route, language=lang, dialect=state["dialect"],
                      session_id=session_id, tool_calls=_calls(state), error=error.code if error else None,
                      latency_ms=int(elapsed * 1000))


def _calls(state: State) -> list[dict[str, Any]]:
    """What the assistant did, for the 'how I got this' panel. Rows are not included."""
    results = {r.tool: r for r in state.get("results", [])}
    out = []
    for c in state["plan"].tool_calls:
        r = results.get(c.tool)
        out.append({"tool": c.tool, "args": r.args if r else c.args, "rows": len(r.rows) if r else 0,
                    "sql": " ".join(r.sql.split()) if r and c.tool == "run_sql" else None})
    return out


__all__ = ["Answer", "Assistant", "NotFoundError"]
