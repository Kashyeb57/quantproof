# Volatility data sources: licence findings

Question: where can QuantProof get historical daily levels for the S&P 500,
the Russell 2000, the Nasdaq-100 and single stocks, under terms that allow
(a) fetching at runtime and showing a derived volatility number, and
(b) keeping a public repo licence-safe? Unofficial endpoints
(yfinance, Yahoo scraping, Nasdaq.com scraping) were excluded up front.

Researched 2026-10-28 from each provider's published terms (via web
search; `fred.stlouisfed.org` itself times out from this development
host, so the FRED obligations below are quoted from its terms pages as
reported by multiple independent sources, and the fetcher in
`quantproof/market/sources.py` is tested with fakes, not live bytes).

## 1. FRED (Federal Reserve Bank of St. Louis) — recommended

- Endpoint (keyless, no account): the graph CSV,
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>&cosd=YYYY-MM-DD&coed=YYYY-MM-DD`.
  The keyed API (`https://api.stlouisfed.org`, free key, ~120 requests/minute)
  is not needed for daily closes.
- Exact terms of use:
  - Data is free to use with attribution of the source (e.g.
    "Source: S&P Dow Jones Indices LLC via FRED" for index series).
  - Applications built on the FRED API must display prominently:
    "This product uses the FRED® API but is not endorsed or certified
    by the Federal Reserve Bank of St. Louis." We display it whenever
    FRED-derived numbers are shown, even though we use the keyless CSV.
  - FRED aggregates 126 sources and flags third-party-copyrighted
    series in their notes (the word "Copyright", e.g.
    "Copyright © 2025, NASDAQ, Inc." on Nasdaq index series). For those
    series, anything beyond personal use needs the owner's permission;
    FRED cannot grant it. Our use — fetch at runtime, show only a
    derived volatility/correlation number with attribution, never
    commit raw data — stays inside the permitted personal/research use,
    and the app's source label names the owner from the series notes.
- Coverage: S&P 500 (`SP500`) and Nasdaq-100 (`NASDAQ100`) daily closes
  are long-standing FRED series. A Russell 2000 daily series id on
  FRED is **not confirmed** (candidate ids are unverified), so the
  fetcher does not guess one: it raises for tickers it cannot resolve
  until the id is confirmed against the FRED catalogue.
- No single-stock prices on FRED at all.

Verdict for our three questions: runtime fetch yes; showing only the
derived volatility with attribution yes; committing raw series to the
public repo no (and we do not: cache lives under gitignored
`.cache/`).

## 2. Stooq — not recommended

- Free bulk and per-ticker CSVs, no key historically (reports say an
  API key became required around 2026, plus captchas/anti-bot pages
  that break automation). Terms are personal/non-commercial with no
  clear grant for redistribution or for display to third parties in a
  public app. Automation is fragile (empty responses reported since
  April 2026). Verdict: runtime fetch no (unreliable + unclear
  licence), derived display no (public app is third-party display),
  committing raw data no.

## 3. Alpha Vantage — not recommended for the public app

- Free key, ~25 requests/day, deep history and intraday behind
  premium tiers. Licence is personal, non-commercial (commercial use is
  defined broadly; even paid tiers bar redistribution without written
  approval). Verdict: fine for Joyeb's private research with his own
  key, but not as the public app's fetcher.

## 4. Tiingo — not recommended for the public app

- Documented, keyed API explicitly meant for programmatic use (free
  Basic tier, generous limits). But API data is for internal
  consumption; redistribution needs special request plus fees.
  Verdict: same as Alpha Vantage — private research only.

## 5. Yahoo Finance / yfinance, Nasdaq.com scraping — excluded

- Unofficial endpoints; Yahoo's terms prohibit the scraping and
  redistribution, and CSV download moved behind a paid tier in 2026.
  Nasdaq.com scraping violates its terms. Not used anywhere.

## Recommendation (implemented)

- Indices with a confirmed FRED series id (S&P 500, Nasdaq-100):
  fetch daily closes at runtime from the keyless FRED graph CSV,
  cache under `.cache/fred/` (gitignored, never committed), compute
  realised volatility/correlation in `quantproof/market/vol.py`, and
  show the derived number with its source label
  (e.g. "S&P Dow Jones Indices LLC via FRED").
- Russell 2000 and single stocks: user-entered values remain the only
  path until a licence-safe series is confirmed; the fetcher raises a
  clear error naming this instead of guessing.
- Raw data is never committed to the public repo; the cache directory
  is gitignored and the tests use synthetic and fake data only.

Links:

- [FRED API terms of use](https://fred.stlouisfed.org/docs/api/terms_of_use.html)
- [FRED legal notices](https://fred.stlouisfed.org/legal/)
- [Daily Treasury Rate Archives](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rate-archives)
  (pattern for how U.S. government data is reused; Treasury series are
  public-domain U.S. government works, unlike index levels)
- [Alpha Vantage documentation](https://www.alphavantage.co/documentation/)
- [Tiingo API](https://api.tiingo.com/)
- [Stooq free quotes](https://stooq.com/db/h/)
