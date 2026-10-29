"""Build data/market.db: fictional companies, synthetic prices, indicators and signals.

Every company, ticker and number here is made up and generated from a fixed seed,
so the demo is reproducible and never presents fake numbers as real market data.

    python scripts/generate_data.py            # writes data/market.db
    python scripts/generate_data.py --out x.db
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from market_assistant.db.builder import SCHEMA, indicators_and_signals

SEED = 2026
END_DATE = date(2026, 10, 23)
TRADING_DAYS = 520  # about two years: room for a 200-day average, a 52-week window and 1-year changes

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

# 60 more fictional companies (20 per market). Their start price, drift, volatility and share count come from
# their own seeded generator below, so the first 30 keep their hand-set profiles. Names avoid real listings and
# common words, so a normal word in a question is never mistaken for a company.
MORE_COMPANIES = [
    ('LMNA', 'Lumina Biotech', 'لومينا للتقنية الحيوية', 'Health Care', 'US', 'NASDAQ', 'USD'),
    ('CDRX', 'Cedar Rail Logistics', 'سيدار للخدمات اللوجستية', 'Industrials', 'US', 'NYSE', 'USD'),
    ('FRWT', 'Freshwater Utilities', 'فريش ووتر للمرافق', 'Utilities', 'US', 'NYSE', 'USD'),
    ('IRNV', 'Ironvale Steel', 'آيرون فيل للصلب', 'Materials', 'US', 'NYSE', 'USD'),
    ('QRSN', 'Quarrystone Homes', 'كواري ستون للإسكان', 'Real Estate', 'US', 'NYSE', 'USD'),
    ('MPLF', 'Maplefield Foods', 'ميبل فيلد للأغذية', 'Consumer Staples', 'US', 'NYSE', 'USD'),
    ('SNWV', 'Sunwave Solar', 'صن ويف للطاقة الشمسية', 'Energy', 'US', 'NASDAQ', 'USD'),
    ('PXLS', 'Pixelstream Games', 'بيكسل ستريم للألعاب', 'Communication Services', 'US', 'NASDAQ', 'USD'),
    ('TDLN', 'Tideline Shipping', 'تايدلاين للشحن', 'Industrials', 'US', 'NYSE', 'USD'),
    ('CMPS', 'Compasspoint Payments', 'كومباس بوينت للمدفوعات', 'Financials', 'US', 'NASDAQ', 'USD'),
    ('HVNH', 'Havenbrook Hospitals', 'هيفن بروك للمستشفيات', 'Health Care', 'US', 'NYSE', 'USD'),
    ('SLTE', 'Silverleaf Telecom', 'سيلفر ليف للاتصالات', 'Communication Services', 'US', 'NYSE', 'USD'),
    ('ARXE', 'Aurex Energy', 'أوريكس للطاقة', 'Energy', 'US', 'NYSE', 'USD'),
    ('NMBL', 'Nimbleware Software', 'نيمبل وير للبرمجيات', 'Technology', 'US', 'NASDAQ', 'USD'),
    ('GRTH', 'Granite Trust Bank', 'جرانيت ترست المصرفية', 'Financials', 'US', 'NYSE', 'USD'),
    ('WLWR', 'Willowmere Retail', 'ويلومير للتجزئة', 'Consumer Staples', 'US', 'NYSE', 'USD'),
    ('ECHS', 'Echoline Audio', 'إيكولاين للصوتيات', 'Technology', 'US', 'NASDAQ', 'USD'),
    ('KSTL', 'Keystone Freight', 'كيستون للنقل', 'Industrials', 'US', 'NYSE', 'USD'),
    ('PRAR', 'Prairieland Agro', 'بريري لاند للزراعة', 'Materials', 'US', 'NYSE', 'USD'),
    ('HLXM', 'Helix Medical Devices', 'هيليكس للأجهزة الطبية', 'Health Care', 'US', 'NASDAQ', 'USD'),
    ('AMWJ.SR', 'Amwaj Shipping', 'أمواج للملاحة', 'Industrials', 'SA', 'Tadawul', 'SAR'),
    ('SFRB.SR', 'Safa Digital Bank', 'بنك صفا الرقمي', 'Financials', 'SA', 'Tadawul', 'SAR'),
    ('BSTN.SR', 'Bustan Agriculture', 'البستان للزراعة', 'Materials', 'SA', 'Tadawul', 'SAR'),
    ('QMRT.SR', 'Qamar Retail', 'قمر للتجزئة', 'Consumer Staples', 'SA', 'Tadawul', 'SAR'),
    ('ASIR.SR', 'Asir Mining', 'عسير للتعدين', 'Materials', 'SA', 'Tadawul', 'SAR'),
    ('TAIF.SR', 'Taif Foods', 'الطائف للأغذية', 'Consumer Staples', 'SA', 'Tadawul', 'SAR'),
    ('DMMN.SR', 'Dammam Logistics', 'الدمام للخدمات اللوجستية', 'Industrials', 'SA', 'Tadawul', 'SAR'),
    ('RMLC.SR', 'Rimal Cement', 'رمال للأسمنت', 'Materials', 'SA', 'Tadawul', 'SAR'),
    ('SHMS.SR', 'Shams Solar', 'شمس للطاقة المتجددة', 'Energy', 'SA', 'Tadawul', 'SAR'),
    ('SHFA.SR', 'Shifa Medical', 'الشفاء الطبية', 'Health Care', 'SA', 'Tadawul', 'SAR'),
    ('NJMT.SR', 'Najma Telecom', 'نجمة للاتصالات', 'Communication Services', 'SA', 'Tadawul', 'SAR'),
    ('ALMS.SR', 'Almas Insurance', 'ألماس للتأمين', 'Financials', 'SA', 'Tadawul', 'SAR'),
    ('SRAB.SR', 'Sarab Real Estate', 'سراب العقارية', 'Real Estate', 'SA', 'Tadawul', 'SAR'),
    ('RWAF.SR', 'Rawafid Tech', 'روافد للتقنية', 'Technology', 'SA', 'Tadawul', 'SAR'),
    ('ZHRN.SR', 'Zahran Energy', 'زهران للطاقة', 'Energy', 'SA', 'Tadawul', 'SAR'),
    ('MYAH.SR', 'Miyah Utilities', 'مياه للمرافق', 'Utilities', 'SA', 'Tadawul', 'SAR'),
    ('BNYN.SR', 'Bunyan Construction', 'بنيان للمقاولات', 'Industrials', 'SA', 'Tadawul', 'SAR'),
    ('MSHQ.SR', 'Mashriq Media', 'المشرق ميديا', 'Communication Services', 'SA', 'Tadawul', 'SAR'),
    ('JDCH.SR', 'Jeddah Chemicals', 'جدة للكيماويات', 'Materials', 'SA', 'Tadawul', 'SAR'),
    ('TBUK.SR', 'Tabuk Health', 'تبوك الصحية', 'Health Care', 'SA', 'Tadawul', 'SAR'),
    ('MNSR.CA', 'Mansoura Dairy', 'المنصورة للألبان', 'Consumer Staples', 'EG', 'EGX', 'EGP'),
    ('QNAL.CA', 'Qanal Shipping', 'القنال للملاحة', 'Industrials', 'EG', 'EGX', 'EGP'),
    ('MADI.CA', 'Maadi Software', 'المعادي للبرمجيات', 'Technology', 'EG', 'EGX', 'EGP'),
    ('NUBA.CA', 'Nubia Mining', 'النوبة للتعدين', 'Materials', 'EG', 'EGX', 'EGP'),
    ('SIWA.CA', 'Siwa Water', 'سيوة للمياه', 'Utilities', 'EG', 'EGX', 'EGP'),
    ('TNTA.CA', 'Tanta Textiles', 'طنطا للمنسوجات', 'Industrials', 'EG', 'EGX', 'EGP'),
    ('DMYT.CA', 'Damietta Furniture', 'دمياط للأثاث', 'Industrials', 'EG', 'EGX', 'EGP'),
    ('ATBA.CA', 'Ataba Steel', 'العتبة للصلب', 'Materials', 'EG', 'EGX', 'EGP'),
    ('MNFI.CA', 'Menoufia Bank', 'بنك المنوفية', 'Financials', 'EG', 'EGX', 'EGP'),
    ('SHRK.CA', 'Sharkia Agro', 'الشرقية للزراعة', 'Materials', 'EG', 'EGX', 'EGP'),
    ('KHLG.CA', 'Kharga Solar', 'الخارجة للطاقة الشمسية', 'Energy', 'EG', 'EGX', 'EGP'),
    ('BRLS.CA', 'Burullus Petroleum', 'البرلس للبترول', 'Energy', 'EG', 'EGX', 'EGP'),
    ('SHMH.CA', 'Sharm Hotels', 'شرم للفنادق', 'Real Estate', 'EG', 'EGX', 'EGP'),
    ('ALAM.CA', 'Alamein Homes', 'العلمين للإسكان', 'Real Estate', 'EG', 'EGX', 'EGP'),
    ('QSRM.CA', 'Qasr Medical', 'القصر الطبية', 'Health Care', 'EG', 'EGX', 'EGP'),
    ('MNYA.CA', 'Minya Pharma', 'المنيا للأدوية', 'Health Care', 'EG', 'EGX', 'EGP'),
    ('WDNT.CA', 'Wadi Net Telecom', 'وادي نت للاتصالات', 'Communication Services', 'EG', 'EGX', 'EGP'),
    ('ISML.CA', 'Ismailia Foods', 'الإسماعيلية للأغذية', 'Consumer Staples', 'EG', 'EGX', 'EGP'),
    ('BHRA.CA', 'Beheira Bank', 'بنك البحيرة', 'Financials', 'EG', 'EGX', 'EGP'),
    ('MNFL.CA', 'Manfalut Cement', 'منفلوط للأسمنت', 'Materials', 'EG', 'EGX', 'EGP'),
]


def profile(rng: random.Random, country: str) -> tuple[float, float, float, float]:
    """Start price, yearly drift, yearly volatility and shares outstanding (millions) for an added company."""
    start = {"US": rng.uniform(15, 160), "SA": rng.uniform(12, 150), "EG": rng.uniform(4, 60)}[country]
    return round(start, 2), round(rng.uniform(-0.15, 0.45), 2), round(rng.uniform(0.15, 0.5), 2), float(rng.randint(150, 4000))


def all_companies() -> list[tuple]:
    rng = random.Random(SEED + 1)
    return COMPANIES + [(*c, *profile(rng, c[4])) for c in MORE_COMPANIES]


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


def build(out: Path) -> Path:
    rng = random.Random(SEED)
    days = trading_days(END_DATE, TRADING_DAYS)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    con.executescript(SCHEMA)
    for t, en, ar, sector, country, exch, cur, start, drift, vol, shares in all_companies():
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
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "market.db")
    path = build(parser.parse_args().out)
    print(f"wrote {path} ({len(all_companies())} fictional companies, {TRADING_DAYS} trading days)")


if __name__ == "__main__":
    main()
