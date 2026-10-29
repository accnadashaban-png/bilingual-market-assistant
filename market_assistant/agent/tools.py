"""Typed tools the planner can call.

Each tool validates its arguments with Pydantic and runs a fixed, parameterized
query, so user text never becomes SQL. `run_sql` is the fallback for questions
no tool covers; its SQL goes through the sqlglot validator first.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from market_assistant.core.errors import NotEnoughHistoryError, NotFoundError
from market_assistant.db.database import BaseDatabase as Database
from market_assistant.db.sql_safety import validate_select

Period = Literal["1d", "1w", "1m", "3m", "6m", "ytd", "1y"]
Country = Literal["US", "SA", "EG"]
SignalType = Literal["52w_high_breakout", "52w_low_breakdown", "golden_cross", "death_cross",
                     "rsi_oversold", "rsi_overbought", "volume_spike"]
PERIOD_OFFSETS = {"1d": 1, "1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}


@dataclass
class ToolResult:
    tool: str
    args: dict[str, Any]
    rows: list[dict[str, Any]]
    sql: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _ticker(db: Database, ticker: str) -> str:
    known = {c["ticker"].upper(): c["ticker"] for c in db.companies}
    key = ticker.strip().upper()
    if key in known:
        return known[key]
    if f"{key}.SR" in known or f"{key}.CA" in known:
        return known.get(f"{key}.SR") or known[f"{key}.CA"]
    raise NotFoundError(ticker)


def base_date(db: Database, period: str) -> str:
    """The trading day the period's change is measured from."""
    if period == "ytd":
        found = db.scalar("SELECT MAX(date) FROM daily_prices WHERE date < :y", {"y": db.latest_date[:4] + "-01-01"})
    else:
        # counted back from the same latest day the answer uses, so a trading day added by a live feed between two
        # reads can never make a period start on its own last day
        found = db.scalar("SELECT date FROM (SELECT DISTINCT date FROM daily_prices WHERE date <= :last ORDER BY date DESC) AS d "
                          "LIMIT 1 OFFSET :n", {"n": PERIOD_OFFSETS[period], "last": db.latest_date})
    if found is None:  # never measure a "1 year" change from a shorter history
        raise NotEnoughHistoryError(period)
    return str(found)


PERFORMANCE_SQL = """
SELECT c.ticker, c.name_en, c.name_ar, c.sector, c.country, c.currency,
       l.close AS last_close, b.close AS base_close,
       ROUND((l.close / b.close - 1) * 100, 2) AS change_pct,
       i.rsi14, i.ma50, i.ma200, i.high_52w
FROM companies c
JOIN daily_prices l ON l.ticker = c.ticker AND l.date = :last
JOIN daily_prices b ON b.ticker = c.ticker AND b.date = :base
JOIN indicators i ON i.ticker = c.ticker AND i.date = :last
WHERE (:country IS NULL OR c.country = :country) AND (:sector IS NULL OR c.sector = :sector)
"""


# ---------- argument models ----------

class TickerArgs(BaseModel):
    ticker: str


class PerformanceArgs(BaseModel):
    ticker: str
    period: Period = "1m"


class MoversArgs(BaseModel):
    period: Period = "1d"
    direction: Literal["up", "down"] = "up"
    country: Country | None = None
    sector: str | None = None
    limit: int = Field(5, ge=1, le=15)


class SectorArgs(BaseModel):
    period: Period = "1m"
    country: Country | None = None


class CompareArgs(BaseModel):
    tickers: list[str] = Field(min_length=2, max_length=6)
    period: Period = "1m"


class ScreenArgs(BaseModel):
    country: Country | None = None
    sector: str | None = None
    above_ma50: bool | None = None
    above_ma200: bool | None = None
    rsi_min: float | None = Field(None, ge=0, le=100)
    rsi_max: float | None = Field(None, ge=0, le=100)
    within_pct_of_52w_high: float | None = Field(None, gt=0, le=50)
    limit: int = Field(10, ge=1, le=30)


