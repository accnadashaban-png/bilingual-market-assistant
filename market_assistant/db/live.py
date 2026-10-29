"""Keep a market database current while the assistant is running.

    python -m market_assistant add-prices new_prices.csv --db my-data.db   # append or correct prices
    python -m market_assistant live-demo --every 10                          # a simulated feed on a copy of the demo

`append_prices` is what a scheduled job or a data pipeline calls: it adds (or corrects) daily prices and
recomputes indicators and signals for the companies it touched, in one transaction. The assistant reads the
database on every question and re-reads its summaries every few seconds, so new data is answered at once.
"""
from __future__ import annotations

import random
import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from market_assistant.db.builder import ImportError_, PriceRow, _read, indicators_and_signals


def append_prices(db_path: Path, rows: list[PriceRow]) -> dict[str, Any]:
    """Insert or replace daily prices, then recompute indicators and signals from the earliest changed date on."""
    if not rows:
        return {"prices": 0, "companies": 0, "signals": 0, "latest_date": None}
    con = sqlite3.connect(Path(db_path), timeout=10)
    try:
        known = {r[0] for r in con.execute("SELECT ticker FROM companies")}
        unknown = sorted({r.ticker for r in rows} - known)
        if unknown:
            raise ImportError_(f"unknown tickers (add them to companies first): {', '.join(unknown[:5])}")
        first_changed: dict[str, date] = {}
        for r in rows:
            first_changed[r.ticker] = min(first_changed.get(r.ticker, r.date), r.date)
        with con:  # one transaction: readers see the old data or the new data, never half of it
            con.executemany("INSERT OR REPLACE INTO daily_prices VALUES (?,?,?,?,?,?,?)",
                            [(r.ticker, r.date.isoformat(), r.open, r.high, r.low, r.close, r.volume) for r in rows])
            signals = 0
            for ticker, since in first_changed.items():
                series = con.execute("SELECT date, open, high, low, close, volume FROM daily_prices WHERE ticker = ? "
                                     "ORDER BY date", (ticker,)).fetchall()
                days = [date.fromisoformat(s[0]) for s in series]
                indicator_rows, signal_rows = indicators_and_signals(ticker, days, [tuple(s[1:]) for s in series])
                cut = since.isoformat()
                con.execute("DELETE FROM indicators WHERE ticker = ? AND date >= ?", (ticker, cut))
                con.execute("DELETE FROM signals WHERE ticker = ? AND date >= ?", (ticker, cut))
                con.executemany("INSERT INTO indicators VALUES (?,?,?,?,?,?,?,?)", [r for r in indicator_rows if r[1] >= cut])
                new_signals = [r for r in signal_rows if r[1] >= cut]
                con.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?)", new_signals)
                signals += len(new_signals)
        latest = con.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
        return {"prices": len(rows), "companies": len(first_changed), "signals": signals, "latest_date": latest}
    finally:
        con.close()


def append_csv(csv_path: Path, db_path: Path) -> dict[str, Any]:
    """Validate a daily_prices.csv (same columns as the import) and append it."""
    return append_prices(db_path, _read(Path(csv_path), PriceRow))


def next_trading_day(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:  # the demo markets close on Saturday and Sunday
        day += timedelta(days=1)
    return day


def simulate_day(db_path: Path, rng: random.Random | None = None, today: date | None = None) -> dict[str, Any]:
    """Demo only. Write the next trading day for every company (a random walk from the last close), or, when the
    data already reaches `today`, move today's prices like trades during the session. Never writes a future date."""
    rng = rng or random.Random()
    con = sqlite3.connect(Path(db_path), timeout=10)
    try:
        latest = con.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
        last = con.execute("SELECT ticker, open, high, low, close, volume FROM daily_prices WHERE date = ?", (latest,)).fetchall()
    finally:
        con.close()
    day = next_trading_day(date.fromisoformat(latest))
    rows = []
    if today is not None and day > today:  # up to date: an intraday tick on the latest day
        for ticker, open_, high, low, close, volume in last:
            new_close = round(max(0.5, close * (1 + rng.gauss(0, 0.004))), 2)
            rows.append(PriceRow(ticker=ticker, date=date.fromisoformat(latest), open=open_, high=max(high, new_close),
                                 low=min(low, new_close), close=new_close, volume=volume + int(volume * rng.uniform(0, .05))))
        return {**append_prices(db_path, rows), "mode": "tick"}
    for ticker, _open, _high, _low, close, volume in last:
        move = rng.gauss(0.0004, 0.016)
        new_close = round(max(0.5, close * (1 + move)), 2)
        open_ = round(close * (1 + rng.gauss(0, 0.004)), 2)
        high = round(max(open_, new_close) * (1 + abs(rng.gauss(0, 0.006))), 2)
        low = round(min(open_, new_close) * (1 - abs(rng.gauss(0, 0.006))), 2)
        rows.append(PriceRow(ticker=ticker, date=day, open=open_, high=high, low=low, close=new_close,
                             volume=max(1, int(volume * rng.uniform(0.6, 1.6)))))
    return {**append_prices(db_path, rows), "mode": "new_day"}


class LiveFeed:
    """A background thread that keeps a demo database moving every `every_s` seconds: missing trading days up to
    today first, then price changes during today's session."""

    def __init__(self, db_path: Path, every_s: float = 10.0, seed: int | None = None):
        self.db_path, self.every_s = Path(db_path), every_s
        self.rng = random.Random(seed)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.days_added = 0
        self.last: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if not self.running:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="live-feed", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.every_s):
            self.last = simulate_day(self.db_path, self.rng, today=date.today())
            self.days_added += 1

