from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from bist_market_rules import liquidity_profile


DEFAULT_PORTFOLIO_LIMITS = {
    "maxPositionWeightPct": 15.0,
    "maxSectorWeightPct": 35.0,
    "maxHighRiskPositions": 3,
}


def to_num(value: Any):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def gap_risk_profile(df: pd.DataFrame, lookback: int = 60) -> Dict[str, Any]:
    if df is None or df.empty or "open" not in df or "close" not in df:
        return {"gapRiskLevel": "Unknown", "averageAbsGapPct": None, "maxAbsGapPct": None}
    tail = df.tail(max(2, int(lookback))).copy()
    previous_close = pd.to_numeric(tail["close"], errors="coerce").shift(1)
    opens = pd.to_numeric(tail["open"], errors="coerce")
    gaps = ((opens - previous_close) / previous_close * 100.0).abs().dropna()
    if gaps.empty:
        return {"gapRiskLevel": "Unknown", "averageAbsGapPct": None, "maxAbsGapPct": None}
    avg_gap = float(gaps.mean())
    max_gap = float(gaps.max())
    if max_gap >= 8 or avg_gap >= 2.5:
        level = "High"
    elif max_gap >= 4 or avg_gap >= 1.2:
        level = "Medium"
    else:
        level = "Low"
    return {
        "gapRiskLevel": level,
        "averageAbsGapPct": round(avg_gap, 4),
        "maxAbsGapPct": round(max_gap, 4),
    }


def benchmark_correlation_profile(
    symbol_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    lookback: int = 90,
) -> Dict[str, Any]:
    if symbol_df is None or benchmark_df is None or symbol_df.empty or benchmark_df.empty:
        return {"benchmarkCorrelation": None, "correlationLevel": "Unknown"}
    asset_returns = pd.to_numeric(symbol_df["close"], errors="coerce").pct_change().dropna().tail(lookback)
    benchmark_returns = pd.to_numeric(benchmark_df["close"], errors="coerce").pct_change().dropna().tail(lookback)
    joined = pd.concat([asset_returns.rename("asset"), benchmark_returns.rename("benchmark")], axis=1).dropna()
    if len(joined) < 20:
        return {"benchmarkCorrelation": None, "correlationLevel": "Unknown"}
    corr = float(joined["asset"].corr(joined["benchmark"]))
    if corr >= 0.75:
        level = "High"
    elif corr >= 0.4:
        level = "Medium"
    else:
        level = "Low"
    return {"benchmarkCorrelation": round(corr, 4), "correlationLevel": level}


def composite_risk_profile(
    *,
    price_df: pd.DataFrame,
    benchmark_df: Optional[pd.DataFrame] = None,
    beta: Any = None,
    atr_pct: Any = None,
    portfolio_limits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    portfolio_limits = {**DEFAULT_PORTFOLIO_LIMITS, **(portfolio_limits or {})}
    liquidity = liquidity_profile(price_df)
    gap = gap_risk_profile(price_df)
    corr = benchmark_correlation_profile(price_df, benchmark_df) if benchmark_df is not None else {
        "benchmarkCorrelation": None,
        "correlationLevel": "Unknown",
    }
    beta_num = to_num(beta)
    atr_num = to_num(atr_pct)

    score = 0
    reasons = []
    if liquidity["liquidityLevel"] == "Low":
        score += 25
        reasons.append("low liquidity")
    elif liquidity["liquidityLevel"] == "Unknown":
        score += 15
        reasons.append("unknown liquidity")

    if gap["gapRiskLevel"] == "High":
        score += 25
        reasons.append("high gap risk")
    elif gap["gapRiskLevel"] == "Medium":
        score += 12
        reasons.append("medium gap risk")

    if corr["correlationLevel"] == "High":
        score += 15
        reasons.append("high benchmark correlation")

    if beta_num is not None and beta_num > 1.3:
        score += 15
        reasons.append("high beta")
    if atr_num is not None and atr_num > 4:
        score += 20
        reasons.append("high ATR volatility")

    if score >= 50:
        level = "High"
        suggested_weight = min(5.0, float(portfolio_limits["maxPositionWeightPct"]))
    elif score >= 25:
        level = "Medium"
        suggested_weight = min(10.0, float(portfolio_limits["maxPositionWeightPct"]))
    else:
        level = "Low"
        suggested_weight = min(15.0, float(portfolio_limits["maxPositionWeightPct"]))

    return {
        "riskLevel": level,
        "riskScore": min(100, score),
        "suggestedWeightPct": suggested_weight,
        "reasons": reasons or ["balanced risk profile"],
        "liquidity": liquidity,
        "gap": gap,
        "correlation": corr,
        "beta": beta_num,
        "portfolioLimits": portfolio_limits,
    }
