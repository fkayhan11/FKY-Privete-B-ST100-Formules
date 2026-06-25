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
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from archive_fundamentals import get_fundamentals_at_date

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


def to_num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        return x if math.isfinite(x) else None
    return None


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
    "PGSUS": "Transportation",
    "TAVHL": "Transportation",
    "THYAO": "Transportation",
    "FROTO": "Industrials",
    "TOASO": "Industrials",
    "TTRAK": "Industrials",
    "TUPRS": "Energy",
    "TCELL": "Telecom",
    "TTKOM": "Telecom",
}


def js_object_to_json(raw_object: str) -> str:
    text = re.sub(r"//.*", "", raw_object)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', text)
    return text


def load_sector_map(path: str = "bist100-config.js") -> Dict[str, str]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"sector config not found: {path}")

    text = config_path.read_text(encoding="utf-8")

    codes_match = re.search(r"window\.BIST100_CODES\s*=\s*(\[[\s\S]*?\]);", text)
    sector_match = re.search(r"window\.BIST100_FALLBACK_SECTORS\s*=\s*(\{[\s\S]*?\});", text)

    codes: List[str] = []
    if codes_match:
        try:
            parsed_codes = json.loads(codes_match.group(1))
            if isinstance(parsed_codes, list):
                codes = [normalize_symbol(str(x)) for x in parsed_codes if str(x).strip()]
        except Exception:
            codes = re.findall(r'"([A-Z0-9]+)"', codes_match.group(1))

    raw_sectors = {}
    if sector_match:
        parsed = json.loads(js_object_to_json(sector_match.group(1)))
        if isinstance(parsed, dict):
            raw_sectors = parsed

    sector_map: Dict[str, str] = {}
    for code in codes or raw_sectors.keys():
        clean_code = normalize_symbol(str(code).removesuffix(".IS"))
        raw_sector = raw_sectors.get(clean_code, COMMON_SECTOR_OVERRIDES.get(clean_code, "Unknown"))
        sector = str(raw_sector or "Unknown").strip()
        sector_map[f"{clean_code}.IS"] = SECTOR_LABELS.get(sector.upper(), sector or "Unknown")

    for code, raw_sector in raw_sectors.items():
        clean_code = normalize_symbol(str(code).removesuffix(".IS"))
        sector = str(raw_sector or "Unknown").strip()
        sector_map[f"{clean_code}.IS"] = SECTOR_LABELS.get(sector.upper(), sector or "Unknown")

    return sector_map


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

    return load_sector_map()


def precalculate_sector_zscores(fundamental_data: Dict[str, dict]) -> Dict[str, float]:
    grouped: Dict[str, List[float]] = {}
    clean_scores: Dict[str, float] = {}
    symbol_sectors: Dict[str, str] = {}

    for symbol, row in fundamental_data.items():
        if not isinstance(row, dict):
            continue
        try:
            score = float(row.get("score"))
        except Exception:
            continue
        if not math.isfinite(score):
            continue
        normalized = normalize_symbol(symbol)
        sector = str(row.get("sector") or "Unknown").strip() or "Unknown"
        clean_scores[normalized] = score
        symbol_sectors[normalized] = sector
        grouped.setdefault(sector, []).append(score)

    sector_stats = {}
    for sector, scores in grouped.items():
        mean = sum(scores) / len(scores)
        variance = sum((score - mean) ** 2 for score in scores) / len(scores)
        std_dev = math.sqrt(variance)
        sector_stats[sector] = {"mean": mean, "std_dev": std_dev}

    zscores: Dict[str, float] = {}
    for symbol, score in clean_scores.items():
        sector = symbol_sectors.get(symbol, "Unknown")
        stats = sector_stats.get(sector, {"mean": score, "std_dev": 0.0})
        std_dev = stats["std_dev"]
        zscores[symbol] = (score - stats["mean"]) / std_dev if std_dev > 0 else 0.0
    return zscores


