"""Trend-Filtered Volatility Breakout Strategy (TFVB).

수학적 근거
-----------
세 개의 독립 필터가 모두 동시에 참일 때만 매수 신호를 발생시킵니다.
각 필터는 독립적으로 검증된 통계적 엣지를 제공합니다.

1. EMA 추세 필터 (Faber 2007, "A Quantitative Approach to Tactical Asset Allocation")
   - EMA(20) > EMA(60): 중기 상승 추세 확인
   - 상승 추세에서만 매수 → 하락장 드로다운을 회피

2. RSI 모멘텀 게이트 (Wilder 1978)
   - RSI(14) ∈ [45, 70]: 모멘텀이 건전하고 과매수 직전
   - 45 미만: 모멘텀 약화, 70 초과: 과매수 → 둘 다 매수 금지

3. 변동성 돌파 진입 (Larry Williams 1979)
   - 목표가 = 오늘 시가 + 전일 변동폭 × k_value (기본 0.4, 기존 0.5보다 엄격)
   - 이미 검증된 모멘텀 추종 진입 기법

포지션 사이징: ATR 리스크 규칙 (Turtle Traders, Dennis & Eckhardt 1983)
---------------------------------------------------------------------------
- 1회 거래 리스크 = 자본 × atr_risk_pct (기본 1%)
- 포지션 = 리스크 예산 / (2 × ATR)
- 이 공식은 ATR이 클수록(변동성↑) 작게, ATR이 작을수록 크게 매수
- 예시: 초기 자본 100만원, ETH가격 300만원, ATR=9만원(3%)일 때
  포지션 = 10,000 / 180,000 × 3,000,000 = 166,667 KRW (16.7%)
"""

from __future__ import annotations

import logging

from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal
from src.strategy.registry import register

logger = logging.getLogger(__name__)

# 안정적인 EMA(60) 계산을 위해 최소 65개 캔들 필요
_MIN_CANDLES = 65


