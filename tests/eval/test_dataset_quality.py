"""Quality checks on the evaluation dataset itself: a wrong expected answer is worse than no test."""
import sqlite3
from collections import Counter

import pytest

from market_assistant.agent.tools import TOOLS
from market_assistant.eval.cases import load

ROUTES = {"data", "greeting", "advice", "unsupported", "clarify"}


@pytest.fixture(scope="module")
def all_cases(db_path):
    return load(db_path)


def test_size_and_coverage(all_cases) -> None:
    assert len(all_cases) >= 500
    registers = Counter(c["category"].split("/")[-1] for c in all_cases if c["category"].startswith("generated/"))
    for register in ("en", "egyptian", "gulf", "levantine", "msa"):
        assert registers[register] >= 30, register
    tools = {c.get("tool") for c in all_cases}
    assert set(TOOLS) - {"run_sql"} <= tools  # every tool is exercised


def test_ids_unique_and_fields_valid(all_cases) -> None:
    ids = [c["id"] for c in all_cases]
    assert len(ids) == len(set(ids))
    for c in all_cases:
        assert c["turns"] and all(t.strip() for t in c["turns"]), c["id"]
        routes = c["route"] if isinstance(c["route"], list) else [c["route"]]
        assert set(routes) <= ROUTES, c["id"]
        assert c.get("tool") in (None, *TOOLS), c["id"]
        assert not (c.get("tool") and "data" not in routes and c["route"] != "advice"), c["id"]


def test_every_reference_query_returns_a_value(all_cases, db_path) -> None:
    con = sqlite3.connect(db_path)
    try:
        for c in all_cases:
            if c.get("facts_sql"):
                rows = con.execute(c["facts_sql"]).fetchall()
                assert rows and all(r[0] is not None for r in rows), c["id"]
    finally:
        con.close()


def test_rankings_have_no_ties_at_the_cutoff(all_cases, db_path) -> None:
    """An ordered check is only fair if the expected order is unambiguous."""
    con = sqlite3.connect(db_path)
    try:
        for c in (c for c in all_cases if c.get("ordered")):
            tickers = [r[0] for r in con.execute(c["facts_sql"]).fetchall()]
            assert len(tickers) == len(set(tickers)) == min(5, len(tickers)), c["id"]
    finally:
        con.close()
