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
import math
import sys
from datetime import datetime, timedelta, timezone
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


DEFAULT_SYMBOLS = ["THYAO.IS", "TUPRS.IS", "FROTO.IS"]
DEFAULT_FUNDAMENTAL_SCORES = {
    "THYAO.IS": 0.72,
    "TUPRS.IS": 0.45,
    "FROTO.IS": 0.66,
}


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


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


class FiscographAlphaStrategy(bt.Strategy):
    params = dict(
        fast_ma=50,
        slow_ma=200,
        take_profit=0.15,
        stop_loss=0.05,
        fundamental_scores=None,
        min_fundamental_score=0.0,
        target_weight=0.30,
        printlog=False,
    )

    def __init__(self):
        self.fast_ma = {}
        self.slow_ma = {}
        self.crossovers = {}
        self.entry_price = {}
        self.closed_trades = []
        self.fundamental_scores = self.p.fundamental_scores or {}

        for data in self.datas:
            self.fast_ma[data] = bt.indicators.SimpleMovingAverage(data.close, period=self.p.fast_ma)
            self.slow_ma[data] = bt.indicators.SimpleMovingAverage(data.close, period=self.p.slow_ma)
            self.crossovers[data] = bt.indicators.CrossOver(self.fast_ma[data], self.slow_ma[data])
            self.entry_price[data] = None

    def log(self, message: str):
        if self.p.printlog:
            dt = self.datas[0].datetime.date(0).isoformat()
            print(f"{dt} {message}")

    def fundamental_score(self, data) -> float:
        symbol = normalize_symbol(data._name)
        raw = self.fundamental_scores.get(symbol, self.fundamental_scores.get(data._name, 0.0))
        try:
            score = float(raw)
            return score if math.isfinite(score) else 0.0
        except Exception:
            return 0.0

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        symbol = order.data._name
        if order.status == order.Completed:
            if order.isbuy():
                self.entry_price[order.data] = float(order.executed.price)
                self.log(f"BUY {symbol} price={order.executed.price:.2f} size={order.executed.size:.0f}")
            else:
                self.log(f"SELL {symbol} price={order.executed.price:.2f} size={order.executed.size:.0f}")
                if self.getposition(order.data).size == 0:
                    self.entry_price[order.data] = None
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log(f"ORDER_FAILED {symbol} status={order.getstatusname()}")

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.closed_trades.append(float(trade.pnlcomm))
        self.log(f"TRADE_CLOSED {trade.data._name} pnl={trade.pnlcomm:.2f}")

    def next(self):
        for data in self.datas:
            symbol = data._name
            position = self.getposition(data)
            close = float(data.close[0])

            if position.size:
                entry = self.entry_price.get(data) or float(position.price)
                if entry > 0:
                    pnl_pct = (close / entry) - 1.0
                    if pnl_pct >= self.p.take_profit:
                        self.log(f"TAKE_PROFIT {symbol} pnl_pct={pnl_pct:.2%}")
                        self.close(data=data)
                        continue
                    if pnl_pct <= -self.p.stop_loss:
                        self.log(f"STOP_LOSS {symbol} pnl_pct={pnl_pct:.2%}")
                        self.close(data=data)
                        continue

            if position.size:
                continue

            score = self.fundamental_score(data)
            if self.crossovers[data][0] > 0 and score > self.p.min_fundamental_score:
                cash = self.broker.getcash()
                portfolio_value = self.broker.getvalue()
                target_value = min(cash, portfolio_value * float(self.p.target_weight))
                size = int(target_value / close)
                if size > 0:
                    self.log(f"BUY_SIGNAL {symbol} score={score:.2f}")
                    self.buy(data=data, size=size)


def build_cerebro(
    price_data: Dict[str, pd.DataFrame],
    fundamental_scores: Dict[str, float],
    initial_cash: float,
    commission: float,
    printlog: bool,
) -> bt.Cerebro:
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(float(initial_cash))
    cerebro.broker.setcommission(commission=float(commission))

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

    price_data: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
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

    cerebro = build_cerebro(
        price_data=price_data,
        fundamental_scores=fundamental_scores,
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
