#!/usr/bin/env python3
"""
Archive fundamentals snapshots into a point-in-time folder structure.

Writes:
  data/history/{symbol}/{year}_Q{quarter}.json

Default input:
  data/fundamentals_snapshot.json

Examples:
  python3 archive_fundamentals.py
  python3 archive_fundamentals.py --input data/fundamentals_snapshot.json --year 2025 --quarter 4
  python3 archive_fundamentals.py --get THYAO.IS --date 2026-03-15
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "data" / "fundamentals_snapshot.json"
DEFAULT_HISTORY_DIR = ROOT / "data" / "history"


def normalize_symbol(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    return s[:-3] if s.endswith(".IS") else s


def to_num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def parse_date_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        if not math.isfinite(x) or x <= 0:
            return None
        return int(x * 1000) if x < 10_000_000_000 else int(x)

    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10,13}", s):
        x = float(s)
        return int(x * 1000) if x < 10_000_000_000 else int(x)

    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def quarter_from_ms(ms: int) -> int:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return ((dt.month - 1) // 3) + 1


def year_from_ms(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def load_snapshot(path: Path) -> Dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a JSON object")
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a 'data' object")
    return parsed


def archive_fundamentals_snapshot(
    input_path: Path = DEFAULT_INPUT,
    history_dir: Path = DEFAULT_HISTORY_DIR,
    year_override: Optional[int] = None,
    quarter_override: Optional[int] = None,
) -> Dict[str, int]:
    snapshot = load_snapshot(input_path)
    data = snapshot["data"]

    now = datetime.now(timezone.utc)
    archived_at_ms = int(now.timestamp() * 1000)
    snapshot_generated_ms = parse_date_ms(snapshot.get("generatedAtMs")) or parse_date_ms(snapshot.get("generatedAt"))

    written = 0
    skipped_existing = 0
    skipped_empty = 0

    for raw_symbol, row in data.items():
        symbol = normalize_symbol(raw_symbol)
        if not symbol or not isinstance(row, dict):
            skipped_empty += 1
            continue
        if not any(to_num(v) is not None for v in row.values()):
            skipped_empty += 1
            continue

        disclosure_ms = parse_date_ms(row.get("kapDisclosureDateMs"))
        available_from_ms = disclosure_ms or snapshot_generated_ms or archived_at_ms
        fiscal_year = int(year_override or to_num(row.get("kapDisclosureYear")) or year_from_ms(available_from_ms))
        fiscal_quarter = int(quarter_override or quarter_from_ms(available_from_ms))
        if fiscal_quarter < 1 or fiscal_quarter > 4:
            raise ValueError(f"invalid quarter for {symbol}: {fiscal_quarter}")

        symbol_dir = history_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        out_path = symbol_dir / f"{fiscal_year}_Q{fiscal_quarter}.json"

        if out_path.exists():
            skipped_existing += 1
            continue

        payload = {
            "symbol": symbol,
            "fiscalYear": fiscal_year,
            "fiscalQuarter": fiscal_quarter,
            "availableFromMs": available_from_ms,
            "availableFrom": datetime.fromtimestamp(available_from_ms / 1000, tz=timezone.utc).isoformat(),
            "archivedAtMs": archived_at_ms,
            "archivedAt": now.isoformat(),
            "sourceSnapshot": str(input_path),
            "snapshotGeneratedAtMs": snapshot_generated_ms,
            "kapDisclosureIndex": to_num(row.get("kapDisclosureIndex")),
            "kapDisclosureDateMs": disclosure_ms,
            "fundamentals": row,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written += 1

    return {
        "written": written,
        "skippedExisting": skipped_existing,
        "skippedEmpty": skipped_empty,
        "symbolsSeen": len(data),
    }


def get_fundamentals_at_date(
    symbol: str,
    target_date: Any,
    history_dir: Path = DEFAULT_HISTORY_DIR,
) -> Optional[Dict[str, Any]]:
    normalized = normalize_symbol(symbol)
    target_ms = parse_date_ms(target_date)
    if target_ms is None:
        raise ValueError(f"invalid target_date: {target_date!r}")

    symbol_dir = history_dir / normalized
    if not symbol_dir.exists():
        return None

    candidates = []
    for path in sorted(symbol_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        available_ms = parse_date_ms(payload.get("availableFromMs")) or parse_date_ms(payload.get("availableFrom"))
        if available_ms is None:
            # Legacy fallback: use fiscal quarter end from filename, still strictly before target_date.
            m = re.match(r"(\d{4})_Q([1-4])\.json$", path.name)
            if not m:
                continue
            year = int(m.group(1))
            quarter = int(m.group(2))
            month = quarter * 3
            available_ms = int(datetime(year, month, 28, tzinfo=timezone.utc).timestamp() * 1000)
        if available_ms < target_ms:
            candidates.append((available_ms, path, payload))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    return candidates[0][2]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive fundamentals snapshots into point-in-time history.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input fundamentals snapshot JSON")
    parser.add_argument("--history-dir", type=Path, default=DEFAULT_HISTORY_DIR, help="History root directory")
    parser.add_argument("--year", type=int, default=None, help="Optional fiscal year override")
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], default=None, help="Optional fiscal quarter override")
    parser.add_argument("--get", dest="get_symbol", default=None, help="Read point-in-time fundamentals for symbol")
    parser.add_argument("--date", default=None, help="Target date for --get, e.g. 2026-03-15")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    if args.get_symbol:
        if not args.date:
            raise SystemExit("--date is required with --get")
        result = get_fundamentals_at_date(args.get_symbol, args.date, args.history_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    stats = archive_fundamentals_snapshot(
        input_path=args.input,
        history_dir=args.history_dir,
        year_override=args.year,
        quarter_override=args.quarter,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
