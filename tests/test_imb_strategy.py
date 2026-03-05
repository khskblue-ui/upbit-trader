"""Tests for IntradayMomentumBreakoutStrategy (IMB) and engine extensions.

Covers:
- IMB signal generation (warmup guard, 3-screen logic, BUY signal)
- Percent-based trailing stop (updates every tick, not per session)
- Hours-based time exit (max_hold_hours)
- buy_datetime set on position creation
- _PositionInfo persistence round-trip with buy_datetime
- /switchstrategy command handler
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.strategy.intraday_momentum_breakout import IntradayMomentumBreakoutStrategy
from src.strategy.base import MarketData, Signal, StrategyConfig, TradeSignal
from src.core.trading_engine import TradingEngine, _HourlyStats, _PositionInfo
from src.risk.base import RiskDecision, RiskCheckResult
from src.notification.command_handler import TelegramCommandHandler
from src.notification.telegram_bot import TelegramNotifier

KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_imb_config(**overrides) -> StrategyConfig:
    params = dict(
        k_value=1.5,
        atr_risk_pct=0.01,
        rsi_min=50.0,
        rsi_max=75.0,
        hard_stop_pct=0.03,
        trailing_stop_pct=0.03,
        max_hold_hours=24.0,
        base_capital=1_000_000.0,
        markets=["KRW-ETH"],
        timeframe="60m",
    )
    params.update(overrides)
    return StrategyConfig(**params)


def _make_market_data(
    price: float = 3_000_000.0,
    ema_24: float | None = None,
    ema_120: float | None = None,
    rsi: float = 60.0,
    atr: float = 30_000.0,
    candle_count: int = 130,
    open_price: float | None = None,
) -> MarketData:
    """Create a MarketData stub for IMB testing."""
    if ema_24 is None:
        ema_24 = price * 1.02   # uptrend by default
    if ema_120 is None:
        ema_120 = price * 0.98
    if open_price is None:
        open_price = price * 0.99

    candles = [{"open": open_price, "close": price, "high": price, "low": open_price * 0.995, "volume": 100}] * candle_count
    md = MagicMock(spec=MarketData)
    md.market = "KRW-ETH"
    md.current_price = price
    md.candles = candles
    md.indicators = {
        "ema_24": ema_24,
        "ema_120": ema_120,
        "rsi_14": rsi,
        "atr_24": atr,
    }
    md.portfolio_balance = 1_000_000.0
    return md


def _make_engine_imb(telegram=None):
    """Return a TradingEngine with an IMB-style strategy mock."""
    strategy = MagicMock()
    strategy.name = "intraday_momentum_breakout"
    strategy.config = _make_imb_config()
    strategy.config.enabled = True
    strategy.required_indicators = MagicMock(return_value=["ema_24", "ema_120", "rsi_14", "atr_24"])

    executor = AsyncMock()
    executor.get_balance = AsyncMock(return_value=1_000_000.0)
    executor.get_positions = AsyncMock(return_value={})

    risk_engine = AsyncMock()
    risk_engine.check = AsyncMock(return_value=(
        RiskDecision.APPROVE,
        [RiskCheckResult(decision=RiskDecision.APPROVE, rule_name="test", reason="ok")],
    ))

    engine = TradingEngine(
        strategies=[strategy],
        executor=executor,
        risk_engine=risk_engine,
        telegram=telegram,
        mode="paper",
    )
    return engine, strategy


# ---------------------------------------------------------------------------
# IMB Strategy — signal generation
# ---------------------------------------------------------------------------

class TestIMBSignalGeneration:
    @pytest.mark.asyncio
    async def test_warmup_guard_hold(self):
        """Returns HOLD when fewer than 125 candles are available."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        md = _make_market_data(candle_count=50)
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.HOLD
        assert "Warmup" in signal.reason

    @pytest.mark.asyncio
    async def test_screen1_fail_downtrend(self):
        """Returns HOLD with Screen1 FAIL when EMA24 ≤ EMA120."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        price = 3_000_000.0
        md = _make_market_data(
            price=price,
            ema_24=price * 0.98,   # below EMA120
            ema_120=price * 1.00,
        )
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.HOLD
        assert "Screen1 FAIL" in signal.reason
        assert "Downtrend" in signal.reason

    @pytest.mark.asyncio
    async def test_screen2_fail_rsi_too_low(self):
        """Returns HOLD when RSI < rsi_min (50)."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        md = _make_market_data(rsi=45.0)
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.HOLD
        assert "Screen2 FAIL" in signal.reason
        assert "weak momentum" in signal.reason

    @pytest.mark.asyncio
    async def test_screen2_fail_rsi_too_high(self):
        """Returns HOLD when RSI > rsi_max (75)."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        md = _make_market_data(rsi=80.0)
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.HOLD
        assert "Screen2 FAIL" in signal.reason
        assert "overbought" in signal.reason

    @pytest.mark.asyncio
    async def test_screen3_fail_no_breakout(self):
        """Returns HOLD when price < open + ATR_24 * k."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        # price = 3_000_000, open = 2_990_000, atr = 30_000, k = 1.5
        # target = 2_990_000 + 30_000 * 1.5 = 3_035_000 > price → HOLD
        md = _make_market_data(
            price=3_000_000.0,
            open_price=2_990_000.0,
            atr=30_000.0,
            rsi=62.0,
        )
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.HOLD
        assert "Screen3 FAIL" in signal.reason
        assert "No breakout" in signal.reason

    @pytest.mark.asyncio
    async def test_all_screens_pass_buy_signal(self):
        """All screens pass → BUY with reason, confidence, metadata."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        # price = 3_100_000, open = 2_990_000, atr = 30_000, k = 1.5
        # target = 2_990_000 + 45_000 = 3_035_000 < price → breakout!
        md = _make_market_data(
            price=3_100_000.0,
            open_price=2_990_000.0,
            atr=30_000.0,
            rsi=62.0,
        )
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.BUY
        assert "ALL SCREENS PASS" in signal.reason
        assert signal.confidence > 0.6
        assert "ema_24" in signal.metadata
        assert "ema_120" in signal.metadata
        assert "rsi" in signal.metadata
        assert "target_price" in signal.metadata
        assert "position_krw" in signal.metadata

    @pytest.mark.asyncio
    async def test_position_sizing_capped_at_20pct(self):
        """Position size is never > 20% of effective capital."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config(atr_risk_pct=0.50))
        md = _make_market_data(
            price=3_100_000.0,
            open_price=2_990_000.0,
            atr=30_000.0,
            rsi=62.0,
        )
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.BUY
        assert signal.metadata["position_krw"] <= 1_000_000.0 * 0.20

    @pytest.mark.asyncio
    async def test_missing_indicators_hold(self):
        """Returns HOLD when any indicator is None."""
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        md = _make_market_data()
        md.indicators = {"ema_24": 3_060_000.0}  # missing ema_120, rsi_14, atr_24
        signal = await strat.generate_signal("KRW-ETH", md)
        assert signal.signal == Signal.HOLD
        assert "Missing indicators" in signal.reason

    def test_required_indicators(self):
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        assert strat.required_indicators() == ["ema_24", "ema_120", "rsi_14", "atr_24"]

    def test_required_timeframes(self):
        strat = IntradayMomentumBreakoutStrategy(config=_make_imb_config())
        assert strat.required_timeframes() == ["60m"]


