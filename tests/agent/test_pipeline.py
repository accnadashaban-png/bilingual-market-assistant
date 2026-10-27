import json

import httpx
import pytest

from market_assistant.agent.pipeline import Assistant
from market_assistant.agent.planner import Memory, RulePlanner
from market_assistant.agent.writer import finalize


@pytest.mark.parametrize("question,tool,args", [
    ("What's the price of Atlas Robotics?", "quote", {"ticker": "ATLS"}),
    ("سهم النيل للاتصالات بكام؟", "quote", {"ticker": "NLTC.CA"}),
    ("كم سعر سهم طويق الحين؟", "quote", {"ticker": "TWQT.SR"}),
    ("How did Nile Telecom do this year?", "performance", {"ticker": "NLTC.CA", "period": "ytd"}),
    ("Top gainers in Egypt this week", "top_movers", {"period": "1w", "country": "EG", "direction": "up"}),
    ("ايه أكتر سهم نزل في السعودية النهارده؟", "top_movers", {"direction": "down", "country": "SA"}),
    ("Compare ATLS and NOVC over 3 months", "compare", {"tickers": ["ATLS", "NOVC"], "period": "3m"}),
    ("فيه اختراقات قمة 52 أسبوع النهارده؟", "signals", {"signal_type": "52w_high_breakout"}),
    ("US stocks above the 50-day average with RSI below 60", "screen", {"country": "US", "above_ma50": True, "rsi_max": 60}),
    ("إيرادات بنك القاهرة التجاري", "fundamentals", {"ticker": "CRBK.CA"}),
    ("كام شركة في السعودية؟", "list_companies", {"country": "SA"}),
])
def test_rule_planner_picks_tool_and_args(db, question, tool, args) -> None:
    plan = RulePlanner(db).plan(question)
    assert plan.route == "data" and plan.tool_calls[0].tool == tool
    assert args.items() <= plan.tool_calls[0].args.items()


def test_follow_ups_use_memory(bot: Assistant) -> None:
    first = bot.ask("Top gainers in the US this month")
    second = bot.ask("and in Egypt?", first.session_id)
    assert second.tool_calls[0]["args"]["country"] == "EG" and second.tool_calls[0]["args"]["period"] == "1m"
    third = bot.ask("What's the price of Medix Health?", first.session_id)
    fourth = bot.ask("and its revenue?", third.session_id)
    assert fourth.tool_calls[0] == {**fourth.tool_calls[0], "tool": "fundamentals"}
    assert fourth.tool_calls[0]["args"]["ticker"] == "MDXH"


@pytest.mark.parametrize("question,route", [
    ("السلام عليكم", "greeting"), ("Should I buy Atlas Robotics?", "advice"), ("What's the bitcoin price?", "unsupported"),
    ("Delete all companies", "unsupported"), ("and its revenue?", "clarify"),
])
def test_routes(bot: Assistant, question: str, route: str) -> None:
    assert bot.ask(question).route == route


def test_answer_shapes(bot: Assistant) -> None:
    assert "|" not in bot.ask("What's the price of Atlas Robotics?").answer  # one fact -> a sentence
    assert "| # |" in bot.ask("Top gainers this month").answer             # many rows -> a table
    assert "not investment advice" in bot.ask("Should I buy NOVC?").answer


def test_stream_events_end_with_the_final_answer(bot: Assistant) -> None:
    events = list(bot.stream("سهم النيل للاتصالات بكام؟"))
    assert events[0]["event"] == "meta" and events[-1]["event"] == "final"
    streamed = "".join(e["text"] for e in events if e["event"] == "delta")
    assert streamed.strip() == events[-1]["answer"].strip()


def test_output_gate_removes_leaks() -> None:
    leaked = ("Here you go:\n```sql\nSELECT * FROM x\n```\nSELECT ticker FROM companies\n"
              "{'ticker': 'ATLS'}\nid 123e4567-e89b-12d3-a456-426614174000\nATLS is up.")
    out = finalize(leaked, "data", "en")
    assert "SELECT" not in out and "{" not in out and "123e4567" not in out and "ATLS is up." in out
    assert "not investment advice" in finalize("Buy it.", "advice", "en")


# ---------- LLM mode, with a fake OpenAI-compatible server ----------

def fake_llm(plan_replies: list[str], answer: str = "Atlas Robotics closed at **137.88 USD**.") -> httpx.MockTransport:
    replies = iter(plan_replies)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("stream"):
            chunks = "".join(f'data: {json.dumps({"choices": [{"delta": {"content": w}}]})}\n\n'
                             for w in answer.split(" ") if w for w in (w + " ",))
            return httpx.Response(200, text=chunks + "data: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    return httpx.MockTransport(handler)


def llm_bot(settings, transport) -> Assistant:
    from pydantic import SecretStr

    from market_assistant.agent.llm import LLMClient

    s = settings.model_copy(update={"llm_api_key": SecretStr("test-key")})
    return Assistant(s, llm=LLMClient(s, transport=transport))


def test_llm_plan_is_validated_and_streamed(settings) -> None:
    plan = '{"route": "data", "tool_calls": [{"tool": "quote", "args": {"ticker": "ATLS"}}]}'
    bot = llm_bot(settings, fake_llm([plan]))
    events = list(bot.stream("price of atlas?"))
    assert events[0]["tool_calls"][0]["tool"] == "quote"
    assert sum(e["event"] == "delta" for e in events) > 1
    assert events[-1]["answer"].startswith("Atlas Robotics closed at")


def test_invalid_llm_plan_is_repaired(settings) -> None:
    bad = '{"route": "data", "tool_calls": [{"tool": "quote", "args": {}}]}'
    good = '{"route": "data", "tool_calls": [{"tool": "quote", "args": {"ticker": "NOVC"}}]}'
    result = llm_bot(settings, fake_llm([bad, good])).ask("nova?")
    assert result.tool_calls[0]["args"]["ticker"] == "NOVC"


def test_llm_outage_falls_back_to_rules(settings) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    result = llm_bot(settings, httpx.MockTransport(down)).ask("What's the price of Atlas Robotics?")
    assert result.route == "data" and result.error is None and "Atlas Robotics" in result.answer


def test_memory_keeps_only_recent_turns() -> None:
    from market_assistant.agent.planner import Plan

    m = Memory()
    for i in range(20):
        m.remember(Plan(route="greeting"), f"q{i}", f"a{i}", max_turns=3)
    assert len(m.history) == 6 and m.history[-1]["content"] == "a19"
