#!/usr/bin/env python3
"""
Parameter Optimizer for Fiscograph Alpha Strategy.
Sweeps strategy parameters to find the mathematically optimal settings.

Examples:
  python3 optimize_parameters.py --symbols THYAO.IS TUPRS.IS FROTO.IS --years 3
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Import from our backtest engine
from backtest_engine import (
    BENCHMARK_SYMBOL,
    DEFAULT_FUNDAMENTAL_SCORES,
    DEFAULT_SYMBOLS,
    build_cerebro,
    download_price_data,
    load_sector_map,
    normalize_symbol,
    precalculate_sector_zscores,
    win_rate,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = ROOT / "data" / "optimization_results.json"


def run_single_iteration(
    benchmark_data: pd.DataFrame,
    price_data: Dict[str, pd.DataFrame],
    fundamental_scores: Dict[str, float],
    fundamental_zscores: Dict[str, float],
    initial_cash: float,
    commission: float,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Runs a single backtest iteration and returns performance metrics."""
    cerebro = build_cerebro(
        benchmark_data=benchmark_data,
        price_data=price_data,
        fundamental_scores=fundamental_scores,
        fundamental_zscores=fundamental_zscores,
        initial_cash=initial_cash,
        commission=commission,
        printlog=False,
        **params,
    )
    try:
        results = cerebro.run()
        strategy = results[0]
        final_value = cerebro.broker.getvalue()
        
        # Calculate drawdown
        drawdown_analysis = strategy.analyzers.drawdown.get_analysis()
        max_drawdown = float(drawdown_analysis.get("max", {}).get("drawdown", 0.0) or 0.0)
        
        # Calculate Sharpe Ratio
        sharpe_analysis = strategy.analyzers.sharpe.get_analysis()
        sharpe_ratio = sharpe_analysis.get("sharperatio")
        if sharpe_ratio is None or math.isnan(sharpe_ratio) or math.isinf(sharpe_ratio):
            sharpe_ratio = 0.0
            
        wr = win_rate(strategy)
        trades = strategy.closed_trades
        total_trades = len(trades)
        net_profit = final_value - initial_cash
        return_pct = (net_profit / initial_cash) * 100.0
        
        return {
            "final_value": round(final_value, 2),
            "return_pct": round(return_pct, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "win_rate_pct": round(wr * 100.0, 2),
            "total_trades": total_trades,
            "status": "success",
        }
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize Fiscograph Alpha strategy parameters.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="BIST Yahoo Finance symbols")
    parser.add_argument("--years", type=float, default=3.0, help="Lookback period in years")
    parser.add_argument("--cash", type=float, default=100_000.0, help="Initial portfolio cash")
    parser.add_argument("--commission", type=float, default=0.002, help="Broker commission rate")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON file path for output results")
    parser.add_argument("--fast", action="store_true", help="Run a smaller parameter grid for speed")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    
    # 1. Download data once
    symbols = [normalize_symbol(s) for s in args.symbols if normalize_symbol(s)]
    if not symbols:
        print("[ERROR] No valid symbols provided.", file=sys.stderr)
        return 1
        
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(args.years * 365.25) + 30)
    
    print("=" * 60)
    print(f"  Fiscograph Parameter Optimizer (BIST100)")
    print("=" * 60)
    print(f"[INFO] Period: {args.years} years ({start.date()} to {end.date()})")
    print(f"[INFO] Initial Cash: {args.cash:,.2f} TRY")
    print(f"[INFO] Commission: {args.commission:.2%}")
    print(f"[INFO] Symbols: {', '.join(symbols)}")
    print("-" * 60)
    print("[DATA] Downloading benchmark and stock price histories...")
    
    try:
        benchmark_data = download_price_data(BENCHMARK_SYMBOL, start=start, end=end)
    except Exception as exc:
        print(f"[ERROR] Benchmark download failed for {BENCHMARK_SYMBOL}: {exc}", file=sys.stderr)
        return 1
    if benchmark_data is None or benchmark_data.empty:
        print(f"[ERROR] Benchmark download returned no usable data for {BENCHMARK_SYMBOL}.", file=sys.stderr)
        return 1
        
    price_data: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        if symbol == BENCHMARK_SYMBOL:
            continue
        try:
            df = download_price_data(symbol, start=start, end=end)
            if df is not None and not df.empty:
                price_data[symbol] = df
        except Exception as exc:
            print(f"[WARN] Skipping {symbol} due to download error: {exc}", file=sys.stderr)
            
    if not price_data:
        print("[ERROR] No usable stock price data downloaded.", file=sys.stderr)
        return 1
        
    print(f"[DATA] Successfully loaded {len(price_data)} symbols.")
    
    # Precalculate Z-scores
    fundamental_scores = {
        symbol: DEFAULT_FUNDAMENTAL_SCORES.get(symbol, 0.50)
        for symbol in price_data
    }
    sector_mappings = load_sector_map()
    fundamental_data = {
        symbol: {
            "score": score,
            "sector": sector_mappings.get(symbol, "Unknown"),
        }
        for symbol, score in fundamental_scores.items()
    }
    fundamental_zscores = precalculate_sector_zscores(fundamental_data)
    
    # 2. Define parameter grid
    if args.fast:
        rsi_pullbacks = [40]
        atr_multipliers = [2.0, 2.5]
        use_dynamic_rsis = [True, False]
        use_dynamic_stops = [True, False]
        require_sector_leaders = [False]
    else:
        rsi_pullbacks = [35, 40, 45]
        atr_multipliers = [1.8, 2.2, 2.5, 3.0]
        use_dynamic_rsis = [True, False]
        use_dynamic_stops = [True, False]
        require_sector_leaders = [True, False]
        
    # Cartesian product of parameter combinations
    keys = ["rsi_pullback_threshold", "atr_multiplier", "use_dynamic_rsi", "use_dynamic_stop", "require_sector_leader"]
    combinations = list(itertools.product(rsi_pullbacks, atr_multipliers, use_dynamic_rsis, use_dynamic_stops, require_sector_leaders))
    total_combinations = len(combinations)
    
    print(f"[SWEEP] Running grid search over {total_combinations} parameter combinations...")
    print("-" * 60)
    
    results_list: List[Dict[str, Any]] = []
    
    for i, combo in enumerate(combinations, 1):
        params = dict(zip(keys, combo))
        sys.stdout.write(f"\rRunning combination {i}/{total_combinations}...")
        sys.stdout.flush()
        
        metrics = run_single_iteration(
            benchmark_data=benchmark_data,
            price_data=price_data,
            fundamental_scores=fundamental_scores,
            fundamental_zscores=fundamental_zscores,
            initial_cash=args.cash,
            commission=args.commission,
            params=params,
        )
        
        if metrics["status"] == "success":
            results_list.append({**params, **metrics})
            
    print("\n[SWEEP] Optimization finished.")
    print("-" * 60)
    
    if not results_list:
        print("[ERROR] All backtest iterations failed.", file=sys.stderr)
        return 1
        
    # Sort results
    # Primary sort: Sharpe Ratio (descending), Secondary sort: Return Pct (descending)
    results_list.sort(key=lambda x: (x["sharpe_ratio"], x["return_pct"]), reverse=True)
    
    # Print top 10 parameter sets
    print(f"Top 10 Parameter Combinations (Sorted by Sharpe Ratio):")
    print(
        f"{'RSI Thresh':<10} | {'ATR Mult':<8} | {'Dyn RSI':<7} | {'Dyn Stop':<8} | {'Sec Ldr':<7} | "
        f"{'Return %':<8} | {'Max DD %':<8} | {'Sharpe':<6} | {'Trades':<6} | {'Win Rate':<8}"
    )
    print("-" * 110)
    for res in results_list[:10]:
        print(
            f"{res['rsi_pullback_threshold']:<10} | "
            f"{res['atr_multiplier']:<8} | "
            f"{str(res['use_dynamic_rsi']):<7} | "
            f"{str(res['use_dynamic_stop']):<8} | "
            f"{str(res['require_sector_leader']):<7} | "
            f"{res['return_pct']:>7}% | "
            f"{res['max_drawdown']:>7}% | "
            f"{res['sharpe_ratio']:>6} | "
            f"{res['total_trades']:>6} | "
            f"{res['win_rate_pct']:>7}%"
        )
    print("-" * 110)
    
    # Save results to file
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "optimized_at": datetime.now(timezone.utc).isoformat(),
        "total_combinations": total_combinations,
        "symbols": symbols,
        "best_combination": results_list[0],
        "top_10": results_list[:10],
        "all_runs": results_list,
    }
    
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Full optimization results saved to: {args.output}")
    
    best = results_list[0]
    print("\n" + "=" * 60)
    print("  RECOMMENDED PARAMETERS:")
    print("=" * 60)
    print(f"  * Base RSI Pullback Threshold: {best['rsi_pullback_threshold']}")
    print(f"  * Base ATR Stop Multiplier  : {best['atr_multiplier']}")
    print(f"  * Use Dynamic RSI            : {best['use_dynamic_rsi']}")
    print(f"  * Use Dynamic Stop           : {best['use_dynamic_stop']}")
    print(f"  * Require Sector Leader      : {best['require_sector_leader']}")
    print("-" * 60)
    print(f"  Expected Return              : {best['return_pct']}%")
    print(f"  Expected Max Drawdown        : {best['max_drawdown']}%")
    print(f"  Sharpe Ratio                 : {best['sharpe_ratio']}")
    print(f"  Win Rate                     : {best['win_rate_pct']}%")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
