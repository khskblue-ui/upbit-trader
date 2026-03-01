"""Tests for src/backtest/engine.py, src/backtest/report.py and the 3 concrete strategies."""

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
from src.strategy.macd_momentum import MacdMomentumStrategy
from src.strategy.rsi_bollinger import RsiBollingerStrategy
from src.strategy.volatility_breakout import VolatilityBreakoutStrategy


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
        candles = _make_candles(50)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        assert isinstance(result, BacktestResult)

    async def test_result_fields_populated(self):
        candles = _make_candles(50)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        assert result.strategy_name == "volatility_breakout"
        assert result.market == "KRW-BTC"
        assert result.initial_capital == 1_000_000.0
        assert isinstance(result.final_capital, float)
        assert isinstance(result.equity_curve, list)
        assert len(result.equity_curve) == len(candles) - 5

    async def test_equity_curve_length(self):
        candles = _make_candles(40)
        warmup = 10
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=warmup)
        assert len(result.equity_curve) == len(candles) - warmup

    async def test_raises_with_too_few_candles(self):
        candles = _make_candles(5)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy)
        with pytest.raises(ValueError, match="Need at least"):
            await engine.run("KRW-BTC", candles, warmup_bars=30)

    async def test_all_trades_have_required_fields(self):
        candles = _make_candles(50)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        for trade in result.trades:
            assert isinstance(trade, BacktestTrade)
            assert trade.side in ("buy", "sell")
            assert trade.price > 0
            assert trade.quantity > 0
            assert trade.fee >= 0
            assert isinstance(trade.timestamp, str)

    async def test_sell_trades_have_pnl(self):
        candles = _make_candles(60)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        for trade in result.trades:
            if trade.side == "sell":
                assert trade.pnl is not None

    async def test_buy_trades_have_no_pnl(self):
        candles = _make_candles(60)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        for trade in result.trades:
            if trade.side == "buy":
                assert trade.pnl is None

    async def test_fees_are_deducted(self):
        candles = _make_candles(60)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0, fee_rate=0.0005)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        total_fee = sum(t.fee for t in result.trades)
        assert total_fee >= 0

    async def test_no_open_position_at_end(self):
        """After run(), engine closes any open position at the last bar."""
        candles = _make_candles(60)
        strategy = VolatilityBreakoutStrategy(StrategyConfig(k_value=0.0))  # always buy
        engine = BacktestEngine(strategy, initial_capital=5_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        # The number of buys must equal the number of sells (position closed at end)
        buys = sum(1 for t in result.trades if t.side == "buy")
        sells = sum(1 for t in result.trades if t.side == "sell")
        assert buys == sells

    async def test_custom_initial_capital(self):
        candles = _make_candles(40)
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=2_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
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
            strategy_name="volatility_breakout",
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
        assert "volatility_breakout" in report
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


# ---------------------------------------------------------------------------
# VolatilityBreakoutStrategy — signal generation
# ---------------------------------------------------------------------------

class TestVolatilityBreakoutStrategy:
    def _make_market_data(
        self,
        current_price: float,
        prev_high: float = 50_500_000.0,
        prev_low: float = 49_500_000.0,
        current_open: float = 50_000_000.0,
    ) -> MarketData:
        return MarketData(
            market="KRW-BTC",
            candles=[
                {
                    "open": prev_high - 200_000,
                    "high": prev_high,
                    "low": prev_low,
                    "close": 50_000_000.0,
                    "volume": 10.0,
                    "timestamp": "2024-01-01T00:00:00+00:00",
                },
                {
                    "open": current_open,
                    "high": current_price + 50_000,
                    "low": current_price - 50_000,
                    "close": current_price,
                    "volume": 12.0,
                    "timestamp": "2024-01-02T00:00:00+00:00",
                },
            ],
            current_price=current_price,
        )

    async def test_buy_signal_when_price_above_target(self):
        cfg = StrategyConfig(k_value=0.5)
        strategy = VolatilityBreakoutStrategy(cfg)
        # prev range = 1_000_000, k=0.5 → target = 50_000_000 + 500_000 = 50_500_000
        data = self._make_market_data(current_price=50_600_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.BUY

    async def test_hold_signal_when_price_below_target(self):
        cfg = StrategyConfig(k_value=0.5)
        strategy = VolatilityBreakoutStrategy(cfg)
        # target = 50_500_000, price = 50_200_000 (below)
        data = self._make_market_data(current_price=50_200_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.HOLD

    async def test_hold_with_insufficient_candles(self):
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        data = MarketData(
            market="KRW-BTC",
            candles=[{"open": 1, "high": 2, "low": 0, "close": 1, "volume": 1}],
            current_price=1.0,
        )
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.HOLD

    async def test_confidence_is_in_valid_range(self):
        cfg = StrategyConfig(k_value=0.5)
        strategy = VolatilityBreakoutStrategy(cfg)
        data = self._make_market_data(current_price=50_600_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert 0.0 <= signal.confidence <= 1.0

    async def test_metadata_contains_target_price(self):
        cfg = StrategyConfig(k_value=0.5)
        strategy = VolatilityBreakoutStrategy(cfg)
        data = self._make_market_data(current_price=50_600_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert "target_price" in signal.metadata
        assert signal.metadata["k_value"] == 0.5

    def test_required_indicators_empty(self):
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        assert strategy.required_indicators() == []

    def test_required_timeframes(self):
        strategy = VolatilityBreakoutStrategy(StrategyConfig())
        assert "1d" in strategy.required_timeframes()


# ---------------------------------------------------------------------------
# RsiBollingerStrategy — signal generation
# ---------------------------------------------------------------------------

class TestRsiBollingerStrategy:
    def _make_data(self, rsi: float, price: float, bb_lower: float, bb_upper: float) -> MarketData:
        return MarketData(
            market="KRW-BTC",
            candles=[{"close": price, "open": price, "high": price, "low": price, "volume": 1}],
            current_price=price,
            indicators={
                "rsi_14": rsi,
                "bb_20_2": {
                    "bb_upper": bb_upper,
                    "bb_middle": (bb_lower + bb_upper) / 2,
                    "bb_lower": bb_lower,
                    "bb_width": (bb_upper - bb_lower) / ((bb_lower + bb_upper) / 2),
                },
            },
        )

    async def test_buy_when_rsi_oversold_and_price_below_lower_band(self):
        strategy = RsiBollingerStrategy(StrategyConfig())
        data = self._make_data(rsi=25.0, price=48_000_000.0, bb_lower=49_000_000.0, bb_upper=52_000_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.BUY

    async def test_sell_when_rsi_overbought_and_price_above_upper_band(self):
        strategy = RsiBollingerStrategy(StrategyConfig())
        data = self._make_data(rsi=75.0, price=53_000_000.0, bb_lower=48_000_000.0, bb_upper=52_000_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.SELL

    async def test_hold_when_rsi_normal(self):
        strategy = RsiBollingerStrategy(StrategyConfig())
        data = self._make_data(rsi=50.0, price=50_000_000.0, bb_lower=48_000_000.0, bb_upper=52_000_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.HOLD

    async def test_hold_when_rsi_oversold_but_price_above_lower_band(self):
        """RSI oversold but price still above lower band → HOLD."""
        strategy = RsiBollingerStrategy(StrategyConfig())
        data = self._make_data(rsi=25.0, price=50_500_000.0, bb_lower=48_000_000.0, bb_upper=52_000_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.HOLD

    async def test_hold_when_missing_indicators(self):
        strategy = RsiBollingerStrategy(StrategyConfig())
        data = MarketData(
            market="KRW-BTC",
            candles=[],
            current_price=50_000_000.0,
            indicators={},
        )
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.HOLD

    def test_required_indicators_match_config(self):
        cfg = StrategyConfig(rsi_period=14, bb_period=20, bb_std=2.0)
        strategy = RsiBollingerStrategy(cfg)
        inds = strategy.required_indicators()
        assert "rsi_14" in inds
        assert "bb_20_2" in inds

    def test_required_timeframes(self):
        strategy = RsiBollingerStrategy(StrategyConfig())
        assert "1h" in strategy.required_timeframes()


# ---------------------------------------------------------------------------
# MacdMomentumStrategy — signal generation
# ---------------------------------------------------------------------------

class TestMacdMomentumStrategy:
    def _make_data(
        self,
        macd: float,
        macd_signal: float,
        sma_20: float | None = None,
        sma_60: float | None = None,
        volume_ok: bool = True,
    ) -> MarketData:
        candles = []
        # Build 21 candles to satisfy volume check
        for i in range(21):
            vol = 15.0 if (i == 20 and volume_ok) else 5.0
            candles.append({"close": 50_000_000.0, "open": 50_000_000.0,
                            "high": 50_100_000.0, "low": 49_900_000.0, "volume": vol})
        return MarketData(
            market="KRW-BTC",
            candles=candles,
            current_price=50_000_000.0,
            indicators={
                "macd_12_26_9": {
                    "macd": macd,
                    "macd_signal": macd_signal,
                    "macd_hist": macd - macd_signal,
                },
                "sma_20": sma_20,
                "sma_60": sma_60,
            },
        )

    async def test_buy_on_golden_cross_with_trend_and_volume(self):
        strategy = MacdMomentumStrategy(StrategyConfig(volume_multiplier=1.0))
        data = self._make_data(macd=100.0, macd_signal=50.0, sma_20=51_000_000.0, sma_60=50_000_000.0, volume_ok=True)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.BUY

    async def test_hold_on_golden_cross_without_trend(self):
        """Golden cross but SMA20 < SMA60 → HOLD."""
        strategy = MacdMomentumStrategy(StrategyConfig(volume_multiplier=1.0))
        data = self._make_data(macd=100.0, macd_signal=50.0, sma_20=49_000_000.0, sma_60=50_000_000.0, volume_ok=True)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.HOLD

    async def test_sell_on_dead_cross(self):
        strategy = MacdMomentumStrategy(StrategyConfig())
        data = self._make_data(macd=-100.0, macd_signal=-50.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.SELL

    async def test_hold_when_macd_missing(self):
        strategy = MacdMomentumStrategy(StrategyConfig())
        data = MarketData(
            market="KRW-BTC",
            candles=[],
            current_price=50_000_000.0,
            indicators={},
        )
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert signal.signal == Signal.HOLD

    async def test_confidence_in_valid_range(self):
        strategy = MacdMomentumStrategy(StrategyConfig(volume_multiplier=1.0))
        data = self._make_data(macd=100.0, macd_signal=50.0, sma_20=51_000_000.0, sma_60=50_000_000.0)
        signal = await strategy.generate_signal("KRW-BTC", data)
        assert 0.0 <= signal.confidence <= 1.0

    def test_required_indicators_default(self):
        strategy = MacdMomentumStrategy(StrategyConfig())
        inds = strategy.required_indicators()
        assert "macd_12_26_9" in inds
        assert "sma_20" in inds
        assert "sma_60" in inds

    def test_required_timeframes(self):
        strategy = MacdMomentumStrategy(StrategyConfig())
        assert "1h" in strategy.required_timeframes()

    def test_strategy_name(self):
        assert MacdMomentumStrategy.name == "macd_momentum"


# ---------------------------------------------------------------------------
# Integration: full backtest with RsiBollingerStrategy
# ---------------------------------------------------------------------------

class TestBacktestIntegration:
    async def test_rsi_bollinger_backtest_runs(self):
        candles = _make_candles(60)
        strategy = RsiBollingerStrategy(StrategyConfig())
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=25)
        assert isinstance(result, BacktestResult)
        metrics = calculate_metrics(result)
        assert isinstance(metrics, PerformanceMetrics)
        report = format_report(result, metrics)
        assert "rsi_bollinger" in report

    async def test_macd_momentum_backtest_runs(self):
        candles = _make_candles(80)
        strategy = MacdMomentumStrategy(StrategyConfig(volume_multiplier=1.0))
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=30)
        assert isinstance(result, BacktestResult)
        metrics = calculate_metrics(result)
        assert isinstance(metrics, PerformanceMetrics)

    async def test_volatility_breakout_backtest_runs(self):
        candles = _make_candles(50)
        strategy = VolatilityBreakoutStrategy(StrategyConfig(k_value=0.5))
        engine = BacktestEngine(strategy, initial_capital=1_000_000.0)
        result = await engine.run("KRW-BTC", candles, warmup_bars=5)
        assert isinstance(result, BacktestResult)
        metrics = calculate_metrics(result)
        assert metrics.total_return_pct is not None
        assert not math.isnan(metrics.total_return_pct)
