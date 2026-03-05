"""Intraday Momentum Breakout Strategy (IMB) — 1-hour timeframe.

수학적 근거
-----------
일봉 TFVB와 동일한 3단계 스크리닝 구조를 1시간봉에 적용합니다.
단기 모멘텀에 특화된 파라미터와 퍼센트 기반 스탑을 사용합니다.

1. EMA 추세 필터 (Faber 2007 원리를 단기에 적용)
   - EMA(24) > EMA(120): 24시간 > 120시간 상승 추세
   - 상승 추세에서만 매수 → 하락장 드로다운 회피

2. RSI 모멘텀 게이트 (Wilder 1978)
   - RSI(14) ∈ [50, 75]: 모멘텀이 강하고 과매수 직전
   - 50 미만: 모멘텀 불충분, 75 초과: 과매수 → 둘 다 매수 금지

3. ATR(24) 변동성 돌파 진입 (Larry Williams 1979)
   - 목표가 = 현재 봉 시가 + ATR(24시간봉) × k (기본 1.5)
   - 1시간봉 ATR을 사용하므로 k값을 일봉보다 크게 설정

스탑 방식: 퍼센트 기반 (ATR 스케일 역설 회피)
-----------------------------------------
1시간봉 ATR은 일봉 ATR보다 훨씬 작습니다. ATR 기반 트레일링 스탑을
1시간봉에 적용하면 스탑이 너무 타이트해져 잦은 허위 청산이 발생합니다.
퍼센트 기반 스탑은 가격 규모에 독립적으로 동작합니다.

- hard_stop = entry_price × (1 - hard_stop_pct)   기본: 3%
- trailing_stop = highest_price × (1 - trailing_stop_pct)  기본: 3%

시간 청산: max_hold_hours (기본 24시간)
- 일봉 전략의 max_hold_days 대신 시간 단위로 청산
- 캔들 세션(09:00 KST)이 아닌 실제 경과 시간 기준
"""

from __future__ import annotations

import logging

from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal
from src.strategy.registry import register

logger = logging.getLogger(__name__)

# EMA(120) 안정화를 위해 최소 125개 1시간봉 캔들 필요
_MIN_CANDLES = 125