class SignalArgs(BaseModel):
    signal_type: SignalType | None = None
    direction: Literal["bullish", "bearish"] | None = None
    ticker: str | None = None
    country: Country | None = None
    days: int = Field(5, ge=1, le=60)
    limit: int = Field(10, ge=1, le=30)


class OverviewArgs(BaseModel):
    country: Country | None = None


class SqlArgs(BaseModel):
    sql: str

    @field_validator("sql")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sql is empty")
        return v


# ---------- tools ----------

def quote(db: Database, a: TickerArgs) -> ToolResult:
    sql = """
    SELECT c.ticker, c.name_en, c.name_ar, c.currency, p.date, p.close, p.volume,
           ROUND((p.close / prev.close - 1) * 100, 2) AS change_pct, i.high_52w, i.low_52w, i.rsi14
    FROM companies c
    JOIN daily_prices p ON p.ticker = c.ticker AND p.date = :last
    JOIN daily_prices prev ON prev.ticker = c.ticker AND prev.date = :prev
    JOIN indicators i ON i.ticker = c.ticker AND i.date = :last
    WHERE c.ticker = :ticker"""
    params = {"ticker": _ticker(db, a.ticker), "last": db.latest_date, "prev": base_date(db, "1d")}
    return ToolResult("quote", a.model_dump(), db.fetch(sql, params), sql)


def performance(db: Database, a: PerformanceArgs) -> ToolResult:
    start = base_date(db, a.period)
    sql = """
    SELECT c.ticker, c.name_en, c.name_ar, c.currency,
           :start AS start_date, b.close AS start_close, :last AS end_date, l.close AS end_close,
           ROUND((l.close / b.close - 1) * 100, 2) AS change_pct,
           (SELECT MAX(high) FROM daily_prices WHERE ticker = c.ticker AND date > :start) AS period_high,
           (SELECT MIN(low) FROM daily_prices WHERE ticker = c.ticker AND date > :start) AS period_low
    FROM companies c
    JOIN daily_prices b ON b.ticker = c.ticker AND b.date = :start
    JOIN daily_prices l ON l.ticker = c.ticker AND l.date = :last
    WHERE c.ticker = :ticker"""
    params = {"ticker": _ticker(db, a.ticker), "start": start, "last": db.latest_date}
    return ToolResult("performance", a.model_dump(), db.fetch(sql, params), sql)


def top_movers(db: Database, a: MoversArgs) -> ToolResult:
    order = "DESC" if a.direction == "up" else "ASC"
    sql = PERFORMANCE_SQL + f" ORDER BY change_pct {order}, c.ticker LIMIT :limit"
    params = {"last": db.latest_date, "base": base_date(db, a.period), "country": a.country,
              "sector": a.sector, "limit": a.limit}
    return ToolResult("top_movers", a.model_dump(), db.fetch(sql, params), sql)


def sector_performance(db: Database, a: SectorArgs) -> ToolResult:
    sql = f"""
    SELECT sector, COUNT(ticker) AS companies, ROUND(AVG(change_pct), 2) AS avg_change_pct,
           MAX(change_pct) AS best_change_pct
    FROM ({PERFORMANCE_SQL}) AS perf GROUP BY sector ORDER BY avg_change_pct DESC, sector"""
    params = {"last": db.latest_date, "base": base_date(db, a.period), "country": a.country, "sector": None}
    return ToolResult("sector_performance", a.model_dump(), db.fetch(sql, params), sql)


def compare(db: Database, a: CompareArgs) -> ToolResult:
    tickers = list(dict.fromkeys(_ticker(db, t) for t in a.tickers))
    marks = ", ".join(f":t{i}" for i in range(len(tickers)))
    sql = PERFORMANCE_SQL + f" AND c.ticker IN ({marks}) ORDER BY change_pct DESC, c.ticker"
    params: dict[str, Any] = {"last": db.latest_date, "base": base_date(db, a.period), "country": None, "sector": None}
    params.update({f"t{i}": t for i, t in enumerate(tickers)})
    return ToolResult("compare", {**a.model_dump(), "tickers": tickers}, db.fetch(sql, params), sql)


