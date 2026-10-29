"""The MCP server exposes the same tools with the same safety layers."""
import asyncio
import json

import pytest

pytest.importorskip("mcp")

from market_assistant.agent.tools import TOOLS  # noqa: E402
from market_assistant.api.mcp_server import build_server  # noqa: E402


@pytest.fixture(scope="module")
def server(settings, db):
    return build_server(settings, db)


def payload(result) -> dict:
    return json.loads(result.content[0].text)


def test_every_tool_is_listed_with_a_schema(server) -> None:
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == set(TOOLS)
    movers = next(t for t in tools if t.name == "top_movers")
    assert movers.input_schema["properties"]["period"]["enum"][0] == "1d"


def test_tools_return_rows(server) -> None:
    data = payload(asyncio.run(server.call_tool("quote", {"ticker": "ATLS"})))
    assert data["rows"][0]["ticker"] == "ATLS"


def test_safety_layers_still_apply(server, db) -> None:
    data = payload(asyncio.run(server.call_tool("run_sql", {"sql": "DELETE FROM companies"})))
    assert data["error"] == "unsafe_query"
    data = payload(asyncio.run(server.call_tool("quote", {"ticker": "ATLS' OR '1'='1"})))
    assert data["error"] == "not_found"
    assert db.scalar("SELECT COUNT(ticker) FROM companies") == 90