@register
class IntradayMomentumBreakoutStrategy(BaseStrategy):
    """1-hour volatility breakout with percent-based stops and 24-hour time exit.

    Config params (all optional, have defaults):
        k_value (float): Volatility breakout factor. Default 1.5.
        atr_risk_pct (float): Risk fraction per trade (0.01 = 1%). Default 0.01.
        rsi_min (float): Lower RSI(14) bound. Default 50.
        rsi_max (float): Upper RSI(14) bound. Default 75.
        hard_stop_pct (float): Hard stop loss fraction from entry. Default 0.03 (3%).
        trailing_stop_pct (float): Trailing stop fraction from peak price. Default 0.03 (3%).
        max_hold_hours (float): Maximum hold duration in hours. Default 24.
        base_capital (float): Fallback capital for ATR sizing when portfolio balance
            unavailable (KRW). Default 1_000_000. In live/paper mode, actual account
            balance is used automatically.
    """

    name = "intraday_momentum_breakout"
    version = "1.0.0"
    description = (
        "EMA(24/120) trend filter + RSI(14) momentum gate + "
        "ATR(24h)-based volatility breakout entry on 1h candles. "
        "Percent-based stops (3%) and 24h time exit. "
        "Designed to complement the daily TFVB strategy."
    )

    def required_indicators(self) -> list[str]:
        return ["ema_24", "ema_120", "rsi_14", "atr_24"]

    def required_timeframes(self) -> list[str]:
        return ["60m"]

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:  # noqa: C901
        candles = data.candles

        # Warmup guard — need enough history for EMA(120) to stabilise
        if len(candles) < _MIN_CANDLES:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=f"Warmup: need {_MIN_CANDLES} candles, have {len(candles)}",
            )

        # --- Read config params ---
        k = float(getattr(self.config, "k_value", 1.5))
        atr_risk_pct = float(getattr(self.config, "atr_risk_pct", 0.01))
        rsi_min = float(getattr(self.config, "rsi_min", 50.0))
        rsi_max = float(getattr(self.config, "rsi_max", 75.0))
        base_capital = float(getattr(self.config, "base_capital", 1_000_000.0))
        # 실제 잔액 우선 사용, 없으면 base_capital fallback (백테스트용)
        effective_capital = data.portfolio_balance if data.portfolio_balance > 0 else base_capital

        # --- Fetch pre-computed indicators ---
        ema_24 = data.indicators.get("ema_24")
        ema_120 = data.indicators.get("ema_120")
        rsi = data.indicators.get("rsi_14")
        atr = data.indicators.get("atr_24")

        if any(v is None for v in (ema_24, ema_120, rsi, atr)):
            missing = [
                name for name, v in {
                    "ema_24": ema_24, "ema_120": ema_120,
                    "rsi_14": rsi, "atr_24": atr,
                }.items() if v is None
            ]
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=f"Missing indicators: {missing}",
            )

        current_price = data.current_price

        # ---------------------------------------------------------------
        # Screen 1: Trend filter — EMA(24) must be above EMA(120)
        # ---------------------------------------------------------------
        if ema_24 <= ema_120:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.0,
                reason=(
                    f"[Screen1 FAIL] Downtrend: EMA24 {ema_24:,.0f} ≤ EMA120 {ema_120:,.0f}"
                ),
                metadata={"ema_24": ema_24, "ema_120": ema_120},
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
        # Screen 3: Volatility breakout entry (using 1h ATR)
        # ---------------------------------------------------------------
        current = candles[-1]
        current_open = float(current["open"])
        # 1시간봉 ATR을 사용하므로 k=1.5 (일봉 0.4보다 크게)
        target_price = current_open + atr * k

        if current_price < target_price:
            return TradeSignal(
                signal=Signal.HOLD,
                market=market,
                confidence=0.5,
                reason=(
                    f"[Screen3 FAIL] No breakout: price {current_price:,.0f} "
                    f"< target {target_price:,.0f} "
                    f"(open {current_open:,.0f} + ATR_24 {atr:,.0f} × k={k})"
                ),
                metadata={"target_price": target_price, "k_value": k},
            )

        # ---------------------------------------------------------------
        # All three screens passed → BUY
        # ---------------------------------------------------------------

        # Confidence: blend breakout strength with trend strength
        breakout_excess = (current_price - target_price) / max(atr, 1)
        trend_strength = min(1.0, (ema_24 - ema_120) / max(ema_120, 1) * 50)
        confidence = round(min(0.90, 0.60 + breakout_excess * 0.15 + trend_strength * 0.05), 3)

        # Percent-based position sizing (1% risk rule aligned with hard_stop_pct)
        # IMB uses a percent-based hard stop (not ATR-based), so the stop distance
        # expressed as a fraction of position value IS hard_stop_pct itself.
        #
        # Risk budget  = effective_capital × atr_risk_pct       (e.g. 10,000 KRW)
        # coin_risk_fraction = hard_stop_pct                     (e.g. 0.03 = 3%)
        # position_krw = risk_budget / hard_stop_pct             (e.g. 333,333 KRW)
        #
        # This is mathematically consistent: if the stop fires at -3%, a position
        # of 333,333 KRW loses exactly 10,000 KRW (= 1% of 1,000,000 KRW capital).
        # Using atr*2/price instead would break this invariant because 1h ATR is
        # unrelated to the configured percent stop.
        hard_stop_pct = float(getattr(self.config, "hard_stop_pct", 0.03))
        risk_budget_krw = effective_capital * atr_risk_pct
        if hard_stop_pct > 0:
            position_krw = risk_budget_krw / hard_stop_pct
        else:
            position_krw = effective_capital * 0.10  # fallback 10%

        # Cap: never more than 20% of effective capital per trade
        max_position_krw = effective_capital * 0.20
        position_krw = min(position_krw, max_position_krw)
        position_krw = round(position_krw, -3)  # round to nearest 1,000 KRW

        atr_pct = atr / current_price * 100 if current_price > 0 else 0.0

        return TradeSignal(
            signal=Signal.BUY,
            market=market,
            confidence=confidence,
            reason=(
                f"[ALL SCREENS PASS] "
                f"EMA24({ema_24:,.0f})>EMA120({ema_120:,.0f}), "
                f"RSI={rsi:.1f}∈[{rsi_min},{rsi_max}], "
                f"price({current_price:,.0f})≥target({target_price:,.0f}, ATR_24×k)"
            ),
            suggested_size=position_krw,
            metadata={
                "ema_24": round(ema_24, 0),
                "ema_120": round(ema_120, 0),
                "rsi": round(rsi, 1),
                "atr": round(atr, 0),
                "atr_pct": round(atr_pct, 2),
                "target_price": round(target_price, 0),
                "k_value": k,
                "position_krw": position_krw,
                "risk_budget_krw": risk_budget_krw,
            },
        )
