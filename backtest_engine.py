#!/usr/bin/env python3
"""
Backtest engine for a BIST stock-selection formula.

Dependencies:
  python3 -m pip install backtrader yfinance pandas

Example:
  python3 backtest_engine.py
  python3 backtest_engine.py --symbols THYAO.IS TUPRS.IS FROTO.IS --cash 100000
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

try:
    import backtrader as bt
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: backtrader. Install it with:\n"
        "  python3 -m pip install backtrader"
    ) from exc

try:
    import yfinance as yf
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: yfinance. Install it with:\n"
        "  python3 -m pip install yfinance"
    ) from exc


BENCHMARK_SYMBOL = "XU100.IS"
DEFAULT_SYMBOLS = ["THYAO.IS", "TUPRS.IS", "FROTO.IS"]
DEFAULT_FUNDAMENTAL_SCORES = {
    "THYAO.IS": 0.90,
    "TUPRS.IS": 0.30,
    "FROTO.IS": 0.55,
    "GARAN.IS": 0.82,
    "AKBNK.IS": 0.68,
    "ISCTR.IS": 0.61,
    "MIATK.IS": 0.88,
    "LOGO.IS": 0.57,
}


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def load_sector_mappings(path: str = "data/sector_mappings.json") -> Dict[str, str]:
    mapping_path = Path(path)
    if mapping_path.exists():
        with mapping_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a JSON object mapping symbols to sectors")
        return {
            normalize_symbol(symbol): str(sector or "Unknown").strip() or "Unknown"
            for symbol, sector in raw.items()
        }

    return {
        "THYAO.IS": "Industrials",
        "TUPRS.IS": "Industrials",
        "FROTO.IS": "Industrials",
        "GARAN.IS": "Financials",
        "AKBNK.IS": "Financials",
        "ISCTR.IS": "Financials",
        "MIATK.IS": "Tech",
        "LOGO.IS": "Tech",
    }


def calculate_sector_relative_zscores(
    raw_scores: Dict[str, float],
    sector_mappings: Dict[str, str],
) -> Dict[str, float]:
    grouped: Dict[str, List[float]] = {}
    clean_scores: Dict[str, float] = {}

    for symbol, raw_score in raw_scores.items():
        try:
            score = float(raw_score)
        except Exception:
            continue
        if not math.isfinite(score):
            continue
        normalized = normalize_symbol(symbol)
        sector = sector_mappings.get(normalized, "Unknown")
        clean_scores[normalized] = score
        grouped.setdefault(sector, []).append(score)

    sector_stats = {}
    for sector, scores in grouped.items():
        mean = sum(scores) / len(scores)
        variance = sum((score - mean) ** 2 for score in scores) / len(scores)
        std_dev = math.sqrt(variance)
        sector_stats[sector] = {"mean": mean, "std_dev": std_dev}

    zscores: Dict[str, float] = {}
    for symbol, score in clean_scores.items():
        sector = sector_mappings.get(symbol, "Unknown")
        stats = sector_stats.get(sector, {"mean": score, "std_dev": 0.0})
        std_dev = stats["std_dev"]
        zscores[symbol] = (score - stats["mean"]) / std_dev if std_dev > 0 else 0.0
    return zscores


def download_price_data(symbol: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    df = yf.download(
        symbol,
        start=start.date().isoformat(),
        end=end.date().isoformat(),
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

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
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"{symbol}: missing columns from yfinance data: {missing}")

    df = df[required].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0)
    df = df[df["close"] > 0]

    if len(df) < 220:
        raise ValueError(f"{symbol}: not enough daily bars for 200-day MA, bars={len(df)}")

    return df


class DailyReturn(bt.Indicator):
    lines = ("ret",)

    def next(self):
        prev_close = float(self.data[-1]) if len(self.data) > 1 else 0.0
        cur_close = float(self.data[0])
        self.lines.ret[0] = (cur_close / prev_close) - 1.0 if prev_close > 0 else 0.0


class FiscographAlphaStrategy(bt.Strategy):
    params = dict(
        fast_ma=50,
        slow_ma=200,
        atr_period=14,
        atr_multiplier=2.5,
        volatility_period=20,
        trading_days=252,
        target_portfolio_risk=0.02,
        min_position_weight=0.05,
        max_position_weight=0.15,
        fundamental_scores=None,
        fundamental_zscores=None,
        min_sector_zscore=1.0,
        printlog=False,
    )

    def __init__(self):
        self.fast_ma = {}
        self.slow_ma = {}
        self.atr = {}
        self.daily_returns = {}
        self.annualized_volatility = {}
        self.crossovers = {}
        self.entry_price = {}
        self.highest_price = {}
        self.closed_trades = []
        self.fundamental_scores = self.p.fundamental_scores or {}
        self.fundamental_zscores = self.p.fundamental_zscores or {}
        self.benchmark_data = self.datas[0]
        self.stock_datas = list(self.datas[1:])
        self.benchmark_sma = bt.indicators.SimpleMovingAverage(self.benchmark_data.close, period=self.p.slow_ma)

        for data in self.stock_datas:
            self.fast_ma[data] = bt.indicators.SimpleMovingAverage(data.close, period=self.p.fast_ma)
            self.slow_ma[data] = bt.indicators.SimpleMovingAverage(data.close, period=self.p.slow_ma)
            self.atr[data] = bt.indicators.ATR(data, period=self.p.atr_period)
            self.daily_returns[data] = DailyReturn(data.close)
            self.annualized_volatility[data] = (
                bt.indicators.StandardDeviation(self.daily_returns[data], period=self.p.volatility_period)
                * math.sqrt(float(self.p.trading_days))
            )
            self.crossovers[data] = bt.indicators.CrossOver(self.fast_ma[data], self.slow_ma[data])
            self.entry_price[data] = None
            self.highest_price[data] = None

    def log(self, message: str):
        if self.p.printlog:
            dt = self.datas[0].datetime.date(0).isoformat()
            print(f"{dt} {message}")

    def sector_zscore(self, data) -> float:
        symbol = normalize_symbol(data._name)
        raw = self.fundamental_zscores.get(symbol, self.fundamental_zscores.get(data._name, 0.0))
        try:
            zscore = float(raw)
            return zscore if math.isfinite(zscore) else 0.0
        except Exception:
            return 0.0

    def inverse_volatility_position_size(self, data, close: float) -> int:
        annualized_vol = float(self.annualized_volatility[data][0])
        if not math.isfinite(annualized_vol) or annualized_vol <= 0 or close <= 0:
            return 0

        raw_weight = float(self.p.target_portfolio_risk) / annualized_vol
        position_weight = min(
            float(self.p.max_position_weight),
            max(float(self.p.min_position_weight), raw_weight),
        )
        target_value = min(self.broker.getcash(), self.broker.getvalue() * position_weight)
        size = int(target_value / close)
        if size > 0:
            self.log(
                f"POSITION_SIZE {data._name} vol={annualized_vol:.2%} "
                f"weight={position_weight:.2%} value={target_value:.2f} size={size}"
            )
        return size

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        symbol = order.data._name
        if order.status == order.Completed:
            if order.isbuy():
                self.entry_price[order.data] = float(order.executed.price)
                self.highest_price[order.data] = float(order.executed.price)
                self.log(f"BUY {symbol} price={order.executed.price:.2f} size={order.executed.size:.0f}")
            else:
                self.log(f"SELL {symbol} price={order.executed.price:.2f} size={order.executed.size:.0f}")
                if self.getposition(order.data).size == 0:
                    self.entry_price[order.data] = None
                    self.highest_price[order.data] = None
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log(f"ORDER_FAILED {symbol} status={order.getstatusname()}")

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.closed_trades.append(float(trade.pnlcomm))
        self.log(f"TRADE_CLOSED {trade.data._name} pnl={trade.pnlcomm:.2f}")

    def next(self):
        index_close = float(self.benchmark_data.close[0])
        index_sma = float(self.benchmark_sma[0])
        is_bull_market = math.isfinite(index_close) and math.isfinite(index_sma) and index_close > index_sma

        for data in self.stock_datas:
            symbol = data._name
            position = self.getposition(data)
            close = float(data.close[0])

            if position.size:
                previous_high = self.highest_price.get(data)
                self.highest_price[data] = max(previous_high or close, close)

                atr_value = float(self.atr[data][0])
                if math.isfinite(atr_value) and atr_value > 0:
                    trailing_stop_price = self.highest_price[data] - (atr_value * float(self.p.atr_multiplier))
                    if close <= trailing_stop_price:
                        self.log(
                            f"ATR_TRAILING_STOP {symbol} close={close:.2f} "
                            f"stop={trailing_stop_price:.2f} high={self.highest_price[data]:.2f} atr={atr_value:.2f}"
                        )
                        self.close(data=data)
                        continue

            if position.size:
                continue

            if not is_bull_market:
                continue

            zscore = self.sector_zscore(data)
            if self.crossovers[data][0] > 0 and zscore > self.p.min_sector_zscore:
                size = self.inverse_volatility_position_size(data, close)
                if size > 0:
                    self.log(f"BUY_SIGNAL {symbol} sector_zscore={zscore:.2f}")
                    self.buy(data=data, size=size)


def build_cerebro(
    benchmark_data: pd.DataFrame,
    price_data: Dict[str, pd.DataFrame],
    fundamental_scores: Dict[str, float],
    fundamental_zscores: Dict[str, float],
    initial_cash: float,
    commission: float,
    printlog: bool,
) -> bt.Cerebro:
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(float(initial_cash))
    cerebro.broker.setcommission(commission=float(commission))

    benchmark_feed = bt.feeds.PandasData(
        dataname=benchmark_data,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=None,
    )
    cerebro.adddata(benchmark_feed, name=BENCHMARK_SYMBOL)

    for symbol, df in price_data.items():
        feed = bt.feeds.PandasData(
            dataname=df,
            open="open",
            high="high",
            low="low",
            close="close",
            volume="volume",
            openinterest=None,
        )
        cerebro.adddata(feed, name=symbol)

    cerebro.addstrategy(
        FiscographAlphaStrategy,
        fundamental_scores=fundamental_scores,
        fundamental_zscores=fundamental_zscores,
        printlog=printlog,
    )
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    return cerebro


def win_rate(strategy: FiscographAlphaStrategy) -> float:
    trades = strategy.closed_trades
    if not trades:
        return 0.0
    wins = sum(1 for pnl in trades if pnl > 0)
    return wins / len(trades)


def run_backtest(args: argparse.Namespace) -> int:
    symbols = [normalize_symbol(s) for s in args.symbols if normalize_symbol(s)]
    if not symbols:
        raise SystemExit("No valid symbols supplied.")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(args.years * 365.25) + 30)

    try:
        benchmark_data = download_price_data(BENCHMARK_SYMBOL, start=start, end=end)
    except Exception as exc:
        raise SystemExit(f"Benchmark download failed for {BENCHMARK_SYMBOL}: {exc}") from exc
    if benchmark_data is None or benchmark_data.empty:
        raise SystemExit(f"Benchmark download returned no usable data for {BENCHMARK_SYMBOL}.")
    print(f"[DATA] {BENCHMARK_SYMBOL}: {len(benchmark_data)} bars benchmark")

    price_data: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        if symbol == BENCHMARK_SYMBOL:
            continue
        try:
            df = download_price_data(symbol, start=start, end=end)
            if df is not None and not df.empty:
                price_data[symbol] = df
                print(f"[DATA] {symbol}: {len(df)} bars")
        except Exception as exc:
            print(f"[WARN] skipping {symbol}: {exc}", file=sys.stderr)

    if not price_data:
        raise SystemExit("No usable price data downloaded.")

    fundamental_scores = {
        symbol: DEFAULT_FUNDAMENTAL_SCORES.get(symbol, 0.50)
        for symbol in price_data
    }
    sector_mappings = load_sector_mappings()
    fundamental_zscores = calculate_sector_relative_zscores(
        raw_scores=fundamental_scores,
        sector_mappings=sector_mappings,
    )
    for symbol in sorted(fundamental_zscores):
        sector = sector_mappings.get(symbol, "Unknown")
        print(
            f"[FACTOR] {symbol}: sector={sector} "
            f"raw={fundamental_scores[symbol]:.2f} z={fundamental_zscores[symbol]:.2f}"
        )

    cerebro = build_cerebro(
        benchmark_data=benchmark_data,
        price_data=price_data,
        fundamental_scores=fundamental_scores,
        fundamental_zscores=fundamental_zscores,
        initial_cash=args.cash,
        commission=args.commission,
        printlog=args.printlog,
    )

    print(f"[START] Portfolio Value: {cerebro.broker.getvalue():,.2f}")
    results = cerebro.run()
    strategy = results[0]
    final_value = cerebro.broker.getvalue()
    drawdown = strategy.analyzers.drawdown.get_analysis()
    max_drawdown = float(drawdown.get("max", {}).get("drawdown", 0.0) or 0.0)
    wr = win_rate(strategy)

    print(f"[FINAL] Portfolio Value: {final_value:,.2f}")
    print(f"[FINAL] Closed Trades: {len(strategy.closed_trades)}")
    print(f"[FINAL] Win Rate: {wr:.2%}")
    print(f"[FINAL] Max Drawdown: {max_drawdown:.2f}%")
    return 0


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest Fiscograph Alpha strategy on BIST symbols.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Yahoo Finance symbols, e.g. THYAO.IS")
    parser.add_argument("--years", type=float, default=3.0, help="Lookback period in years")
    parser.add_argument("--cash", type=float, default=100_000.0, help="Initial portfolio cash")
    parser.add_argument("--commission", type=float, default=0.001, help="Broker commission rate, e.g. 0.001 = 0.10%%")
    parser.add_argument("--printlog", action="store_true", help="Print order and signal logs")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    return run_backtest(args)


if __name__ == "__main__":
    raise SystemExit(main())
