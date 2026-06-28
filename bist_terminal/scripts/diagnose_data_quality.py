#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean
from typing import Callable, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
FUND_PATH = ROOT / "data" / "fundamentals_snapshot.json"
DIV_PATH = ROOT / "data" / "dividend_snapshot.json"
BIST_PATH = ROOT / "bist100-config.js"


def load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def parse_bist100_codes() -> List[str]:
    text = BIST_PATH.read_text(encoding="utf-8")
    m = re.search(r"window\.BIST100_CODES\s*=\s*\[(.*?)\];", text, re.S)
    if not m:
        return []
    return re.findall(r'"([A-Z0-9]+)"', m.group(1))


def to_num(v) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def coverage(rows: Dict[str, dict], field: str, symbols: List[str]) -> float:
    if not symbols:
        return 0.0
    non_null = sum(1 for s in symbols if to_num((rows.get(s) or {}).get(field)) is not None)
    return (non_null / len(symbols)) * 100.0


def positive_coverage(rows: Dict[str, dict], field: str, symbols: List[str]) -> float:
    if not symbols:
        return 0.0
    cnt = 0
    for s in symbols:
        v = to_num((rows.get(s) or {}).get(field))
        if v is not None and v > 0:
            cnt += 1
    return (cnt / len(symbols)) * 100.0


def rel_err(a: float, b: float) -> float:
    if b == 0:
        return abs(a - b)
    return abs((a - b) / b)


def formula_consistency(
    rows: Dict[str, dict],
    symbols: List[str],
    field: str,
    fn: Callable[[dict], Optional[float]],
    tol_rel: float = 1e-6,
) -> Tuple[int, int, float]:
    comparable = 0
    mismatch = 0
    errors: List[float] = []
    for s in symbols:
        r = rows.get(s) or {}
        stored = to_num(r.get(field))
        calc = fn(r)
        if stored is None or calc is None:
            continue
        comparable += 1
        e = rel_err(stored, calc)
        errors.append(e)
        if e > tol_rel:
            mismatch += 1
    mean_err_pct = (mean(errors) * 100.0) if errors else 0.0
    return comparable, mismatch, mean_err_pct


def run() -> int:
    symbols = parse_bist100_codes()
    fund = load_json(FUND_PATH)
    div = load_json(DIV_PATH)

    print("=== DATA QUALITY DIAGNOSTIC ===")
    print(f"expectedSymbols={len(symbols)}")

    if not fund:
        print("fundamentalsSnapshot=missing_or_invalid")
        return 2

    fund_rows = fund.get("data") or {}
    present_symbols = sorted(fund_rows.keys())
    expected_set = set(symbols)
    present_set = set(present_symbols)
    missing_symbols = sorted(expected_set - present_set)

    print("--- fundamentals ---")
    print(f"source={fund.get('source')}")
    print(f"symbolCountInFile={len(present_symbols)}")
    print(f"expectedMissing={len(missing_symbols)}")
    if missing_symbols:
        print(f"missingSample={','.join(missing_symbols[:10])}")

    core_fields = [
        "trailingEps",
        "returnOnEquity",
        "returnOnAssets",
        "debtToEquity",
        "currentRatio",
        "revenueGrowth",
        "earningsGrowth",
        "grossMargins",
        "operatingMargins",
        "profitMargins",
        "ebitda",
        "totalDebt",
        "totalCash",
        "cfoToNetIncome",
        "netDebtToEbitda",
        "interestCoverage",
        "quickRatio",
    ]
    for f in core_fields:
        pct = coverage(fund_rows, f, symbols)
        print(f"coverage.{f}={pct:.1f}%")

    formula_checks = [
        (
            "returnOnEquity",
            lambda r: (to_num(r.get("netIncome")) / to_num(r.get("equity")))
            if to_num(r.get("netIncome")) is not None and to_num(r.get("equity")) not in (None, 0)
            else None,
        ),
        (
            "returnOnAssets",
            lambda r: (to_num(r.get("netIncome")) / to_num(r.get("assets")))
            if to_num(r.get("netIncome")) is not None and to_num(r.get("assets")) not in (None, 0)
            else None,
        ),
        (
            "profitMargins",
            lambda r: (to_num(r.get("netIncome")) / to_num(r.get("revenue")))
            if to_num(r.get("netIncome")) is not None and to_num(r.get("revenue")) not in (None, 0)
            else None,
        ),
        (
            "quickRatio",
            lambda r: (
                (to_num(r.get("currentAssets")) - (to_num(r.get("inventories")) or 0.0))
                / to_num(r.get("currentLiabilities"))
            )
            if to_num(r.get("currentAssets")) is not None and to_num(r.get("currentLiabilities")) not in (None, 0)
            else None,
        ),
        (
            "cfoToNetIncome",
            lambda r: (to_num(r.get("cfo")) / to_num(r.get("netIncome")))
            if to_num(r.get("cfo")) is not None and to_num(r.get("netIncome")) not in (None, 0)
            else None,
        ),
        (
            "netDebtToEbitda",
            lambda r: (
                (to_num(r.get("totalDebt")) - (to_num(r.get("totalCash")) or 0.0))
                / to_num(r.get("ebitda"))
            )
            if to_num(r.get("totalDebt")) is not None and to_num(r.get("ebitda")) not in (None, 0)
            else None,
        ),
    ]
    print("--- formulaConsistency ---")
    total_comp = 0
    total_mis = 0
    for field, fn in formula_checks:
        comp, mis, err_pct = formula_consistency(fund_rows, symbols, field, fn)
        total_comp += comp
        total_mis += mis
        pass_pct = ((comp - mis) / comp * 100.0) if comp else 0.0
        print(f"formula.{field}.comparable={comp} mismatch={mis} passPct={pass_pct:.1f}% meanRelErrPct={err_pct:.6f}")
    total_pass = ((total_comp - total_mis) / total_comp * 100.0) if total_comp else 0.0
    print(f"formula.totalPassPct={total_pass:.1f}%")

    if div and isinstance(div.get("data"), dict):
        div_rows = div["data"]
        print("--- dividend ---")
        print(f"source={div.get('source')}")
        print(f"symbolCountInFile={len(div_rows)}")
        print(f"coverage.lastDividendDateMs={coverage(div_rows, 'lastDividendDateMs', symbols):.1f}%")
        print(f"coverage.lastDividendPerShare={coverage(div_rows, 'lastDividendPerShare', symbols):.1f}%")
        print(f"coverage.annualDividendPerShare={coverage(div_rows, 'annualDividendPerShare', symbols):.1f}%")
        print(f"coverage.paidYears3yPos={positive_coverage(div_rows, 'paidYears3y', symbols):.1f}%")
        print(f"coverage.eventCountPos={positive_coverage(div_rows, 'eventCount', symbols):.1f}%")
    else:
        print("--- dividend ---")
        print("snapshot=missing_or_invalid")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
