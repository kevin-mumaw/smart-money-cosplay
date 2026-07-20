"""
Smart Money Cosplay -- v1.2 Unusual Options Activity Scanner

Scans a ticker universe for options contracts with statistically unusual
volume relative to open interest, using free delayed data via yfinance.

v1.2 changes:
- Cross-references each ticker's next known earnings date against contract
  expiry. A high vol/OI ratio on a contract that expires *before* the next
  earnings date is not catalyst-driven by earnings -- it's either an
  unscheduled event, dealer/hedging flow, or noise. This was added after
  the v1.1 run flagged an AMD put and a PLTR cluster that both turned out
  to expire weeks before either company's actual earnings date.

v1.1 changes:
- min_dte floor excludes 0DTE/1DTE contracts, which reset open interest
  structurally every day and produce meaningless vol/OI ratios.
- Index ETFs (SPY/QQQ/IWM/DIA) get their own, stricter threshold bucket
  since their baseline liquidity is not comparable to single names.

Known limitation: no trade-by-trade tape here, so we cannot tell whether
flagged volume was aggressive (bought at ask) or passive (sold at bid,
rolled, hedged). Every result is "worth a manual look," not a signal.
See README.md.

Usage:
    python scanner/uoa_scanner.py
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import yfinance as yf
from tabulate import tabulate

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"


def load_config():
    with open(CONFIG_DIR / "tickers.json") as f:
        tickers = json.load(f)["tickers"]
    with open(CONFIG_DIR / "thresholds.json") as f:
        raw = json.load(f)

    base = raw["base"]
    index_tickers = set(raw.get("index_tickers", []))
    index_overrides = raw.get("index_overrides", {})
    earnings_filter_mode = raw.get("earnings_filter_mode", "flag_only")
    otm_tier_cfg = raw.get("otm_tiers", {"near_money_max": 5, "moderate_max": 10, "aggressive_max": 20})

    return tickers, base, index_tickers, index_overrides, earnings_filter_mode, otm_tier_cfg


def thresholds_for(ticker: str, base: dict, index_tickers: set, index_overrides: dict) -> dict:
    """Resolve the effective threshold set for a given ticker."""
    if ticker in index_tickers:
        merged = dict(base)
        merged.update(index_overrides)
        return merged
    return base


def days_to_expiration(expiry_str: str) -> int:
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    return (expiry - date.today()).days


def classify_otm_tier(otm_pct: float, otm_tier_cfg: dict) -> str:
    """Classify how far OTM a contract is. Negative otm_pct means ITM."""
    if otm_pct < 0:
        return "itm"
    if otm_pct <= otm_tier_cfg["near_money_max"]:
        return "near_money"
    if otm_pct <= otm_tier_cfg["moderate_max"]:
        return "moderate"
    if otm_pct <= otm_tier_cfg["aggressive_max"]:
        return "aggressive"
    return "lottery"


OTM_TIER_RANK = {"itm": 0, "near_money": 0, "moderate": 1, "aggressive": 2, "lottery": 3}


def get_next_earnings_date(tk: "yf.Ticker", ticker: str, is_index: bool, cache: dict):
    """Return the next known earnings date for a ticker, or None. Cached per ticker.
    Tries get_earnings_dates() first, falls back to tk.calendar if that fails or
    returns nothing. Prints diagnostics on failure instead of swallowing errors."""
    if ticker in cache:
        return cache[ticker]

    if is_index:
        cache[ticker] = None
        return None

    today = date.today()
    next_date = None

    try:
        edf = tk.get_earnings_dates(limit=8)
        if edf is not None and not edf.empty:
            future_dates = [idx.date() for idx in edf.index if idx.date() >= today]
            if future_dates:
                next_date = min(future_dates)
            else:
                print(f"  [earnings] {ticker}: get_earnings_dates returned {len(edf)} rows, none in the future")
        else:
            print(f"  [earnings] {ticker}: get_earnings_dates returned empty")
    except Exception as e:
        print(f"  [earnings] {ticker}: get_earnings_dates failed ({type(e).__name__}: {e})")

    if next_date is None:
        try:
            cal = tk.calendar
            candidates = []
            if isinstance(cal, dict) and "Earnings Date" in cal:
                raw_dates = cal["Earnings Date"]
                if isinstance(raw_dates, (list, tuple)):
                    candidates = [d for d in raw_dates if isinstance(d, date)]
            elif hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
                val = cal.loc["Earnings Date"]
                vals = val.tolist() if hasattr(val, "tolist") else [val]
                candidates = [v for v in vals if isinstance(v, date)]

            future_candidates = [d for d in candidates if d >= today]
            if future_candidates:
                next_date = min(future_candidates)
                print(f"  [earnings] {ticker}: recovered via tk.calendar fallback -> {next_date}")
            elif candidates:
                print(f"  [earnings] {ticker}: tk.calendar had dates but none in the future: {candidates}")
            else:
                print(f"  [earnings] {ticker}: tk.calendar had no usable 'Earnings Date' (type={type(cal).__name__})")
        except Exception as e:
            print(f"  [earnings] {ticker}: tk.calendar fallback failed ({type(e).__name__}: {e})")

    if next_date is None:
        print(f"  [earnings] {ticker}: FINAL RESULT = unknown")

    cache[ticker] = next_date
    return next_date


def scan_ticker(ticker: str, thresholds: dict, is_index: bool, earnings_cache: dict, otm_tier_cfg: dict) -> pd.DataFrame:
    """Pull the options chain for one ticker and return flagged contracts."""
    rows = []
    try:
        tk = yf.Ticker(ticker)
        spot = tk.history(period="1d")["Close"].iloc[-1]
        expirations = tk.options
    except Exception as e:
        print(f"  [skip] {ticker}: could not load ({e})")
        return pd.DataFrame()

    next_earnings = get_next_earnings_date(tk, ticker, is_index, earnings_cache)

    for expiry in expirations:
        dte = days_to_expiration(expiry)
        if dte < thresholds["min_dte"] or dte > thresholds["max_dte"]:
            continue

        expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        if is_index:
            earnings_before_expiry = "N/A (index)"
        elif next_earnings is None:
            earnings_before_expiry = "unknown"
        elif next_earnings <= expiry_date:
            earnings_before_expiry = True
        else:
            earnings_before_expiry = False

        try:
            chain = tk.option_chain(expiry)
        except Exception as e:
            print(f"  [skip] {ticker} {expiry}: could not load chain ({e})")
            continue

        for opt_type, df in (("call", chain.calls), ("put", chain.puts)):
            if df.empty:
                continue
            df = df.copy()
            df["volume"] = df["volume"].fillna(0)
            df["openInterest"] = df["openInterest"].fillna(0)
            df["lastPrice"] = df["lastPrice"].fillna(0)

            for _, r in df.iterrows():
                volume = r["volume"]
                oi = r["openInterest"]
                last = r["lastPrice"]
                strike = r["strike"]

                if volume < thresholds["min_volume"]:
                    continue

                notional = volume * last * 100
                if notional < thresholds["min_notional"]:
                    continue

                is_otm = (strike > spot) if opt_type == "call" else (strike < spot)
                if thresholds["otm_only"] and not is_otm:
                    continue

                if opt_type == "call":
                    otm_pct = (strike - spot) / spot * 100
                else:
                    otm_pct = (spot - strike) / spot * 100
                otm_tier = classify_otm_tier(otm_pct, otm_tier_cfg)

                if oi <= 0:
                    vol_oi_ratio = float("inf")
                    new_contract = True
                else:
                    vol_oi_ratio = volume / oi
                    new_contract = False

                if vol_oi_ratio < thresholds["min_vol_oi_ratio"]:
                    continue

                rows.append({
                    "ticker": ticker,
                    "type": opt_type,
                    "strike": strike,
                    "expiration": expiry,
                    "dte": dte,
                    "spot": round(spot, 2),
                    "otm": is_otm,
                    "volume": int(volume),
                    "open_interest": int(oi),
                    "vol_oi_ratio": (round(vol_oi_ratio, 2) if vol_oi_ratio != float("inf") else "NEW"),
                    "new_contract": new_contract,
                    "last_price": round(last, 2),
                    "notional_est": round(notional, 0),
                    "next_earnings": next_earnings.isoformat() if next_earnings else ("N/A" if is_index else "unknown"),
                    "earnings_before_expiry": earnings_before_expiry,
                    "otm_pct": round(otm_pct, 1),
                    "otm_tier": otm_tier,
                })

    return pd.DataFrame(rows)


def main():
    tickers, base, index_tickers, index_overrides, earnings_filter_mode, otm_tier_cfg = load_config()
    print(f"Scanning {len(tickers)} tickers.")
    print(f"  Base thresholds: {base}")
    if index_tickers:
        print(f"  Index override tickers {sorted(index_tickers)}: {index_overrides}")
    print(f"  Earnings filter mode: {earnings_filter_mode}")
    print(f"  OTM tiers: {otm_tier_cfg}\n")

    earnings_cache = {}
    all_results = []
    for ticker in tickers:
        is_index = ticker in index_tickers
        t = thresholds_for(ticker, base, index_tickers, index_overrides)
        tag = "[index]" if is_index else "[single]"
        print(f"Scanning {ticker} {tag}...")
        result = scan_ticker(ticker, t, is_index, earnings_cache, otm_tier_cfg)
        if not result.empty:
            all_results.append(result)

    if not all_results:
        print("\nNo contracts matched your thresholds. Loosen them in config/thresholds.json and try again.")
        sys.exit(0)

    combined = pd.concat(all_results, ignore_index=True)

    if earnings_filter_mode == "require":
        before = len(combined)
        combined = combined[combined["earnings_before_expiry"] == True]  # noqa: E712
        print(f"earnings_filter_mode=require: dropped {before - len(combined)} contracts with no earnings before expiry.")
        if combined.empty:
            print("\nNo contracts have earnings before expiry at current thresholds. Try 'flag_only' mode instead.")
            sys.exit(0)

    # Sort: earnings-before-expiry hits first, then plausible OTM distance (not lottery tickets),
    # then new contracts, then by notional size
    combined["_earnings_sort"] = combined["earnings_before_expiry"].apply(lambda x: 1 if x is True else 0)
    combined["_otm_sort"] = combined["otm_tier"].map(OTM_TIER_RANK).fillna(4)
    combined["_sort_key"] = combined["notional_est"]
    combined = combined.sort_values(
        by=["_earnings_sort", "_otm_sort", "new_contract", "_sort_key"],
        ascending=[False, True, False, False],
    ).drop(columns=["_earnings_sort", "_otm_sort", "_sort_key"])

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"scan_{timestamp}.csv"
    combined.to_csv(out_path, index=False)

    print(f"\n{len(combined)} contracts flagged. Saved to {out_path}\n")
    print(tabulate(combined.head(25), headers="keys", tablefmt="simple", showindex=False))
    if len(combined) > 25:
        print(f"\n...and {len(combined) - 25} more in the CSV.")


if __name__ == "__main__":
    main()