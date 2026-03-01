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
    pnl: float | None = None  # Realised PnL; set on sell trades only


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

        capital = self.initial_capital
        position_qty: float = 0.0
        position_buy_price: float = 0.0

        trades: list[BacktestTrade] = []
        equity_curve: list[float] = []

        start_date = str(candles[warmup_bars].get("timestamp", f"bar_{warmup_bars}"))

        for i in range(warmup_bars, len(candles)):
            candle = candles[i]
            current_price = float(candle["close"])
            ts = str(candle.get("timestamp", f"bar_{i}"))

            # Compute indicators on the window [0 .. i]
            sub_df = df_full.iloc[: i + 1].copy()
            indicators = compute_indicators(sub_df, indicator_names) if indicator_names else {}

            market_data = MarketData(
                market=market,
                candles=candles[: i + 1],
                current_price=current_price,
                indicators=indicators,
            )

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
                    "[%s] BUY %.6f @ %,.0f | capital=%,.0f", ts, quantity, exec_price, capital
                )

            # --- SELL ---
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
                    )
                )
                logger.debug(
                    "[%s] SELL %.6f @ %,.0f | pnl=%,.0f | capital=%,.0f",
                    ts, position_qty, exec_price, pnl, capital,
                )
                position_qty = 0.0
                position_buy_price = 0.0

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
