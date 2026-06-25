#!/usr/bin/env python3
"""
Nightly BIST 100 universe scanner.

The scanner evaluates the live universe once, using latest fundamentals plus
recent daily bars, and writes frontend-ready signals to data/signals.json.

Dependencies:
  python3 -m pip install yfinance pandas

Example:
  python3 nightly_scanner.py
  python3 nightly_scanner.py --positions data/scanner_positions.json --output data/signals.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

try:
    import yfinance as yf
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: yfinance. Install it with:\n"
        "  python3 -m pip install yfinance pandas"
    ) from exc


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "bist100-config.js"
FUNDAMENTALS_PATH = ROOT / "data" / "fundamentals_snapshot.json"
POSITIONS_PATH = ROOT / "data" / "scanner_positions.json"
OUTPUT_PATH = ROOT / "data" / "signals.json"
BENCHMARK_SYMBOL = "XU100.IS"

SECTOR_LABELS = {
    "BANK": "Financials",
    "CHEM": "Chemicals",
    "ENER": "Energy",
    "FOOD": "Food",
    "HLTH": "Healthcare",
    "HOLD": "Holding",
    "MANU": "Industrials",
    "MINE": "Mining",
    "REIT": "Real Estate",
    "RETL": "Retail",
    "TECH": "Tech",
    "TRAN": "Transportation",
}

COMMON_SECTOR_OVERRIDES = {
    "AKBNK": "Financials",
    "GARAN": "Financials",
    "HALKB": "Financials",
    "ISCTR": "Financials",
    "SKBNK": "Financials",
    "TSKB": "Financials",
    "VAKBN": "Financials",
    "YKBNK": "Financials",
    "THYAO": "Transportation",
    "PGSUS": "Transportation",
    "TAVHL": "Transportation",
    "FROTO": "Industrials",
    "TOASO": "Industrials",
    "TUPRS": "Energy",
    "TCELL": "Telecom",
    "TTKOM": "Telecom",
}


def normalize_base_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    return text[:-3] if text.endswith(".IS") else text


def normalize_yahoo_symbol(symbol: str) -> str:
    base = normalize_base_symbol(symbol)
    return f"{base}.IS" if base else ""


def to_num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        return x if math.isfinite(x) else None
    return None


def js_object_to_json(raw_object: str) -> str:
    text = re.sub(r"//.*", "", raw_object)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', text)
    return text


def load_bist100_universe(path: Path = CONFIG_PATH) -> Tuple[List[str], Dict[str, str]]:
    text = path.read_text(encoding="utf-8")

    codes_match = re.search(r"window\.BIST100_CODES\s*=\s*(\[[\s\S]*?\]);", text)
    if not codes_match:
        raise ValueError(f"{path} does not contain window.BIST100_CODES")

    codes = [
        normalize_yahoo_symbol(symbol)
        for symbol in json.loads(codes_match.group(1))
        if normalize_base_symbol(symbol)
    ]

    raw_sectors: Dict[str, str] = {}
    sector_match = re.search(r"window\.BIST100_FALLBACK_SECTORS\s*=\s*(\{[\s\S]*?\});", text)
    if sector_match:
        parsed = json.loads(js_object_to_json(sector_match.group(1)))
        if isinstance(parsed, dict):
            raw_sectors = parsed

    sector_map: Dict[str, str] = {}
    for symbol in codes:
        base = normalize_base_symbol(symbol)
        raw_sector = raw_sectors.get(base, COMMON_SECTOR_OVERRIDES.get(base, "Unknown"))
        sector = str(raw_sector or "Unknown").strip() or "Unknown"
        sector_map[symbol] = SECTOR_LABELS.get(sector.upper(), sector)

    return codes, sector_map


def load_latest_fundamentals(path: Path = FUNDAMENTALS_PATH) -> Dict[str, Dict[str, Any]]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object with a 'data' object")

    fundamentals: Dict[str, Dict[str, Any]] = {}
    for raw_symbol, row in data.items():
        if isinstance(row, dict):
            fundamentals[normalize_yahoo_symbol(raw_symbol)] = row
    return fundamentals


def fundamental_raw_score(row: Dict[str, Any]) -> Optional[float]:
    if not isinstance(row, dict):
        return None

    score_parts: List[float] = []
    roe = to_num(row.get("returnOnEquity"))
    profit_margin = to_num(row.get("profitMargins"))
    operating_margin = to_num(row.get("operatingMargins"))
    earnings_growth = to_num(row.get("earningsGrowth"))
    peg_ratio = to_num(row.get("pegRatio"))
    debt_to_equity = to_num(row.get("debtToEquity"))

    if roe is not None:
        score_parts.append(1.0 if roe > 0.15 else 0.5 if roe > 0.05 else -0.5 if roe > 0 else -1.0)
    if profit_margin is not None:
        score_parts.append(1.0 if profit_margin > 0.12 else 0.5 if profit_margin > 0.03 else -1.0)
    if operating_margin is not None:
        score_parts.append(1.0 if operating_margin > 0.15 else 0.5 if operating_margin > 0.03 else -1.0)
    if earnings_growth is not None:
        score_parts.append(1.0 if earnings_growth > 0.15 else 0.5 if earnings_growth > 0 else -1.0)
    if peg_ratio is not None and peg_ratio > 0:
        score_parts.append(1.0 if peg_ratio <= 1.0 else 0.5 if peg_ratio <= 2.0 else -0.5)
    if debt_to_equity is not None:
        score_parts.append(0.5 if debt_to_equity <= 1.5 else -0.5)

    if not score_parts:
        return None
    return sum(score_parts) / len(score_parts)


def calculate_sector_zscores(
    symbols: Iterable[str],
    fundamentals: Dict[str, Dict[str, Any]],
    sector_map: Dict[str, str],
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    raw_scores: Dict[str, float] = {}
    grouped: Dict[str, List[float]] = {}

    for symbol in symbols:
        score = fundamental_raw_score(fundamentals.get(symbol, {}))
        if score is None or not math.isfinite(score):
            continue
        raw_scores[symbol] = score
        grouped.setdefault(sector_map.get(symbol, "Unknown"), []).append(score)

    sector_stats: Dict[str, Tuple[float, float]] = {}
    for sector, values in grouped.items():
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        sector_stats[sector] = (mean, math.sqrt(variance))

    zscores: Dict[str, float] = {}
    for symbol, score in raw_scores.items():
        mean, std_dev = sector_stats.get(sector_map.get(symbol, "Unknown"), (score, 0.0))
        zscores[symbol] = (score - mean) / std_dev if std_dev > 0 else 0.0

    leader_thresholds: Dict[str, float] = {}
    by_sector_zscores: Dict[str, List[float]] = {}
    for symbol, zscore in zscores.items():
        by_sector_zscores.setdefault(sector_map.get(symbol, "Unknown"), []).append(zscore)

    for sector, values in by_sector_zscores.items():
        ordered = sorted(values)
        idx = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.80) - 1))
        leader_thresholds[sector] = ordered[idx]

    return raw_scores, zscores, leader_thresholds


def download_price_history(symbols: List[str], period: str = "18mo") -> Dict[str, pd.DataFrame]:
    data = yf.download(
        tickers=symbols,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    if data is None or data.empty:
        return {}

    result: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        if isinstance(data.columns, pd.MultiIndex):
            if symbol not in data.columns.get_level_values(0):
                continue
            df = data[symbol].copy()
        else:
            df = data.copy()

        rename = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
        df = df.rename(columns=rename)
        required = ["open", "high", "low", "close", "volume"]
        if any(col not in df.columns for col in required):
            continue
        df = df[required].apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df[df["close"] > 0]
        if len(df) >= 220:
            result[symbol] = df
    return result


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    values = 100 - (100 / (1 + rs))
    values = values.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    values = values.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return values


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def latest_indicators(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    enriched = df.copy()
    enriched["sma20"] = enriched["close"].rolling(20, min_periods=20).mean()
    enriched["sma200"] = enriched["close"].rolling(200, min_periods=200).mean()
    enriched["rsi14"] = rsi(enriched["close"], 14)
    enriched["atr14"] = atr(enriched, 14)
    latest = enriched.iloc[-1]

    keys = ["close", "sma20", "sma200", "rsi14", "atr14"]
    values = {key: to_num(latest.get(key)) for key in keys}
    if any(values[key] is None for key in keys):
        return None
    return {key: float(values[key]) for key in keys}


def load_position_state(path: Path = POSITIONS_PATH) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain an object keyed by symbol")
    return {
        normalize_yahoo_symbol(symbol): row
        for symbol, row in parsed.items()
        if isinstance(row, dict) and normalize_base_symbol(symbol)
    }


def scan_universe(
    symbols: List[str],
    sector_map: Dict[str, str],
    fundamentals: Dict[str, Dict[str, Any]],
    price_history: Dict[str, pd.DataFrame],
    position_state: Dict[str, Dict[str, Any]],
    *,
    rsi_pullback_threshold: float = 40.0,
    atr_multiplier: float = 2.5,
) -> Dict[str, Any]:
    raw_scores, zscores, leader_thresholds = calculate_sector_zscores(symbols, fundamentals, sector_map)

    benchmark_indicators = latest_indicators(price_history[BENCHMARK_SYMBOL]) if BENCHMARK_SYMBOL in price_history else None
    is_bull_market = bool(
        benchmark_indicators
        and benchmark_indicators["close"] > benchmark_indicators["sma200"]
    )

    buy_candidates: List[Dict[str, Any]] = []
    sell_exit_candidates: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []

    for symbol in symbols:
        df = price_history.get(symbol)
        indicators = latest_indicators(df) if df is not None else None
        sector = sector_map.get(symbol, "Unknown")
        zscore = zscores.get(symbol)
        leader_threshold = leader_thresholds.get(sector)

        if indicators is None:
            rows.append({"symbol": symbol, "sector": sector, "status": "missing_price_history"})
            continue

        close = indicators["close"]
        sma20 = indicators["sma20"]
        sma200 = indicators["sma200"]
        rsi14 = indicators["rsi14"]
        atr14 = indicators["atr14"]

        is_stock_uptrend = close > sma200
        is_pullback = close < sma20 or rsi14 < rsi_pullback_threshold
        is_sector_leader = zscore is not None and leader_threshold is not None and zscore > leader_threshold

        row = {
            "symbol": symbol,
            "sector": sector,
            "close": close,
            "sma20": sma20,
            "sma200": sma200,
            "rsi14": rsi14,
            "atr14": atr14,
            "rawScore": raw_scores.get(symbol),
            "zScore": zscore,
            "sectorLeaderThreshold": leader_threshold,
            "isBullMarket": is_bull_market,
            "isStockUptrend": is_stock_uptrend,
            "isPullback": is_pullback,
            "isSectorLeader": is_sector_leader,
        }

        position = position_state.get(symbol)
        if position:
            previous_high = to_num(position.get("highestPrice")) or to_num(position.get("entryPrice")) or close
            highest_price = max(previous_high, close)
            trailing_stop = highest_price - (atr14 * atr_multiplier)
            row.update(
                {
                    "highestPrice": highest_price,
                    "trailingStopPrice": trailing_stop,
                    "isTrailingStopViolated": close <= trailing_stop,
                }
            )
            if close <= trailing_stop:
                sell_exit_candidates.append(row.copy())

        if is_bull_market and is_stock_uptrend and is_sector_leader and is_pullback:
            buy_candidates.append(row.copy())

        rows.append(row)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "benchmarkSymbol": BENCHMARK_SYMBOL,
        "benchmark": benchmark_indicators,
        "isBullMarket": is_bull_market,
        "symbolCount": len(symbols),
        "buyCandidates": buy_candidates,
        "sellExitCandidates": sell_exit_candidates,
        "rows": rows,
    }


def format_frontend_signals(scan: Dict[str, Any]) -> Dict[str, Any]:
    buy_candidates = []
    for row in scan.get("buyCandidates", []):
        score = to_num(row.get("zScore"))
        buy_candidates.append(
            {
                "symbol": row.get("symbol"),
                "score": round(score, 4) if score is not None else None,
                "reason": "Sector Leader, Pullback Buy",
            }
        )

    sell_candidates = []
    for row in scan.get("sellExitCandidates", []):
        sell_candidates.append(
            {
                "symbol": row.get("symbol"),
                "reason": "Trailing Stop Hit",
            }
        )

    return {
        "generatedAt": scan.get("generatedAt") or datetime.now(timezone.utc).isoformat(),
        "buy_candidates": buy_candidates,
        "sell_candidates": sell_candidates,
        "market_regime": "Bullish" if scan.get("isBullMarket") else "Bearish",
    }


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the nightly BIST 100 universe scanner.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to bist100-config.js")
    parser.add_argument("--fundamentals", type=Path, default=FUNDAMENTALS_PATH, help="Latest fundamentals snapshot")
    parser.add_argument("--positions", type=Path, default=POSITIONS_PATH, help="Optional scanner position state JSON")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Frontend signals JSON output path")
    parser.add_argument("--period", default="18mo", help="Price history period for yfinance")
    parser.add_argument("--rsi-pullback-threshold", type=float, default=40.0)
    parser.add_argument("--atr-multiplier", type=float, default=2.5)
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    symbols, sector_map = load_bist100_universe(args.config)
    fundamentals = load_latest_fundamentals(args.fundamentals)
    position_state = load_position_state(args.positions)

    tickers = [BENCHMARK_SYMBOL, *symbols]
    price_history = download_price_history(tickers, period=args.period)

    scan = scan_universe(
        symbols=symbols,
        sector_map=sector_map,
        fundamentals=fundamentals,
        price_history=price_history,
        position_state=position_state,
        rsi_pullback_threshold=args.rsi_pullback_threshold,
        atr_multiplier=args.atr_multiplier,
    )

    signals = format_frontend_signals(scan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "generatedAt": signals["generatedAt"],
                "market_regime": signals["market_regime"],
                "symbolCount": scan["symbolCount"],
                "buy_candidates": [row["symbol"] for row in signals["buy_candidates"]],
                "sell_candidates": [row["symbol"] for row in signals["sell_candidates"]],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
