"""
Smart Money Cosplay -- v1.1 Unusual Options Activity Scanner

Scans a ticker universe for options contracts with statistically unusual
volume relative to open interest, using free delayed data via yfinance.

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

    return tickers, base, index_tickers, index_overrides


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


def scan_ticker(ticker: str, thresholds: dict) -> pd.DataFrame:
    """Pull the options chain for one ticker and return flagged contracts."""
    rows = []
    try:
        tk = yf.Ticker(ticker)
        spot = tk.history(period="1d")["Close"].iloc[-1]
        expirations = tk.options
    except Exception as e:
        print(f"  [skip] {ticker}: could not load ({e})")
        return pd.DataFrame()

    for expiry in expirations:
        dte = days_to_expiration(expiry)
        if dte < thresholds["min_dte"] or dte > thresholds["max_dte"]:
            continue

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
                })

    return pd.DataFrame(rows)


def main():
    tickers, base, index_tickers, index_overrides = load_config()
    print(f"Scanning {len(tickers)} tickers.")
    print(f"  Base thresholds: {base}")
    if index_tickers:
        print(f"  Index override tickers {sorted(index_tickers)}: {index_overrides}\n")
    else:
        print()

    all_results = []
    for ticker in tickers:
        t = thresholds_for(ticker, base, index_tickers, index_overrides)
        tag = "[index]" if ticker in index_tickers else "[single]"
        print(f"Scanning {ticker} {tag}...")
        result = scan_ticker(ticker, t)
        if not result.empty:
            all_results.append(result)

    if not all_results:
        print("\nNo contracts matched your thresholds. Loosen them in config/thresholds.json and try again.")
        sys.exit(0)

    combined = pd.concat(all_results, ignore_index=True)
    # Sort: new contracts (inf ratio) first, then by notional size
    combined["_sort_key"] = combined["notional_est"]
    combined = combined.sort_values(
        by=["new_contract", "_sort_key"], ascending=[False, False]
    ).drop(columns="_sort_key")

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