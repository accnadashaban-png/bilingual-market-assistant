"""Model Context Protocol server: exposes the same typed tools to any MCP client (desktop AI apps, IDEs, agents).

    python -m market_assistant.api.mcp_server           # stdio transport

Tools keep every safety layer: arguments are validated by the same Pydantic models,
SQL is parameterized, and `run_sql` still goes through the sqlglot validator and the
read-only database. Clients only ever receive rows, never errors with internals.
"""
from __future__ import annotations

import inspect
from typing import Any

from mcp.server.mcpserver import MCPServer

from market_assistant.agent.tools import TOOLS, Tool, call_tool
from market_assistant.core.errors import AssistantError
from market_assistant.core.settings import Settings, get_settings
from market_assistant.db.database import BaseDatabase, open_database


def _tool_function(db: BaseDatabase, tool: Tool):  # type: ignore[no-untyped-def]
    """Build a function whose signature mirrors the tool's argument model, so MCP derives the JSON schema."""
    params = [inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=field.annotation,
                                default=inspect.Parameter.empty if field.is_required() else field.default)
              for name, field in tool.args_model.model_fields.items()]

    def run(**kwargs: Any) -> dict[str, Any]:
        try:
            result = call_tool(db, tool.name, kwargs)
        except AssistantError as exc:
            return {"error": exc.code, "message": exc.message_en}
        return {"tool": result.tool, "args": result.args, "rows": result.rows}

    run.__name__ = tool.name
    run.__doc__ = tool.description
    run.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    run.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict[str, Any]}
    return run


def build_server(settings: Settings | None = None, db: BaseDatabase | None = None) -> MCPServer:
    settings = settings or get_settings()
    db = db or open_database(settings)
    server = MCPServer("bilingual-market-assistant",
                       instructions="Read-only market data tools over fictional demo companies (US, Saudi Arabia, Egypt).")
    for tool in TOOLS.values():
        server.add_tool(_tool_function(db, tool), name=tool.name, description=tool.description)
    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
