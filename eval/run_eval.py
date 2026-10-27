"""Golden-dataset evaluation.

Each case says which route and tool the assistant should pick, and (optionally) a
reference SQL query whose values must appear in the answer. Expected numbers are
computed from the database at run time, so the checks stay correct if the data changes.

    python eval/run_eval.py                 # offline planner + writer
    python eval/run_eval.py --min-score 0.95 --report eval/reports/latest
Exits with code 1 when the overall score is below --min-score (used as a CI gate).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market_assistant.language import detect_language  # noqa: E402
from market_assistant.pipeline import Assistant  # noqa: E402

GOLDEN = Path(__file__).parent / "golden.jsonl"


def expected_values(db_path: Path, sql: str, as_pct: bool) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        values = [row[0] for row in con.execute(sql).fetchall()]
    finally:
        con.close()
    out = []
    for v in values:
        if isinstance(v, float):
            out.append(f"{v:+.2f}%" if as_pct else f"{v:,.2f}")
        else:
            out.append(str(v))
    return out


def run_case(bot: Assistant, case: dict[str, Any]) -> dict[str, Any]:
    session = None
    start = time.perf_counter()
    for turn in case["turns"]:
        result = bot.ask(turn, session)
        session = result.session_id
    latency = (time.perf_counter() - start) / len(case["turns"])

    routes = case["route"] if isinstance(case["route"], list) else [case["route"]]
    checks = {"route": result.route in routes}
    if case.get("tool"):
        checks["tool"] = bool(result.tool_calls) and result.tool_calls[0]["tool"] == case["tool"]
    checks["language"] = detect_language(result.answer) == detect_language(case["turns"][-1])
    missing: list[str] = []
    if case.get("facts_sql"):
        facts = expected_values(bot.settings.db_path, case["facts_sql"], case.get("pct", False))
        missing = [f for f in facts if f not in result.answer]
        checks["facts"] = bool(facts) and not missing
    for text in case.get("must_include", []):
        checks[f"includes:{text}"] = text in result.answer
    return {"id": case["id"], "category": case["category"], "language": detect_language(case["turns"][-1]),
            "passed": all(checks.values()), "checks": checks, "missing_facts": missing,
            "route": result.route, "tools": [c["tool"] for c in result.tool_calls],
            "latency_ms": round(latency * 1000, 1), "answer": result.answer}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(items: list[dict[str, Any]], key: str | None = None) -> float:
        pool = [r for r in items if key is None or key in r["checks"]]
        if not pool:
            return 1.0
        ok = sum(r["passed"] if key is None else r["checks"][key] for r in pool)
        return round(ok / len(pool), 3)

    by_category: dict[str, list] = defaultdict(list)
    by_language: dict[str, list] = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)
        by_language[r["language"]].append(r)
    latencies = sorted(r["latency_ms"] for r in results)
    return {
        "cases": len(results), "score": rate(results),
        "route_accuracy": rate(results, "route"), "tool_accuracy": rate(results, "tool"),
        "fact_accuracy": rate(results, "facts"), "language_match": rate(results, "language"),
        "by_category": {k: rate(v) for k, v in sorted(by_category.items())},
        "by_language": {k: rate(v) for k, v in sorted(by_language.items())},
        "latency_ms_p50": statistics.median(latencies),
        "latency_ms_p95": latencies[max(0, int(len(latencies) * 0.95) - 1)],
    }


def write_report(path: Path, summary: dict[str, Any], results: list[dict[str, Any]], mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps({"mode": mode, "summary": summary, "results": results},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Evaluation report ({mode})", "", f"**Score: {summary['score']:.1%}** on {summary['cases']} cases", "",
             "| Metric | Value |", "|---|---|"]
    for key in ("route_accuracy", "tool_accuracy", "fact_accuracy", "language_match"):
        lines.append(f"| {key.replace('_', ' ')} | {summary[key]:.1%} |")
    lines += [f"| latency p50 / p95 | {summary['latency_ms_p50']} / {summary['latency_ms_p95']} ms |", "",
              "| Category | Pass rate |", "|---|---|"]
    lines += [f"| {k} | {v:.1%} |" for k, v in summary["by_category"].items()]
    failed = [r for r in results if not r["passed"]]
    lines += ["", f"## Failed cases ({len(failed)})", ""]
    lines += [f"- `{r['id']}`: {', '.join(k for k, v in r['checks'].items() if not v)}" for r in failed] or ["None."]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-score", type=float, default=0.95)
    parser.add_argument("--report", type=Path, default=Path(__file__).parent / "reports" / "latest")
    args = parser.parse_args()

    bot = Assistant()
    mode = f"LLM: {bot.settings.llm_model}" if bot.llm else "offline"
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = [run_case(bot, c) for c in cases]
    summary = summarize(results)
    write_report(args.report, summary, results, mode)

    print(json.dumps(summary, indent=2))
    for r in results:
        if not r["passed"]:
            print(f"FAIL {r['id']}: {r['checks']} missing={r['missing_facts']}")
    return 0 if summary["score"] >= args.min_score else 1


if __name__ == "__main__":
    raise SystemExit(main())
