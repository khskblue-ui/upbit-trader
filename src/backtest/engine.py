"""Backtesting engine — simulate strategy execution on historical OHLCV data."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from src.indicators.technical import compute_indicators
from src.strategy.base import BaseStrategy, MarketData, Signal

logger = logging.getLogger(__name__)

UPBIT_FEE_RATE = 0.0005  # 0.05% per order (buy and sell)


@dataclass
class BacktestTrade:
    """A single executed trade during a backtest run."""

    market: str
    side: str           # "buy" or "sell"
    price: float
    quantity: float
    fee: float
    timestamp: str
    strategy: str
    pnl: float | None = None        # Realised PnL; set on sell trades only
    exit_reason: str | None = None  # "HARD_STOP" / "ATR_TRAIL" / "TIME_EXIT" / "SIGNAL" / "END"


@dataclass
class BacktestResult:
    """Complete result of one backtest run."""

    strategy_name: str
    market: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


class BacktestEngine:
    """Simulate strategy signals on historical candle data.

    One position at a time per market (no pyramiding).
    Fees and slippage are applied on every fill.

    Sell algorithm (mirrors TradingEngine 3-layer exit):
        Layer 1 — HARD_STOP: candle ``low`` ≤ entry × (1 - hard_stop_pct)
            Fills at hard_stop level (pessimistic).
        Layer 2 — ATR_TRAIL: candle ``low`` ≤ trailing_stop
            Trailing stop ratchets up each bar (= one daily session in backtest).
            Fills at trailing_stop level.
        Layer 3 — TIME_EXIT: bars_held ≥ max_hold_days
            Fills at bar's closing price.

    Exit layers are checked before the buy signal each bar to ensure stops
    are never silently skipped.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 1_000_000.0,
        fee_rate: float = UPBIT_FEE_RATE,
        slippage_rate: float = 0.0001,  # 0.01%
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    async def run(
        self,
        market: str,
        candles: list[dict],
        warmup_bars: int = 30,
    ) -> BacktestResult:
        """Execute the strategy on *candles* and return a :class:`BacktestResult`.

        Args:
            market: Market identifier e.g. ``"KRW-BTC"``.
            candles: OHLCV dicts sorted oldest-first (ascending timestamp).
            warmup_bars: Number of leading bars consumed for indicator warmup
                before signal generation begins.

        Returns:
            :class:`BacktestResult` populated with trades and equity curve.
        """
        if len(candles) < warmup_bars + 2:
            raise ValueError(
                f"Need at least {warmup_bars + 2} candles, got {len(candles)}"
            )

        df_full = pd.DataFrame(candles)
        for col in ("open", "high", "low", "close", "volume"):
            if col in df_full.columns:
                df_full[col] = df_full[col].astype(float)

        indicator_names = self.strategy.required_indicators()

        # Read sell config from strategy (with safe defaults)
        atr_trail_mult = float(getattr(self.strategy.config, "atr_trail_mult", 2.0))
        hard_stop_pct = float(getattr(self.strategy.config, "hard_stop_pct", 0.05))
        max_hold_days = int(getattr(self.strategy.config, "max_hold_days", 5))

        capital = self.initial_capital
        position_qty: float = 0.0
        position_buy_price: float = 0.0

        # 3-layer exit tracking state
        position_entry_atr: float = 0.0
        position_trailing_stop: float = 0.0
        position_hard_stop: float = 0.0
        position_buy_bar: int = 0

        trades: list[BacktestTrade] = []
        equity_curve: list[float] = []

        start_date = str(candles[warmup_bars].get("timestamp", f"bar_{warmup_bars}"))

        for i in range(warmup_bars, len(candles)):
            candle = candles[i]
            current_price = float(candle["close"])
            candle_low = float(candle.get("low", current_price))
            ts = str(candle.get("timestamp", f"bar_{i}"))

            # Compute indicators on the window [0 .. i]
            sub_df = df_full.iloc[: i + 1].copy()
            indicators = compute_indicators(sub_df, indicator_names) if indicator_names else {}
            current_atr = float(indicators.get("atr_14", 0) or position_entry_atr or 0)

            market_data = MarketData(
                market=market,
                candles=candles[: i + 1],
                current_price=current_price,
                indicators=indicators,
            )

            # ------------------------------------------------------------------
            # 3-Layer Sell Check (executed BEFORE buy signal each bar)
            # ------------------------------------------------------------------
            if position_qty > 0.0:
                exit_reason: str | None = None
                sell_price = current_price * (1.0 - self.slippage_rate)

                # Layer 1: HARD_STOP — check if candle low breached the floor
                if candle_low <= position_hard_stop:
                    sell_price = position_hard_stop * (1.0 - self.slippage_rate)
                    exit_reason = f"HARD_STOP({position_hard_stop:,.0f})"

                # Layer 2: ATR_TRAIL — check if candle low breached trailing stop
                elif candle_low <= position_trailing_stop:
                    sell_price = position_trailing_stop * (1.0 - self.slippage_rate)
                    exit_reason = f"ATR_TRAIL({position_trailing_stop:,.0f})"

                # Layer 3: TIME_EXIT — max holding period reached
                elif (i - position_buy_bar) >= max_hold_days:
                    sell_price = current_price * (1.0 - self.slippage_rate)
                    exit_reason = (
                        f"TIME_EXIT({i - position_buy_bar} bars / max {max_hold_days})"
                    )

                # Update trailing stop (ratchet-up only) — only if no exit this bar
                if exit_reason is None and current_atr > 0:
                    new_trail = current_price - atr_trail_mult * current_atr
                    if new_trail > position_trailing_stop:
                        position_trailing_stop = new_trail

                if exit_reason:
                    proceeds = position_qty * sell_price
                    fee = proceeds * self.fee_rate
                    net_proceeds = proceeds - fee
                    pnl = net_proceeds - (position_qty * position_buy_price)

                    capital += net_proceeds
                    trades.append(
                        BacktestTrade(
                            market=market,
                            side="sell",
                            price=sell_price,
                            quantity=position_qty,
                            fee=fee,
                            timestamp=ts,
                            strategy=self.strategy.name,
                            pnl=pnl,
                            exit_reason=exit_reason,
                        )
                    )
                    logger.debug(
                        "[%s] SELL %s %.6f @ %,.0f | pnl=%,.0f | capital=%,.0f",
                        ts, exit_reason, position_qty, sell_price, pnl, capital,
                    )
                    position_qty = 0.0
                    position_buy_price = 0.0
                    position_entry_atr = 0.0
                    position_trailing_stop = 0.0
                    position_hard_stop = 0.0

                    equity_curve.append(capital)
                    continue  # Skip signal generation this bar

            # ------------------------------------------------------------------
            # Strategy signal
            # ------------------------------------------------------------------
            signal = await self.strategy.generate_signal(market, market_data)

            # --- BUY ---
            if signal.signal == Signal.BUY and position_qty == 0.0:
                exec_price = current_price * (1.0 + self.slippage_rate)
                trade_amount = signal.suggested_size or capital * 0.95
                trade_amount = min(trade_amount, capital)

                if trade_amount < 5_000:  # Upbit minimum order
                    equity_curve.append(capital)
                    continue

                fee = trade_amount * self.fee_rate
                quantity = (trade_amount - fee) / exec_price

                position_qty = quantity
                position_buy_price = exec_price
                capital -= trade_amount

                # Initialise 3-layer exit state
                position_entry_atr = current_atr
                position_trailing_stop = exec_price - atr_trail_mult * current_atr
                position_hard_stop = exec_price * (1.0 - hard_stop_pct)
                position_buy_bar = i

                trades.append(
                    BacktestTrade(
                        market=market,
                        side="buy",
                        price=exec_price,
                        quantity=quantity,
                        fee=fee,
                        timestamp=ts,
                        strategy=self.strategy.name,
                    )
                )
                logger.debug(
                    "[%s] BUY %.6f @ %,.0f | capital=%,.0f | hard_stop=%,.0f | trail=%,.0f",
                    ts, quantity, exec_price, capital,
                    position_hard_stop, position_trailing_stop,
                )

            # --- SELL (strategy signal fallback) ---
            elif signal.signal == Signal.SELL and position_qty > 0.0:
                exec_price = current_price * (1.0 - self.slippage_rate)
                proceeds = position_qty * exec_price
                fee = proceeds * self.fee_rate
                net_proceeds = proceeds - fee
                pnl = net_proceeds - (position_qty * position_buy_price)

                capital += net_proceeds
                trades.append(
                    BacktestTrade(
                        market=market,
                        side="sell",
                        price=exec_price,
                        quantity=position_qty,
                        fee=fee,
                        timestamp=ts,
                        strategy=self.strategy.name,
                        pnl=pnl,
                        exit_reason="SIGNAL",
                    )
                )
                logger.debug(
                    "[%s] SELL SIGNAL %.6f @ %,.0f | pnl=%,.0f | capital=%,.0f",
                    ts, position_qty, exec_price, pnl, capital,
                )
                position_qty = 0.0
                position_buy_price = 0.0
                position_entry_atr = 0.0
                position_trailing_stop = 0.0
                position_hard_stop = 0.0

            # Mark-to-market portfolio value
            equity_curve.append(capital + position_qty * current_price)

        # Close any remaining open position at the last bar
        if position_qty > 0.0:
            last_price = float(candles[-1]["close"]) * (1.0 - self.slippage_rate)
            proceeds = position_qty * last_price
            fee = proceeds * self.fee_rate
            net_proceeds = proceeds - fee
            pnl = net_proceeds - (position_qty * position_buy_price)
            capital += net_proceeds
            trades.append(
                BacktestTrade(
                    market=market,
                    side="sell",
                    price=last_price,
                    quantity=position_qty,
                    fee=fee,
                    timestamp=str(candles[-1].get("timestamp", "end")),
                    strategy=self.strategy.name,
                    pnl=pnl,
                    exit_reason="END",
                )
            )
            position_qty = 0.0

        end_date = str(candles[-1].get("timestamp", f"bar_{len(candles) - 1}"))

        return BacktestResult(
            strategy_name=self.strategy.name,
            market=market,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=capital,
            trades=trades,
            equity_curve=equity_curve,
        )
