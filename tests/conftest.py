"""Shared fixtures for the upbit-trader test suite."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest

# Allow "import src.xxx" without installing the package
sys.path.insert(0, "/Users/khs/Desktop/upbit-trader/src")

from src.risk.base import PortfolioState
from src.strategy.base import MarketData, Signal, StrategyConfig, TradeSignal


# ---------------------------------------------------------------------------
# Candle data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_candle_data() -> list[dict]:
    """Return 50 candle dicts with synthetic OHLCV data."""
    from datetime import timedelta

    candles = []
    base_price = 50_000_000.0  # KRW-BTC ~50M KRW
    base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(50):
        close = base_price + i * 100_000
        # Offset by hours to avoid exceeding month boundaries
        ts = base_ts + timedelta(hours=i)
        candles.append(
            {
                "market": "KRW-BTC",
                "timestamp": ts.isoformat(),
                "open": close - 50_000,
                "high": close + 100_000,
                "low": close - 100_000,
                "close": close,
                "volume": 10.0 + i * 0.5,
            }
        )
    return candles


# ---------------------------------------------------------------------------
# Portfolio state
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_portfolio() -> PortfolioState:
    """Return a PortfolioState with reasonable defaults."""
    return PortfolioState(
        total_balance=10_000_000.0,
        available_balance=8_000_000.0,
        positions={
            "KRW-ETH": {
                "quantity": 1.0,
                "avg_price": 2_000_000.0,
                "current_value": 2_000_000.0,
            }
        },
        daily_pnl=0.0,
        peak_balance=10_000_000.0,
        consecutive_losses=0,
    )


# ---------------------------------------------------------------------------
# Trade signal
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_trade_signal() -> TradeSignal:
    """Return a BUY TradeSignal for KRW-BTC."""
    return TradeSignal(
        signal=Signal.BUY,
        market="KRW-BTC",
        confidence=0.8,
        reason="RSI oversold",
        suggested_size=1_000_000.0,
    )
