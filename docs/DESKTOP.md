# Local app

Run the assistant as a private app on your own computer, with your own data and your own AI keys.

```bash
pip install -e .
python -m market_assistant app                 # opens in its own window (Edge or Chrome app mode)
python -m market_assistant app --no-window     # just start it, then open http://127.0.0.1:8765/app
python -m market_assistant app --port 9000
```

The app has three pages:

- **Chat:** the same assistant as the API, streaming, with a price chart, follow-up questions, copy, CSV export of tables and "How I got this" showing the tools and steps behind each answer. The sidebar keeps your chats: grouped by day, searchable, and each can be renamed, pinned or deleted. They are saved in `history.db` next to the settings, on this computer only.
- **Connections:** what a connection is, where the data comes from and how fresh it is, live updates, and which AI providers to use.
- **About:** what runs where.

The theme switch at the bottom of the sidebar picks light, dark or the system setting. On a phone the sidebar slides out from the menu button.

## Data

| Source | What to give it |
|---|---|
| Demo | Nothing: 30 fictional companies with synthetic prices. |
| CSV files | `companies.csv` and `daily_prices.csv`, optionally `fundamentals.csv` (formats below). |
| SQLite | The path of a database file with the same tables. It is opened read-only. |
| PostgreSQL | A `postgresql://` URL for a **read-only** user (see [DEPLOYMENT.md](DEPLOYMENT.md) for the role). |

Switching is atomic: if the new source fails to open or has no companies, the app keeps the one that works and says why.

### Live updates

The assistant reads the database on every question and re-checks its summary (companies, latest trading day, price rows) every 5 seconds, so data written by anything else (your pipeline, a scheduled job) is answered at once. The Connections page shows the numbers and when new data was last seen.

- **Add prices:** upload a CSV with the `daily_prices.csv` columns. New days are added, existing days are corrected, and indicators and signals are recalculated for those companies in one transaction. Available for the live demo, your imported CSV data and your own SQLite file (the bundled demo and PostgreSQL are read-only here).
- **Demo feed:** switch it on to use a private copy of the demo (`live-demo.db`) that fills in trading days up to today and then moves today's prices every few seconds. Ask "Top gainers today" twice to see the answer change. It never writes a future date.
- From a terminal: `python -m market_assistant add-prices new.csv --db path/to/market.db`.

### CSV formats

UTF-8 (with or without BOM), header row required, one row per line.

`companies.csv`

| Column | Rule |
|---|---|
| `ticker` | Letters, digits, `.`, `_`, `-`; unique |
| `name_en` | Required |
| `name_ar` | Optional |
| `sector` | Required |
| `country` | 2-letter code, e.g. `US`, `SA`, `EG` |
| `exchange` | Required |
| `currency` | 3-letter code, e.g. `USD` |
| `shares_outstanding_m` | Optional, millions |

`daily_prices.csv`: `ticker, date (YYYY-MM-DD), open, high, low, close, volume`. Prices must be positive,
`low ≤ open, close ≤ high`, one row per ticker and date, and every ticker must exist in `companies.csv`.

`fundamentals.csv` (optional): `ticker, fiscal_year, revenue_m, net_income_m, eps`.

On import the app computes 50- and 200-day moving averages, RSI(14), 52-week highs and lows, 20-day average
volume and the technical signals (golden and death crosses, 52-week breakouts, RSI below 30 or above 70, volume
spikes above 2.5× the 20-day average). An invalid row stops the import with
the file and line number, and the previous data stays in use.

A period longer than your history (for example "this year" with three months of prices) gets a clear
"not enough history" answer rather than a number measured over a shorter window.

## AI providers

Add one or more OpenAI-compatible providers. Presets fill in the address for Gemini, Groq, OpenRouter and a local
Ollama model; any other compatible endpoint works too. With several providers, they are tried in order: when one is
out of quota, rate limited, unreachable or rejects the key, the next one answers and the failing one rests for a
while. **Test** sends one tiny request and reports only the kind of problem (quota, rate limit, bad key,
unreachable), never the provider's raw response.

With no provider, or when all are unavailable, the app still answers using the built-in rule planner and templates.

## Security

The app holds API keys, so it is built like one:

| Threat | Protection |
|---|---|
| Other devices on the network | Listens on `127.0.0.1` only. |
| A website you visit calling the app (CSRF) | Settings endpoints need a token created at each launch and embedded in the app page. Browsers do not let other sites read that page, so they cannot get the token. Requests with a foreign `Origin` are refused. |
| DNS rebinding | Every request must carry a `Host` of `127.0.0.1:<port>` or `localhost:<port>`; anything else gets `421`. |
| Keys leaking to the page | Keys and database passwords are never returned by any endpoint, only a masked hint (`••••7890`). Saving with an empty key keeps the stored one. |
| Keys leaking in errors | Validation errors are returned without the submitted values; provider tests return the problem kind only. |
| Embedding the app in another site | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, a strict Content-Security-Policy and `no-referrer`. |
| Plain-text keys over the network | Provider addresses must be `https`, except a model running on your own machine. |
| Writing to your data | SQLite is opened read-only, PostgreSQL should use a read-only user, and every query goes through the same validation as the API ([SECURITY.md](SECURITY.md)). |

Settings are saved in `~/.bilingual-market-assistant/settings.json` (on Windows,
`C:\Users\<you>\.bilingual-market-assistant\`), written atomically. On macOS and Linux the file is readable by
you only (`0600`); on Windows it inherits the permissions of your user profile. Imported CSV data is stored next to
it as `my-data.db`. To reset the app, close it and delete that folder.

Each test in `tests/desktop/` covers one of these rules.
