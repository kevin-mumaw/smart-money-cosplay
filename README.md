# Smart Money Cosplay

A DIY unusual options activity (UOA) scanner — because the guys charging you $2,000/year for this are running the same math on the same public data.

## What this actually does

Scans a ticker universe for options contracts showing statistically unusual volume relative to open interest, then flags candidates for manual review. This is the raw signal-generation layer that "insider tracking" services build their marketing around. It is not insider information — it's public volume/OI data, filtered.

## v1 scope (current)

- Pulls live options chains via `yfinance` (free, ~15-20 min delayed)
- Flags contracts by:
  - **Volume / Open Interest ratio** — new positioning vs. rolled/existing
  - **Absolute volume threshold** — filters out illiquid noise
  - **Notional size** — volume × last price × 100, filters out small retail clutter
  - **Days to expiration (DTE) cap** — short-dated bets suggest a near-term catalyst thesis
  - **OTM filter (optional)** — classic "someone thinks something happens soon" pattern
- Outputs a ranked CSV per scan, no scoring/weighting yet (that's v2)

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
