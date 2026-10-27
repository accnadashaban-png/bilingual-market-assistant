"""Build data/market.db: fictional companies, synthetic prices, indicators and signals.

Every company, ticker and number here is made up and generated from a fixed seed,
so the demo is reproducible and never presents fake numbers as real market data.

    python data/generate.py            # writes data/market.db
    python data/generate.py --out x.db
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 2026
END_DATE = date(2026, 10, 23)
TRADING_DAYS = 320  # enough history for a 200-day average and a 52-week window

# ticker, English name, Arabic name, sector, country, exchange, currency,
# start price, yearly drift, yearly volatility, shares outstanding (millions)
COMPANIES = [
    ("ATLS", "Atlas Robotics", "أطلس للروبوتات", "Technology", "US", "NASDAQ", "USD", 84, 0.35, 0.38, 410),
    ("NOVC", "Nova Cloud Systems", "نوفا للأنظمة السحابية", "Technology", "US", "NASDAQ", "USD", 132, 0.22, 0.32, 760),
    ("BRSM", "Brightline Semiconductors", "برايتلاين لأشباه الموصلات", "Technology", "US", "NASDAQ", "USD", 58, -0.10, 0.45, 980),
    ("HRBB", "Harbor Bancorp", "هاربور المصرفية", "Financials", "US", "NYSE", "USD", 46, 0.08, 0.22, 1250),
    ("PNIN", "Pinnacle Insurance Group", "بيناكل للتأمين", "Financials", "US", "NYSE", "USD", 71, 0.05, 0.18, 300),
    ("CRSE", "Crestline Energy", "كرستلاين للطاقة", "Energy", "US", "NYSE", "USD", 39, -0.05, 0.30, 1500),
    ("MDXH", "Medix Health", "ميديكس للرعاية الصحية", "Health Care", "US", "NYSE", "USD", 118, 0.12, 0.25, 520),
    ("GRVF", "Greenvale Foods", "جرين فيل للأغذية", "Consumer Staples", "US", "NYSE", "USD", 52, 0.03, 0.15, 640),
    ("SKAW", "Skyline Airways", "سكاي لاين للطيران", "Industrials", "US", "NYSE", "USD", 27, 0.18, 0.40, 870),
    ("ORMT", "Orbit Media", "أوربت ميديا", "Communication Services", "US", "NASDAQ", "USD", 64, 0.00, 0.33, 450),
    ("SLRU", "Solara Utilities", "سولارا للمرافق", "Utilities", "US", "NYSE", "USD", 33, 0.06, 0.14, 1100),
    ("ALMT", "Alder Materials", "ألدر للمواد", "Materials", "US", "NYSE", "USD", 47, -0.12, 0.28, 390),
    ("RSPC.SR", "Red Sea Petrochemicals", "البحر الأحمر للبتروكيماويات", "Materials", "SA", "Tadawul", "SAR", 38, 0.04, 0.24, 2400),
    ("DNBK.SR", "Dunes National Bank", "بنك الكثبان الوطني", "Financials", "SA", "Tadawul", "SAR", 72, 0.10, 0.20, 1800),
    ("NJTC.SR", "Najd Telecom", "نجد للاتصالات", "Communication Services", "SA", "Tadawul", "SAR", 41, 0.07, 0.19, 2000),
    ("OAEN.SR", "Oasis Energy", "الواحة للطاقة", "Energy", "SA", "Tadawul", "SAR", 29, 0.02, 0.21, 5200),
    ("HJRE.SR", "Hejaz Real Estate", "الحجاز العقارية", "Real Estate", "SA", "Tadawul", "SAR", 18, 0.15, 0.30, 950),
    ("FLHC.SR", "Falcon Healthcare", "فالكون للرعاية الصحية", "Health Care", "SA", "Tadawul", "SAR", 145, 0.20, 0.26, 260),
    ("SDRT.SR", "Sidra Retail", "سدرة للتجزئة", "Consumer Staples", "SA", "Tadawul", "SAR", 88, -0.08, 0.23, 300),
    ("TWQT.SR", "Tuwaiq Technologies", "طويق للتقنية", "Technology", "SA", "Tadawul", "SAR", 210, 0.30, 0.35, 120),
    ("JZCM.SR", "Jazan Cement", "جازان للأسمنت", "Materials", "SA", "Tadawul", "SAR", 24, -0.02, 0.22, 600),
    ("NLTC.CA", "Nile Telecom", "النيل للاتصالات", "Communication Services", "EG", "EGX", "EGP", 31, 0.40, 0.35, 1700),
    ("DLCM.CA", "Delta Cement", "الدلتا للأسمنت", "Materials", "EG", "EGX", "EGP", 12, 0.25, 0.40, 900),
    ("CRBK.CA", "Cairo Commerce Bank", "بنك القاهرة التجاري", "Financials", "EG", "EGX", "EGP", 74, 0.30, 0.30, 3000),
    ("PYFD.CA", "Pyramids Foods", "الأهرام للأغذية", "Consumer Staples", "EG", "EGX", "EGP", 19, 0.10, 0.28, 1100),
    ("ALXP.CA", "Alexandria Ports", "الإسكندرية للموانئ", "Industrials", "EG", "EGX", "EGP", 9, 0.50, 0.45, 2500),
    ("SNRG.CA", "Sinai Energy", "سيناء للطاقة", "Energy", "EG", "EGX", "EGP", 22, -0.15, 0.38, 800),
    ("LXHM.CA", "Luxor Homes", "الأقصر للإسكان", "Real Estate", "EG", "EGX", "EGP", 6, 0.35, 0.50, 4000),
    ("ASPH.CA", "Aswan Pharma", "أسوان للأدوية", "Health Care", "EG", "EGX", "EGP", 55, 0.18, 0.30, 400),
    ("FYTK.CA", "Fayoum Tech", "الفيوم للتكنولوجيا", "Technology", "EG", "EGX", "EGP", 14, 0.60, 0.55, 700),
]


def trading_days(end: date, count: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < count:
        if d.weekday() < 5:  # Monday to Friday
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def simulate(rng: random.Random, start: float, drift: float, vol: float, n: int) -> list[tuple]:
    """Geometric random walk with daily open/high/low/close and volume."""
    dt = 1 / 252
    rows, close = [], start
    base_volume = rng.uniform(0.8, 6.0) * 1_000_000
    for _ in range(n):
        shock = rng.gauss(0, 1)
        open_ = close * (1 + rng.gauss(0, vol * math.sqrt(dt) / 3))
        close = max(0.5, close * math.exp((drift - vol * vol / 2) * dt + vol * math.sqrt(dt) * shock))
        high = max(open_, close) * (1 + abs(rng.gauss(0, vol * math.sqrt(dt) / 2)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, vol * math.sqrt(dt) / 2)))
        volume = int(base_volume * math.exp(rng.gauss(0, 0.35)) * (1 + 1.5 * abs(shock) ** 2 / 4))
        rows.append((round(open_, 2), round(high, 2), round(low, 2), round(close, 2), volume))
    return rows


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


def indicators_and_signals(ticker: str, days: list[date], prices: list[tuple]):
    closes = [p[3] for p in prices]
    volumes = [p[4] for p in prices]
    ma50, ma200, rsi14 = moving_average(closes, 50), moving_average(closes, 200), rsi(closes)
    avg_vol20 = moving_average([float(v) for v in volumes], 20)

    indicator_rows, signal_rows = [], []
    for i, d in enumerate(days):
        window = closes[max(0, i - 251): i + 1]
        high_52w, low_52w = max(window), min(window)
        indicator_rows.append((ticker, d.isoformat(), ma50[i], ma200[i], rsi14[i], high_52w, low_52w,
                               int(avg_vol20[i]) if avg_vol20[i] else None))
        if i < 120:  # need some history before calling a signal
            continue

        def add(kind: str, direction: str, level: float | None, note: str, day: str = d.isoformat()) -> None:
            signal_rows.append((ticker, day, kind, direction, level, note))

        prev_high, prev_low = max(closes[max(0, i - 252): i]), min(closes[max(0, i - 252): i])
        if closes[i] > prev_high:
            add("52w_high_breakout", "bullish", prev_high, f"closed {closes[i]} above the 52-week high {prev_high}")
        if closes[i] < prev_low:
            add("52w_low_breakdown", "bearish", prev_low, f"closed {closes[i]} below the 52-week low {prev_low}")
        if ma50[i] and ma200[i] and ma50[i - 1] and ma200[i - 1]:
            if ma50[i - 1] <= ma200[i - 1] and ma50[i] > ma200[i]:
                add("golden_cross", "bullish", ma200[i], "50-day average crossed above the 200-day average")
            if ma50[i - 1] >= ma200[i - 1] and ma50[i] < ma200[i]:
                add("death_cross", "bearish", ma200[i], "50-day average crossed below the 200-day average")
        if rsi14[i] is not None and rsi14[i - 1] is not None:
            if rsi14[i - 1] >= 30 > rsi14[i]:
                add("rsi_oversold", "bullish", rsi14[i], "RSI(14) fell below 30")
            if rsi14[i - 1] <= 70 < rsi14[i]:
                add("rsi_overbought", "bearish", rsi14[i], "RSI(14) rose above 70")
        if avg_vol20[i - 1] and volumes[i] > 2.5 * avg_vol20[i - 1]:
            direction = "bullish" if closes[i] >= closes[i - 1] else "bearish"
            add("volume_spike", direction, float(volumes[i]), "volume above 2.5x its 20-day average")
    return indicator_rows, signal_rows


def fundamentals(rng: random.Random, ticker: str, last_price: float, shares_m: float) -> list[tuple]:
    """Three fiscal years of revenue, net income and EPS that fit the share price."""
    pe = rng.uniform(9, 32)
    eps_2025 = last_price / pe
    margin = rng.uniform(0.06, 0.24)
    revenue_2025 = eps_2025 * shares_m / margin
    rows, growth = [], [rng.uniform(-0.08, 0.30) for _ in range(2)]
    for year, factor in ((2023, 1 / ((1 + growth[0]) * (1 + growth[1]))), (2024, 1 / (1 + growth[1])), (2025, 1.0)):
        revenue = revenue_2025 * factor
        net_income = revenue * (margin + rng.uniform(-0.02, 0.02))
        rows.append((ticker, year, round(revenue, 1), round(net_income, 1), round(net_income / shares_m, 2)))
    return rows


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


def build(out: Path) -> Path:
    rng = random.Random(SEED)
    days = trading_days(END_DATE, TRADING_DAYS)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    con.executescript(SCHEMA)
    for t, en, ar, sector, country, exch, cur, start, drift, vol, shares in COMPANIES:
        con.execute("INSERT INTO companies VALUES (?,?,?,?,?,?,?,?)", (t, en, ar, sector, country, exch, cur, shares))
        prices = simulate(rng, start, drift, vol, len(days))
        con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?)",
                        [(t, d.isoformat(), *p) for d, p in zip(days, prices, strict=True)])
        indicator_rows, signal_rows = indicators_and_signals(t, days, prices)
        con.executemany("INSERT INTO indicators VALUES (?,?,?,?,?,?,?,?)", indicator_rows)
        con.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?)", signal_rows)
        con.executemany("INSERT INTO fundamentals VALUES (?,?,?,?,?)", fundamentals(rng, t, prices[-1][3], shares))
    con.commit()
    con.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "market.db")
    path = build(parser.parse_args().out)
    print(f"wrote {path} ({len(COMPANIES)} fictional companies, {TRADING_DAYS} trading days)")


if __name__ == "__main__":
    main()
