"""Write the reply the way a strong chat assistant would.

The shape follows the content: a sentence for one fact, bullets for a few items,
a short lead line plus a table when comparing many rows. With an LLM configured
the model writes (and streams) the reply from the tool results; offline, templates
produce the same shapes in English, Egyptian Arabic or Modern Standard Arabic.
`finalize` is the output gate applied to every reply.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from market_assistant.agent.llm import ChatModel
from market_assistant.agent.planner import Plan
from market_assistant.agent.tools import ToolResult

DISCLAIMER = {
    "en": "This is information, not investment advice.",
    "ar": "دي معلومات مش نصيحة استثمارية.",
}
PERIOD_LABEL = {
    "en": {"1d": "the last day", "1w": "the past week", "1m": "the past month", "3m": "the past 3 months",
           "6m": "the past 6 months", "ytd": "this year so far", "1y": "the past year"},
    "ar": {"1d": "آخر يوم", "1w": "آخر أسبوع", "1m": "آخر شهر", "3m": "آخر 3 شهور",
           "6m": "آخر 6 شهور", "ytd": "الفترة من أول السنة", "1y": "آخر سنة"},
}
SIGNAL_LABEL = {
    "52w_high_breakout": ("52-week high breakout", "اختراق قمة 52 أسبوع"),
    "52w_low_breakdown": ("52-week low breakdown", "كسر قاع 52 أسبوع"),
    "golden_cross": ("golden cross", "تقاطع ذهبي"),
    "death_cross": ("death cross", "تقاطع الموت"),
    "rsi_oversold": ("RSI oversold", "تشبع بيعي (RSI)"),
    "rsi_overbought": ("RSI overbought", "تشبع شرائي (RSI)"),
    "volume_spike": ("volume spike", "حجم تداول غير معتاد"),
}
COUNTRY_LABEL = {"US": ("the US", "أمريكا"), "SA": ("Saudi Arabia", "السعودية"), "EG": ("Egypt", "مصر")}
EXAMPLES = {
    "en": ["What's the price of Atlas Robotics?", "Top gainers in Egypt this week", "Compare NOVC and ATLS over 3 months",
           "Any 52-week breakouts today?", "Which sector did best this month?"],
    "ar": ["سهم النيل للاتصالات بكام؟", "أكتر الأسهم طلوعًا في السعودية الأسبوع ده", "قارن ATLS و NOVC آخر 3 شهور",
           "فيه اختراقات قمة 52 أسبوع النهارده؟", "أنهي قطاع كان أحسن الشهر ده؟"],
}


# ---------- formatting helpers ----------

def price(v: Any) -> str:
    return f"{float(v):,.2f}"


def pct(v: Any) -> str:
    return f"{float(v):+.2f}%"


def money_m(v: Any, lang: str) -> str:
    """Format a value given in millions: 83,546 -> 83.5B / 83.5 مليار."""
    v = float(v)
    if abs(v) >= 1000:
        return f"{v / 1000:,.1f} مليار" if lang == "ar" else f"{v / 1000:,.1f}B"
    return f"{v:,.0f} مليون" if lang == "ar" else f"{v:,.0f}M"


def name(row: dict[str, Any], lang: str) -> str:
    return str(row.get("name_ar") if lang == "ar" and row.get("name_ar") else row.get("name_en") or row.get("ticker"))


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


class TemplateWriter:
    """Offline writer: natural replies without an LLM."""

    def write(self, question: str, plan: Plan, results: list[ToolResult], lang: str, dialect: str) -> str:
        eg = dialect == "egyptian"
        if plan.route == "greeting":
            return self._greeting(plan.note, lang)
        if plan.route == "unsupported":
            if plan.note == "write":
                return ("أنا بقرأ بيانات السوق بس، فمقدرش أمسح أو أعدّل أي حاجة." if lang == "ar"
                        else "I only read market data, so I can't delete or change anything.")
            if plan.note == "news":
                return ("معنديش أخبار، أنا بشتغل على بيانات الأسعار والمؤشرات بس. أقدر أوريك آخر سعر أو الأداء أو الإشارات الفنية."
                        if lang == "ar" else "I don't have news, only market data. I can show the latest price, performance "
                        "or technical signals instead.")
            if plan.note == "forecast":
                return ("مقدرش أتوقع الأسعار الجاية، ومحدش يقدر يضمنها. لكن أقدر أوريك الأداء اللي فات والإشارات الفنية الحالية."
                        if lang == "ar" else "I can't predict future prices; nobody can promise those. I can show past "
                        "performance and the current technical signals instead.")
            examples = "\n".join(f"- {e}" for e in EXAMPLES[lang][:3])
            return (f"ده خارج البيانات اللي عندي. أنا بجاوب عن أسعار وأداء وقطاعات وإشارات فنية لشركات تجريبية. جرّب مثلًا:\n{examples}"
                    if lang == "ar" else
                    f"That's outside the data I have. I answer questions about prices, performance, sectors and technical "
                    f"signals for demo companies. For example:\n{examples}")
        if plan.route == "clarify":
            if plan.note == "which_company":
                return ("تقصد أنهي شركة؟ اكتب اسمها أو الرمز بتاعها." if lang == "ar"
                        else "Which company do you mean? Give me its name or ticker.")
            examples = "\n".join(f"- {e}" for e in EXAMPLES[lang][:3])
            return (f"مفهمتش السؤال قوي. ممكن تسأل كده مثلًا:\n{examples}" if lang == "ar"
                    else f"I'm not sure what you're asking. You could try:\n{examples}")
        if plan.route == "advice":
            text = ("مقدرش أقولك تشتري أو تبيع، لأن ده قرار بيعتمد على أهدافك ومخاطرك. بس أقدر أديك الأرقام:"
                    if lang == "ar" else
                    "I can't tell you whether to buy or sell; that depends on your goals and risk. Here are the facts instead:")
            if results and results[0].rows:
                text += "\n\n" + self._quote(results[0].rows[0], lang, eg)
            return text + "\n\n" + DISCLAIMER[lang]
        parts = [self._result(r, lang, eg) for r in results]
        return "\n\n".join(p for p in parts if p)

    def _greeting(self, note: str | None, lang: str) -> str:
        if note == "thanks":
            return "العفو! لو عندك سؤال تاني أنا موجود." if lang == "ar" else "You're welcome! Ask me anything else about the market."
        examples = "\n".join(f"- {e}" for e in EXAMPLES[lang])
        return (f"أهلًا! أنا مساعد بيانات السوق. بجاوب بالعربي أو الإنجليزي عن الأسعار والأداء والقطاعات والإشارات الفنية. جرّب:\n{examples}"
                if lang == "ar" else
                f"Hi! I'm a market-data assistant. Ask me in English or Arabic about prices, performance, sectors and "
                f"technical signals. Try:\n{examples}")

    def _quote(self, r: dict[str, Any], lang: str, eg: bool) -> str:
        cur = r["currency"]
        if lang == "ar":
            move = ("طالع" if r["change_pct"] >= 0 else "نازل") if eg else ("مرتفعًا" if r["change_pct"] >= 0 else "منخفضًا")
            if eg:
                return (f"سهم **{name(r, lang)}** ({r['ticker']}) قفل عند **{price(r['close'])} {cur}** يوم {r['date']}، "
                        f"{move} {pct(abs(r['change_pct']))[1:]} عن اليوم اللي قبله. "
                        f"أعلى سعر في 52 أسبوع {price(r['high_52w'])} وأقل سعر {price(r['low_52w'])}.")
            return (f"أغلق سهم **{name(r, lang)}** ({r['ticker']}) عند **{price(r['close'])} {cur}** في {r['date']}، "
                    f"{move} {pct(abs(r['change_pct']))[1:]} عن الجلسة السابقة. "
                    f"نطاقه خلال 52 أسبوعًا بين {price(r['low_52w'])} و{price(r['high_52w'])}.")
        move = "up" if r["change_pct"] >= 0 else "down"
        return (f"**{name(r, lang)}** ({r['ticker']}) closed at **{price(r['close'])} {cur}** on {r['date']}, "
                f"{move} {pct(abs(r['change_pct']))[1:]} on the day. Its 52-week range is "
                f"{price(r['low_52w'])} to {price(r['high_52w'])}.")

    def _result(self, res: ToolResult, lang: str, eg: bool) -> str:
        rows, a, ar = res.rows, res.args, lang == "ar"
        where = ""
        if a.get("country"):
            where = (" في " + COUNTRY_LABEL[a["country"]][1]) if ar else (" in " + COUNTRY_LABEL[a["country"]][0])
        if not rows and res.tool == "signals":
            label = SIGNAL_LABEL.get(a.get("signal_type") or "", ("technical signals", "إشارات فنية"))[1 if ar else 0]
            days = a.get("days", 5)
            return (f"مفيش أي {label}{where} في آخر {days} أيام تداول (**0**)." if ar else
                    f"There are **0** {label}{where} in the last {days} trading days.")
        if not rows:
            return "مفيش نتايج للطلب ده في البيانات." if ar else "Nothing in the data matches that."
        period = PERIOD_LABEL[lang].get(a.get("period", ""), "")

        if res.tool == "quote":
            return self._quote(rows[0], lang, eg)

        if res.tool == "performance":
            r = rows[0]
            if ar:
                return (f"خلال {period}، سهم **{name(r, lang)}** راح من {price(r['start_close'])} لـ "
                        f"**{price(r['end_close'])} {r['currency']}**، يعني **{pct(r['change_pct'])}**. "
                        f"اتحرك في الفترة دي بين {price(r['period_low'])} و{price(r['period_high'])}.")
            return (f"Over {period}, **{name(r, lang)}** went from {price(r['start_close'])} to "
                    f"**{price(r['end_close'])} {r['currency']}**, a change of **{pct(r['change_pct'])}**. "
                    f"It traded between {price(r['period_low'])} and {price(r['period_high'])} in that time.")

        if res.tool in ("top_movers", "compare", "screen"):
            top = rows[0]
            body = table(["#", "الشركة" if ar else "Company", "الرمز" if ar else "Ticker",
                          "التغير" if ar else "Change", "آخر سعر" if ar else "Last price"],
                         [[i + 1, name(r, lang), r["ticker"], pct(r["change_pct"]), price(r["last_close"])]
                          for i, r in enumerate(rows)])
            if res.tool == "top_movers":
                up = a.get("direction", "up") == "up"
                lead = (f"{'أكتر سهم طلع' if up else 'أكتر سهم نزل'}{where} خلال {period} هو **{name(top, lang)}** "
                        f"({pct(top['change_pct'])})." if ar else
                        f"The biggest {'gainer' if up else 'decliner'}{where} over {period} is **{name(top, lang)}** "
                        f"({pct(top['change_pct'])}).")
            elif res.tool == "compare":
                low = rows[-1]
                lead = (f"خلال {period}، **{name(top, lang)}** كان الأحسن ({pct(top['change_pct'])}) و"
                        f"**{name(low, lang)}** الأضعف ({pct(low['change_pct'])})." if ar else
                        f"Over {period}, **{name(top, lang)}** did best ({pct(top['change_pct'])}) and "
                        f"**{name(low, lang)}** did worst ({pct(low['change_pct'])}).")
            else:
                n = len(rows)
                lead = (f"لقيت **{n}** {'سهم' if n > 10 or n < 3 else 'أسهم'} مطابقة للشروط (مترتبة بأداء آخر شهر):" if ar
                        else f"I found **{n}** stock{'s' if n != 1 else ''} matching your filters (sorted by 1-month change):")
            if len(rows) <= 2 and res.tool != "compare":
                bullets = "\n".join(f"- **{name(r, lang)}** ({r['ticker']}): {pct(r['change_pct'])}, "
                                    f"{price(r['last_close'])} {r['currency']}" for r in rows)
                return f"{lead}\n\n{bullets}"
            return f"{lead}\n\n{body}"

        if res.tool == "sector_performance":
            best, worst = rows[0], rows[-1]
            lead = (f"خلال {period}{where}، أحسن قطاع كان **{best['sector']}** (متوسط {pct(best['avg_change_pct'])}) "
                    f"وأضعف قطاع **{worst['sector']}** ({pct(worst['avg_change_pct'])})." if ar else
                    f"Over {period}{where}, the best sector was **{best['sector']}** (average {pct(best['avg_change_pct'])}) "
                    f"and the weakest was **{worst['sector']}** ({pct(worst['avg_change_pct'])}).")
            body = table(["القطاع" if ar else "Sector", "عدد الشركات" if ar else "Companies",
                          "متوسط التغير" if ar else "Avg change", "أفضل سهم" if ar else "Best stock"],
                         [[r["sector"], r["companies"], pct(r["avg_change_pct"]), pct(r["best_change_pct"])] for r in rows])
            return f"{lead}\n\n{body}"

        if res.tool == "signals":
            label = SIGNAL_LABEL.get(a.get("signal_type") or "", ("technical signals", "إشارات فنية"))[1 if ar else 0]
            n = len(rows)
            lead = (f"فيه **{n}** {label}{where} في آخر {a.get('days', 5)} أيام تداول:" if ar else
                    f"There {'is' if n == 1 else 'are'} **{n}** {label}{where} in the last {a.get('days', 5)} trading days:")
            body = table(["التاريخ" if ar else "Date", "الشركة" if ar else "Company", "الإشارة" if ar else "Signal",
                          "الاتجاه" if ar else "Direction"],
                         [[r["date"], f"{name(r, lang)} ({r['ticker']})",
                           SIGNAL_LABEL[r["signal_type"]][1 if ar else 0],
                           ("إيجابي" if r["direction"] == "bullish" else "سلبي") if ar else r["direction"]] for r in rows])
            return f"{lead}\n\n{body}"

        if res.tool == "company_profile":
            r = rows[0]
            if ar:
                return (f"**{name(r, lang)}** ({r['ticker']}) شركة في قطاع {r['sector']}، مدرجة في {r['exchange']}. "
                        f"قيمتها السوقية حوالي **{money_m(r['market_cap_m'], lang)} {r['currency']}**، "
                        f"وإيراداتها في {r['fiscal_year']} كانت {money_m(r['revenue_m'], lang)} "
                        f"وصافي الربح {money_m(r['net_income_m'], lang)} "
                        f"(ربحية السهم {price(r['eps'])}، ومكرر الربحية {r['pe_ratio']}).")
            return (f"**{name(r, lang)}** ({r['ticker']}) is a {r['sector']} company listed on {r['exchange']}. "
                    f"Its market cap is about **{money_m(r['market_cap_m'], lang)} {r['currency']}**. In {r['fiscal_year']} it had "
                    f"revenue of {money_m(r['revenue_m'], lang)} and net income of {money_m(r['net_income_m'], lang)} "
                    f"(EPS {price(r['eps'])}, P/E {r['pe_ratio']}).")

        if res.tool == "fundamentals":
            last, cur = rows[-1], rows[-1]["currency"]
            growth = f" ({pct(last['revenue_growth_pct'])})" if last.get("revenue_growth_pct") is not None else ""
            lead = (f"إيرادات **{name(last, lang)}** في {last['fiscal_year']} كانت **{money_m(last['revenue_m'], lang)} {cur}**{growth}، "
                    f"وصافي الربح {money_m(last['net_income_m'], lang)}." if ar else
                    f"**{name(last, lang)}** had revenue of **{money_m(last['revenue_m'], lang)} {cur}** in "
                    f"{last['fiscal_year']}{growth} and net income of {money_m(last['net_income_m'], lang)}.")
            body = table(["السنة" if ar else "Year", "الإيرادات" if ar else "Revenue", "صافي الربح" if ar else "Net income",
                          "ربحية السهم" if ar else "EPS", "نمو الإيرادات" if ar else "Revenue growth"],
                         [[r["fiscal_year"], money_m(r["revenue_m"], lang), money_m(r["net_income_m"], lang),
                           price(r["eps"]), pct(r["revenue_growth_pct"]) if r["revenue_growth_pct"] is not None else "-"]
                          for r in rows])
            return f"{lead}\n\n{body}"

        if res.tool == "market_overview":
            r = rows[0]
            if ar:
                return (f"يوم {r['date']}{where}: **{r['advancers']}** سهم طلعوا و**{r['decliners']}** نزلوا من أصل "
                        f"{r['companies']}، ومتوسط التغير {pct(r['avg_change_pct'])}. أكتر سهم طلع **{r['top_gainer']}** "
                        f"({pct(r['top_gainer_pct'])}) وأكتر سهم نزل **{r['top_loser']}** ({pct(r['top_loser_pct'])}).")
            return (f"On {r['date']}{where}, **{r['advancers']}** stocks rose and **{r['decliners']}** fell out of "
                    f"{r['companies']}, with an average change of {pct(r['avg_change_pct'])}. The top gainer was "
                    f"**{r['top_gainer']}** ({pct(r['top_gainer_pct'])}) and the top loser **{r['top_loser']}** "
                    f"({pct(r['top_loser_pct'])}).")

        if res.tool == "signal_performance":
            h = a.get("horizon_days", 10)
            top = rows[0]
            label = SIGNAL_LABEL.get(top["signal_type"], (top["signal_type"], top["signal_type"]))[1 if ar else 0]
            lead = (f"بعد {h} أيام تداول من إشارة **{label}**، متوسط حركة السهم كان **{pct(top['avg_return_pct'])}**، "
                    f"وطلع في {top['up_rate_pct']}% من الحالات ({top['signals']} إشارة). ده أداء تاريخي على بيانات تجريبية، مش توقع."
                    if ar else
                    f"{h} trading days after a **{label}**, stocks moved **{pct(top['avg_return_pct'])}** on average and were "
                    f"higher {top['up_rate_pct']}% of the time ({top['signals']} signals). Historical, demo data, not a forecast.")
            if len(rows) == 1:
                return lead
            body = table(["الإشارة" if ar else "Signal", "العدد" if ar else "Signals", "متوسط العائد" if ar else "Avg return",
                          "نسبة الصعود" if ar else "Up rate", "الأفضل" if ar else "Best", "الأسوأ" if ar else "Worst"],
                         [[SIGNAL_LABEL.get(r["signal_type"], (r["signal_type"],) * 2)[1 if ar else 0], r["signals"],
                           pct(r["avg_return_pct"]), f"{r['up_rate_pct']}%", pct(r["best_pct"]), pct(r["worst_pct"])] for r in rows])
            return f"{lead}\n\n{body}"

        if res.tool == "risk_metrics":
            r = rows[0]
            per = PERIOD_LABEL[lang].get(r["period"], r["period"])
            beta = r["beta"] if r["beta"] is not None else "-"
            if ar:
                return (f"مخاطر سهم **{name(r, lang)}** خلال {per}: التذبذب السنوي **{r['volatility_pct']}%**، وأقصى تراجع "
                        f"**{r['max_drawdown_pct']}%**، وبيتا {beta} مقارنة بسوقه. أحسن يوم {pct(r['best_day_pct'])} وأسوأ يوم "
                        f"{pct(r['worst_day_pct'])}، والعائد في الفترة {pct(r['period_return_pct'])}.")
            return (f"Over {per}, **{name(r, lang)}** had annualized volatility of **{r['volatility_pct']}%**, a max drawdown of "
                    f"**{r['max_drawdown_pct']}%** and a beta of {beta} vs its market. Best day {pct(r['best_day_pct'])}, worst day "
                    f"{pct(r['worst_day_pct'])}, period return {pct(r['period_return_pct'])}.")

        if res.tool == "list_companies":
            n = int(res.extra.get("total", len(rows)))
            lead = (f"فيه **{n}** شركة{where} في البيانات:" if ar else f"There are **{n}** companies{where} in the data:")
            if n > len(rows):
                lead += (f" (أول {len(rows)} في الجدول)" if ar else f" (the first {len(rows)} are listed)")
            return f"{lead}\n\n" + table(["الشركة" if ar else "Company", "الرمز" if ar else "Ticker", "القطاع" if ar else "Sector"],
                                         [[name(r, lang), r["ticker"], r["sector"]] for r in rows])

        # run_sql: generic shape
        if len(rows) == 1 and len(rows[0]) == 1:
            value = next(iter(rows[0].values()))
            return f"الإجابة: **{value}**" if ar else f"The answer is **{value}**."
        headers = list(rows[0].keys())
        n = len(rows)
        lead = f"النتيجة ({n} صف):" if ar else f"Here {'is' if n == 1 else 'are'} the {n} result{'s' if n != 1 else ''}:"
        return f"{lead}\n\n" + table(headers, [list(r.values()) for r in rows[:20]])


WRITER_SYSTEM = """You are a friendly, sharp market-data assistant, like a top chat assistant with access to a database.

