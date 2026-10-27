"""Golden-dataset evaluation.

Each case says which route and tool the assistant should pick, and (optionally) a
reference SQL query whose values must appear in the answer. Expected numbers are
computed from the database at run time, so the checks stay correct if the data changes.

    python -m market_assistant.eval                 # offline planner + writer
    python -m market_assistant.eval --min-score 0.95 --report reports/eval-latest
    python -m market_assistant.eval --suite golden --judge   # add LLM-as-judge rubric scores
Every run is appended to reports/runs.jsonl and compared with the previous run of the same suite.
Exits with code 1 when the overall score is below --min-score (used as a CI gate).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_assistant.agent.language import detect_language
from market_assistant.agent.pipeline import Assistant
from market_assistant.eval.cases import load as load_cases
from market_assistant.eval.cases import risk_facts
from market_assistant.eval.judge import Judge
from market_assistant.eval.judge import summarize as summarize_grades

ROOT = Path(__file__).resolve().parents[2]


def expected_values(db_path: Path, sql: str, as_pct: bool, fmt: str | None = None) -> list[str]:
    """Run the reference query and format values exactly as the writer prints them."""
    con = sqlite3.connect(db_path)
    try:
        values = [row[0] for row in con.execute(sql).fetchall()]
    finally:
        con.close()
    out = []
    for v in values:
        if fmt == "bold":
            out.append(f"**{v}**")  # counts are bolded, which also stops "7" matching inside a date
        elif isinstance(v, float):
            out.append(f"{v:+.2f}%" if as_pct else f"{v:,.2f}")
        else:
            out.append(str(v))
    return out


def in_order(answer: str, facts: list[str]) -> bool:
    """Every fact appears, and in the expected order (for rankings)."""
    positions = [answer.find(f) for f in facts]
    return all(p >= 0 for p in positions) and positions == sorted(positions)


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
    facts: list[str] = []
    if case.get("facts_sql"):
        facts = expected_values(bot.settings.db_path, case["facts_sql"], case.get("pct", False), case.get("format"))
    elif case.get("facts_fn"):
        kind, *fn_args = case["facts_fn"]
        facts = {"risk": risk_facts}[kind](bot.settings.db_path, *fn_args)
    if facts:
        missing = [f for f in facts if f not in result.answer]
        checks["facts"] = not missing
        if case.get("ordered"):
            checks["order"] = in_order(result.answer, facts)
    elif case.get("facts_sql") or case.get("facts_fn"):
        checks["facts"] = False  # a reference query that returns nothing is a broken case, never a pass
    for text in case.get("must_include", []):
        checks[f"includes:{text}"] = text in result.answer
    for text in case.get("must_not_include", []):
        checks[f"excludes:{text}"] = text not in result.answer
    return {"id": case["id"], "category": case["category"], "language": detect_language(case["turns"][-1]),
            "passed": all(checks.values()), "checks": checks, "missing_facts": missing,
            "route": result.route, "tools": [c["tool"] for c in result.tool_calls],
            "latency_ms": round(latency * 1000, 1), "question": case["turns"][-1], "expected_facts": facts,
            "answer": result.answer}


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
        "latency_ms_p50": round(statistics.median(latencies), 1),
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


def track(log: Path, suite: str, mode: str, cases: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    """Append this run to a JSONL log and print the change against the previous run of the same suite."""
    dataset = hashlib.sha256(json.dumps([[c["id"], c["turns"]] for c in cases], ensure_ascii=False).encode()).hexdigest()[:12]
    entry = {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "suite": suite, "mode": mode, "dataset": dataset,
             **{k: summary[k] for k in ("cases", "score", "route_accuracy", "tool_accuracy", "fact_accuracy",
                                        "language_match", "latency_ms_p50", "latency_ms_p95")}}
    if "judge" in summary:
        entry["judge_overall"] = summary["judge"].get("overall")
    log.parent.mkdir(parents=True, exist_ok=True)
    previous = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] if log.exists() else []
    last = next((r for r in reversed(previous) if r["suite"] == suite and r["mode"] == mode), None)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if last:
        delta = round(entry["score"] - last["score"], 3)
        note = "same dataset" if last["dataset"] == dataset else "dataset changed"
        print(f"vs previous run ({last['at']}, {note}): score {last['score']:.1%} -> {entry['score']:.1%} ({delta:+.1%})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-score", type=float, default=0.95)
    parser.add_argument("--report", type=Path, default=ROOT / "reports" / "eval-latest")
    parser.add_argument("--suite", choices=["all", "golden", "generated"], default="all")
    parser.add_argument("--judge", action="store_true", help="also grade answers with the configured LLM")
    parser.add_argument("--runs-log", type=Path, default=ROOT / "reports" / "runs.jsonl")
    args = parser.parse_args()

    bot = Assistant()
    mode = f"LLM: {bot.llm.model}" if bot.llm else "offline"
    cases = load_cases(bot.settings.db_path, args.suite)
    results = [run_case(bot, c) for c in cases]
    summary = summarize(results)
    if args.judge and bot.llm:
        judge = Judge(bot.llm)
        grades = [judge.grade(r["question"], r["answer"], r["expected_facts"]) for r in results]
        for r, g in zip(results, grades, strict=True):
            r["judge"] = g.model_dump() | {"overall": g.overall} if g else None
        summary["judge"] = summarize_grades(grades)
    write_report(args.report, summary, results, mode)
    track(args.runs_log, args.suite, mode, cases, summary)

    print(json.dumps(summary, indent=2))
    for r in results:
        if not r["passed"]:
            print(f"FAIL {r['id']}: {r['checks']} missing={r['missing_facts']}")
    return 0 if summary["score"] >= args.min_score else 1


if __name__ == "__main__":
    raise SystemExit(main())
