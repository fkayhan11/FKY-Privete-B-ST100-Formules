#!/usr/bin/env python3
"""
Generate metric_quality_report.json by auditing the local fundamentals and dividend snapshots.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
FUND_PATH = ROOT / "data" / "fundamentals_snapshot.json"
DIV_PATH = ROOT / "data" / "dividend_snapshot.json"
CONFIG_PATH = ROOT / "bist100-config.js"
OUT_PATH = ROOT / "data" / "metric_quality_report.json"

AUDIT_FIELDS = [
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


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    print("[INFO] Auditing data quality...")
    fund_data = load_json(FUND_PATH)
    div_data = load_json(DIV_PATH)

    fund_rows = fund_data.get("data", {})
    div_rows = div_data.get("data", {})

    symbols = sorted(list(fund_rows.keys()))
    if not symbols:
        # Fallback to config symbols if snapshot is empty
        try:
            import re
            text = CONFIG_PATH.read_text(encoding="utf-8")
            m = re.search(r"window\.BIST100_CODES\s*=\s*\[(.*?)\];", text, re.S)
            if m:
                symbols = re.findall(r'"([A-Z0-9]+)"', m.group(1))
        except Exception:
            pass

    if not symbols:
        print("[ERROR] No BIST100 symbols found to audit.")
        return 1

    total_symbols = len(symbols)
    total_metrics = len(AUDIT_FIELDS)

    # 1. Audit Metrics Coverage
    worst_metrics = []
    missing_source_total = 0
    valid_count = 0
    estimated_count = 0

    for metric in AUDIT_FIELDS:
        missing_symbols = []
        filled = 0
        for sym in symbols:
            val = fund_rows.get(sym, {}).get(metric)
            if val is not None and val != "":
                filled += 1
                valid_count += 1
            else:
                missing_symbols.append(sym)
                missing_source_total += 1

        coverage_pct = (filled / total_symbols) * 100.0 if total_symbols > 0 else 0.0
        worst_metrics.append({
            "metric": metric,
            "coveragePct": round(coverage_pct, 1),
            "missingSourceCount": len(missing_symbols),
            "statusCounts": {
                "Valid": filled,
                "Missing": len(missing_symbols)
            },
            "missingSourceSymbols": missing_symbols[:15]
        })

    # Sort worst metrics by lowest coverage percentage
    worst_metrics.sort(key=lambda x: x["coveragePct"])

    # 2. Audit Symbols Quality
    worst_symbols = []
    for sym in symbols:
        missing_metrics = []
        filled = 0
        for metric in AUDIT_FIELDS:
            val = fund_rows.get(sym, {}).get(metric)
            if val is not None and val != "":
                filled += 1
            else:
                missing_metrics.append(metric)

        quality_score = int((filled / total_metrics) * 100.0) if total_metrics > 0 else 0
        worst_symbols.append({
            "symbol": f"{sym}.IS" if not sym.endswith(".IS") else sym,
            "qualityScore": quality_score,
            "missingSourceCount": len(missing_metrics),
            "estimatedCount": 0,
            "missingSourceMetrics": missing_metrics[:10]
        })

    # Sort worst symbols by lowest quality score
    worst_symbols.sort(key=lambda x: x["qualityScore"])

    now = datetime.now(timezone.utc)
    report = {
        "generatedAt": now.isoformat(),
        "generatedAtMs": int(now.timestamp() * 1000),
        "symbolCount": total_symbols,
        "metricCount": total_metrics,
        "missingSourceTotal": missing_source_total,
        "statusCounts": {
            "Valid": valid_count,
            "Provider Estimate": estimated_count,
            "Model Estimate": 0
        },
        "worstMetrics": worst_metrics[:10],
        "worstSymbols": worst_symbols[:15],
        "disclaimer": "Veri kalitesi ve eksik metrik oranları KAP ve yfinance kaynaklarının doluluk denetimidir."
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Wrote quality report to: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
