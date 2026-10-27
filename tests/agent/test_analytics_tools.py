"""signal_performance and risk_metrics, checked against independent Python calculations."""
import math
import sqlite3
import statistics

import pytest

from market_assistant.agent.pipeline import Assistant
from market_assistant.agent.planner import RulePlanner
from market_assistant.agent.tools import call_tool


def closes(db, ticker, start):
    con = sqlite3.connect(db.path)
    try:
        return [r[0] for r in con.execute(
            "SELECT close FROM daily_prices WHERE ticker = ? AND date >= ? ORDER BY date", (ticker, start))]
    finally:
        con.close()


def test_signal_performance_matches_a_manual_calculation(db) -> None:
    rows = call_tool(db, "signal_performance", {"signal_type": "volume_spike", "horizon_days": 5}).rows
    con = sqlite3.connect(db.path)
    dates = [r[0] for r in con.execute("SELECT DISTINCT date FROM daily_prices ORDER BY date")]
    index = {d: i for i, d in enumerate(dates)}
    price = {(t, d): c for t, d, c in con.execute("SELECT ticker, date, close FROM daily_prices")}
    by_direction: dict[str, list[float]] = {}
    for ticker, date, direction in con.execute("SELECT ticker, date, direction FROM signals WHERE signal_type = 'volume_spike'"):
        i = index[date] + 5
        if i < len(dates):
            by_direction.setdefault(direction, []).append((price[(ticker, dates[i])] / price[(ticker, date)] - 1) * 100)
    con.close()
    for row in rows:
        values = by_direction[row["direction"]]
        assert row["signals"] == len(values)
        assert row["avg_return_pct"] == pytest.approx(statistics.fmean(values), abs=0.01)
        assert row["up_rate_pct"] == pytest.approx(100 * sum(v > 0 for v in values) / len(values), abs=0.06)


def test_risk_metrics_match_a_manual_calculation(db) -> None:
    from market_assistant.agent.tools import base_date

    row = call_tool(db, "risk_metrics", {"ticker": "ATLS", "period": "6m"}).rows[0]
    c = closes(db, "ATLS", base_date(db, "6m"))
    returns = [b / a - 1 for a, b in zip(c, c[1:], strict=False)]
    assert row["volatility_pct"] == pytest.approx(statistics.stdev(returns) * math.sqrt(252) * 100, abs=0.06)
    peak, worst = c[0], 0.0
    for x in c:
        peak = max(peak, x)
        worst = min(worst, x / peak - 1)
    assert row["max_drawdown_pct"] == pytest.approx(worst * 100, abs=0.06)
    assert row["period_return_pct"] == pytest.approx((c[-1] / c[0] - 1) * 100, abs=0.01)
    assert row["beta"] is not None and -3 < row["beta"] < 5


@pytest.mark.parametrize("question,tool,args", [
    ("What's the hit rate after a 52-week breakout?", "signal_performance", {"signal_type": "52w_high_breakout", "horizon_days": 10}),
    ("نسبة نجاح إشارة التقاطع الذهبي بعد 20 يوم؟", "signal_performance", {"signal_type": "golden_cross", "horizon_days": 20}),
    ("breakout success rate after 5 days", "signal_performance", {"horizon_days": 5}),
    ("How risky is Atlas Robotics?", "risk_metrics", {"ticker": "ATLS", "period": "1y"}),
    ("بيتا سهم النيل للاتصالات قد ايه؟", "risk_metrics", {"ticker": "NLTC.CA"}),
])
def test_planner_routes_analytics_questions(db, question, tool, args) -> None:
    call = RulePlanner(db).plan(question).tool_calls[0]
    assert call.tool == tool and args.items() <= call.args.items()


def test_gold_is_unsupported_but_golden_cross_is_not(bot: Assistant) -> None:
    assert bot.ask("what's the gold price?").route == "unsupported"
    assert bot.ask("golden cross signals this month").tool_calls[0]["tool"] == "signals"


def test_every_answer_carries_a_step_trace(bot: Assistant) -> None:
    steps = [t["step"] for t in bot.ask("Top gainers in Egypt this week").trace]
    assert steps == ["understand", "plan", "execute", "write", "finalize"]
    final = list(bot.stream("How risky is NOVC?"))[-1]
    assert [t["step"] for t in final["trace"]][:3] == ["understand", "plan", "execute"]
