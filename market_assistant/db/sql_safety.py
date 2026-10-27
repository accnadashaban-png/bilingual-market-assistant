"""Layer 1: parse model-written SQL with sqlglot and allow only safe, read-only SELECTs.

Working on the syntax tree (not on regexes) means tricks like mixed case,
odd spacing or keywords hidden inside strings can't fool the check.
"""
from __future__ import annotations

from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from market_assistant.core.errors import UnsafeQueryError

ALLOWED_TABLES = {"companies", "daily_prices", "indicators", "signals", "fundamentals"}
BLOCKED_FUNCTIONS = {
    # SQLite
    "load_extension", "randomblob", "zeroblob", "readfile", "writefile", "edit",
    "fts3_tokenizer", "sqlite_version", "sqlite_source_id", "sqlite_compileoption_used",
    # PostgreSQL
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "lo_get", "set_config", "current_setting", "dblink", "dblink_exec", "pg_terminate_backend",
    "pg_cancel_backend", "pg_reload_conf", "query_to_xml", "table_to_xml", "version", "inet_server_addr",
    "generate_series",
}
WRITE_NODES = tuple(getattr(exp, name) for name in
                    ("Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma",
                     "Attach", "Detach", "Merge", "TruncateTable", "Into", "Transaction", "Commit")
                    if hasattr(exp, name))
QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return str(node.sql_name()).lower()


def validate_select(sql: str, max_rows: int = 50, dialect: str = "sqlite") -> str:
    """Return a safe, LIMIT-ed version of `sql`, or raise UnsafeQueryError."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise UnsafeQueryError("empty query")
    if "--" in text or "/*" in text or "*/" in text:
        raise UnsafeQueryError("comments are not allowed")
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except ParseError as exc:
        raise UnsafeQueryError(f"could not parse query: {exc.errors[0]['description'] if exc.errors else exc}") from exc
    if len(statements) != 1:
        raise UnsafeQueryError("only one statement is allowed")

    tree = statements[0]
    if not isinstance(tree, QUERY_ROOTS):
        raise UnsafeQueryError("only SELECT queries are allowed")

    if any(w.args.get("recursive") for w in tree.find_all(exp.With)):
        raise UnsafeQueryError("recursive queries are not allowed")
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise UnsafeQueryError(f"{node.key.upper()} is not allowed")
        if isinstance(node, exp.Table):
            if node.args.get("db") or node.args.get("catalog"):
                raise UnsafeQueryError("schema-qualified tables are not allowed")
            name = node.name.lower()
            if name not in ALLOWED_TABLES and name not in cte_names:
                raise UnsafeQueryError(f"unknown table: {name}")
        if isinstance(node, exp.Func) and _function_name(node) in BLOCKED_FUNCTIONS:
            raise UnsafeQueryError(f"function {_function_name(node)} is not allowed")

    for select in tree.find_all(exp.Select):
        if any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
               for e in select.expressions):
            raise UnsafeQueryError("SELECT * is not allowed, name the columns you need")

    return _enforce_limit(tree, max_rows).sql(dialect=dialect)


def _enforce_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    if isinstance(tree, exp.Select):
        limit = tree.args.get("limit")
        current = limit.expression if limit is not None else None
        if not isinstance(current, exp.Literal) or not current.is_int or int(current.this) > max_rows:
            return tree.limit(max_rows)
        return tree
    # UNION / INTERSECT / EXCEPT: wrap it so the whole result is capped
    return exp.select("*").from_(cast(exp.Query, tree).subquery("q")).limit(max_rows)