def calculate_sector_relative_zscores(
    raw_scores: Dict[str, float],
    sector_mappings: Dict[str, str],
) -> Dict[str, float]:
    fundamental_data = {
        normalize_symbol(symbol): {
            "score": score,
            "sector": sector_mappings.get(normalize_symbol(symbol), "Unknown"),
        }
        for symbol, score in raw_scores.items()
    }
    return precalculate_sector_zscores(fundamental_data)


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
        pullback_ma_period=20,
        trend_ma_period=200,
        rsi_period=14,
        rsi_pullback_threshold=40,
        atr_period=14,
        atr_multiplier=2.5,
        risk_atr_period=20,
        target_risk_per_trade=0.02,
        fundamental_scores=None,
        fundamental_zscores=None,
        printlog=False,
    )

    def __init__(self):
        self.pullback_ma = {}
        self.trend_ma = {}
        self.rsi = {}
        self.atr = {}
        self.risk_atr = {}
        self.entry_price = {}
        self.highest_price = {}
        self.closed_trades = []
        self.fundamental_scores = self.p.fundamental_scores or {}
        self.fundamental_zscores = self.p.fundamental_zscores or {}
        self.sector_map = load_sector_map()
        self.sector_leader_thresholds = self.calculate_sector_leader_thresholds()
        self.benchmark_data = self.data1
        self.stock_datas = [data for i, data in enumerate(self.datas) if i != 1]
        self.sma_200_index = bt.indicators.SimpleMovingAverage(self.data1.close, period=self.p.trend_ma_period)

        for data in self.stock_datas:
            symbol = normalize_symbol(data._name)
            self.sector_map.setdefault(symbol, "Unknown")
            self.pullback_ma[data] = bt.indicators.SimpleMovingAverage(data.close, period=self.p.pullback_ma_period)
            self.trend_ma[data] = bt.indicators.SimpleMovingAverage(data.close, period=self.p.trend_ma_period)
            self.rsi[data] = bt.indicators.RSI(data.close, period=self.p.rsi_period)
            self.atr[data] = bt.indicators.ATR(data, period=self.p.atr_period)
            self.risk_atr[data] = bt.indicators.ATR(data, period=self.p.risk_atr_period)
            self.entry_price[data] = None
            self.highest_price[data] = None

    def log(self, message: str):
        if self.p.printlog:
            dt = self.datas[0].datetime.date(0).isoformat()
            print(f"{dt} {message}")

    def fundamental_zscore(self, data) -> float:
        symbol = normalize_symbol(data._name)
        raw = self.fundamental_zscores.get(symbol, self.fundamental_zscores.get(data._name, 0.0))
        try:
            zscore = float(raw)
            return zscore if math.isfinite(zscore) else 0.0
        except Exception:
            return 0.0

    def point_in_time_fundamental_score(self, fundamentals: Dict[str, Any]) -> Optional[float]:
        if not isinstance(fundamentals, dict):
            return None

        score_parts: List[float] = []
        roe = to_num(fundamentals.get("returnOnEquity"))
        profit_margin = to_num(fundamentals.get("profitMargins"))
        earnings_growth = to_num(fundamentals.get("earningsGrowth"))
        peg_ratio = to_num(fundamentals.get("pegRatio"))

        if roe is not None:
            score_parts.append(1.0 if roe > 0.12 else 0.5 if roe > 0 else -1.0)
        if profit_margin is not None:
            score_parts.append(1.0 if profit_margin > 0.10 else 0.5 if profit_margin > 0 else -1.0)
        if earnings_growth is not None:
            score_parts.append(1.0 if earnings_growth > 0.10 else 0.5 if earnings_growth > 0 else -1.0)
        if peg_ratio is not None and peg_ratio > 0:
            score_parts.append(1.0 if peg_ratio <= 1.0 else 0.5 if peg_ratio <= 2.0 else -0.5)

        if not score_parts:
            return None
        return sum(score_parts) / len(score_parts)

    def calculate_sector_leader_thresholds(self) -> Dict[str, float]:
        grouped: Dict[str, List[float]] = {}
        for symbol, raw_zscore in self.fundamental_zscores.items():
            try:
                zscore = float(raw_zscore)
            except Exception:
                continue
            if not math.isfinite(zscore):
                continue
            sector = self.sector_map.get(normalize_symbol(symbol), "Unknown")
            grouped.setdefault(sector, []).append(zscore)

        thresholds: Dict[str, float] = {}
        for sector, values in grouped.items():
            values = sorted(values)
            if not values:
                continue
            # 80th percentile: stock must be in the top 20% of its own sector.
            idx = min(len(values) - 1, max(0, math.ceil(len(values) * 0.80) - 1))
            thresholds[sector] = values[idx]
        return thresholds

    def is_sector_leader(self, symbol: str, zscore: float) -> bool:
        sector = self.sector_map.get(normalize_symbol(symbol), "Unknown")
        threshold = self.sector_leader_thresholds.get(sector)
        return threshold is not None and zscore > threshold

    def inverse_volatility_position_size(self, data, close: float) -> int:
        atr_value = float(self.risk_atr[data][0])
        if not math.isfinite(atr_value) or atr_value <= 0 or close <= 0:
            return 0

        risk_budget = self.broker.getvalue() * float(self.p.target_risk_per_trade)
        risk_per_share = atr_value * float(self.p.atr_multiplier)
        risk_sized_shares = int(risk_budget / risk_per_share) if risk_per_share > 0 else 0
        cash_sized_shares = int(self.broker.getcash() / close)
        size = max(0, min(risk_sized_shares, cash_sized_shares))
        if size > 0:
            actual_risk = size * risk_per_share
            self.log(
                f"POSITION_SIZE {data._name} atr20={atr_value:.2f} risk_per_share={risk_per_share:.2f} "
                f"risk_budget={risk_budget:.2f} actual_risk={actual_risk:.2f} size={size}"
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
        current_date = self.data.datetime.date(0)
        index_close = float(self.data1.close[0])
        index_sma = float(self.sma_200_index[0])
        is_bull_market = math.isfinite(index_close) and math.isfinite(index_sma) and index_close > index_sma

        if not is_bull_market:
            for data in self.stock_datas:
                if self.getposition(data).size:
                    self.log(
                        f"REGIME_EXIT {data._name} index_close={index_close:.2f} "
                        f"index_sma200={index_sma:.2f}"
                    )
                    self.close(data=data)
            return

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

            trend_ma = float(self.trend_ma[data][0])
            pullback_ma = float(self.pullback_ma[data][0])
            rsi_value = float(self.rsi[data][0])

            pit_payload = get_fundamentals_at_date(symbol, current_date)
            if not pit_payload:
                self.log(f"SKIP_NO_PIT_FUNDAMENTALS {symbol} date={current_date.isoformat()}")
                continue
            pit_fundamentals = pit_payload.get("fundamentals") if isinstance(pit_payload, dict) else None
            pit_score = self.point_in_time_fundamental_score(pit_fundamentals)
            if pit_score is None or pit_score <= 0:
                continue

            is_stock_uptrend = math.isfinite(trend_ma) and close > trend_ma
            is_pullback = (
                (math.isfinite(pullback_ma) and close < pullback_ma)
                or (math.isfinite(rsi_value) and rsi_value < float(self.p.rsi_pullback_threshold))
            )

            if is_stock_uptrend and is_pullback:
                size = self.inverse_volatility_position_size(data, close)
                if size > 0:
                    self.log(
                        f"BUY_SIGNAL {symbol} pit_score={pit_score:.2f} date={current_date.isoformat()} "
                        f"available_from={pit_payload.get('availableFrom')} close={close:.2f} "
                        f"sma20={pullback_ma:.2f} sma200={trend_ma:.2f} rsi={rsi_value:.2f}"
                    )
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
    cerebro.broker.set_slippage_perc(0.001)

    price_items = list(price_data.items())
    if not price_items:
        raise ValueError("price_data must include at least one stock feed before adding benchmark data")

    first_symbol, first_df = price_items[0]
    first_feed = bt.feeds.PandasData(
        dataname=first_df,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=None,
    )
    cerebro.adddata(first_feed, name=first_symbol)

    benchmark_feed = bt.feeds.PandasData(
        dataname=benchmark_data,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=None,
    )
    cerebro.adddata(benchmark_feed, name="XU100")

    for symbol, df in price_items[1:]:
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
    sector_mappings = load_sector_map()
    fundamental_data = {
        symbol: {
            "score": score,
            "sector": sector_mappings.get(symbol, "Unknown"),
        }
        for symbol, score in fundamental_scores.items()
    }
    fundamental_zscores = precalculate_sector_zscores(fundamental_data)
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
    parser.add_argument("--commission", type=float, default=0.002, help="Broker commission rate, e.g. 0.002 = 0.20%%")
    parser.add_argument("--printlog", action="store_true", help="Print order and signal logs")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    return run_backtest(args)


if __name__ == "__main__":
    raise SystemExit(main())
