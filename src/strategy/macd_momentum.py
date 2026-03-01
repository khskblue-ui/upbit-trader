"""MACD + Moving Average Momentum Strategy.

Buy on MACD golden cross with trend confirmation (SMA20 > SMA60) and volume filter.
Sell on MACD dead cross.
"""

from __future__ import annotations

import logging

from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal
from src.strategy.registry import register

logger = logging.getLogger(__name__)


@register
class MacdMomentumStrategy(BaseStrategy):
    """MACD + Moving Average momentum strategy.

    Buy: MACD golden cross + SMA20 > SMA60 (uptrend) + volume spike.
    Sell: MACD dead cross.
    """

    name = "macd_momentum"
    version = "1.0.0"
    description = "MACD momentum - golden/dead cross with MA trend filter"

    def required_indicators(self) -> list[str]:
        fast = int(getattr(self.config, "fast_period", 12))
        slow = int(getattr(self.config, "slow_period", 26))
        signal = int(getattr(self.config, "signal_period", 9))
        return [f"macd_{fast}_{slow}_{signal}", "sma_20", "sma_60"]

    def required_timeframes(self) -> list[str]:
        return ["1h"]

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        fast = int(getattr(self.config, "fast_period", 12))
        slow = int(getattr(self.config, "slow_period", 26))
        signal_period = int(getattr(self.config, "signal_period", 9))
        volume_multiplier = float(getattr(self.config, "volume_multiplier", 1.5))

        macd_key = f"macd_{fast}_{slow}_{signal_period}"
        indicators = data.indicators

        if macd_key not in indicators:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=f"Missing MACD indicator: {macd_key}",
            )

        macd_data = indicators[macd_key]
        sma_20 = indicators.get("sma_20")
        sma_60 = indicators.get("sma_60")

        if macd_data is None:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason="MACD values are None (insufficient data)",
            )

        macd_line = float(macd_data["macd"])
        signal_line = float(macd_data["macd_signal"])
        macd_hist = float(macd_data["macd_hist"])

        # Trend filter: SMA20 > SMA60 = uptrend
        trend_bullish = (
            sma_20 is not None and sma_60 is not None and float(sma_20) > float(sma_60)
        )

        # Volume filter from raw candle data
        candles = data.candles
        volume_ok = True
        if len(candles) >= 21:
            recent_volume = float(candles[-1].get("volume", 0))
            avg_volume = sum(float(c.get("volume", 0)) for c in candles[-21:-1]) / 20
            volume_ok = recent_volume >= avg_volume * volume_multiplier

        metadata = {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_hist": macd_hist,
            "sma_20": sma_20,
            "sma_60": sma_60,
        }

        # BUY: golden cross + trend confirmed + volume confirmed
        if macd_hist > 0 and macd_line > signal_line and trend_bullish and volume_ok:
            hist_ratio = min(abs(macd_hist) / (abs(macd_line) + 1e-9), 1.0)
            confidence = round(min(0.9, 0.65 + hist_ratio * 0.25), 3)
            return TradeSignal(
                signal=Signal.BUY,
                market=market,
                confidence=confidence,
                reason=(
                    f"MACD golden cross: macd={macd_line:.4f} > signal={signal_line:.4f}, "
                    f"trend bullish (SMA20>SMA60), volume confirmed"
                ),
                metadata=metadata,
            )

        # SELL: dead cross
        if macd_hist < 0 and macd_line < signal_line:
            hist_ratio = min(abs(macd_hist) / (abs(macd_line) + 1e-9), 1.0)
            confidence = round(min(0.85, 0.6 + hist_ratio * 0.25), 3)
            return TradeSignal(
                signal=Signal.SELL,
                market=market,
                confidence=confidence,
                reason=(
                    f"MACD dead cross: macd={macd_line:.4f} < signal={signal_line:.4f}"
                ),
                metadata=metadata,
            )

        reason_parts = [f"MACD hist={macd_hist:.4f}"]
        if macd_hist > 0 and not trend_bullish:
            reason_parts.append("trend not bullish (SMA20<=SMA60)")
        if macd_hist > 0 and not volume_ok:
            reason_parts.append("volume insufficient")

        return TradeSignal(
            signal=Signal.HOLD,
            market=market,
            confidence=0.5,
            reason="; ".join(reason_parts),
            metadata=metadata,
        )