def screen(db: Database, a: ScreenArgs) -> ToolResult:
    sql = PERFORMANCE_SQL + """
      AND (:above50 IS NULL OR (l.close > i.ma50) = :above50)
      AND (:above200 IS NULL OR (l.close > i.ma200) = :above200)
      AND (:rsi_min IS NULL OR i.rsi14 >= :rsi_min)
      AND (:rsi_max IS NULL OR i.rsi14 <= :rsi_max)
      AND (:near IS NULL OR l.close >= i.high_52w * (1 - :near / 100.0))
    ORDER BY change_pct DESC, c.ticker LIMIT :limit"""
    params = {"last": db.latest_date, "base": base_date(db, "1m"), "country": a.country, "sector": a.sector,
              "above50": a.above_ma50, "above200": a.above_ma200, "rsi_min": a.rsi_min, "rsi_max": a.rsi_max,
              "near": a.within_pct_of_52w_high, "limit": a.limit}
    return ToolResult("screen", a.model_dump(), db.fetch(sql, params), sql)


def signals(db: Database, a: SignalArgs) -> ToolResult:
    sql = """
    SELECT s.date, s.ticker, c.name_en, c.name_ar, s.signal_type, s.direction, s.level, s.note
    FROM signals s JOIN companies c ON c.ticker = s.ticker
    WHERE s.date >= (SELECT date FROM (SELECT DISTINCT date FROM daily_prices ORDER BY date DESC) AS d
                     LIMIT 1 OFFSET :offset)
      AND (:type IS NULL OR s.signal_type = :type)
      AND (:direction IS NULL OR s.direction = :direction)
      AND (:ticker IS NULL OR s.ticker = :ticker)
      AND (:country IS NULL OR c.country = :country)
    ORDER BY s.date DESC, s.ticker LIMIT :limit"""
    params = {"offset": a.days - 1, "type": a.signal_type, "direction": a.direction,
              "ticker": _ticker(db, a.ticker) if a.ticker else None, "country": a.country, "limit": a.limit}
    return ToolResult("signals", a.model_dump(), db.fetch(sql, params), sql)


def company_profile(db: Database, a: TickerArgs) -> ToolResult:
    sql = """
    SELECT c.ticker, c.name_en, c.name_ar, c.sector, c.country, c.exchange, c.currency,
           ROUND(p.close * c.shares_outstanding_m, 0) AS market_cap_m,
           f.fiscal_year, f.revenue_m, f.net_income_m, f.eps,
           CASE WHEN f.eps > 0 THEN ROUND(p.close / f.eps, 1) END AS pe_ratio
    FROM companies c
    JOIN daily_prices p ON p.ticker = c.ticker AND p.date = :last
    LEFT JOIN fundamentals f ON f.ticker = c.ticker
         AND f.fiscal_year = (SELECT MAX(fiscal_year) FROM fundamentals WHERE ticker = c.ticker)
    WHERE c.ticker = :ticker"""
    params = {"ticker": _ticker(db, a.ticker), "last": db.latest_date}
    return ToolResult("company_profile", a.model_dump(), db.fetch(sql, params), sql)


def fundamentals(db: Database, a: TickerArgs) -> ToolResult:
    sql = """
    SELECT f.fiscal_year, c.name_en, c.name_ar, c.currency, f.revenue_m, f.net_income_m, f.eps,
           ROUND((f.revenue_m / prev.revenue_m - 1) * 100, 1) AS revenue_growth_pct
    FROM fundamentals f
    JOIN companies c ON c.ticker = f.ticker
    LEFT JOIN fundamentals prev ON prev.ticker = f.ticker AND prev.fiscal_year = f.fiscal_year - 1
    WHERE f.ticker = :ticker ORDER BY f.fiscal_year"""
    return ToolResult("fundamentals", a.model_dump(), db.fetch(sql, {"ticker": _ticker(db, a.ticker)}), sql)


