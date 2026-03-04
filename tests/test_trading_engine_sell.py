"""Unit tests for TradingEngine 3-layer sell algorithm and position persistence.

Coverage:
  - HARD_STOP triggers sell when price gaps below floor
  - ATR_TRAIL trailing stop ratchets up (never down)
  - ATR_TRAIL triggers sell when price touches trailing stop
  - TIME_EXIT forces exit after max_hold_days sessions
  - positions.json roundtrip (save → load → identical)
  - Orphan position recovery creates a conservative fallback record
  - _upbit_session_date: before 09:00 KST returns previous day
  - _upbit_session_date: after 09:00 KST returns current day
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.trading_engine import (
    TradingEngine,
    _PositionInfo,
    _upbit_session_date,
)
from src.risk.base import PortfolioState, RiskDecision
from src.strategy.base import MarketData, Signal, TradeSignal

KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_strategy(
    atr_trail_mult: float = 2.0,
    hard_stop_pct: float = 0.05,
    max_hold_days: int = 5,
) -> MagicMock:
    """Return a mock strategy whose config carries sell parameters."""
    strategy = MagicMock()
    strategy.name = "trend_filtered_breakout"
    strategy.config.enabled = True
    strategy.config.markets = ["KRW-ETH"]
    strategy.config.atr_trail_mult = atr_trail_mult
    strategy.config.hard_stop_pct = hard_stop_pct
    strategy.config.max_hold_days = max_hold_days
    # Default: HOLD — prevents accidental buy during sell-focused tests
    strategy.generate_signal = AsyncMock(
        return_value=TradeSignal(
            signal=Signal.HOLD,
            market="KRW-ETH",
            confidence=0.0,
            reason="hold",
        )
    )
    strategy.on_trade_executed = AsyncMock()
    strategy.on_startup = AsyncMock()
    strategy.on_shutdown = AsyncMock()
    return strategy


def _mock_executor(sell_price: float = 3_000_000.0) -> MagicMock:
    executor = MagicMock()
    executor.get_balance = AsyncMock(return_value=100_000.0)
    executor.get_positions = AsyncMock(return_value={})
    sell_result = MagicMock()
    sell_result.success = True
    sell_result.price = sell_price
    sell_result.quantity = 0.1
    sell_result.fee = 150.0
    sell_result.order_id = "test-sell-id"
    sell_result.market = "KRW-ETH"
    sell_result.side = "sell"
    executor.execute_order = AsyncMock(return_value=sell_result)
    return executor


def _mock_risk_engine(decision: RiskDecision = RiskDecision.APPROVE) -> MagicMock:
    risk_engine = MagicMock()
    risk_engine.check = AsyncMock(return_value=(decision, []))
    return risk_engine


def _make_market_data(
    current_price: float = 3_000_000.0,
    atr: float = 90_000.0,
    market: str = "KRW-ETH",
) -> MarketData:
    return MarketData(
        market=market,
        candles=[
            {
                "open": current_price,
                "high": current_price * 1.01,
                "low": current_price * 0.99,
                "close": current_price,
                "volume": 100.0,
            }
        ],
        current_price=current_price,
        indicators={
            "atr_14": atr,
            "ema_20": current_price * 1.05,
            "ema_60": current_price * 0.95,
            "rsi_14": 55.0,
        },
    )


def _make_portfolio(
    market: str = "KRW-ETH",
    qty: float = 0.1,
    avg_price: float = 3_200_000.0,
) -> PortfolioState:
    return PortfolioState(
        total_balance=3_200_000.0 + 200_000.0,
        available_balance=200_000.0,
        positions=(
            {
                market: {
                    "quantity": qty,
                    "avg_price": avg_price,
                    "current_value": qty * avg_price,
                }
            }
            if qty > 0
            else {}
        ),
    )


def _make_engine(
    atr_trail_mult: float = 2.0,
    hard_stop_pct: float = 0.05,
    max_hold_days: int = 5,
) -> tuple[TradingEngine, MagicMock, MagicMock]:
    strategy = _mock_strategy(atr_trail_mult, hard_stop_pct, max_hold_days)
    executor = _mock_executor()
    risk_engine = _mock_risk_engine()
    engine = TradingEngine(
        strategies=[strategy],
        executor=executor,
        risk_engine=risk_engine,
        db=None,
        poll_interval=1.0,
        upbit_client=None,
        telegram=None,
        mode="paper",
    )
    return engine, strategy, executor


# ---------------------------------------------------------------------------
# Tests: _upbit_session_date (pure function — no mocking required)
# ---------------------------------------------------------------------------


def test_upbit_session_date_before_0900_kst_returns_previous_day():
    """A datetime before 09:00 KST belongs to the *previous* session day.

    Example: March 5 00:30 KST = March 4 15:30 UTC.
    Expected session date: March 4.
    """
    # 15:30 UTC on March 4 = 00:30 KST on March 5 (before 09:00)
    dt = datetime(2026, 3, 4, 15, 30, tzinfo=timezone.utc)
    result = _upbit_session_date(dt)
    assert result == date(2026, 3, 4), (
        f"Expected March 4 (previous session), got {result}"
    )


def test_upbit_session_date_after_0900_kst_returns_current_day():
    """A datetime at or after 09:00 KST belongs to *today's* session day.

    Example: March 4 10:00 KST = March 4 01:00 UTC.
    Expected session date: March 4.
    """
    # 01:00 UTC on March 4 = 10:00 KST on March 4 (after 09:00)
    dt = datetime(2026, 3, 4, 1, 0, tzinfo=timezone.utc)
    result = _upbit_session_date(dt)
    assert result == date(2026, 3, 4), (
        f"Expected March 4 (current session), got {result}"
    )


def test_upbit_session_date_exactly_0900_kst():
    """09:00 KST exactly belongs to the current session (boundary is inclusive)."""
    # 00:00 UTC on March 4 = 09:00 KST on March 4
    dt = datetime(2026, 3, 4, 0, 0, tzinfo=timezone.utc)
    result = _upbit_session_date(dt)
    assert result == date(2026, 3, 4)


# ---------------------------------------------------------------------------
# Tests: position persistence roundtrip
# ---------------------------------------------------------------------------


def test_position_persistence_roundtrip(tmp_path: Path):
    """Save positions to JSON, reload them, and verify they are identical."""
    engine, _, _ = _make_engine()
    market = "KRW-ETH"
    buy_session = date(2026, 3, 3)

    engine._positions[market] = _PositionInfo(
        entry_price=3_200_000.0,
        entry_atr=90_000.0,
        trailing_stop=3_020_000.0,
        hard_stop=3_040_000.0,
        buy_session=buy_session,
        highest_price=3_350_000.0,  # simulate a new high reached after entry
    )

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        engine._save_positions()
        assert positions_file.exists(), "positions.json was not created"

        # Load into a fresh engine
        engine2, _, _ = _make_engine()
        engine2._load_positions()  # should be a no-op (different path)

        engine2._positions.clear()
        engine2._load_positions.__func__  # ensure method exists

        # Reload manually
        raw = json.loads(positions_file.read_text())
        assert market in raw

        loaded_pinfo = _PositionInfo(
            entry_price=float(raw[market]["entry_price"]),
            entry_atr=float(raw[market]["entry_atr"]),
            trailing_stop=float(raw[market]["trailing_stop"]),
            hard_stop=float(raw[market]["hard_stop"]),
            buy_session=date.fromisoformat(raw[market]["buy_session"]),
            highest_price=float(raw[market].get("highest_price", raw[market]["entry_price"])),
        )

    original = engine._positions[market]
    assert loaded_pinfo.entry_price == original.entry_price
    assert loaded_pinfo.entry_atr == original.entry_atr
    assert loaded_pinfo.trailing_stop == original.trailing_stop
    assert loaded_pinfo.hard_stop == original.hard_stop
    assert loaded_pinfo.buy_session == original.buy_session
    assert loaded_pinfo.highest_price == original.highest_price, (
        f"highest_price must round-trip: saved {original.highest_price}, "
        f"loaded {loaded_pinfo.highest_price}"
    )


def test_save_load_positions_roundtrip_via_engine(tmp_path: Path):
    """Full engine save → engine load roundtrip using patched file path."""
    engine, _, _ = _make_engine()
    market = "KRW-BTC"
    positions_file = tmp_path / "positions.json"

    engine._positions[market] = _PositionInfo(
        entry_price=95_000_000.0,
        entry_atr=2_800_000.0,
        trailing_stop=89_400_000.0,
        hard_stop=90_250_000.0,
        buy_session=date(2026, 3, 1),
        highest_price=97_000_000.0,  # simulate a peak 2M above entry
    )

    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        engine._save_positions()

        engine2, _, _ = _make_engine()
        engine2._load_positions()  # no-op (different path default)

        # Simulate engine2 using the same patched path
        import src.core.trading_engine as te_mod
        original_file = te_mod._POSITIONS_FILE
        te_mod._POSITIONS_FILE = positions_file
        try:
            engine2._load_positions()
        finally:
            te_mod._POSITIONS_FILE = original_file

    pinfo = engine2._positions.get(market)
    assert pinfo is not None
    assert pinfo.entry_price == 95_000_000.0
    assert pinfo.buy_session == date(2026, 3, 1)
    assert pinfo.highest_price == 97_000_000.0, (
        f"highest_price must survive engine save→load: expected 97_000_000, got {pinfo.highest_price}"
    )


# ---------------------------------------------------------------------------
# Tests: sell trigger conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_stop_triggers_sell(tmp_path: Path):
    """HARD_STOP: when current_price drops below hard_stop, a sell must fire."""
    engine, strategy, executor = _make_engine(hard_stop_pct=0.05)
    market = "KRW-ETH"
    entry_price = 3_200_000.0
    hard_stop = entry_price * 0.95  # 3,040,000

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=entry_price - 2.0 * 90_000.0,  # 3,020,000
        hard_stop=hard_stop,
        buy_session=date.today() - timedelta(days=2),
        highest_price=entry_price,
    )

    # current_price is well BELOW hard_stop
    market_data = _make_market_data(current_price=2_950_000.0, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    executor.execute_order.assert_called_once()
    order_arg = executor.execute_order.call_args[0][0]
    assert order_arg.side == "sell", f"Expected sell, got {order_arg.side}"
    # Position should be cleared after successful sell
    assert market not in engine._positions


@pytest.mark.asyncio
async def test_trailing_stop_triggers_sell(tmp_path: Path):
    """ATR_TRAIL: when price falls to trailing_stop, a sell must fire."""
    engine, strategy, executor = _make_engine()
    market = "KRW-ETH"
    entry_price = 3_200_000.0
    trailing_stop = 3_100_000.0  # well above hard_stop

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=trailing_stop,
        hard_stop=entry_price * 0.95,  # 3,040,000 — lower than trailing_stop
        buy_session=date.today() - timedelta(days=2),
        highest_price=entry_price,
    )

    # current_price is BELOW trailing_stop but above hard_stop
    market_data = _make_market_data(current_price=3_080_000.0, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    executor.execute_order.assert_called_once()
    order_arg = executor.execute_order.call_args[0][0]
    assert order_arg.side == "sell"
    assert market not in engine._positions


@pytest.mark.asyncio
async def test_trailing_stop_ratchets_up_on_new_session(tmp_path: Path):
    """Trailing stop must be updated upward when price rises and session advances."""
    engine, strategy, executor = _make_engine(atr_trail_mult=2.0)
    market = "KRW-ETH"
    entry_price = 3_000_000.0
    initial_trail = entry_price - 2.0 * 90_000.0  # 2,820,000

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=initial_trail,
        hard_stop=entry_price * 0.95,
        # buy_session in the past so current_session > buy_session → update fires
        buy_session=date.today() - timedelta(days=2),
        highest_price=entry_price,  # tracking starts at entry; will rise to new_price
    )

    # Price has risen significantly — new trailing stop should be higher
    new_price = 3_300_000.0
    market_data = _make_market_data(current_price=new_price, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    # No sell should have been triggered (price above all stops)
    executor.execute_order.assert_not_called()

    # Trailing stop must have ratcheted up
    pinfo = engine._positions.get(market)
    assert pinfo is not None, "Position should still exist after non-exit tick"
    expected_new_trail = new_price - 2.0 * 90_000.0  # 3,120,000
    assert pinfo.trailing_stop >= expected_new_trail, (
        f"Expected trailing_stop ≥ {expected_new_trail:,.0f}, "
        f"got {pinfo.trailing_stop:,.0f}"
    )
    assert pinfo.trailing_stop > initial_trail, "Trailing stop must have increased"


@pytest.mark.asyncio
async def test_trailing_stop_does_not_decrease(tmp_path: Path):
    """Trailing stop must NEVER decrease even when price drops."""
    engine, strategy, executor = _make_engine()
    market = "KRW-ETH"
    entry_price = 3_200_000.0
    high_trail = 3_100_000.0  # already ratcheted up high

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=high_trail,
        hard_stop=entry_price * 0.95,
        # Same session as today → no trailing stop update this tick
        buy_session=date.today(),
        highest_price=entry_price,
    )

    # Price is still above trailing_stop but below where new trail would be
    market_data = _make_market_data(current_price=3_150_000.0, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    # No sell triggered (3,150,000 > trailing_stop 3,100,000 > hard_stop)
    executor.execute_order.assert_not_called()

    # Trailing stop must not have decreased (buy_session == today → no update)
    pinfo = engine._positions.get(market)
    assert pinfo is not None
    assert pinfo.trailing_stop == high_trail, (
        f"Trailing stop should remain {high_trail:,.0f}, got {pinfo.trailing_stop:,.0f}"
    )


@pytest.mark.asyncio
async def test_time_exit_after_max_hold_days(tmp_path: Path):
    """TIME_EXIT: position held for max_hold_days sessions must be force-closed."""
    max_hold = 5
    engine, strategy, executor = _make_engine(max_hold_days=max_hold)
    market = "KRW-ETH"
    entry_price = 3_200_000.0

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=entry_price - 2.0 * 90_000.0,  # 3,020,000
        hard_stop=entry_price * 0.95,                  # 3,040,000
        # buy_session is max_hold days ago → sessions_held == max_hold
        buy_session=date.today() - timedelta(days=max_hold),
        highest_price=entry_price,
    )

    # Current price is well above all stops (only TIME_EXIT should trigger)
    market_data = _make_market_data(current_price=3_250_000.0, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    executor.execute_order.assert_called_once()
    order_arg = executor.execute_order.call_args[0][0]
    assert order_arg.side == "sell"
    assert market not in engine._positions


@pytest.mark.asyncio
async def test_no_sell_when_price_above_all_stops(tmp_path: Path):
    """No sell should fire when price is above all stops and hold period not exceeded."""
    engine, strategy, executor = _make_engine(max_hold_days=5)
    market = "KRW-ETH"
    entry_price = 3_000_000.0

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=entry_price - 2.0 * 90_000.0,  # 2,820,000
        hard_stop=entry_price * 0.95,                  # 2,850,000
        buy_session=date.today() - timedelta(days=1),  # held 1 day out of 5
        highest_price=entry_price,
    )

    # Price is clearly above all stops
    market_data = _make_market_data(current_price=3_100_000.0, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    executor.execute_order.assert_not_called()
    assert market in engine._positions, "Position should still be open"


@pytest.mark.asyncio
async def test_orphan_position_recovery_creates_fallback(tmp_path: Path):
    """Orphan position (coin held, no entry record) gets a conservative fallback."""
    engine, strategy, _ = _make_engine()
    market = "KRW-ETH"
    current_price = 3_000_000.0

    # No entry record in engine._positions
    assert market not in engine._positions

    # But portfolio reports a position
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=current_price)
    market_data = _make_market_data(current_price=current_price, atr=90_000.0)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            # db=None → fallback path is taken
            await engine._evaluate(strategy, market, portfolio)

    # After evaluate, a fallback record must exist
    pinfo = engine._positions.get(market)
    assert pinfo is not None, "Fallback _PositionInfo must be created for orphan position"

    # Fallback should use yesterday's session to trigger TIME_EXIT soon
    today_session = _upbit_session_date(datetime.now(KST))
    assert pinfo.buy_session < today_session, (
        f"Orphan buy_session {pinfo.buy_session} should be before today {today_session}"
    )

    # Hard stop must be conservative (95% of current price)
    assert pinfo.hard_stop <= current_price * 0.96, (
        f"Hard stop {pinfo.hard_stop:,.0f} should be ≤ 96% of {current_price:,.0f}"
    )


# ---------------------------------------------------------------------------
# Tests: Fix 1 — highest_price tracking and trailing stop from peak
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_highest_price_is_updated_every_tick(tmp_path: Path):
    """highest_price must be updated on every tick when current_price rises."""
    engine, strategy, executor = _make_engine(atr_trail_mult=2.0)
    market = "KRW-ETH"
    entry_price = 3_000_000.0

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=entry_price - 2.0 * 90_000.0,
        hard_stop=entry_price * 0.95,
        buy_session=date.today(),  # same session → no trailing stop ratchet
        highest_price=entry_price,
    )

    new_price = 3_400_000.0  # price climbs well above entry
    market_data = _make_market_data(current_price=new_price, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    pinfo = engine._positions.get(market)
    assert pinfo is not None
    assert pinfo.highest_price == new_price, (
        f"highest_price should be updated to {new_price:,.0f}, got {pinfo.highest_price:,.0f}"
    )


@pytest.mark.asyncio
async def test_trailing_stop_uses_highest_price_not_current_price(tmp_path: Path):
    """Trailing stop must be anchored to highest_price, not the current (dipped) price.

    Scenario:
        Entry: 3,000,000 — peak already reached 3,500,000 (highest_price).
        Current tick: price has pulled back to 3,400,000 (still above the stop).
        Expected: trailing stop = highest_price - 2*ATR = 3,500,000 - 180,000 = 3,320,000.
        Wrong (old) behaviour: trailing stop = 3,400,000 - 180,000 = 3,220,000 (100k too low).

    We use current_price=3,400,000 which is above both the correct (3,320,000) and wrong
    (3,220,000) trailing-stop values so the position stays open and we can inspect the value.
    """
    engine, strategy, executor = _make_engine(atr_trail_mult=2.0)
    market = "KRW-ETH"
    entry_price = 3_000_000.0
    peak_price = 3_500_000.0   # highest ever seen
    atr = 90_000.0

    # Position was already at peak; price has pulled back but is still above the stop
    initial_trail = entry_price - 2.0 * atr  # 2,820,000 (set at entry)
    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=atr,
        trailing_stop=initial_trail,
        hard_stop=entry_price * 0.95,
        buy_session=date.today() - timedelta(days=1),  # new session → ratchet fires
        highest_price=peak_price,  # already tracked the peak
    )

    # 3,400,000 > expected trail (3,320,000) → no sell triggered; position stays open
    current_price = 3_400_000.0
    market_data = _make_market_data(current_price=current_price, atr=atr)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    executor.execute_order.assert_not_called(), "Price above trailing stop — no sell expected"

    pinfo = engine._positions.get(market)
    assert pinfo is not None, "Position should still be open (price above trailing stop)"

    expected_trail = peak_price - 2.0 * atr      # 3,500,000 - 180,000 = 3,320,000
    wrong_trail = current_price - 2.0 * atr       # 3,400,000 - 180,000 = 3,220,000

    assert pinfo.trailing_stop >= expected_trail, (
        f"Trailing stop should be anchored to peak ({expected_trail:,.0f}), "
        f"not to current dip ({wrong_trail:,.0f}). Got {pinfo.trailing_stop:,.0f}"
    )
    assert pinfo.trailing_stop > wrong_trail, (
        "Trailing stop must exceed the old (current-price-based) calculation"
    )


# ---------------------------------------------------------------------------
# Tests: Fix 3 — protective exit blocked on system/infra REJECT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protective_exit_blocked_when_system_error_reject(tmp_path: Path):
    """HARD_STOP protective exit must NOT execute when RiskEngine REJECT is a system error.

    If the REJECT reason contains infrastructure keywords ('insufficient', 'maintenance'
    …), overriding the rejection can cause a crash. The engine must abort and log
    instead of force-executing the order.
    """
    from src.risk.base import RiskCheckResult

    engine, strategy, executor = _make_engine(hard_stop_pct=0.05)
    market = "KRW-ETH"
    entry_price = 3_200_000.0

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=entry_price - 2.0 * 90_000.0,
        hard_stop=entry_price * 0.95,  # 3,040,000
        buy_session=date.today() - timedelta(days=1),
        highest_price=entry_price,
    )

    # Price triggers HARD_STOP
    market_data = _make_market_data(current_price=2_900_000.0, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    # Risk engine REJECTS with a system/infrastructure error reason
    infra_error_result = RiskCheckResult(
        decision=RiskDecision.REJECT,
        rule_name="some_rule",
        reason="insufficient funds to complete order",
    )
    engine.risk_engine.check = AsyncMock(
        return_value=(RiskDecision.REJECT, [infra_error_result])
    )

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    # Order must NOT have been sent — system error blocked the override
    executor.execute_order.assert_not_called(), (
        "Sell must be blocked when REJECT reason is a system/infra error"
    )


@pytest.mark.asyncio
async def test_protective_exit_overrides_policy_reject(tmp_path: Path):
    """HARD_STOP protective exit MUST override a pure policy REJECT (e.g. MDD limit).

    Policy rejections (MDD, daily-loss, position-size limits) do NOT contain
    infrastructure keywords, so the protective override should proceed normally.
    """
    from src.risk.base import RiskCheckResult

    engine, strategy, executor = _make_engine(hard_stop_pct=0.05)
    market = "KRW-ETH"
    entry_price = 3_200_000.0

    engine._positions[market] = _PositionInfo(
        entry_price=entry_price,
        entry_atr=90_000.0,
        trailing_stop=entry_price - 2.0 * 90_000.0,
        hard_stop=entry_price * 0.95,  # 3,040,000
        buy_session=date.today() - timedelta(days=1),
        highest_price=entry_price,
    )

    # Price triggers HARD_STOP
    market_data = _make_market_data(current_price=2_900_000.0, atr=90_000.0)
    portfolio = _make_portfolio(market=market, qty=0.1, avg_price=entry_price)

    # Risk engine REJECTS with a policy reason (no system-error keywords)
    policy_reject_result = RiskCheckResult(
        decision=RiskDecision.REJECT,
        rule_name="mdd_circuit_breaker",
        reason="MDD limit exceeded: current drawdown 16.0% > 15.0%",
    )
    engine.risk_engine.check = AsyncMock(
        return_value=(RiskDecision.REJECT, [policy_reject_result])
    )

    positions_file = tmp_path / "positions.json"
    with patch("src.core.trading_engine._POSITIONS_FILE", positions_file):
        with patch.object(engine, "_fetch_market_data", AsyncMock(return_value=market_data)):
            await engine._evaluate(strategy, market, portfolio)

    # Order MUST have been sent — policy reject must be overridden
    executor.execute_order.assert_called_once(), (
        "HARD_STOP protective exit must override a policy-only REJECT"
    )
    order_arg = executor.execute_order.call_args[0][0]
    assert order_arg.side == "sell"
