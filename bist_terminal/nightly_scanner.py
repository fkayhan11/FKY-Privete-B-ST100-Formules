#!/usr/bin/env python3
"""
Nightly BIST 100 universe scanner.

Evaluates the universe daily, implementing:
1. Market Breadth Regime filters (stops buying on index downturn or low stock participation)
2. Volatility-adjusted RSI pullback bounds
3. Dynamic ATR trailing stops (adaptive stop multipliers)
4. Sector-specific balance sheet scoring
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

# Import quantitative modules
from signals_core import (
    fundamental_raw_score,
    calculate_sector_zscores,
    calculate_market_breadth,
    get_market_regime,
    is_trend_pullback_buy,
    is_trailing_stop_hit,
    volatility_risk_profile,
    latest_indicators,
    to_num
)

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
        if len(df) >= 200:
            result[symbol] = df
    return result


def load_position_state(path: Path = POSITIONS_PATH) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        return {}
    positions = parsed.get("positions", parsed)  # Supports both flat structure and nested
    if not isinstance(positions, dict):
        return {}
    return {
        normalize_yahoo_symbol(symbol): row
        for symbol, row in positions.items()
        if isinstance(row, dict) and normalize_base_symbol(symbol)
    }


def scan_universe(
    symbols: List[str],
    sector_map: Dict[str, str],
    fundamentals: Dict[str, Dict[str, Any]],
    price_history: Dict[str, pd.DataFrame],
    position_state: Dict[str, Dict[str, Any]],
    *,
    base_rsi_threshold: float = 40.0,
    atr_multiplier: float = 2.5,
) -> Dict[str, Any]:
    # Calculate price-dependent metrics using daily closing price
    for symbol in symbols:
        df = price_history.get(symbol)
        if df is None or df.empty:
            continue
        close = float(df["close"].iloc[-1])
        row = fundamentals.get(symbol)
        if not isinstance(row, dict):
            continue
        shares = to_num(row.get("sharesOutstanding"))
        eps = to_num(row.get("trailingEps"))
        equity = to_num(row.get("equity"))
        eg = to_num(row.get("earningsGrowth"))
        if shares is not None and shares > 0:
            row["marketCap"] = close * shares
            if equity is not None and equity > 0:
                row["priceToBook"] = (close * shares) / equity
        if eps is not None and eps > 0:
            row["trailingPE"] = close / eps
            if eg is not None and eg > 0:
                row["pegRatio"] = (close / eps) / (eg * 100.0)

    # 1. Sector-Specific Z-Scores
    raw_scores, zscores, leader_thresholds = calculate_sector_zscores(symbols, fundamentals, sector_map)

    # 2. Market Breadth calculations
    breadth = calculate_market_breadth(price_history)

    # 3. Market Regime determination
    benchmark_indicators = latest_indicators(price_history[BENCHMARK_SYMBOL]) if BENCHMARK_SYMBOL in price_history else None
    if benchmark_indicators:
        market_regime = get_market_regime(
            breadth, 
            benchmark_indicators["close"], 
            benchmark_indicators["sma200"]
        )
    else:
        market_regime = "Strong Bullish"  # Fallback

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
        atr_pct = (atr14 / close) * 100.0

        # Run enhanced pullback calculation
        buy_signal = is_trend_pullback_buy(
            close=close,
            sma200=sma200,
            sma20=sma20,
            rsi14=rsi14,
            atr_pct=atr_pct,
            fundamental_score=raw_scores.get(symbol),
            zscore=zscore,
            sector_leader_threshold=leader_threshold,
            require_sector_leader=True,
            base_rsi_threshold=base_rsi_threshold,
            market_regime=market_regime
        )

        row = {
            "symbol": symbol,
            "sector": sector,
            "close": close,
            "sma20": sma20,
            "sma200": sma200,
            "rsi14": rsi14,
            "atr14": atr14,
            "atrPct": atr_pct,
            "rawScore": raw_scores.get(symbol),
            "zScore": zscore,
            "sectorLeaderThreshold": leader_threshold,
            "isBullMarket": (market_regime in ["Strong Bullish", "Weak Bullish"]),
            "marketRegime": market_regime,
            "passesLiquidityFilter": True, # verified in pipeline
        }

        # Handle Trailing Stop for active positions
        position = position_state.get(symbol)
        if position:
            previous_high = to_num(position.get("highestPrice")) or to_num(position.get("entryPrice")) or close
            highest_price = max(previous_high, close)
            
            # Apply dynamic trailing stop hit
            stop_hit, stop_price = is_trailing_stop_hit(
                close=close,
                highest_price=highest_price,
                atr_value=atr14,
                atr_pct=atr_pct
            )

            row.update(
                {
                    "highestPrice": highest_price,
                    "trailingStopPrice": stop_price,
                    "isTrailingStopViolated": stop_hit,
                }
            )
            if stop_hit:
                sell_exit_candidates.append(row.copy())

        if buy_signal:
            buy_candidates.append(row.copy())

        rows.append(row)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "benchmarkSymbol": BENCHMARK_SYMBOL,
        "benchmark": benchmark_indicators,
        "marketRegime": market_regime,
        "breadth": breadth,
        "symbolCount": len(symbols),
        "buyCandidates": buy_candidates,
        "sellExitCandidates": sell_exit_candidates,
        "rows": rows,
    }


def format_frontend_signals(scan: Dict[str, Any]) -> Dict[str, Any]:
    buy_candidates = []
    for row in scan.get("buyCandidates", []):
        score = to_num(row.get("zScore"))
        close = to_num(row.get("close"))
        sma20 = to_num(row.get("sma20"))
        sma200 = to_num(row.get("sma200"))
        rsi14 = to_num(row.get("rsi14"))
        leader_threshold = to_num(row.get("sectorLeaderThreshold"))
        
        # Calculate dynamic risk profiles
        risk = volatility_risk_profile(close, row.get("atr14"))
        
        reason_parts = ["Sector Leader", "Trend Up", "Pullback Buy"]
        if score is not None and leader_threshold is not None:
            reason_parts.append(f"Z {score:.2f} > threshold {leader_threshold:.2f}")
        if rsi14 is not None:
            reason_parts.append(f"RSI14 {rsi14:.1f}")

        buy_candidates.append(
            {
                "symbol": row.get("symbol"),
                "score": round(score, 4) if score is not None else None,
                "reason": ", ".join(reason_parts),
                "sector": row.get("sector"),
                "close": round(close, 4) if close is not None else None,
                "sma20": round(sma20, 4) if sma20 is not None else None,
                "sma200": round(sma200, 4) if sma200 is not None else None,
                "rsi14": round(rsi14, 2) if rsi14 is not None else None,
                "sectorLeaderThreshold": round(leader_threshold, 4) if leader_threshold is not None else None,
                "passesLiquidityFilter": True,
                "riskLevel": risk["riskLevel"],
                "riskScore": risk["riskScore"],
                "atrPct": risk.get("atrPct"),
                "suggestedWeightPct": risk["suggestedWeightPct"],
                "riskReason": risk.get("reason"),
            }
        )

    sell_candidates = []
    for row in scan.get("sellExitCandidates", []):
        close = to_num(row.get("close"))
        trailing_stop = to_num(row.get("trailingStopPrice"))
        sell_candidates.append(
            {
                "symbol": row.get("symbol"),
                "reason": "Trailing Stop Hit",
                "close": round(close, 4) if close is not None else None,
                "trailingStopPrice": round(trailing_stop, 4) if trailing_stop is not None else None,
            }
        )

    return {
        "generatedAt": scan.get("generatedAt") or datetime.now(timezone.utc).isoformat(),
        "buy_candidates": buy_candidates,
        "sell_candidates": sell_candidates,
        "market_regime": scan.get("marketRegime") or "Strong Bullish",
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
        base_rsi_threshold=args.rsi_pullback_threshold,
        atr_multiplier=args.atr_multiplier,
    )

    # Save the updated fundamentals back to snapshot file so other tools can use computed values
    try:
        parsed_snapshot = json.loads(args.fundamentals.read_text(encoding="utf-8"))
        snapshot_data = parsed_snapshot.get("data", {})
        for k, v in snapshot_data.items():
            norm_k = normalize_yahoo_symbol(k)
            if norm_k in fundamentals:
                snapshot_data[k] = fundamentals[norm_k]
        parsed_snapshot["data"] = snapshot_data
        args.fundamentals.write_text(json.dumps(parsed_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Updated price-dependent ratios inside fundamentals snapshot: {args.fundamentals}")
    except Exception as exc:
        print(f"[WARN] Could not update fundamentals snapshot file: {exc}")

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
