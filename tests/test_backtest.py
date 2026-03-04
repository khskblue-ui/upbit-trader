"""Tests for src/backtest/engine.py, src/backtest/report.py and the TFVB strategy."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.backtest.engine import BacktestEngine, BacktestResult, BacktestTrade
from src.backtest.report import (
    PerformanceMetrics,
    calculate_metrics,
    format_report,
    _max_drawdown,
    _sharpe_ratio,
)
from src.strategy.base import MarketData, Signal, StrategyConfig
from src.strategy.trend_filtered_breakout import TrendFilteredBreakoutStrategy


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_candles(n: int = 60, base_price: float = 50_000_000.0) -> list[dict]:
    """Return *n* synthetic daily OHLCV candle dicts (ascending timestamps)."""
    base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = base_price
    for i in range(n):
        ts = base_ts + timedelta(days=i)
        close = price + i * 100_000
        candles.append(
            {
                "market": "KRW-BTC",
                "timestamp": ts.isoformat(),
                "open": close - 50_000,
                "high": close + 100_000,
                "low": close - 100_000,
                "close": close,
                "volume": 10.0 + i * 0.1,
            }
        )
    return candles


# ---------------------------------------------------------------------------
# BacktestEngine — basic execution
# ---------------------------------------------------------------------------

class TestBacktestEngine:
    async def test_run_returns_backtest_result(self):
        candles = _make_candles(100)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=65)
        assert isinstance(result, BacktestResult)

    async def test_result_fields_populated(self):
        candles = _make_candles(100)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=65)
        assert result.strategy_name == "trend_filtered_breakout"
        assert result.market == "KRW-BTC"
        assert result.initial_capital == 1_000_000.0
        assert isinstance(result.final_capital, float)
        assert isinstance(result.equity_curve, list)
        assert len(result.equity_curve) == len(candles) - 65

    async def test_equity_curve_length(self):
        candles = _make_candles(100)
        warmup = 65
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=warmup)
        assert len(result.equity_curve) == len(candles) - warmup

    async def test_raises_with_too_few_candles(self):
        candles = _make_candles(5)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy)
        with pytest.raises(ValueError, match="Need at least"):
            await engine.run("KRW-BTC", candles, warmup_bars=30)

    async def test_all_trades_have_required_fields(self):
        candles = _make_candles(100)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=65)
        for trade in result.trades:
            assert isinstance(trade, BacktestTrade)
            assert trade.side in ("buy", "sell")
            assert trade.price > 0
            assert trade.quantity > 0
            assert trade.fee >= 0
            assert isinstance(trade.timestamp, str)

    async def test_sell_trades_have_pnl(self):
        candles = _make_candles(100)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=65)
        for trade in result.trades:
            if trade.side == "sell":
                assert trade.pnl is not None

    async def test_buy_trades_have_no_pnl(self):
        candles = _make_candles(100)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=65)
        for trade in result.trades:
            if trade.side == "buy":
                assert trade.pnl is None

    async def test_fees_are_deducted(self):
        candles = _make_candles(100)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0, fee_rate=0.0005)
        result = await engine.run("KRW-BTC", candles, warmup_bars=65)
        total_fee = sum(t.fee for t in result.trades)
        assert total_fee >= 0

    async def test_custom_initial_capital(self):
        candles = _make_candles(100)
        strategy = TrendFilteredBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=2_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=65)
        assert result.initial_capital == 2_000_000.0


# ---------------------------------------------------------------------------
# PerformanceMetrics helpers
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    def test_no_drawdown_flat_equity(self):
        equity = [1_000_000.0] * 10
        assert _max_drawdown(equity) == 0.0

    def test_simple_drawdown(self):
        equity = [100.0, 90.0, 80.0, 100.0]
        dd = _max_drawdown(equity)
        assert dd == pytest.approx(-20.0, abs=0.01)

    def test_empty_equity(self):
        assert _max_drawdown([]) == 0.0

    def test_monotonically_increasing(self):
        equity = [100.0 + i for i in range(20)]
        assert _max_drawdown(equity) == 0.0


class TestSharpeRatio:
    def test_returns_none_for_too_short(self):
        assert _sharpe_ratio([]) is None
        assert _sharpe_ratio([1.0]) is None

    def test_returns_none_for_zero_std(self):
        # Identical values → zero std
        assert _sharpe_ratio([100.0] * 10) is None

    def test_returns_float_for_varying_equity(self):
        import random
        rng = random.Random(42)
        equity = [100_000.0]
        for _ in range(100):
            equity.append(equity[-1] * (1 + rng.gauss(0.001, 0.01)))
        result = _sharpe_ratio(equity)
        assert result is not None
        assert isinstance(result, float)


class TestCalculateMetrics:
    def _make_result(self, initial=1_000_000.0, final=1_100_000.0, trades=None, equity=None):
        return BacktestResult(
            strategy_name="test",
            market="KRW-BTC",
            start_date="2024-01-01",
            end_date="2024-12-31",
            initial_capital=initial,
            final_capital=final,
            trades=trades or [],
            equity_curve=equity or [initial, final],
        )

    def test_total_return_positive(self):
        result = self._make_result(initial=1_000_000.0, final=1_200_000.0)
        metrics = calculate_metrics(result)
        assert metrics.total_return_pct == pytest.approx(20.0, abs=0.01)

    def test_total_return_negative(self):
        result = self._make_result(initial=1_000_000.0, final=800_000.0)
        metrics = calculate_metrics(result)
        assert metrics.total_return_pct == pytest.approx(-20.0, abs=0.01)

    def test_zero_trades(self):
        result = self._make_result(trades=[])
        metrics = calculate_metrics(result)
        assert metrics.total_trades == 0
        assert metrics.win_trades == 0
        assert metrics.win_rate_pct == 0.0
        assert metrics.profit_factor is None

    def test_win_rate_calculation(self):
        trades = [
            BacktestTrade("KRW-BTC", "sell", 50_000_000, 0.01, 25, "t1", "s", pnl=10_000),
            BacktestTrade("KRW-BTC", "sell", 49_000_000, 0.01, 25, "t2", "s", pnl=-5_000),
            BacktestTrade("KRW-BTC", "sell", 51_000_000, 0.01, 25, "t3", "s", pnl=8_000),
        ]
        result = self._make_result(trades=trades)
        metrics = calculate_metrics(result)
        assert metrics.total_trades == 3
        assert metrics.win_trades == 2
        assert metrics.lose_trades == 1
        assert metrics.win_rate_pct == pytest.approx(66.67, abs=0.01)

    def test_profit_factor(self):
        trades = [
            BacktestTrade("KRW-BTC", "sell", 1, 1, 0, "t", "s", pnl=20_000),
            BacktestTrade("KRW-BTC", "sell", 1, 1, 0, "t", "s", pnl=-10_000),
        ]
        result = self._make_result(trades=trades)
        metrics = calculate_metrics(result)
        assert metrics.profit_factor == pytest.approx(2.0, abs=0.001)

    def test_buy_trades_excluded_from_count(self):
        trades = [
            BacktestTrade("KRW-BTC", "buy", 50_000_000, 0.01, 25, "t1", "s"),
            BacktestTrade("KRW-BTC", "sell", 51_000_000, 0.01, 25, "t2", "s", pnl=5_000),
        ]
        result = self._make_result(trades=trades)
        metrics = calculate_metrics(result)
        assert metrics.total_trades == 1

    def test_total_fee_includes_all_trades(self):
        trades = [
            BacktestTrade("KRW-BTC", "buy", 50_000_000, 0.01, 25.0, "t1", "s"),
            BacktestTrade("KRW-BTC", "sell", 51_000_000, 0.01, 25.0, "t2", "s", pnl=5_000),
        ]
        result = self._make_result(trades=trades)
        metrics = calculate_metrics(result)
        assert metrics.total_fee == pytest.approx(50.0, abs=0.01)

    def test_max_drawdown_is_non_positive(self):
        equity = [100.0, 90.0, 80.0, 95.0, 100.0]
        result = self._make_result(equity=equity)
        metrics = calculate_metrics(result)
        assert metrics.max_drawdown_pct <= 0.0


class TestFormatReport:
    def test_format_report_returns_string(self):
        result = BacktestResult(
            strategy_name="trend_filtered_breakout",
            market="KRW-BTC",
            start_date="2024-01-01",
            end_date="2024-12-31",
            initial_capital=1_000_000.0,
            final_capital=1_100_000.0,
            trades=[],
            equity_curve=[1_000_000.0, 1_050_000.0, 1_100_000.0],
        )
        metrics = calculate_metrics(result)
        report = format_report(result, metrics)
        assert isinstance(report, str)
        assert "trend_filtered_breakout" in report
        assert "KRW-BTC" in report

    def test_format_report_contains_key_metrics(self):
        result = BacktestResult(
            strategy_name="test_strategy",
            market="KRW-ETH",
            start_date="2024-01-01",
            end_date="2024-06-30",
            initial_capital=2_000_000.0,
            final_capital=2_200_000.0,
            trades=[],
            equity_curve=[2_000_000.0, 2_200_000.0],
        )
        metrics = calculate_metrics(result)
        report = format_report(result, metrics)
        assert "총 수익률" in report
        assert "최대 낙폭" in report
        assert "샤프 비율" in report
        assert "승률" in report
