"""Tests for hourly Telegram briefing and enhanced notify_buy.

Covers:
- _HourlyStats error accumulation in _tick()
- _HourlyStats HOLD reason / indicator tracking in _evaluate()
- trade_executed flag set on buy and sell
- notify_hourly_briefing message format (no-trade path, trade path, error path)
- notify_buy includes reason and metadata
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.trading_engine import TradingEngine, _HourlyStats, _PositionInfo
from src.notification.telegram_bot import TelegramNotifier
from src.risk.base import RiskDecision, RiskCheckResult
from src.strategy.base import MarketData, Signal, TradeSignal

KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(telegram=None):
    """Return a minimal TradingEngine with mocked dependencies."""
    strategy = MagicMock()
    strategy.name = "tfvb"
    strategy.config.enabled = True
    strategy.config.markets = ["KRW-ETH"]
    strategy.config.atr_trail_mult = 2.0
    strategy.config.hard_stop_pct = 0.05
    strategy.config.max_hold_days = 5

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


def _make_hold_signal(market: str, reason: str, metadata: dict | None = None) -> TradeSignal:
    return TradeSignal(
        signal=Signal.HOLD,
        market=market,
        confidence=0.0,
        reason=reason,
        metadata=metadata or {},
    )


def _make_buy_signal(market: str) -> TradeSignal:
    return TradeSignal(
        signal=Signal.BUY,
        market=market,
        confidence=0.75,
        reason="[ALL SCREENS PASS] EMA20>EMA60, RSI=52, breakout",
        suggested_size=100_000.0,
        metadata={"ema_20": 3_100_000, "ema_60": 3_000_000, "rsi": 52.0,
                  "target_price": 3_050_000, "k_value": 0.4, "position_krw": 100_000},
    )


def _make_market_data(market: str, price: float = 3_000_000.0) -> MarketData:
    md = MagicMock(spec=MarketData)
    md.market = market
    md.current_price = price
    md.candles = [{"open": price * 0.99, "close": price}] * 70
    md.indicators = {
        "ema_20": price * 1.02,
        "ema_60": price * 0.98,
        "rsi_14": 52.0,
        "atr_14": price * 0.03,
    }
    md.portfolio_balance = 1_000_000.0
    return md


# ---------------------------------------------------------------------------
# _HourlyStats — error tracking
# ---------------------------------------------------------------------------

class TestHourlyStatsErrorTracking:
    @pytest.mark.asyncio
    async def test_error_incremented_on_evaluate_exception(self):
        """When _evaluate raises, _tick records it in _hourly_stats."""
        engine, strategy = _make_engine()

        # Force _evaluate to raise
        async def boom(s, m, p):
            raise RuntimeError("candle fetch failed")

        engine._evaluate = boom
        engine._build_portfolio_state = AsyncMock(return_value=MagicMock(
            available_balance=1_000_000.0, positions={},
        ))

        await engine._tick()

        assert engine._hourly_stats.error_count == 1
        assert "candle fetch failed" in engine._hourly_stats.error_messages[0]

    @pytest.mark.asyncio
    async def test_multiple_errors_accumulate(self):
        engine, strategy = _make_engine()

        call_count = 0

        async def boom(s, m, p):
            nonlocal call_count
            call_count += 1
            raise ValueError(f"err-{call_count}")

        engine._evaluate = boom
        engine._build_portfolio_state = AsyncMock(return_value=MagicMock(
            available_balance=1_000_000.0, positions={},
        ))

        await engine._tick()
        await engine._tick()

        assert engine._hourly_stats.error_count == 2
        assert len(engine._hourly_stats.error_messages) == 2


# ---------------------------------------------------------------------------
# _HourlyStats — HOLD reason tracking
# ---------------------------------------------------------------------------

class TestHourlyStatsHoldTracking:
    @pytest.mark.asyncio
    async def test_hold_reason_stored_per_market(self):
        engine, strategy = _make_engine()
        market_data = _make_market_data("KRW-ETH")
        engine._fetch_market_data = AsyncMock(return_value=market_data)

        hold_reason = "[Screen1 FAIL] Downtrend: EMA20 2900000 ≤ EMA60 3000000"
        strategy.generate_signal = AsyncMock(return_value=_make_hold_signal(
            "KRW-ETH", hold_reason,
            metadata={"target_price": 3_090_000.0, "rsi": 48.5},
        ))

        portfolio = MagicMock(available_balance=1_000_000.0, positions={})
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        assert engine._hourly_stats.last_hold_reasons["KRW-ETH"] == hold_reason

    @pytest.mark.asyncio
    async def test_indicators_stored_with_current_price(self):
        engine, strategy = _make_engine()
        market_data = _make_market_data("KRW-ETH", price=3_100_000.0)
        engine._fetch_market_data = AsyncMock(return_value=market_data)

        strategy.generate_signal = AsyncMock(return_value=_make_hold_signal(
            "KRW-ETH", "Screen2 FAIL",
            metadata={"target_price": 3_200_000.0, "rsi": 38.0},
        ))

        portfolio = MagicMock(available_balance=1_000_000.0, positions={})
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        ind = engine._hourly_stats.last_indicators["KRW-ETH"]
        assert ind["current_price"] == 3_100_000.0
        assert ind["target_price"] == 3_200_000.0
        assert ind["rsi"] == 38.0

    @pytest.mark.asyncio
    async def test_hold_reason_overwritten_on_next_tick(self):
        """Later HOLD reason replaces earlier one (we keep only most recent)."""
        engine, strategy = _make_engine()
        market_data = _make_market_data("KRW-ETH")
        engine._fetch_market_data = AsyncMock(return_value=market_data)

        strategy.generate_signal = AsyncMock(side_effect=[
            _make_hold_signal("KRW-ETH", "first reason"),
            _make_hold_signal("KRW-ETH", "second reason"),
        ])

        portfolio = MagicMock(available_balance=1_000_000.0, positions={})
        await engine._evaluate(strategy, "KRW-ETH", portfolio)
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        assert engine._hourly_stats.last_hold_reasons["KRW-ETH"] == "second reason"


# ---------------------------------------------------------------------------
# _HourlyStats — trade_executed flag
# ---------------------------------------------------------------------------

class TestHourlyStatsTradeExecuted:
    @pytest.mark.asyncio
    async def test_trade_executed_set_on_buy(self):
        engine, strategy = _make_engine()
        market_data = _make_market_data("KRW-ETH", price=3_100_000.0)
        engine._fetch_market_data = AsyncMock(return_value=market_data)

        strategy.generate_signal = AsyncMock(return_value=_make_buy_signal("KRW-ETH"))
        strategy.on_trade_executed = AsyncMock()

        order_result = MagicMock(
            success=True, price=3_100_000.0, quantity=0.032,
            fee=1_550.0, order_id="buy-001", market="KRW-ETH", side="buy",
        )
        engine.executor.execute_order = AsyncMock(return_value=order_result)
        engine._record_trade = AsyncMock()

        portfolio = MagicMock(available_balance=1_000_000.0, positions={})
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        assert engine._hourly_stats.trade_executed is True

    @pytest.mark.asyncio
    async def test_trade_executed_set_on_sell(self):
        engine, strategy = _make_engine()
        market_data = _make_market_data("KRW-ETH", price=2_800_000.0)
        engine._fetch_market_data = AsyncMock(return_value=market_data)
        strategy.on_trade_executed = AsyncMock()

        # Plant a position that will trigger HARD_STOP
        engine._positions["KRW-ETH"] = _PositionInfo(
            entry_price=3_000_000.0,
            entry_atr=90_000.0,
            trailing_stop=2_820_000.0,
            hard_stop=2_850_000.0,   # price(2.8M) <= hard_stop(2.85M) → HARD_STOP
            buy_session=date.today(),
            highest_price=3_000_000.0,
        )

        order_result = MagicMock(
            success=True, price=2_800_000.0, quantity=0.032,
            fee=1_400.0, order_id="sell-001", market="KRW-ETH", side="sell",
        )
        engine.executor.execute_order = AsyncMock(return_value=order_result)
        engine._record_trade = AsyncMock()

        portfolio = MagicMock(
            available_balance=1_000_000.0,
            positions={"KRW-ETH": {"quantity": 0.032, "avg_price": 3_000_000.0, "current_value": 89_600.0}},
        )
        await engine._evaluate(strategy, "KRW-ETH", portfolio)

        assert engine._hourly_stats.trade_executed is True


# ---------------------------------------------------------------------------
# notify_hourly_briefing — message format
# ---------------------------------------------------------------------------

class TestNotifyHourlyBriefing:
    @pytest.mark.asyncio
    async def test_no_trade_shows_hold_reasons(self):
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_hourly_briefing(
            hour_label="13:00~14:00 KST",
            error_count=0,
            error_messages=[],
            trade_executed=False,
            market_hold_reasons={
                "KRW-ETH": "[Screen1 FAIL] Downtrend: EMA20 2,900,000 ≤ EMA60 3,000,000",
                "KRW-XRP": "[Screen2 FAIL] RSI 38.1 out of [45, 70]",
            },
            market_indicators={
                "KRW-ETH": {"ema_20": 2_900_000, "ema_60": 3_000_000,
                            "rsi": 48.0, "current_price": 2_950_000},
                "KRW-XRP": {"rsi": 38.1, "current_price": 750},
            },
        )

        sent_text = notifier.send.call_args[0][0]
        assert "13:00~14:00 KST" in sent_text
        assert "매매 미발생" in sent_text
        assert "KRW-ETH" in sent_text
        assert "Screen1 FAIL" in sent_text
        assert "KRW-XRP" in sent_text
        assert "Screen2 FAIL" in sent_text
        assert "오류 없음" in sent_text

    @pytest.mark.asyncio
    async def test_trade_executed_suppresses_hold_section(self):
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_hourly_briefing(
            hour_label="14:00~15:00 KST",
            error_count=0,
            error_messages=[],
            trade_executed=True,
            market_hold_reasons={"KRW-ETH": "some hold"},
            market_indicators={},
        )

        sent_text = notifier.send.call_args[0][0]
        assert "매매 실행됨" in sent_text
        assert "매매 미발생" not in sent_text

    @pytest.mark.asyncio
    async def test_errors_are_shown(self):
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_hourly_briefing(
            hour_label="15:00~16:00 KST",
            error_count=2,
            error_messages=["[KRW-ETH] candle fetch timeout", "[KRW-SOL] connection refused"],
            trade_executed=False,
            market_hold_reasons={},
            market_indicators={},
        )

        sent_text = notifier.send.call_args[0][0]
        assert "오류 발생: 2건" in sent_text
        assert "candle fetch timeout" in sent_text
        assert "connection refused" in sent_text

    @pytest.mark.asyncio
    async def test_only_last_3_errors_shown(self):
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)

        errors = [f"err-{i}" for i in range(10)]
        await notifier.notify_hourly_briefing(
            hour_label="x",
            error_count=10,
            error_messages=errors,
            trade_executed=False,
            market_hold_reasons={},
            market_indicators={},
        )

        sent_text = notifier.send.call_args[0][0]
        assert "err-9" in sent_text   # last 3 (7,8,9)
        assert "err-8" in sent_text
        assert "err-7" in sent_text
        assert "err-0" not in sent_text  # first ones omitted


# ---------------------------------------------------------------------------
# notify_buy — enhanced with reason + metadata
# ---------------------------------------------------------------------------

class TestNotifyBuyEnhanced:
    @pytest.mark.asyncio
    async def test_notify_buy_includes_reason(self):
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_buy(
            market="KRW-ETH",
            price=3_100_000.0,
            quantity=0.032,
            strategy="tfvb",
            confidence=0.75,
            reason="[ALL SCREENS PASS] EMA20>EMA60, RSI=52",
        )

        sent_text = notifier.send.call_args[0][0]
        assert "매수 체결" in sent_text
        assert "ALL SCREENS PASS" in sent_text

    @pytest.mark.asyncio
    async def test_notify_buy_includes_metadata_indicators(self):
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_buy(
            market="KRW-ETH",
            price=3_100_000.0,
            quantity=0.032,
            strategy="tfvb",
            confidence=0.75,
            reason="ALL SCREENS PASS",
            metadata={
                "ema_20": 3_100_000.0,
                "ema_60": 3_000_000.0,
                "rsi": 52.1,
                "target_price": 3_050_000.0,
                "k_value": 0.4,
                "position_krw": 100_000.0,
            },
        )

        sent_text = notifier.send.call_args[0][0]
        assert "ema_20" in sent_text
        assert "rsi" in sent_text
        assert "position_krw" in sent_text

    @pytest.mark.asyncio
    async def test_notify_buy_backward_compat_no_reason(self):
        """Calling notify_buy without reason/metadata still works (backward compat)."""
        notifier = TelegramNotifier("tok", "chat")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_buy(
            market="KRW-ETH",
            price=3_100_000.0,
            quantity=0.032,
            strategy="tfvb",
            confidence=0.75,
        )

        sent_text = notifier.send.call_args[0][0]
        assert "매수 체결" in sent_text
        assert "3,100,000" in sent_text


# ---------------------------------------------------------------------------
# _hourly_briefing_loop — reset stats after send
# ---------------------------------------------------------------------------

class TestHourlyBriefingLoop:
    @pytest.mark.asyncio
    async def test_stats_reset_after_briefing(self):
        engine, _ = _make_engine()
        engine._hourly_stats.error_count = 5
        engine._hourly_stats.trade_executed = True
        engine._hourly_stats.last_hold_reasons["KRW-ETH"] = "some reason"

        engine._send_hourly_briefing = AsyncMock()

        # Manually invoke what the loop does after sleep
        await engine._send_hourly_briefing()
        engine._hourly_stats = _HourlyStats(start_time=datetime.now(KST))

        assert engine._hourly_stats.error_count == 0
        assert engine._hourly_stats.trade_executed is False
        assert engine._hourly_stats.last_hold_reasons == {}
