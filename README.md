# Smart Money Cosplay

A DIY unusual options activity (UOA) scanner — because the guys charging you $2,000/year for this are running the same math on the same public data.

## What this actually does

Scans a ticker universe for options contracts showing statistically unusual volume relative to open interest, then flags candidates for manual review. This is the raw signal-generation layer that "insider tracking" services build their marketing around. It is not insider information — it's public volume/OI data, filtered.

## v1.3 scope (current)

- **OTM distance tiering (new):** every contract now gets `otm_pct` (exact % distance between strike and spot) and `otm_tier` (`near_money` ≤5%, `moderate` ≤10%, `aggressive` ≤20%, `lottery` >20%). Added after manually digging into the 5 earnings-before-expiry hits from v1.2: TSLA and GOOGL strikes sat inside the options market's own implied earnings move (plausible informed positioning), while an MSFT call needed a ~16% rally and a META call needed a ~26% rally in the same window -- both far beyond any realistic earnings reaction, more consistent with cheap lottery-ticket retail speculation than real size behind a thesis.
- Results are now sorted with earnings-before-expiry hits first, then by OTM tier (near_money/moderate ranked above aggressive/lottery), then notional size. High vol/OI ratio + deep OTM distance is not automatically a stronger signal -- it can just as easily mean retail gamma-chasing.
- Controlled by `otm_tiers` in `config/thresholds.json`. Nothing is filtered out by default (same `flag_only`-style philosophy as the earnings layer) -- it re-ranks, it doesn't hide.

## v1.2 scope

- **Earnings-calendar cross-reference (new):** every flagged contract is now annotated with the ticker's next known earnings date and whether that date falls before or after the contract's expiry (`next_earnings`, `earnings_before_expiry` columns). This was added because the first v1.1 run's two most "unusual" flags (an AMD put, a PLTR cluster) both turned out to expire weeks before either company's actual earnings date — meaning the high vol/OI ratio wasn't earnings positioning at all. A flag with `earnings_before_expiry: False` isn't necessarily noise (could be an unscheduled event, M&A rumor, or dealer hedging flow worth checking manually) but it should not be read as "the market is pricing in earnings."
- Controlled by `earnings_filter_mode` in `config/thresholds.json`:
  - `flag_only` (default) — annotates every row, filters nothing out. Earnings-before-expiry hits are sorted to the top of the output.
  - `require` — drops any contract whose expiry is before the next known earnings date. Use this once you've decided you only care about earnings-driven positioning.
  - Index tickers (SPY/QQQ/IWM/DIA) are marked `N/A (index)` since they have no single-company earnings date.
- Earnings dates come from `yfinance`'s `get_earnings_dates()`, which is estimate-based until a company formally confirms — treat as directionally useful, not exact, especially for dates further out.

## v1.1 scope

- Pulls live options chains via `yfinance` (free, ~15-20 min delayed)
- Flags contracts by:
  - **Volume / Open Interest ratio** — new positioning vs. rolled/existing
  - **Absolute volume threshold** — filters out illiquid noise
  - **Notional size** — volume × last price × 100, filters out small retail clutter
  - **Days to expiration (DTE) floor and cap** — excludes 0DTE/1DTE (see note below), caps at a max lookout window
  - **OTM filter (optional)** — classic "someone thinks something happens soon" pattern
- **Index ETFs (SPY, QQQ, IWM, DIA) use a separate, stricter threshold bucket** (`index_overrides` in `config/thresholds.json`) instead of the single-name thresholds. First live run flagged 295 contracts, almost entirely 1DTE SPY/QQQ/NVDA/TSLA activity that turned out to be routine daily-expiry mechanics, not genuine anomalies — see "Why the DTE floor" below.
- Outputs a ranked CSV per scan, no scoring/weighting yet (that's v2)

### Why the DTE floor exists

Products with daily expirations (SPY, QQQ, and increasingly single names like NVDA/TSLA) reset open interest structurally every day — a contract expiring tomorrow only existed for a day or two, so its OI is naturally near zero. That makes vol/OI ratio meaningless for 0DTE/1DTE contracts: a 20-50x ratio there is just how those products normally trade, not a signal. `min_dte` in `config/thresholds.json` excludes these (2 days for single names, 5 for indices).

## Known limitation (read this before you trust a signal)

Free snapshot data has no trade-by-trade tape. We can see *that* volume spiked, not whether it was bought at the ask (aggressive) or sold at the bid (could be someone unwinding, writing covered calls, or a market maker hedging). That distinction is what a real-time paid feed (Tradier/Schwab Level 1) gets you. Until this is wired to Schwab, treat every flag as "worth a manual look," not "someone knows something."

## Roadmap

- **v2:** Swap yfinance → Schwab Trader API (once app registration clears review) for real-time data + ask-side fill detection
- **v3:** Scoring model (vol/OI weight, notional weight, DTE weight, OTM%) — output a single rank instead of a raw filtered list
- **v4:** Paper trade logging (reuse `options-bot`'s trade log pattern) + Streamlit dashboard
- **v5:** Backtest scoring logic against realized outcomes before any real capital touches it

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
python scanner/uoa_scanner.py
```

Results land in `output/scan_YYYYMMDD_HHMMSS.csv`.

## Config

- `config/tickers.json` — your ticker universe (starter list included, expand freely)
- `config/thresholds.json` — tune the filter sensitivity here first before touching the scanner logic

## Disclaimer

This is a research/screening tool, not a signal service. Every flagged contract requires your own judgment before any trade. Paper trade until the logic proves itself — that was the whole point of building it instead of paying for it.