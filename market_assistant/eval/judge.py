"""LLM-as-judge: grades answers on what rule checks can't see (helpfulness, tone, dialect, format).

Rule checks stay the source of truth for facts; the judge adds a 1-5 rubric score and a
short reason per answer. It runs only when an LLM is configured (any provider group).
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from market_assistant.agent.llm import ChatModel
from market_assistant.core.errors import LLMUnavailableError

RUBRIC = """You grade replies from a bilingual (Arabic/English) market-data assistant. Score each criterion 1-5:
- correctness: uses only numbers present in the expected facts, no invented data (5 = fully correct)
- language: replies in the user's language and, for Arabic, a matching dialect or clear MSA
- format: the shape fits (sentence for one fact, bullets for a few items, a table for comparisons)
- helpfulness: leads with the answer, no filler, refuses unsafe or advice requests politely
Return JSON only: {"correctness": n, "language": n, "format": n, "helpfulness": n, "reason": "<one sentence>"}"""


class Grade(BaseModel):
    correctness: int = Field(ge=1, le=5)
    language: int = Field(ge=1, le=5)
    format: int = Field(ge=1, le=5)
    helpfulness: int = Field(ge=1, le=5)
    reason: str = ""

    @property
    def overall(self) -> float:
        return round((self.correctness + self.language + self.format + self.helpfulness) / 4, 2)


class Judge:
    def __init__(self, llm: ChatModel):
        self.llm = llm

    def grade(self, question: str, answer: str, expected_facts: list[str]) -> Grade | None:
        prompt = json.dumps({"question": question, "answer": answer, "expected_facts": expected_facts}, ensure_ascii=False)
        try:
            raw = self.llm.complete([{"role": "system", "content": RUBRIC}, {"role": "user", "content": prompt}],
                                    json_mode=True, temperature=0)
            return Grade.model_validate_json(raw)
        except (LLMUnavailableError, ValidationError, ValueError):
            return None  # a judge failure never fails the run; rule checks still apply


def summarize(grades: list[Grade | None]) -> dict[str, Any]:
    done = [g for g in grades if g is not None]
    if not done:
        return {"judged": 0}
    avg = lambda attr: round(sum(getattr(g, attr) for g in done) / len(done), 2)  # noqa: E731
    return {"judged": len(done), "overall": round(sum(g.overall for g in done) / len(done), 2),
            "correctness": avg("correctness"), "language": avg("language"), "format": avg("format"),
            "helpfulness": avg("helpfulness")}
