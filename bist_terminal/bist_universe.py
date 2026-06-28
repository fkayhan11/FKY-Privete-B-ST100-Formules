from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "bist100-config.js"
DEFAULT_HISTORY_PATH = ROOT / "data" / "bist100_constituents_history.json"


def normalize_base_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    return text[:-3] if text.endswith(".IS") else text


def normalize_yahoo_symbol(symbol: str) -> str:
    base = normalize_base_symbol(symbol)
    return f"{base}.IS" if base else ""


def parse_config_symbols(path: Path = DEFAULT_CONFIG_PATH) -> List[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"window\.BIST100_CODES\s*=\s*(\[[\s\S]*?\]);", text)
    if not match:
        raise ValueError(f"{path} does not contain window.BIST100_CODES")
    raw = json.loads(match.group(1))
    if not isinstance(raw, list):
        raise ValueError("window.BIST100_CODES must be an array")
    return [normalize_yahoo_symbol(item) for item in raw if normalize_base_symbol(item)]


def load_constituent_history(path: Path = DEFAULT_HISTORY_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"source": "missing", "snapshots": []}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain an object")
    snapshots = parsed.get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError(f"{path} must contain a snapshots array")
    return parsed


def parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def universe_as_of(
    target_date: Any,
    *,
    history_path: Path = DEFAULT_HISTORY_PATH,
    fallback_config_path: Path = DEFAULT_CONFIG_PATH,
) -> Dict[str, Any]:
    target = parse_date(target_date) or date.today()
    history = load_constituent_history(history_path)
    snapshots = []
    for row in history.get("snapshots", []):
        if not isinstance(row, dict):
            continue
        effective_from = parse_date(row.get("effectiveFrom"))
        effective_to = parse_date(row.get("effectiveTo"))
        symbols = row.get("symbols")
        if effective_from is None or not isinstance(symbols, list):
            continue
        if effective_from <= target and (effective_to is None or target <= effective_to):
            snapshots.append((effective_from, row))
    if snapshots:
        snapshots.sort(key=lambda item: item[0], reverse=True)
        row = snapshots[0][1]
        return {
            "symbols": [normalize_yahoo_symbol(symbol) for symbol in row.get("symbols", [])],
            "source": history.get("source", "bist100_constituents_history"),
            "effectiveFrom": row.get("effectiveFrom"),
            "effectiveTo": row.get("effectiveTo"),
            "isHistorical": True,
            "biasWarning": None,
        }

    return {
        "symbols": parse_config_symbols(fallback_config_path),
        "source": "current_bist100_config_fallback",
        "effectiveFrom": None,
        "effectiveTo": None,
        "isHistorical": False,
        "biasWarning": "Historical BIST100 constituents are missing for this date; current universe fallback may introduce survivorship bias.",
    }
