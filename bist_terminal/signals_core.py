#!/usr/bin/env python3
"""
Shared Fiscograph signal engine.

Includes:
1. Sector-Specific Fundamental Scoring (Dynamic weighting for Banks, Industrials, etc.)
2. Market Breadth Regime Detection (Percentage of stocks above SMAs)
3. Dynamic Volatility-Adjusted RSI Pullback Thresholds
4. Volatility-Adaptive Trailing Stop Multipliers (Dynamic Chandelier Exit)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


def to_num(value: Any) -> Optional[float]:
    if value in (None, "", "null", "NaN", "nan"):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        return x if math.isfinite(x) else None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def fundamental_raw_score(row: Dict[str, Any], sector: str = "Unknown") -> Optional[float]:
    """
    Computes a fundamental health score [-1.0 to 1.0] dynamically tailored to the company's sector.
    """
    if not isinstance(row, dict) or not row:
        return None

    sector = str(sector or "Unknown").strip().upper()
    score_parts: List[float] = []

    # Common metrics
    roe = to_num(row.get("returnOnEquity"))
    profit_margin = to_num(row.get("profitMargins"))
    operating_margin = to_num(row.get("operatingMargins"))
    earnings_growth = to_num(row.get("earningsGrowth"))
    peg_ratio = to_num(row.get("pegRatio"))
    debt_to_equity = to_num(row.get("debtToEquity"))
    current_ratio = to_num(row.get("currentRatio"))

    # Normalize debtToEquity if in percentage format (e.g. 88.84% instead of 0.8884)
    if debt_to_equity is not None and debt_to_equity > 5.0:
        debt_to_equity /= 100.0

    # 1. BANKING / FINANCE SECTOR RULES
    if "BANK" in sector or "FINAN" in sector or "SIGORTA" in sector or "HOLDING" in sector:
        # Debt-to-Equity and Current Ratio are ignored (irrelevant or skewed for banks)
        if roe is not None:
            score_parts.append(1.0 if roe > 0.18 else 0.5 if roe > 0.08 else -0.5 if roe > 0 else -1.0)
        if profit_margin is not None:
            score_parts.append(1.0 if profit_margin > 0.15 else 0.5 if profit_margin > 0.05 else -1.0)
        if earnings_growth is not None:
            score_parts.append(1.0 if earnings_growth > 0.10 else 0.5 if earnings_growth > 0 else -0.5)

    # 2. TECHNOLOGY / HIGH GROWTH SECTOR RULES
    elif "TEKNO" in sector or "YAZILIM" in sector or "TELEKOM" in sector:
        # High emphasis on growth, margins, and PEG. Lower penalty on moderate leverage.
        if roe is not None:
            score_parts.append(1.0 if roe > 0.20 else 0.5 if roe > 0.10 else -0.5)
        if operating_margin is not None:
            score_parts.append(1.0 if operating_margin > 0.18 else 0.5 if operating_margin > 0.08 else -1.0)
        if earnings_growth is not None:
            score_parts.append(1.0 if earnings_growth > 0.20 else 0.5 if earnings_growth > 0.05 else -1.0)
        if peg_ratio is not None and peg_ratio > 0:
            score_parts.append(1.0 if peg_ratio <= 1.2 else 0.5 if peg_ratio <= 2.2 else -0.5)
        if debt_to_equity is not None:
            score_parts.append(0.5 if debt_to_equity <= 1.8 else -0.5)

    # 3. INDUSTRIAL / ENERGY / RAW MATERIALS RULES
    elif any(x in sector for x in ["SANAYI", "ENERJI", "METAL", "CIMENTO", "KIMYA", "MADEN"]):
        # Focus on operating margins, current ratio (liquidity), and moderate leverage limits.
        if roe is not None:
            score_parts.append(1.0 if roe > 0.15 else 0.5 if roe > 0.05 else -0.5)
        if operating_margin is not None:
            score_parts.append(1.0 if operating_margin > 0.12 else 0.5 if operating_margin > 0.04 else -1.0)
        if debt_to_equity is not None:
            score_parts.append(1.0 if debt_to_equity <= 1.0 else 0.5 if debt_to_equity <= 1.5 else -1.0)
        if current_ratio is not None:
            score_parts.append(1.0 if current_ratio >= 1.5 else 0.5 if current_ratio >= 1.0 else -1.0)
        if earnings_growth is not None:
            score_parts.append(1.0 if earnings_growth > 0.10 else 0.5 if earnings_growth > 0 else -0.5)

    # 4. STANDARD/DEFAULT RULES (Retail, Tourism, Real Estate, Construction)
    else:
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
        sector = sector_map.get(symbol, "Unknown")
        score = fundamental_raw_score(fundamentals.get(symbol, {}), sector)
        if score is None or not math.isfinite(score):
            continue
        raw_scores[symbol] = score
        grouped.setdefault(sector, []).append(score)

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


def calculate_market_breadth(universe_dfs: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    """
    Computes Market Breadth indicators for BIST100 constituents:
    - pctAbove50Sma: % of stocks trading above their 50-day SMA.
    - pctAbove200Sma: % of stocks trading above their 200-day SMA.
    """
    above_50 = 0
    above_200 = 0
    total = 0

    for df in universe_dfs.values():
        if df is None or len(df) < 200:
            continue
        closes = pd.to_numeric(df["close"], errors="coerce")
        last_close = closes.iloc[-1]
        
        sma50 = closes.rolling(50).mean().iloc[-1]
        sma200 = closes.rolling(200).mean().iloc[-1]

        if not math.isnan(last_close):
            total += 1
            if not math.isnan(sma50) and last_close > sma50:
                above_50 += 1
            if not math.isnan(sma200) and last_close > sma200:
                above_200 += 1

    if total == 0:
        return {"pctAbove50Sma": 0.0, "pctAbove200Sma": 0.0}
    
    return {
        "pctAbove50Sma": (above_50 / total) * 100.0,
        "pctAbove200Sma": (above_200 / total) * 100.0
    }


def get_market_regime(breadth: Dict[str, float], index_close: float, index_sma200: float) -> str:
    """
    Determines market regime using index price level AND market breadth.
    - Strong Bullish: Index > SMA200 and Breadth(Above 50) > 60%
    - Weak Bullish: Index > SMA200 but Breadth(Above 50) <= 60% (Warning regime)
    - Bearish: Index <= SMA200 or Breadth(Above 50) < 35%
    """
    idx_close = to_num(index_close)
    idx_sma = to_num(index_sma200)
    breadth50 = breadth.get("pctAbove50Sma", 0.0)

    if idx_close is None or idx_sma is None:
        return "Unknown"

    if idx_close > idx_sma:
        if breadth50 >= 60.0:
            return "Strong Bullish"
        elif breadth50 >= 40.0:
            return "Weak Bullish"
        else:
            return "Distribution/Bearish Warning"
    else:
        return "Bearish"


def dynamic_rsi_pullback_threshold(atr_pct: float, base_threshold: float = 40.0) -> float:
    """
    Adjusts the RSI buy threshold based on stock volatility (ATR%):
    - High volatility (ATR% > 4.0): Set a lower RSI limit (e.g. 33-35) to buy only on deep pullbacks.
    - Low volatility (ATR% < 2.0): Set a higher RSI limit (e.g. 42-45) to capture milder pullbacks.
    """
    vol = to_num(atr_pct)
    if vol is None:
        return base_threshold
    
    # Scale: for every 1% deviation from 3.0% ATR, shift RSI threshold by 2 points.
    deviation = vol - 3.0
    dynamic_threshold = base_threshold - (deviation * 2.0)
    
    # Cap threshold between 30 (deep panic) and 46 (mild pullback)
    return max(30.0, min(46.0, dynamic_threshold))


def is_trend_pullback_buy(
    *,
    close: Any,
    sma200: Any,
    sma20: Any,
    rsi14: Any,
    atr_pct: Any = None,
    fundamental_score: Any = None,
    zscore: Any = None,
    sector_leader_threshold: Any = None,
    require_sector_leader: bool = False,
    base_rsi_threshold: float = 40.0,
    market_regime: str = "Strong Bullish",
) -> bool:
    """
    Enhanced Pullback Signal:
    - Stops buying entirely if market regime is Bearish.
    - Employs dynamic volatility-adjusted RSI pullback bounds.
    """
    close_num = to_num(close)
    sma200_num = to_num(sma200)
    sma20_num = to_num(sma20)
    rsi_num = to_num(rsi14)
    score_num = to_num(fundamental_score)
    atr_val = to_num(atr_pct) or 3.0

    # 1. Market Regime Constraint
    if market_regime in ("Bearish", "Distribution/Bearish Warning"):
        return False

    # 2. Bullish Trend Validation
    if close_num is None or sma200_num is None or close_num <= sma200_num:
        return False
    if score_num is not None and score_num <= 0:
        return False

    # 3. Dynamic RSI Calculation
    rsi_threshold = dynamic_rsi_pullback_threshold(atr_val, base_rsi_threshold)

    # 4. Pullback trigger check
    is_pullback = (
        (sma20_num is not None and close_num < sma20_num)
        or (rsi_num is not None and rsi_num < rsi_threshold)
    )
    if not is_pullback:
        return False

    # 5. Sector Leader Filter
    if require_sector_leader:
        zscore_num = to_num(zscore)
        threshold_num = to_num(sector_leader_threshold)
        return zscore_num is not None and threshold_num is not None and zscore_num > threshold_num

    return True


def dynamic_stop_multiplier(atr_pct: float, base_multiplier: float = 2.5) -> float:
    """
    Computes volatility-adaptive ATR trailing stop multipliers:
    - Low volatility (ATR% < 2.0): Tight stop multiplier (e.g. 1.8 - 2.0) to lock in gains quickly.
    - High volatility (ATR% > 4.5): Wider stop multiplier (e.g. 3.0 - 3.2) to survive standard noise.
    """
    vol = to_num(atr_pct)
    if vol is None:
        return base_multiplier
    
    if vol <= 2.0:
        return 1.8
    elif vol >= 4.5:
        return 3.2
    else:
        # Linear interpolation between 1.8 and 3.2
        fraction = (vol - 2.0) / (4.5 - 2.0)
        return 1.8 + fraction * (3.2 - 1.8)


def trailing_stop_price(highest_price: Any, atr_value: Any, atr_pct: Any = None) -> Optional[float]:
    high = to_num(highest_price)
    atr = to_num(atr_value)
    vol = to_num(atr_pct) or 3.0
    
    if high is None or atr is None or atr <= 0:
        return None
        
    multiplier = dynamic_stop_multiplier(vol)
    return high - (atr * multiplier)


def is_trailing_stop_hit(
    *,
    close: Any,
    highest_price: Any,
    atr_value: Any,
    atr_pct: Any = None,
) -> Tuple[bool, Optional[float]]:
    close_num = to_num(close)
    stop = trailing_stop_price(highest_price, atr_value, atr_pct)
    if close_num is None or stop is None:
        return False, stop
    return close_num <= stop, stop


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


def volatility_risk_profile(close: Any, atr14: Any) -> Dict[str, Any]:
    close_num = to_num(close)
    atr_num = to_num(atr14)
    if close_num is None or close_num <= 0 or atr_num is None or atr_num <= 0:
        return {
            "riskLevel": "Unknown",
            "riskScore": None,
            "atrPct": None,
            "suggestedWeightPct": None,
            "reason": "Volatility unavailable",
        }

    atr_pct = (atr_num / close_num) * 100.0
    if atr_pct < 2.0:
        risk_level = "Low"
        suggested_weight = 15.0
    elif atr_pct < 4.0:
        risk_level = "Medium"
        suggested_weight = 10.0
    else:
        risk_level = "High"
        suggested_weight = 5.0

    return {
        "riskLevel": risk_level,
        "riskScore": round(atr_pct, 4),
        "atrPct": round(atr_pct, 4),
        "suggestedWeightPct": suggested_weight,
        "reason": f"ATR14 is {atr_pct:.2f}% of price",
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
        risk_model = row.get("riskModel") if isinstance(row.get("riskModel"), dict) else None
        risk = risk_model or volatility_risk_profile(row.get("close"), row.get("atr14"))
        reason_parts = ["Sector Leader", "Trend Up", "Pullback Buy"]
        if score is not None and leader_threshold is not None:
            reason_parts.append(f"Z {score:.2f} > sector threshold {leader_threshold:.2f}")
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
                "liquidity": row.get("liquidity"),
                "passesLiquidityFilter": bool(row.get("passesLiquidityFilter")),
                "riskLevel": risk["riskLevel"],
                "riskScore": risk["riskScore"],
                "atrPct": risk.get("atrPct"),
                "suggestedWeightPct": risk["suggestedWeightPct"],
                "riskReason": risk.get("reason") or ", ".join(risk.get("reasons", [])),
                "riskModel": risk_model,
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
