"""Tests for execution layer: BacktestExecutor, PositionTracker, TradingEngine."""

from __future__ import annotations

import pytest

from src.execution.backtest_executor import BacktestExecutor
from src.execution.base import OrderRequest, OrderResult
from src.execution.position_tracker import PositionTracker
from src.risk.base import PortfolioState, RiskCheckResult, RiskDecision
from src.risk.engine import RiskEngine
from src.strategy.base import MarketData, Signal, StrategyConfig, TradeSignal
from src.strategy.base import BaseStrategy


# ---------------------------------------------------------------------------
# PositionTracker
# ---------------------------------------------------------------------------

class TestPositionTracker:
    def test_no_position_initially(self):
        tracker = PositionTracker()
        assert tracker.get_position("KRW-BTC") is None
        assert not tracker.has_position("KRW-BTC")

    def test_on_buy_creates_position(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", quantity=0.01, price=50_000_000.0)
        pos = tracker.get_position("KRW-BTC")
        assert pos is not None
        assert pos["quantity"] == pytest.approx(0.01)
        assert pos["avg_price"] == pytest.approx(50_000_000.0)

    def test_on_buy_adds_to_existing_position(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", 1.0, 50_000_000.0)
        tracker.on_buy("KRW-BTC", 1.0, 60_000_000.0)
        pos = tracker.get_position("KRW-BTC")
        assert pos["quantity"] == pytest.approx(2.0)
        assert pos["avg_price"] == pytest.approx(55_000_000.0)

    def test_on_sell_removes_position_when_fully_sold(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", 0.01, 50_000_000.0)
        tracker.on_sell("KRW-BTC", 0.01, 51_000_000.0)
        assert not tracker.has_position("KRW-BTC")

    def test_on_sell_returns_pnl(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", 1.0, 50_000_000.0)
        pnl = tracker.on_sell("KRW-BTC", 1.0, 55_000_000.0)
        assert pnl == pytest.approx(5_000_000.0)

    def test_on_sell_negative_pnl(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", 1.0, 50_000_000.0)
        pnl = tracker.on_sell("KRW-BTC", 1.0, 45_000_000.0)
        assert pnl == pytest.approx(-5_000_000.0)

    def test_partial_sell_reduces_position(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", 2.0, 50_000_000.0)
        tracker.on_sell("KRW-BTC", 1.0, 52_000_000.0)
        pos = tracker.get_position("KRW-BTC")
        assert pos is not None
        assert pos["quantity"] == pytest.approx(1.0)

    def test_get_all_positions(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", 0.01, 50_000_000.0)
        tracker.on_buy("KRW-ETH", 1.0, 3_000_000.0)
        all_pos = tracker.get_all_positions()
        assert "KRW-BTC" in all_pos
        assert "KRW-ETH" in all_pos

    def test_on_sell_unknown_market_returns_zero(self):
        tracker = PositionTracker()
        pnl = tracker.on_sell("KRW-UNKNOWN", 1.0, 100.0)
        assert pnl == 0.0

    def test_total_cost_basis(self):
        tracker = PositionTracker()
        tracker.on_buy("KRW-BTC", 1.0, 50_000_000.0)
        tracker.on_buy("KRW-ETH", 1.0, 3_000_000.0)
        basis = tracker.total_cost_basis()
        assert basis == pytest.approx(53_000_000.0)


# ---------------------------------------------------------------------------
# BacktestExecutor
# ---------------------------------------------------------------------------

class TestBacktestExecutor:
    async def test_initial_balance(self):
        executor = BacktestExecutor(initial_capital=5_000_000.0)
        balance = await executor.get_balance("KRW")
        assert balance == pytest.approx(5_000_000.0)

    async def test_buy_order_reduces_balance(self):
        executor = BacktestExecutor(initial_capital=1_000_000.0, fee_rate=0.0, slippage_rate=0.0)
        order = OrderRequest(market="KRW-BTC", side="buy", price=50_000_000.0, quantity=500_000.0)
        result = await executor.execute_order(order)
        assert result.success is True
        balance = await executor.get_balance("KRW")
        assert balance < 1_000_000.0

    async def test_buy_creates_position(self):
        executor = BacktestExecutor(initial_capital=2_000_000.0, fee_rate=0.0, slippage_rate=0.0)
        order = OrderRequest(market="KRW-BTC", side="buy", price=50_000_000.0, quantity=1_000_000.0)
        await executor.execute_order(order)
        positions = await executor.get_positions()
        assert "KRW-BTC" in positions
        assert positions["KRW-BTC"]["quantity"] > 0

    async def test_sell_order_increases_balance(self):
        executor = BacktestExecutor(initial_capital=2_000_000.0, fee_rate=0.0, slippage_rate=0.0)
        # Buy first
        buy = OrderRequest(market="KRW-BTC", side="buy", price=50_000_000.0, quantity=1_000_000.0)
        await executor.execute_order(buy)
        balance_after_buy = await executor.get_balance("KRW")
        # Sell
        positions = await executor.get_positions()
        qty = positions["KRW-BTC"]["quantity"]
        sell = OrderRequest(market="KRW-BTC", side="sell", price=55_000_000.0, quantity=qty)
        result = await executor.execute_order(sell)
        assert result.success is True
        balance_after_sell = await executor.get_balance("KRW")
        assert balance_after_sell > balance_after_buy

    async def test_sell_without_position_fails(self):
        executor = BacktestExecutor(initial_capital=1_000_000.0)
        order = OrderRequest(market="KRW-BTC", side="sell", price=50_000_000.0, quantity=0.01)
        result = await executor.execute_order(order)
        assert result.success is False
        assert result.error is not None

    async def test_buy_with_insufficient_funds_fails(self):
        executor = BacktestExecutor(initial_capital=100.0)  # too small
        order = OrderRequest(market="KRW-BTC", side="buy", price=50_000_000.0, quantity=1_000_000.0)
        result = await executor.execute_order(order)
        assert result.success is False

    async def test_order_result_has_correct_fields(self):
        executor = BacktestExecutor(initial_capital=2_000_000.0, fee_rate=0.0, slippage_rate=0.0)
        order = OrderRequest(market="KRW-BTC", side="buy", price=50_000_000.0, quantity=500_000.0)
        result = await executor.execute_order(order)
        assert isinstance(result, OrderResult)
        assert result.market == "KRW-BTC"
        assert result.side == "buy"
        assert result.order_id is not None
        assert result.quantity > 0

    async def test_fee_deducted_on_buy(self):
        executor = BacktestExecutor(
            initial_capital=1_000_000.0, fee_rate=0.001, slippage_rate=0.0
        )
        order = OrderRequest(market="KRW-BTC", side="buy", price=50_000_000.0, quantity=500_000.0)
        result = await executor.execute_order(order)
        assert result.fee > 0

    async def test_get_positions_returns_empty_initially(self):
        executor = BacktestExecutor(initial_capital=1_000_000.0)
        positions = await executor.get_positions()
        assert positions == {}


# ---------------------------------------------------------------------------
# TradingEngine
# ---------------------------------------------------------------------------

class _AlwaysBuyStrategy(BaseStrategy):
    """Test strategy that always returns BUY."""
    name = "_always_buy"

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        return TradeSignal(
            signal=Signal.BUY,
            market=market,
            confidence=0.9,
            reason="test",
            suggested_size=100_000.0,
        )

    def required_indicators(self) -> list[str]:
        return []

    def required_timeframes(self) -> list[str]:
        return ["1h"]


class _AlwaysApproveRule:
    """Minimal risk rule that always approves."""
    name = "_always_approve"
    enabled = True

    async def evaluate(self, signal, portfolio) -> RiskCheckResult:
        return RiskCheckResult(
            decision=RiskDecision.APPROVE,
            rule_name=self.name,
            reason="test approve",
        )


class _AlwaysRejectRule:
    """Minimal risk rule that always rejects."""
    name = "_always_reject"
    enabled = True

    async def evaluate(self, signal, portfolio) -> RiskCheckResult:
        return RiskCheckResult(
            decision=RiskDecision.REJECT,
            rule_name=self.name,
            reason="test reject",
        )


class TestTradingEngine:
    def _make_engine(self, strategy=None, reject=False):
        from src.core.trading_engine import TradingEngine

        cfg = StrategyConfig(markets=["KRW-BTC"])
        strat = strategy or _AlwaysBuyStrategy(cfg)
        executor = BacktestExecutor(initial_capital=2_000_000.0)
        rule = _AlwaysRejectRule() if reject else _AlwaysApproveRule()
        risk = RiskEngine([rule])
        return TradingEngine(
            strategies=[strat],
            executor=executor,
            risk_engine=risk,
            poll_interval=0.0,
        )

    async def test_start_calls_on_startup(self):
        engine = self._make_engine()
        startup_called = []

        original = engine.strategies[0].on_startup
        async def patched():
            startup_called.append(True)
            await original()
        engine.strategies[0].on_startup = patched

        await engine.start()
        assert startup_called

    async def test_stop_calls_on_shutdown(self):
        engine = self._make_engine()
        shutdown_called = []

        original = engine.strategies[0].on_shutdown
        async def patched():
            shutdown_called.append(True)
            await original()
        engine.strategies[0].on_shutdown = patched

        await engine.start()
        await engine.stop()
        assert shutdown_called

    async def test_tick_does_not_raise(self):
        """_tick() should complete without error even with mocked market data."""
        engine = self._make_engine()
        await engine.start()
        await engine._tick()  # _fetch_market_data returns None → skipped gracefully

    async def test_build_portfolio_state_returns_portfolio(self):
        engine = self._make_engine()
        portfolio = await engine._build_portfolio_state()
        assert isinstance(portfolio, PortfolioState)
        assert portfolio.available_balance >= 0

    async def test_signal_to_order_buy(self):
        from src.core.trading_engine import TradingEngine
        signal = TradeSignal(
            signal=Signal.BUY,
            market="KRW-BTC",
            confidence=0.8,
            reason="test",
            suggested_size=500_000.0,
        )
        order = TradingEngine._signal_to_order(signal)
        assert order.side == "buy"
        assert order.market == "KRW-BTC"
        assert order.quantity == 500_000.0

    async def test_signal_to_order_sell(self):
        from src.core.trading_engine import TradingEngine
        signal = TradeSignal(
            signal=Signal.SELL,
            market="KRW-ETH",
            confidence=0.7,
            reason="test",
        )
        order = TradingEngine._signal_to_order(signal)
        assert order.side == "sell"

    async def test_disabled_strategy_is_skipped(self):
        from src.core.trading_engine import TradingEngine
        cfg = StrategyConfig(enabled=False, markets=["KRW-BTC"])
        strat = _AlwaysBuyStrategy(cfg)
        executor = BacktestExecutor(initial_capital=2_000_000.0)
        risk = RiskEngine([_AlwaysApproveRule()])
        engine = TradingEngine(strategies=[strat], executor=executor, risk_engine=risk)
        await engine.start()
        # _tick should not call generate_signal for disabled strategies
        balance_before = await executor.get_balance("KRW")
        await engine._tick()
        balance_after = await executor.get_balance("KRW")
        # Balance unchanged because strategy is disabled (skipped)
        assert balance_before == balance_after
