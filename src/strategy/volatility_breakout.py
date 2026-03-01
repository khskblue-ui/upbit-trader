"""Volatility Breakout Strategy (Larry Williams).

Buy when the intraday price breaks above the previous day's range * K factor.
Sell at the next session open (handled by trading engine).
"""

from __future__ import annotations

import logging

from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal
from src.strategy.registry import register

logger = logging.getLogger(__name__)


@register
class VolatilityBreakoutStrategy(BaseStrategy):
    """Larry Williams volatility breakout strategy.

    Target price = today's open + (prev_high - prev_low) * k_value.
    Buy when current price >= target price.
    """

    name = "volatility_breakout"
    version = "1.0.0"
    description = "Larry Williams volatility breakout - buy on intraday breakout"

    def required_indicators(self) -> list[str]:
        return []  # Uses raw OHLCV only

    def required_timeframes(self) -> list[str]:
        return ["1d"]

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        candles = data.candles
        if len(candles) < 2:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason="Insufficient candle data (need at least 2 candles)",
            )

        k = float(getattr(self.config, "k_value", 0.5))

        prev = candles[-2]
        current = candles[-1]

        prev_high = float(prev["high"])
        prev_low = float(prev["low"])
        prev_range = prev_high - prev_low

        current_open = float(current["open"])
        current_price = data.current_price

        target_price = current_open + prev_range * k

        if current_price >= target_price:
            breakout_ratio = (current_price - target_price) / (prev_range + 1)
            confidence = round(min(0.9, 0.65 + breakout_ratio * 0.2), 3)
            return TradeSignal(
                signal=Signal.BUY,
                market=market,
                confidence=confidence,
                reason=(
                    f"Price {current_price:,.0f} broke above target {target_price:,.0f} "
                    f"(open={current_open:,.0f} + range={prev_range:,.0f} * k={k})"
                ),
                metadata={
                    "target_price": target_price,
                    "k_value": k,
                    "prev_range": prev_range,
                    "current_open": current_open,
                },
            )

        return TradeSignal(
            signal=Signal.HOLD,
            market=market,
            confidence=0.5,
            reason=(
                f"Price {current_price:,.0f} below target {target_price:,.0f} "
                f"(gap: {target_price - current_price:,.0f})"
            ),
            metadata={
                "target_price": target_price,
                "k_value": k,
                "prev_range": prev_range,
            },
        )
