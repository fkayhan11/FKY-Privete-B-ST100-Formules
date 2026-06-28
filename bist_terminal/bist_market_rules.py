from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd


ROOT = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = ROOT / "data" / "bist_market_rules.json"


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def load_market_rules(path: Path = DEFAULT_RULES_PATH) -> Dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return parsed


def is_bist_trading_day(value: Any, rules: Optional[Dict[str, Any]] = None) -> bool:
    rules = rules or load_market_rules()
    dt = parse_date(value)
    if dt.weekday() >= 5:
        return False
    holidays = set(str(x) for x in rules.get("holidays", []))
    return dt.isoformat() not in holidays


def is_bist_half_day(value: Any, rules: Optional[Dict[str, Any]] = None) -> bool:
    rules = rules or load_market_rules()
    return parse_date(value).isoformat() in set(str(x) for x in rules.get("halfDays", []))


def next_bist_trading_day(value: Any, rules: Optional[Dict[str, Any]] = None) -> date:
    rules = rules or load_market_rules()
    dt = parse_date(value) + timedelta(days=1)
    while not is_bist_trading_day(dt, rules):
        dt += timedelta(days=1)
    return dt


def tick_size_for_price(price: float, rules: Optional[Dict[str, Any]] = None) -> float:
    rules = rules or load_market_rules()
    px = float(price)
    for band in rules.get("tickSizeBands", []):
        min_px = float(band.get("min") or 0)
        max_raw = band.get("max")
        max_px = math.inf if max_raw is None else float(max_raw)
        if min_px <= px < max_px:
            return float(band["tick"])
    return 0.01


def round_to_bist_tick(price: float, rules: Optional[Dict[str, Any]] = None) -> float:
    tick = tick_size_for_price(price, rules)
    return round(round(float(price) / tick) * tick, 4)


def daily_limit_prices(
    previous_close: float,
    rules: Optional[Dict[str, Any]] = None,
    limit_pct: Optional[float] = None,
) -> Dict[str, float]:
    rules = rules or load_market_rules()
    pct = float(limit_pct if limit_pct is not None else rules.get("defaultDailyLimitPct", 10.0))
    prev = float(previous_close)
    upper = round_to_bist_tick(prev * (1 + pct / 100.0), rules)
    lower = round_to_bist_tick(prev * (1 - pct / 100.0), rules)
    return {"lower": lower, "upper": upper, "limitPct": pct}


def liquidity_profile(df: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
    if df is None or df.empty:
        return {
            "averageVolume": None,
            "averageTurnoverTry": None,
            "lookbackBars": 0,
            "liquidityLevel": "Unknown",
        }
    tail = df.tail(max(1, int(lookback))).copy()
    volume = pd.to_numeric(tail.get("volume"), errors="coerce").fillna(0)
    close = pd.to_numeric(tail.get("close"), errors="coerce")
    turnover = volume * close
    avg_volume = float(volume.mean()) if len(volume) else 0.0
    avg_turnover = float(turnover.dropna().mean()) if len(turnover.dropna()) else 0.0
    if avg_turnover >= 100_000_000 and avg_volume >= 1_000_000:
        level = "High"
    elif avg_turnover >= 25_000_000 and avg_volume >= 250_000:
        level = "Medium"
    elif avg_turnover > 0:
        level = "Low"
    else:
        level = "Unknown"
    return {
        "averageVolume": round(avg_volume, 2),
        "averageTurnoverTry": round(avg_turnover, 2),
        "lookbackBars": len(tail),
        "liquidityLevel": level,
    }


def passes_liquidity_filter(
    df: pd.DataFrame,
    rules: Optional[Dict[str, Any]] = None,
    lookback: int = 20,
) -> Dict[str, Any]:
    rules = rules or load_market_rules()
    profile = liquidity_profile(df, lookback)
    min_turnover = float(rules.get("minimumAverageTurnoverTry", 0) or 0)
    min_volume = float(rules.get("minimumAverageVolume", 0) or 0)
    avg_turnover = profile.get("averageTurnoverTry")
    avg_volume = profile.get("averageVolume")
    passed = (
        avg_turnover is not None
        and avg_volume is not None
        and float(avg_turnover) >= min_turnover
        and float(avg_volume) >= min_volume
    )
    return {
        **profile,
        "passesLiquidityFilter": bool(passed),
        "minimumAverageTurnoverTry": min_turnover,
        "minimumAverageVolume": min_volume,
    }
