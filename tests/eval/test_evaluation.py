"""Evaluation harness: case generation, the LLM judge and run tracking."""
import json

import httpx

from market_assistant.agent.llm import LLMClient
from market_assistant.eval import cases
from market_assistant.eval.judge import Judge, summarize
from market_assistant.eval.runner import track


def test_generated_cases_cover_every_company_and_register(db_path) -> None:
    generated = cases.generated(db_path)
    assert len(generated) == 30 * (5 + 2 + 2 + 2)
    assert {c["category"].split("/")[-1] for c in generated} == {"en", "egyptian", "gulf", "levantine", "msa"}
    assert len({c["id"] for c in generated}) == len(generated)
    assert len(cases.load(db_path)) > 300


def fake_judge(reply: str) -> Judge:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]}))
    return Judge(LLMClient(transport=transport, base_url="https://judge.test/v1", model="judge"))


def test_judge_scores_and_tolerates_bad_output() -> None:
    good = fake_judge('{"correctness": 5, "language": 4, "format": 5, "helpfulness": 4, "reason": "fine"}')
    grade = good.grade("price of ATLS?", "ATLS closed at 137.88 USD.", ["137.88"])
    assert grade is not None and grade.overall == 4.5
    assert fake_judge("not json").grade("q", "a", []) is None
    assert summarize([grade, None]) == {"judged": 1, "overall": 4.5, "correctness": 5.0, "language": 4.0,
                                        "format": 5.0, "helpfulness": 4.0}


def test_runs_are_logged_and_compared(tmp_path, capsys) -> None:
    log = tmp_path / "runs.jsonl"
    summary = {"cases": 2, "score": 0.5, "route_accuracy": 1, "tool_accuracy": 1, "fact_accuracy": 0.5,
               "language_match": 1, "latency_ms_p50": 5, "latency_ms_p95": 9}
    some = [{"id": "a", "turns": ["q"]}]
    track(log, "golden", "offline", some, summary)
    track(log, "golden", "offline", some, {**summary, "score": 1.0})
    runs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["score"] for r in runs] == [0.5, 1.0] and runs[0]["dataset"] == runs[1]["dataset"]
    assert "+50.0%" in capsys.readouterr().out