def market_overview(db: Database, a: OverviewArgs) -> ToolResult:
    sql = f"""
    SELECT COUNT(ticker) AS companies,
           SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) AS advancers,
           SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) AS decliners,
           SUM(CASE WHEN change_pct = 0 THEN 1 ELSE 0 END) AS unchanged,
           ROUND(AVG(change_pct), 2) AS avg_change_pct,
           SUM(CASE WHEN last_close >= high_52w THEN 1 ELSE 0 END) AS at_52w_high
    FROM ({PERFORMANCE_SQL}) AS perf"""
    params = {"last": db.latest_date, "base": base_date(db, "1d"), "country": a.country, "sector": None}
    rows = db.fetch(sql, params)
    movers = db.fetch(PERFORMANCE_SQL + " ORDER BY change_pct DESC, c.ticker", params)
    if rows and movers:
        rows[0].update(date=db.latest_date, top_gainer=movers[0]["ticker"], top_gainer_pct=movers[0]["change_pct"],
                       top_loser=movers[-1]["ticker"], top_loser_pct=movers[-1]["change_pct"])
    return ToolResult("market_overview", a.model_dump(), rows, sql)


class ListArgs(BaseModel):
    country: Country | None = None
    sector: str | None = None


def list_companies(db: Database, a: ListArgs) -> ToolResult:
    sql = """
    SELECT ticker, name_en, name_ar, sector, country FROM companies
    WHERE (:country IS NULL OR country = :country) AND (:sector IS NULL OR sector = :sector)
    ORDER BY sector, ticker"""
    return ToolResult("list_companies", a.model_dump(), db.fetch(sql, a.model_dump()), sql)


class SignalPerformanceArgs(BaseModel):
    signal_type: SignalType | None = None
    horizon_days: Literal[5, 10, 20] = 10
    country: Country | None = None


def signal_performance(db: Database, a: SignalPerformanceArgs) -> ToolResult:
    """How prices moved N trading days after each type of signal: average return and hit rate."""
    sql = """
    WITH days AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS n FROM (SELECT DISTINCT date FROM daily_prices) AS d),
    outcomes AS (
        SELECT s.signal_type, s.direction, (p1.close / p0.close - 1) * 100 AS fwd_pct
        FROM signals s
        JOIN companies c ON c.ticker = s.ticker
        JOIN days d0 ON d0.date = s.date
        JOIN days d1 ON d1.n = d0.n + :h
        JOIN daily_prices p0 ON p0.ticker = s.ticker AND p0.date = s.date
        JOIN daily_prices p1 ON p1.ticker = s.ticker AND p1.date = d1.date
        WHERE (:type IS NULL OR s.signal_type = :type) AND (:country IS NULL OR c.country = :country)
    )
    SELECT signal_type, direction, COUNT(fwd_pct) AS signals, ROUND(AVG(fwd_pct), 2) AS avg_return_pct,
           ROUND(100.0 * SUM(CASE WHEN fwd_pct > 0 THEN 1 ELSE 0 END) / COUNT(fwd_pct), 1) AS up_rate_pct,
           ROUND(MAX(fwd_pct), 2) AS best_pct, ROUND(MIN(fwd_pct), 2) AS worst_pct
    FROM outcomes GROUP BY signal_type, direction ORDER BY signals DESC"""
    params = {"h": a.horizon_days, "type": a.signal_type, "country": a.country}
    return ToolResult("signal_performance", a.model_dump(), db.fetch(sql, params), sql)


class RiskArgs(BaseModel):
    ticker: str
    period: Literal["3m", "6m", "1y"] = "1y"