# ---------------------------------------------------------------------------
# Engine — percent-based trailing stop
# ---------------------------------------------------------------------------

class TestPercentBasedTrailingStop:
    @pytest.mark.asyncio
    async def test_pct_trailing_stop_updates_every_tick(self):
        """Percent-based trailing stop updates on every tick, not per session."""
        engine, strategy = _make_engine_imb()
        market_data = _make_market_data(price=3_200_000.0)
        engine._fetch_market_data = AsyncMock(return_value=market_data)

        # Plant a position with trailing_stop_pct=0.03 (3%)
        engine._positions["KRW-ETH"] = _PositionInfo(
            entry_price=3_000_000.0,
            entry_atr=30_000.0,
            trailing_stop=2_910_000.0,    # old trail
            hard_stop=2_910_000.0,
            buy_session=date.today(),      # same session → ATR trail would NOT update
            highest_price=3_000_000.0,
        )

        strategy.generate_signal = AsyncMock(return_value=TradeSignal(
            signal=Signal.HOLD, market="KRW-ETH", confidence=0.0, reason="hold"
        ))
        portfolio = MagicMock(
            available_balance=1_000_000.0,
            positions={"KRW-ETH": {"quantity": 0.03, "avg_price": 3_000_000.0, "current_value": 90_000.0}},
        )
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        # highest_price updated to 3_200_000; new trail = 3_200_000 * 0.97 = 3_104_000
        pinfo = engine._positions["KRW-ETH"]
        assert pinfo.highest_price == 3_200_000.0
        assert abs(pinfo.trailing_stop - 3_200_000.0 * 0.97) < 1.0

    @pytest.mark.asyncio
    async def test_pct_trailing_stop_triggers_pct_trail_label(self):
        """Exit label is PCT_TRAIL when trailing_stop_pct > 0."""
        engine, strategy = _make_engine_imb()

        current_price = 2_850_000.0
        market_data = _make_market_data(price=current_price)
        engine._fetch_market_data = AsyncMock(return_value=market_data)
        strategy.on_trade_executed = AsyncMock()

        # trailing_stop above current_price → triggers PCT_TRAIL exit
        engine._positions["KRW-ETH"] = _PositionInfo(
            entry_price=3_000_000.0,
            entry_atr=30_000.0,
            trailing_stop=2_900_000.0,   # above current_price → sell
            hard_stop=2_700_000.0,
            buy_session=date.today(),
            highest_price=3_000_000.0,
            buy_datetime=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        order_result = MagicMock(
            success=True, price=current_price, quantity=0.03,
            fee=1_425.0, order_id="sell-001", market="KRW-ETH", side="sell",
        )
        engine.executor.execute_order = AsyncMock(return_value=order_result)
        engine._record_trade = AsyncMock()

        portfolio = MagicMock(
            available_balance=1_000_000.0,
            positions={"KRW-ETH": {"quantity": 0.03, "avg_price": 3_000_000.0, "current_value": 85_500.0}},
        )
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        # Position should be cleared after sell
        assert "KRW-ETH" not in engine._positions
        assert engine._hourly_stats.trade_executed is True


# ---------------------------------------------------------------------------
# Engine — hours-based time exit
# ---------------------------------------------------------------------------

class TestHoursBasedTimeExit:
    @pytest.mark.asyncio
    async def test_time_exit_fires_after_max_hold_hours(self):
        """TIME_EXIT triggers when hours_held >= max_hold_hours."""
        engine, strategy = _make_engine_imb()

        current_price = 3_050_000.0
        market_data = _make_market_data(price=current_price)
        engine._fetch_market_data = AsyncMock(return_value=market_data)
        strategy.on_trade_executed = AsyncMock()

        # Position held for 25 hours (> max_hold_hours=24)
        engine._positions["KRW-ETH"] = _PositionInfo(
            entry_price=3_000_000.0,
            entry_atr=30_000.0,
            trailing_stop=2_910_000.0,   # below current → no trail exit
            hard_stop=2_910_000.0,        # below current → no hard stop
            buy_session=date.today(),
            highest_price=3_100_000.0,
            buy_datetime=datetime.now(timezone.utc) - timedelta(hours=25),
        )

        order_result = MagicMock(
            success=True, price=current_price, quantity=0.03,
            fee=1_500.0, order_id="sell-time", market="KRW-ETH", side="sell",
        )
        engine.executor.execute_order = AsyncMock(return_value=order_result)
        engine._record_trade = AsyncMock()

        portfolio = MagicMock(
            available_balance=1_000_000.0,
            positions={"KRW-ETH": {"quantity": 0.03, "avg_price": 3_000_000.0, "current_value": 91_500.0}},
        )
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        assert "KRW-ETH" not in engine._positions

    @pytest.mark.asyncio
    async def test_time_exit_does_not_fire_before_max_hours(self):
        """TIME_EXIT does NOT trigger when hours_held < max_hold_hours."""
        engine, strategy = _make_engine_imb()

        current_price = 3_050_000.0
        market_data = _make_market_data(price=current_price)
        engine._fetch_market_data = AsyncMock(return_value=market_data)

        # Position held for 10 hours (< max_hold_hours=24)
        engine._positions["KRW-ETH"] = _PositionInfo(
            entry_price=3_000_000.0,
            entry_atr=30_000.0,
            trailing_stop=2_910_000.0,   # below current → no trail exit
            hard_stop=2_910_000.0,        # below current → no hard stop
            buy_session=date.today(),
            highest_price=3_100_000.0,
            buy_datetime=datetime.now(timezone.utc) - timedelta(hours=10),
        )

        strategy.generate_signal = AsyncMock(return_value=TradeSignal(
            signal=Signal.HOLD, market="KRW-ETH", confidence=0.0, reason="hold"
        ))

        portfolio = MagicMock(
            available_balance=1_000_000.0,
            positions={"KRW-ETH": {"quantity": 0.03, "avg_price": 3_000_000.0, "current_value": 91_500.0}},
        )
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        # Position still open
        assert "KRW-ETH" in engine._positions

    @pytest.mark.asyncio
    async def test_hours_exit_skipped_when_buy_datetime_is_none(self):
        """Hours-based exit does NOT fire when buy_datetime is None (backward compat)."""
        engine, strategy = _make_engine_imb()

        current_price = 3_050_000.0
        market_data = _make_market_data(price=current_price)
        engine._fetch_market_data = AsyncMock(return_value=market_data)

        engine._positions["KRW-ETH"] = _PositionInfo(
            entry_price=3_000_000.0,
            entry_atr=30_000.0,
            trailing_stop=2_910_000.0,
            hard_stop=2_910_000.0,
            buy_session=date.today(),
            highest_price=3_100_000.0,
            buy_datetime=None,   # old position without buy_datetime
        )

        strategy.generate_signal = AsyncMock(return_value=TradeSignal(
            signal=Signal.HOLD, market="KRW-ETH", confidence=0.0, reason="hold"
        ))

        portfolio = MagicMock(
            available_balance=1_000_000.0,
            positions={"KRW-ETH": {"quantity": 0.03, "avg_price": 3_000_000.0, "current_value": 91_500.0}},
        )
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        # Position still open (hours exit skipped because buy_datetime is None)
        assert "KRW-ETH" in engine._positions


# ---------------------------------------------------------------------------
# Engine — buy_datetime set on position creation
# ---------------------------------------------------------------------------

class TestBuyDatetimeOnPositionCreation:
    @pytest.mark.asyncio
    async def test_buy_datetime_set_on_buy(self):
        """buy_datetime is set (UTC) when a BUY order executes."""
        engine, strategy = _make_engine_imb()
        market_data = _make_market_data(price=3_100_000.0)
        engine._fetch_market_data = AsyncMock(return_value=market_data)
        strategy.on_trade_executed = AsyncMock()

        strategy.generate_signal = AsyncMock(return_value=TradeSignal(
            signal=Signal.BUY,
            market="KRW-ETH",
            confidence=0.75,
            reason="[ALL SCREENS PASS]",
            suggested_size=100_000.0,
            metadata={"ema_24": 3_060_000, "ema_120": 2_940_000, "rsi": 62.0,
                      "target_price": 3_050_000, "k_value": 1.5, "position_krw": 100_000},
        ))

        order_result = MagicMock(
            success=True, price=3_100_000.0, quantity=0.032,
            fee=1_550.0, order_id="buy-001", market="KRW-ETH", side="buy",
        )
        engine.executor.execute_order = AsyncMock(return_value=order_result)
        engine._record_trade = AsyncMock()

        before = datetime.now(timezone.utc)
        portfolio = MagicMock(available_balance=1_000_000.0, positions={})
        await engine._evaluate(strategy, "KRW-ETH", portfolio)
        after = datetime.now(timezone.utc)

        assert "KRW-ETH" in engine._positions
        pinfo = engine._positions["KRW-ETH"]
        assert pinfo.buy_datetime is not None
        assert before <= pinfo.buy_datetime <= after

    @pytest.mark.asyncio
    async def test_initial_trailing_stop_is_pct_based_for_imb(self):
        """IMB positions get percent-based initial trailing stop."""
        engine, strategy = _make_engine_imb()
        market_data = _make_market_data(price=3_100_000.0)
        engine._fetch_market_data = AsyncMock(return_value=market_data)
        strategy.on_trade_executed = AsyncMock()

        strategy.generate_signal = AsyncMock(return_value=TradeSignal(
            signal=Signal.BUY,
            market="KRW-ETH",
            confidence=0.75,
            reason="[ALL SCREENS PASS]",
            suggested_size=100_000.0,
            metadata={},
        ))

        order_result = MagicMock(
            success=True, price=3_100_000.0, quantity=0.032,
            fee=1_550.0, order_id="buy-001", market="KRW-ETH", side="buy",
        )
        engine.executor.execute_order = AsyncMock(return_value=order_result)
        engine._record_trade = AsyncMock()

        portfolio = MagicMock(available_balance=1_000_000.0, positions={})
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        pinfo = engine._positions["KRW-ETH"]
        # trailing_stop_pct = 0.03 → initial = 3_100_000 * 0.97 = 3_007_000
        expected_trail = 3_100_000.0 * (1.0 - 0.03)
        assert abs(pinfo.trailing_stop - expected_trail) < 1.0


# ---------------------------------------------------------------------------
# Engine — _PositionInfo persistence with buy_datetime
# ---------------------------------------------------------------------------

class TestPositionPersistenceWithBuyDatetime:
    def test_save_and_load_roundtrip(self, tmp_path):
        """buy_datetime survives a JSON save/load cycle."""
        import src.core.trading_engine as engine_module
        original_file = engine_module._POSITIONS_FILE
        engine_module._POSITIONS_FILE = tmp_path / "positions.json"

        try:
            engine, _ = _make_engine_imb()
            now_utc = datetime(2025, 3, 1, 12, 30, 0, tzinfo=timezone.utc)
            engine._positions["KRW-ETH"] = _PositionInfo(
                entry_price=3_000_000.0,
                entry_atr=30_000.0,
                trailing_stop=2_910_000.0,
                hard_stop=2_910_000.0,
                buy_session=date(2025, 3, 1),
                highest_price=3_100_000.0,
                buy_datetime=now_utc,
            )
            engine._save_positions()

            # Load into a new engine
            engine2, _ = _make_engine_imb()
            engine2._load_positions()
            loaded = engine2._positions["KRW-ETH"]

            assert loaded.buy_datetime is not None
            assert loaded.buy_datetime == now_utc
            assert loaded.highest_price == 3_100_000.0
        finally:
            engine_module._POSITIONS_FILE = original_file

    def test_load_old_snapshot_without_buy_datetime(self, tmp_path):
        """Old snapshots without buy_datetime load with buy_datetime=None."""
        import src.core.trading_engine as engine_module
        original_file = engine_module._POSITIONS_FILE
        engine_module._POSITIONS_FILE = tmp_path / "positions.json"

        try:
            # Write a snapshot without buy_datetime field
            snapshot = {
                "KRW-ETH": {
                    "entry_price": 3_000_000.0,
                    "entry_atr": 30_000.0,
                    "trailing_stop": 2_910_000.0,
                    "hard_stop": 2_910_000.0,
                    "buy_session": "2025-03-01",
                    "highest_price": 3_100_000.0,
                    # No buy_datetime field — simulates old snapshot
                }
            }
            (tmp_path / "positions.json").write_text(json.dumps(snapshot))

            engine, _ = _make_engine_imb()
            engine._load_positions()
            loaded = engine._positions["KRW-ETH"]

            assert loaded.buy_datetime is None
        finally:
            engine_module._POSITIONS_FILE = original_file


# ---------------------------------------------------------------------------
# /switchstrategy command handler
# ---------------------------------------------------------------------------

class TestSwitchStrategyCommand:
    def _make_handler(self):
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)
        notifier.notify_strategy_changed = AsyncMock(return_value=True)

        tfvb = MagicMock()
        tfvb.name = "trend_filtered_breakout"
        tfvb.config = MagicMock()
        tfvb.config.enabled = True

        imb = MagicMock()
        imb.name = "intraday_momentum_breakout"
        imb.config = MagicMock()
        imb.config.enabled = False

        engine = MagicMock()
        executor = AsyncMock()
        handler = TelegramCommandHandler(
            notifier=notifier,
            engine=engine,
            executor=executor,
            strategies=[tfvb, imb],
            mode="paper",
            stop_callback=lambda: None,
            authorized_chat_id="chat",
        )
        return handler, notifier, tfvb, imb

    @pytest.mark.asyncio
    async def test_switchstrategy_imb_alias(self):
        """'/switchstrategy imb' enables IMB and disables TFVB."""
        handler, notifier, tfvb, imb = self._make_handler()
        await handler._cmd_switchstrategy(["imb"])

        assert imb.config.enabled is True
        assert tfvb.config.enabled is False
        sent = notifier.send.call_args[0][0]
        assert "intraday_momentum_breakout" in sent

    @pytest.mark.asyncio
    async def test_switchstrategy_tfvb_alias(self):
        """'/switchstrategy tfvb' enables TFVB and disables IMB."""
        handler, notifier, tfvb, imb = self._make_handler()
        imb.config.enabled = True
        tfvb.config.enabled = False

        await handler._cmd_switchstrategy(["tfvb"])

        assert tfvb.config.enabled is True
        assert imb.config.enabled is False
        sent = notifier.send.call_args[0][0]
        assert "trend_filtered_breakout" in sent

    @pytest.mark.asyncio
    async def test_switchstrategy_unknown_alias(self):
        """Unknown alias returns usage message."""
        handler, notifier, tfvb, imb = self._make_handler()
        await handler._cmd_switchstrategy(["unknown"])
        sent = notifier.send.call_args[0][0]
        assert "알 수 없는 전략" in sent

    @pytest.mark.asyncio
    async def test_switchstrategy_no_args(self):
        """No args returns list of available strategies."""
        handler, notifier, tfvb, imb = self._make_handler()
        await handler._cmd_switchstrategy([])
        sent = notifier.send.call_args[0][0]
        assert "switchstrategy" in sent
        assert "tfvb" in sent
        assert "imb" in sent

    @pytest.mark.asyncio
    async def test_switchstrategy_not_loaded(self):
        """Reports error when target strategy is not loaded."""
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)
        engine = MagicMock()
        executor = AsyncMock()
        # Only TFVB loaded, no IMB
        tfvb = MagicMock()
        tfvb.name = "trend_filtered_breakout"
        tfvb.config = MagicMock()
        tfvb.config.enabled = True

        handler = TelegramCommandHandler(
            notifier=notifier,
            engine=engine,
            executor=executor,
            strategies=[tfvb],
            mode="paper",
            stop_callback=lambda: None,
            authorized_chat_id="chat",
        )
        await handler._cmd_switchstrategy(["imb"])
        sent = notifier.send.call_args[0][0]
        assert "로드되지 않음" in sent