Style:
- Reply in the user's language and dialect (Egyptian question -> Egyptian answer, Gulf -> Gulf, English -> English).
- Lead with the answer, then the key detail. No filler, no repeating the question.
- Choose the shape that fits: 1-2 sentences for one fact, short bullets for 2-4 items, a short lead line plus a
  Markdown table when comparing 5+ rows or several columns. Bold the key number.
- Use only numbers from the tool results, formatted with 2 decimals for prices and a sign for % changes.
  Never invent numbers, companies or dates. All companies are fictional demo data.
- Never show SQL, column names, JSON or internal errors.
- For buy/sell questions: say you give information, not investment advice, then share the relevant facts."""


class LLMWriter:
    def __init__(self, llm: ChatModel):
        self.llm = llm

    def messages(self, question: str, plan: Plan, results: list[ToolResult], lang: str, dialect: str,
                 history: list[dict[str, str]]) -> list[dict[str, str]]:
        data = [{"tool": r.tool, "args": r.args, "rows": r.rows[:30]} for r in results]
        content = (f"User message: {question}\nRoute: {plan.route}\nLanguage: {lang}, dialect: {dialect}\n"
                   f"Tool results (JSON): {json.dumps(data, ensure_ascii=False, default=str)}\n\nWrite the reply now.")
        return [{"role": "system", "content": WRITER_SYSTEM}, *history, {"role": "user", "content": content}]

    def stream(self, *args: Any) -> Iterator[str]:
        return self.llm.stream(self.messages(*args))


# ---------- output gate ----------

SQL_FRAGMENT = re.compile(r"\b(select|insert|update|delete|drop|create|alter|pragma|attach)\b[^\n]*?"
                          r"\b(from|into|table|set|database)\b[^\n]*", re.IGNORECASE)
RAW_DICT = re.compile(r"\{\s*['\"][^{}\n]*\}")
CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
RAW_ROW = re.compile(r"^\s*[\[{(].*[\]})]\s*$", re.MULTILINE)


def finalize(text: str, route: str, lang: str) -> str:
    """Remove anything that shouldn't reach a user and make sure the reply is never empty."""
    text = CODE_BLOCK.sub("", text or "")
    text = SQL_FRAGMENT.sub("", text)
    text = RAW_DICT.sub("", text)
    text = UUID.sub("", text)
    text = RAW_ROW.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if route == "advice" and DISCLAIMER[lang] not in text:
        text += "\n\n" + DISCLAIMER[lang]
    if not text:
        text = "مقدرتش أكوّن إجابة، جرّب تسأل بطريقة تانية." if lang == "ar" else "I couldn't put an answer together. Try rephrasing."
    return text


def chunk_text(text: str, size: int = 3) -> Iterator[str]:
    """Split a finished reply into small word chunks so offline replies stream like model output."""
    words = re.split(r"(\s+)", text)
    for i in range(0, len(words), size * 2):
        yield "".join(words[i:i + size * 2])
