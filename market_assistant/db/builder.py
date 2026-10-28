"""Build the read-only market database: schema, indicators, signals, and import from your own CSV files.

    from market_assistant.db.builder import import_csv
    import_csv(Path("my-data"), Path("data/market.db"))

CSV files (UTF-8, header row):
  companies.csv     ticker, name_en, [name_ar], sector, country, exchange, currency, [shares_outstanding_m]
  daily_prices.csv  ticker, date (YYYY-MM-DD), open, high, low, close, volume
  fundamentals.csv  (optional) ticker, fiscal_year, revenue_m, net_income_m, eps

Every row is validated; the first problem stops the import with its file and line number.
Indicators (50/200-day averages, RSI(14), 52-week range, 20-day volume) and technical signals are computed here.
"""
from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI."""
    out: list[float | None] = [None] * len(closes)
    gains = losses = 0.0
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain, loss = max(change, 0), max(-change, 0)
        if i <= period:
            gains += gain
            losses += loss
            if i == period:
                gains, losses = gains / period, losses / period
            else:
                continue
        else:
            gains = (gains * (period - 1) + gain) / period
            losses = (losses * (period - 1) + loss) / period
        out[i] = 100.0 if losses == 0 else round(100 - 100 / (1 + gains / losses), 2)
    return out


def moving_average(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            out[i] = round(total / window, 2)
    return out


Row = tuple[Any, ...]


def indicators_and_signals(ticker: str, days: list[date], prices: list[Row]) -> tuple[list[Row], list[Row]]:
    closes = [p[3] for p in prices]
    volumes = [p[4] for p in prices]
    ma50, ma200, rsi14 = moving_average(closes, 50), moving_average(closes, 200), rsi(closes)
    avg_vol20 = moving_average([float(v) for v in volumes], 20)

    indicator_rows, signal_rows = [], []
    for i, d in enumerate(days):
        window = closes[max(0, i - 251): i + 1]
        high_52w, low_52w = max(window), min(window)
        vol20 = avg_vol20[i]
        indicator_rows.append((ticker, d.isoformat(), ma50[i], ma200[i], rsi14[i], high_52w, low_52w,
                               int(vol20) if vol20 else None))
        if i < 120:  # need some history before calling a signal
            continue

        def add(kind: str, direction: str, level: float | None, note: str, day: str = d.isoformat()) -> None:
            signal_rows.append((ticker, day, kind, direction, level, note))

        prev_high, prev_low = max(closes[max(0, i - 252): i]), min(closes[max(0, i - 252): i])
        if closes[i] > prev_high:
            add("52w_high_breakout", "bullish", prev_high, f"closed {closes[i]} above the 52-week high {prev_high}")
        if closes[i] < prev_low:
            add("52w_low_breakdown", "bearish", prev_low, f"closed {closes[i]} below the 52-week low {prev_low}")
        fast, slow, fast_prev, slow_prev = ma50[i], ma200[i], ma50[i - 1], ma200[i - 1]
        if fast is not None and slow is not None and fast_prev is not None and slow_prev is not None:
            if fast_prev <= slow_prev and fast > slow:
                add("golden_cross", "bullish", slow, "50-day average crossed above the 200-day average")
            if fast_prev >= slow_prev and fast < slow:
                add("death_cross", "bearish", slow, "50-day average crossed below the 200-day average")
        rsi_now, rsi_prev = rsi14[i], rsi14[i - 1]
        if rsi_now is not None and rsi_prev is not None:
            if rsi_prev >= 30 > rsi_now:
                add("rsi_oversold", "bullish", rsi_now, "RSI(14) fell below 30")
            if rsi_prev <= 70 < rsi_now:
                add("rsi_overbought", "bearish", rsi_now, "RSI(14) rose above 70")
        avg_volume = avg_vol20[i - 1]
        if avg_volume and volumes[i] > 2.5 * avg_volume:
            direction = "bullish" if closes[i] >= closes[i - 1] else "bearish"
            add("volume_spike", direction, float(volumes[i]), "volume above 2.5x its 20-day average")
    return indicator_rows, signal_rows


SCHEMA = """
CREATE TABLE companies (
    ticker TEXT PRIMARY KEY, name_en TEXT NOT NULL, name_ar TEXT NOT NULL, sector TEXT NOT NULL,
    country TEXT NOT NULL, exchange TEXT NOT NULL, currency TEXT NOT NULL, shares_outstanding_m REAL NOT NULL
);
CREATE TABLE daily_prices (
    ticker TEXT NOT NULL REFERENCES companies(ticker), date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL NOT NULL, volume INTEGER NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE indicators (
    ticker TEXT NOT NULL, date TEXT NOT NULL, ma50 REAL, ma200 REAL, rsi14 REAL,
    high_52w REAL, low_52w REAL, avg_volume_20d INTEGER, PRIMARY KEY (ticker, date)
);
CREATE TABLE signals (
    ticker TEXT NOT NULL, date TEXT NOT NULL, signal_type TEXT NOT NULL,
    direction TEXT NOT NULL, level REAL, note TEXT
);
CREATE TABLE fundamentals (
    ticker TEXT NOT NULL, fiscal_year INTEGER NOT NULL, revenue_m REAL, net_income_m REAL, eps REAL,
    PRIMARY KEY (ticker, fiscal_year)
);
CREATE INDEX idx_prices_date ON daily_prices(date);
CREATE INDEX idx_signals_date ON signals(date);
"""




class CompanyRow(BaseModel):
    ticker: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9._-]+$")
    name_en: str = Field(min_length=1, max_length=120)
    name_ar: str = ""
    sector: str = Field(min_length=1, max_length=60)
    country: str = Field(min_length=2, max_length=2)
    exchange: str = Field(min_length=1, max_length=30)
    currency: str = Field(min_length=3, max_length=3)
    shares_outstanding_m: float = Field(0.0, ge=0)

    @field_validator("country", "currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class PriceRow(BaseModel):
    ticker: str
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> PriceRow:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("expected low <= open/close <= high")
        return self


class FundamentalRow(BaseModel):
    ticker: str
    fiscal_year: int = Field(ge=1900, le=2100)
    revenue_m: float
    net_income_m: float
    eps: float


class ImportError_(ValueError):
    """Raised with the file and line of the first invalid row."""


def _read(path: Path, model: type[BaseModel]) -> list[Any]:
    rows: list[Any] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for line, raw in enumerate(csv.DictReader(f), start=2):
            clean = {k.strip(): v.strip() for k, v in raw.items() if k and v is not None and v.strip() != ""}
            try:
                rows.append(model.model_validate(clean))
            except ValidationError as exc:
                first = exc.errors(include_url=False)[0]
                raise ImportError_(f"{path.name} line {line}: {'.'.join(map(str, first['loc'])) or 'row'}: {first['msg']}") from exc
    return rows


def write_database(out: Path, companies: list[CompanyRow], prices: list[PriceRow],
                   fundamentals: list[FundamentalRow] | None = None) -> dict[str, int]:
    """Create the database file from validated rows, computing indicators and signals per company."""
    by_ticker: dict[str, list[PriceRow]] = defaultdict(list)
    for p in prices:
        by_ticker[p.ticker].append(p)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        counts = {"companies": 0, "prices": 0, "signals": 0, "fundamentals": 0}
        for c in companies:
            con.execute("INSERT INTO companies VALUES (?,?,?,?,?,?,?,?)",
                        (c.ticker, c.name_en, c.name_ar or c.name_en, c.sector, c.country, c.exchange, c.currency,
                         c.shares_outstanding_m))
            series = sorted(by_ticker.get(c.ticker, []), key=lambda p: p.date)
            days = [p.date for p in series]
            if len(set(days)) != len(days):
                raise ImportError_(f"daily_prices.csv: duplicate dates for {c.ticker}")
            ohlcv = [(p.open, p.high, p.low, p.close, p.volume) for p in series]
            con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?)",
                            [(c.ticker, d.isoformat(), *row) for d, row in zip(days, ohlcv, strict=True)])
            indicator_rows, signal_rows = indicators_and_signals(c.ticker, days, ohlcv) if ohlcv else ([], [])
            con.executemany("INSERT INTO indicators VALUES (?,?,?,?,?,?,?,?)", indicator_rows)
            con.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?)", signal_rows)
            counts["companies"] += 1
            counts["prices"] += len(ohlcv)
            counts["signals"] += len(signal_rows)
        for f in fundamentals or []:
            con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?)", (f.ticker, f.fiscal_year, f.revenue_m, f.net_income_m, f.eps))
            counts["fundamentals"] += 1
        con.commit()
    finally:
        con.close()
    tmp.replace(out)  # the old database stays in place until the new one is complete
    return counts


def import_csv(folder: Path, out: Path) -> dict[str, int]:
    folder = Path(folder)
    for required in ("companies.csv", "daily_prices.csv"):
        if not (folder / required).exists():
            raise ImportError_(f"missing {required} in {folder}")
    companies = _read(folder / "companies.csv", CompanyRow)
    known = {c.ticker for c in companies}
    if len(known) != len(companies):
        raise ImportError_("companies.csv: duplicate tickers")
    prices = _read(folder / "daily_prices.csv", PriceRow)
    unknown = sorted({p.ticker for p in prices} - known)
    if unknown:
        raise ImportError_(f"daily_prices.csv: tickers not in companies.csv: {', '.join(unknown[:5])}")
    fundamentals = _read(folder / "fundamentals.csv", FundamentalRow) if (folder / "fundamentals.csv").exists() else []
    return write_database(Path(out), companies, prices, fundamentals)