def risk_metrics(db: Database, a: RiskArgs) -> ToolResult:
    """Annualized volatility, max drawdown and beta against an equal-weight index of the same market."""
    ticker = _ticker(db, a.ticker)
    start = base_date(db, a.period)
    closes = [float(r["close"]) for r in db.fetch(
        "SELECT close FROM daily_prices WHERE ticker = :t AND date >= :start ORDER BY date", {"t": ticker, "start": start},
        limit=400)]
    market_sql = """
    WITH r AS (
        SELECT p.date, p.close / LAG(p.close) OVER (PARTITION BY p.ticker ORDER BY p.date) - 1 AS ret
        FROM daily_prices p JOIN companies c ON c.ticker = p.ticker
        WHERE c.country = (SELECT country FROM companies WHERE ticker = :t) AND p.date >= :start
    )
    SELECT date, AVG(ret) AS mret FROM r WHERE ret IS NOT NULL GROUP BY date ORDER BY date"""
    market = [float(r["mret"]) for r in db.fetch(market_sql, {"t": ticker, "start": start}, limit=400)]
    returns = [b / a_ - 1 for a_, b in zip(closes, closes[1:], strict=False)]
    if len(returns) < 2:
        raise NotEnoughHistoryError(a.period)
    peak, max_dd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        max_dd = min(max_dd, c / peak - 1)
    n = min(len(returns), len(market))
    beta = None
    if n > 20 and statistics.pvariance(market[-n:]) > 0:
        beta = round(statistics.covariance(returns[-n:], market[-n:]) / statistics.variance(market[-n:]), 2)
    name = next(c for c in db.companies if c["ticker"] == ticker)
    row = {"ticker": ticker, "name_en": name["name_en"], "name_ar": name["name_ar"], "period": a.period,
           "trading_days": len(returns), "period_return_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
           "volatility_pct": round(statistics.stdev(returns) * math.sqrt(252) * 100, 1),
           "max_drawdown_pct": round(max_dd * 100, 1), "current_drawdown_pct": round((closes[-1] / peak - 1) * 100, 1),
           "best_day_pct": round(max(returns) * 100, 2), "worst_day_pct": round(min(returns) * 100, 2), "beta": beta}
    return ToolResult("risk_metrics", a.model_dump(), [row], market_sql)


def run_sql(db: Database, a: SqlArgs) -> ToolResult:
    safe = validate_select(a.sql, db.max_rows, dialect=db.dialect)
    return ToolResult("run_sql", {"sql": safe}, db.fetch(safe), safe)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[Database, Any], ToolResult]


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("quote", "Latest price, daily change, volume and 52-week range of one company.", TickerArgs, quote),
    Tool("performance", "How one company's price moved over a period (start/end price, change, high/low).",
         PerformanceArgs, performance),
    Tool("top_movers", "Biggest gainers (up) or losers (down) over a period, optionally by country/sector.",
         MoversArgs, top_movers),
    Tool("sector_performance", "Average change per sector over a period, optionally in one country.",
         SectorArgs, sector_performance),
    Tool("compare", "Compare 2-6 companies over a period (change, price, RSI, moving averages).", CompareArgs, compare),
    Tool("screen", "Find stocks by filters: country, sector, above 50/200-day average, RSI range, near 52-week high.",
         ScreenArgs, screen),
    Tool("signals", "Recent technical signals: breakouts, crosses, RSI extremes, volume spikes.", SignalArgs, signals),
    Tool("company_profile", "Sector, exchange, market cap, revenue, net income, EPS and P/E of one company.",
         TickerArgs, company_profile),
    Tool("fundamentals", "Revenue, net income and EPS by fiscal year (2023-2025) with revenue growth.",
         TickerArgs, fundamentals),
    Tool("market_overview", "Today's market breadth: advancers/decliners, average change, top gainer and loser.",
         OverviewArgs, market_overview),
    Tool("signal_performance", "How stocks moved 5/10/20 trading days after each signal type: average return and how often they rose.",
         SignalPerformanceArgs, signal_performance),
    Tool("risk_metrics", "Risk profile of one company: annualized volatility, max drawdown, best/worst day, beta vs its market.",
         RiskArgs, risk_metrics),
    Tool("list_companies", "List (and count) the companies, optionally by country and/or sector.", ListArgs,
         list_companies),
    Tool("run_sql", "Fallback: one read-only SELECT for questions no other tool covers.", SqlArgs, run_sql),
]}


def call_tool(db: Database, name: str, args: dict[str, Any] | None = None) -> ToolResult:
    """Validate the arguments and run a tool. Raises KeyError or pydantic.ValidationError."""
    tool = TOOLS[name]
    return tool.fn(db, tool.args_model.model_validate(args or {}))


def tool_specs() -> list[dict[str, Any]]:
    """Tool names, descriptions and JSON schemas, for the planner prompt and the /tools endpoint."""
    return [{"name": t.name, "description": t.description, "parameters": t.args_model.model_json_schema()}
            for t in TOOLS.values()]
