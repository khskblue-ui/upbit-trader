"""RSI + Bollinger Bands Mean Reversion Strategy.

Buy when RSI is oversold AND price is at or below the lower Bollinger Band.
Sell when RSI is overbought AND price is at or above the upper Bollinger Band.
"""

from __future__ import annotations

import logging

from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal
from src.strategy.registry import register

logger = logging.getLogger(__name__)


@register
class RsiBollingerStrategy(BaseStrategy):
    """RSI + Bollinger Bands mean reversion strategy.

    Buy on RSI oversold + price at lower band.
    Sell on RSI overbought + price at upper band.
    """

    name = "rsi_bollinger"
    version = "1.0.0"
    description = "RSI + Bollinger Bands mean reversion - buy oversold, sell overbought"

    def required_indicators(self) -> list[str]:
        rsi_period = int(getattr(self.config, "rsi_period", 14))
        bb_period = int(getattr(self.config, "bb_period", 20))
        bb_std = float(getattr(self.config, "bb_std", 2.0))
        std_str = str(int(bb_std)) if bb_std == int(bb_std) else str(bb_std)
        return [f"rsi_{rsi_period}", f"bb_{bb_period}_{std_str}"]

    def required_timeframes(self) -> list[str]:
        return ["1h"]

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        rsi_period = int(getattr(self.config, "rsi_period", 14))
        bb_period = int(getattr(self.config, "bb_period", 20))
        bb_std = float(getattr(self.config, "bb_std", 2.0))
        rsi_oversold = float(getattr(self.config, "rsi_oversold", 30))
        rsi_overbought = float(getattr(self.config, "rsi_overbought", 70))

        std_str = str(int(bb_std)) if bb_std == int(bb_std) else str(bb_std)
        rsi_key = f"rsi_{rsi_period}"
        bb_key = f"bb_{bb_period}_{std_str}"

        indicators = data.indicators

        if rsi_key not in indicators or bb_key not in indicators:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=f"Missing indicators: need {rsi_key} and {bb_key}",
            )

        rsi = indicators[rsi_key]
        bb = indicators[bb_key]

        if rsi is None or bb is None:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason="Indicator values are None (insufficient data)",
            )

        bb_lower = float(bb["bb_lower"])
        bb_upper = float(bb["bb_upper"])
        current_price = data.current_price

        # BUY: RSI oversold AND price at/below lower band
        if rsi < rsi_oversold and current_price <= bb_lower:
            rsi_strength = (rsi_oversold - rsi) / rsi_oversold
            confidence = round(min(0.9, 0.6 + rsi_strength * 0.3), 3)
            return TradeSignal(
                signal=Signal.BUY,
                market=market,
                confidence=confidence,
                reason=(
                    f"RSI={rsi:.1f} (oversold<{rsi_oversold}) AND "
                    f"price={current_price:,.0f} <= BB_lower={bb_lower:,.0f}"
                ),
                metadata={"rsi": rsi, "bb_lower": bb_lower, "bb_upper": bb_upper},
            )

        # SELL: RSI overbought AND price at/above upper band
        if rsi > rsi_overbought and current_price >= bb_upper:
            rsi_strength = (rsi - rsi_overbought) / (100 - rsi_overbought)
            confidence = round(min(0.9, 0.6 + rsi_strength * 0.3), 3)
            return TradeSignal(
                signal=Signal.SELL,
                market=market,
                confidence=confidence,
                reason=(
                    f"RSI={rsi:.1f} (overbought>{rsi_overbought}) AND "
                    f"price={current_price:,.0f} >= BB_upper={bb_upper:,.0f}"
                ),
                metadata={"rsi": rsi, "bb_lower": bb_lower, "bb_upper": bb_upper},
            )

        return TradeSignal(
            signal=Signal.HOLD,
            market=market,
            confidence=0.5,
            reason=(
                f"RSI={rsi:.1f}, price={current_price:,.0f} within bands "
                f"[{bb_lower:,.0f}, {bb_upper:,.0f}]"
            ),
            metadata={"rsi": rsi, "bb_lower": bb_lower, "bb_upper": bb_upper},
        )
