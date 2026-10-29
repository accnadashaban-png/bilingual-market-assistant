"""What an answer carries besides its text: a price chart and follow-up suggestions.

Both are computed from the same tool results as the answer, never invented: the chart is the closing prices
the answer talks about, and every suggestion is a question the assistant is tested to answer.
"""
from __future__ import annotations

from typing import Any

from market_assistant.agent.tools import ToolResult, _ticker, base_date
from market_assistant.core.errors import AssistantError
from market_assistant.db.database import BaseDatabase

# tool -> chart period when the question did not name one (a one-day chart says nothing, so it shows a month)
CHART_TOOLS = {"quote": "3m", "performance": "1m", "compare": "1m", "risk_metrics": "1y", "company_profile": "6m"}
MAX_POINTS = 120

COMPANY = {
    "quote": ("What's the price of {en}?", "سهم {ar} بكام؟"),
    "performance": ("How did {en} do this year?", "سهم {ar} عمل ايه من أول السنة؟"),
    "risk_metrics": ("How risky is {en}?", "مخاطر سهم {ar} عاملة ازاي؟"),
    "fundamentals": ("What was the revenue of {en}?", "ما هي إيرادات {ar}؟"),
}
MARKET = [
    ("top_movers", "Top gainers this week", "أكتر الأسهم طلوعًا الأسبوع ده"),
    ("top_movers", "Biggest losers this week", "أكتر سهم نزل الأسبوع ده"),
    ("sector_performance", "Which sector did best this month?", "أنهي قطاع كان الأحسن الشهر ده؟"),
    ("market_overview", "How is the market today?", "السوق عامل ايه النهارده؟"),
    ("signals", "Any 52-week breakouts this month?", "فيه اختراقات النهارده؟"),
]


def chart_for(db: BaseDatabase, results: list[ToolResult]) -> dict[str, Any] | None:
    """Closing prices of the companies in the answer, over the answer's period. None when there is nothing to plot."""
    if not results or results[0].tool not in CHART_TOOLS:
        return None
    first = results[0]
    names = first.args.get("tickers") or ([first.args["ticker"]] if first.args.get("ticker") else [])
    period = first.args.get("period") or CHART_TOOLS[first.tool]
    period = "1m" if period == "1d" else period
    try:
        start = base_date(db, period)
    except AssistantError:
        start = "0000-00-00"  # not enough history for the period: plot what there is
    companies = {c["ticker"]: c for c in db.companies}
    series = []
    for name in names[:6]:
        try:
            ticker = _ticker(db, str(name))
        except AssistantError:
            continue
        rows = db.fetch("SELECT date, close FROM daily_prices WHERE ticker = :t AND date >= :s ORDER BY date",
                        {"t": ticker, "s": start}, limit=1000)
        if len(rows) < 2:
            continue
        step = max(1, -(-len(rows) // MAX_POINTS))
        points = rows[::step]
        if points[-1] is not rows[-1]:
            points.append(rows[-1])  # always end on the latest close
        c = companies.get(ticker, {})
        series.append({"ticker": ticker, "name_en": c.get("name_en", ticker), "name_ar": c.get("name_ar", ticker),
                       "currency": c.get("currency", ""), "points": [[r["date"], r["close"]] for r in points]})
    if not series:
        return None
    return {"period": period, "series": series, "normalized": len(series) > 1}


def suggestions(db: BaseDatabase, results: list[ToolResult], language: str, limit: int = 3) -> list[str]:
    """Follow-up questions in the user's language, about the same company when there is one."""
    ar = language == "ar"
    tool = results[0].tool if results else ""
    names = (results[0].args.get("tickers") or ([results[0].args["ticker"]] if results[0].args.get("ticker") else [])
             if results else [])
    out: list[str] = []
    if names:
        try:
            ticker = _ticker(db, str(names[0]))
            c = next(x for x in db.companies if x["ticker"] == ticker)
            out = [(ar_t if ar else en_t).format(en=c["name_en"], ar=c["name_ar"])
                   for t, (en_t, ar_t) in COMPANY.items() if t != tool]
        except (AssistantError, StopIteration):
            out = []
    out += [ar_t if ar else en_t for t, en_t, ar_t in MARKET if t != tool]
    return out[:limit]
