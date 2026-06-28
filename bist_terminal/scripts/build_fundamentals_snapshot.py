#!/usr/bin/env python3
"""
Build fundamentals snapshot for BIST100 symbols via yfinance.

Output:
  data/fundamentals_snapshot_yfinance.json
"""

from __future__ import annotations

import json
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
BIST_CONFIG = ROOT / "bist100-config.js"
OUT_PATH = ROOT / "data" / "fundamentals_snapshot_yfinance.json"

FIELDS = [
    "marketCap",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "trailingEps",
    "dividendYield",
    "returnOnEquity",
    "returnOnAssets",
    "debtToEquity",
    "currentRatio",
    "revenueGrowth",
    "earningsGrowth",
    "grossMargins",
    "operatingMargins",
    "profitMargins",
    "freeCashflow",
    "totalDebt",
    "totalCash",
    "enterpriseValue",
    "ebitda",
    "pegRatio",
    "beta",
    "priceToSalesTrailing12Months",
]


def to_num(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def load_bist100_codes() -> List[str]:
    text = BIST_CONFIG.read_text(encoding="utf-8")
    m = re.search(r"window\.BIST100_CODES\s*=\s*\[(.*?)\];", text, re.S)
    if not m:
        raise RuntimeError("BIST100_CODES block not found in bist100-config.js")
    return re.findall(r'"([A-Z0-9]+)"', m.group(1))


def fetch_symbol_fundamentals(yf, symbol: str) -> Tuple[Dict, str]:
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}
    if not info:
        return {}, "empty_info"

    row = {field: to_num(info.get(field)) for field in FIELDS}
    filled = sum(1 for v in row.values() if v is not None)
    if filled == 0:
        return row, "no_fields"
    return row, ""


def main() -> int:
    try:
        import yfinance as yf
    except Exception as exc:
        print(f"[ERR] yfinance import failed: {exc}")
        print("Install: python3 -m pip install yfinance")
        return 1

    codes = load_bist100_codes()
    data = {}
    failures = []
    socket.setdefaulttimeout(15)

    print(f"[INFO] start symbols={len(codes)}", flush=True)

    for i, code in enumerate(codes, start=1):
        symbol = f"{code}.IS"
        try:
            row, reason = fetch_symbol_fundamentals(yf, symbol)
            data[code] = row
            if reason:
                failures.append({"symbol": symbol, "reason": reason})
                print(f"[{i}/{len(codes)}] {symbol} -> {reason}", flush=True)
            else:
                filled = sum(1 for v in row.values() if v is not None)
                print(f"[{i}/{len(codes)}] {symbol} -> ok ({filled} fields)", flush=True)
        except Exception as exc:
            data[code] = {field: None for field in FIELDS}
            failures.append({"symbol": symbol, "reason": str(exc)})
            print(f"[{i}/{len(codes)}] {symbol} -> ERROR: {exc}", flush=True)
        time.sleep(0.2)

    now = datetime.now(timezone.utc)
    payload = {
        "generatedAt": now.isoformat(),
        "generatedAtMs": int(now.timestamp() * 1000),
        "source": "yfinance",
        "symbolCount": len(codes),
        "failureCount": len(failures),
        "failures": failures,
        "data": data,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] snapshot written: {OUT_PATH}")
    print(f"[OK] symbols={len(codes)} failures={len(failures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