@register
class TrendFilteredBreakoutStrategy(BaseStrategy):
    """Triple-screen volatility breakout with ATR-based position sizing.

    Config params (all optional, have defaults):
        k_value (float): Volatility breakout factor. Default 0.4.
        atr_risk_pct (float): Risk fraction per trade (0.01 = 1%). Default 0.01.
        rsi_min (float): Lower RSI bound. Default 45.
        rsi_max (float): Upper RSI bound. Default 70.
        base_capital (float): Reference capital for ATR sizing (KRW). Default 1_000_000.
    """

    name = "trend_filtered_breakout"
    version = "1.0.0"
    description = (
        "EMA trend filter + RSI momentum gate + volatility breakout entry "
        "with ATR-based position sizing. Prioritises capital preservation."
    )

    def required_indicators(self) -> list[str]:
        return ["ema_20", "ema_60", "rsi_14", "atr_14"]

    def required_timeframes(self) -> list[str]:
        return ["1d"]

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:  # noqa: C901
        candles = data.candles

        # Warmup guard — need enough history for EMA(60) to stabilise
        if len(candles) < _MIN_CANDLES:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=f"Warmup: need {_MIN_CANDLES} candles, have {len(candles)}",
            )

        # --- Read config params ---
        k = float(getattr(self.config, "k_value", 0.4))
        atr_risk_pct = float(getattr(self.config, "atr_risk_pct", 0.01))
        rsi_min = float(getattr(self.config, "rsi_min", 45.0))
        rsi_max = float(getattr(self.config, "rsi_max", 70.0))
        base_capital = float(getattr(self.config, "base_capital", 1_000_000.0))

        # --- Fetch pre-computed indicators ---
        ema_20 = data.indicators.get("ema_20")
        ema_60 = data.indicators.get("ema_60")
        rsi = data.indicators.get("rsi_14")
        atr = data.indicators.get("atr_14")

        if any(v is None for v in (ema_20, ema_60, rsi, atr)):
            missing = [k for k, v in {"ema_20": ema_20, "ema_60": ema_60, "rsi_14": rsi, "atr_14": atr}.items() if v is None]
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=f"Missing indicators: {missing}",
            )

        current_price = data.current_price

        # ---------------------------------------------------------------
        # Screen 1: Trend filter — EMA(20) must be above EMA(60)
        # ---------------------------------------------------------------
        if ema_20 <= ema_60:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=(
                    f"[Screen1 FAIL] Downtrend: EMA20 {ema_20:,.0f} ≤ EMA60 {ema_60:,.0f}"
                ),
                metadata={"ema_20": ema_20, "ema_60": ema_60},
            )

        # ---------------------------------------------------------------
        # Screen 2: Momentum gate — RSI(14) must be in healthy range
        # ---------------------------------------------------------------
        if not (rsi_min <= rsi <= rsi_max):
            zone = "overbought" if rsi > rsi_max else "weak momentum"
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=(
                    f"[Screen2 FAIL] RSI {rsi:.1f} out of [{rsi_min}, {rsi_max}] ({zone})"
                ),
                metadata={"rsi": rsi},
            )

        # ---------------------------------------------------------------
        # Screen 3: Volatility breakout entry
        # ---------------------------------------------------------------
        prev = candles[-2]
        current = candles[-1]
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])
        prev_range = prev_high - prev_low
        current_open = float(current["open"])
        target_price = current_open + prev_range * k

        if current_price < target_price:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.5,
                reason=(
                    f"[Screen3 FAIL] No breakout: price {current_price:,.0f} "
                    f"< target {target_price:,.0f} "
                    f"(open {current_open:,.0f} + range {prev_range:,.0f} × k={k})"
                ),
                metadata={"target_price": target_price, "k_value": k},
            )

        # ---------------------------------------------------------------
        # All three screens passed → BUY
        # ---------------------------------------------------------------

        # Confidence: blend breakout strength with trend strength
        breakout_excess = (current_price - target_price) / max(prev_range, 1)
        trend_strength = min(1.0, (ema_20 - ema_60) / max(ema_60, 1) * 50)
        confidence = round(min(0.90, 0.60 + breakout_excess * 0.15 + trend_strength * 0.05), 3)

        # ATR position sizing (Turtle Trading 1% risk rule)
        # Risk budget = base_capital × atr_risk_pct
        # Position KRW = (risk_budget / (2 × ATR_KRW)) × current_price
        # = risk_budget × current_price / (2 × ATR_KRW)
        risk_budget_krw = base_capital * atr_risk_pct
        atr_stop_dist = atr * 2.0  # 2-ATR stop distance
        if atr_stop_dist > 0 and current_price > 0:
            # Fraction of one ETH coin = atr_stop_dist / current_price
            # KRW to invest = risk_budget_krw / coin_risk_fraction
            coin_risk_fraction = atr_stop_dist / current_price
            position_krw = risk_budget_krw / coin_risk_fraction
        else:
            position_krw = base_capital * 0.10  # fallback 10%

        # Cap: never more than 20% of base capital per trade
        max_position_krw = base_capital * 0.20
        position_krw = min(position_krw, max_position_krw)
        position_krw = round(position_krw, -3)  # round to nearest 1,000 KRW

        atr_pct = atr / current_price * 100 if current_price > 0 else 0.0

        return TradeSignal(
            signal=Signal.BUY,
            market=market,
            confidence=confidence,
            reason=(
                f"[ALL SCREENS PASS] "
                f"EMA20({ema_20:,.0f})>EMA60({ema_60:,.0f}), "
                f"RSI={rsi:.1f}∈[{rsi_min},{rsi_max}], "
                f"price({current_price:,.0f})≥target({target_price:,.0f})"
            ),
            suggested_size=position_krw,
            metadata={
                "ema_20": round(ema_20, 0),
                "ema_60": round(ema_60, 0),
                "rsi": round(rsi, 1),
                "atr": round(atr, 0),
                "atr_pct": round(atr_pct, 2),
                "target_price": round(target_price, 0),
                "k_value": k,
                "position_krw": position_krw,
                "risk_budget_krw": risk_budget_krw,
            },
        )
