"""TradingEngine — main orchestration loop connecting data → strategy → risk → execution."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from src.data.database import Database
from src.data.models import Trade
from src.execution.base import BaseExecutor, OrderRequest
from src.indicators.technical import compute_indicators
from src.risk.base import PortfolioState, RiskDecision
from src.risk.engine import RiskEngine
from src.strategy.base import BaseStrategy, MarketData, Signal, TradeSignal

if TYPE_CHECKING:
    from src.api.upbit_client import UpbitClient
    from src.notification.telegram_bot import TelegramNotifier

logger = logging.getLogger(__name__)

_CANDLE_FETCH_COUNT = 100  # candles to fetch per evaluation cycle
KST = timezone(timedelta(hours=9))


class TradingEngine:
    """Orchestrate the full trading loop: data collection → signal → risk → order.

    This class is intentionally a *pure orchestrator*: it holds no trading
    logic itself, delegating all decisions to injected strategy and risk objects.

    Args:
        strategies: List of :class:`BaseStrategy` instances to run.
        executor: :class:`BaseExecutor` for order submission (live or paper).
        risk_engine: :class:`RiskEngine` that validates signals before execution.
        db: Optional :class:`Database` for persisting trade records.
        poll_interval: Seconds between strategy evaluation cycles.
        telegram: Optional :class:`TelegramNotifier` for trade/error alerts.
    """

    def __init__(
        self,
        strategies: list[BaseStrategy],
        executor: BaseExecutor,
        risk_engine: RiskEngine,
        db: Database | None = None,
        poll_interval: float = 60.0,
        upbit_client: "UpbitClient | None" = None,
        telegram: "TelegramNotifier | None" = None,
    ) -> None:
        self.strategies = strategies
        self.executor = executor
        self.risk_engine = risk_engine
        self.db = db
        self.poll_interval = poll_interval
        self._upbit_client = upbit_client
        self._telegram = telegram
        self._running = False
        self._paused = False  # Set True via command handler to pause without stopping

        # Track buy date per market for next-session sell logic (KST date)
        self._buy_dates: dict[str, date] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Call ``on_startup`` on all strategies."""
        logger.info("TradingEngine starting with %d strategies.", len(self.strategies))
        for strategy in self.strategies:
            await strategy.on_startup()
        self._running = True

    async def stop(self) -> None:
        """Signal the engine to stop and call ``on_shutdown`` on all strategies."""
        logger.info("TradingEngine stopping.")
        self._running = False
        for strategy in self.strategies:
            await strategy.on_shutdown()

    async def run(self) -> None:
        """Main event loop — runs until :meth:`stop` is called."""
        await self.start()
        try:
            while self._running:
                await self._tick()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("TradingEngine cancelled.")
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Single evaluation cycle
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """Run one evaluation cycle across all strategies and their markets."""
        if self._paused:
            logger.debug("TradingEngine paused — skipping tick.")
            return

        portfolio = await self._build_portfolio_state()

        for strategy in self.strategies:
            if not strategy.config.enabled:
                continue
            for market in strategy.config.markets:
                try:
                    await self._evaluate(strategy, market, portfolio)
                except Exception as exc:
                    logger.error(
                        "Error evaluating %s for %s: %s",
                        strategy.name, market, exc, exc_info=True,
                    )

    async def _evaluate(
        self,
        strategy: BaseStrategy,
        market: str,
        portfolio: PortfolioState,
    ) -> None:
        """Evaluate a single strategy/market pair and execute if approved.

        Sell logic (volatility breakout next-session sell):
          If we hold a position from a previous KST day, sell immediately at
          current price (simulates selling at next session's open).

        Buy logic:
          Ask the strategy for a signal; run through risk engine; execute order.
          Price is passed explicitly so paper/backtest fills are realistic.
        """
        market_data = await self._fetch_market_data(strategy, market)
        if market_data is None:
            return

        # ------------------------------------------------------------------
        # Sell check: if we hold a position from a PREVIOUS trading session
        # ------------------------------------------------------------------
        pos = portfolio.positions.get(market, {})
        position_qty = float(pos.get("quantity", 0)) if pos else 0.0

        if position_qty > 0 and market in self._buy_dates:
            today_kst = datetime.now(KST).date()
            if today_kst > self._buy_dates[market]:
                await self._execute_next_session_sell(strategy, market, portfolio, position_qty, market_data)
                return  # Done for this market this tick

        # ------------------------------------------------------------------
        # Buy check: ask strategy for signal, validate risk, execute
        # ------------------------------------------------------------------
        signal: TradeSignal = await strategy.generate_signal(market, market_data)
        logger.debug(
            "[%s/%s] signal=%s conf=%.2f",
            strategy.name, market, signal.signal.value, signal.confidence,
        )

        if signal.signal == Signal.HOLD:
            return

        decision, results = await self.risk_engine.check(signal, portfolio)

        if decision == RiskDecision.REJECT:
            logger.info(
                "[%s/%s] Signal REJECTED by risk engine: %s",
                strategy.name, market,
                [r.reason for r in results if r.decision == RiskDecision.REJECT],
            )
            return

        # APPROVE or MODIFY — execute the (possibly modified) order
        order = OrderRequest(
            market=signal.market,
            side=signal.signal.value,    # "buy" or "sell"
            quantity=signal.suggested_size,
            order_type="market",
            price=market_data.current_price,  # critical: realistic paper fills
        )
        result = await self.executor.execute_order(order)

        if result.success:
            logger.info(
                "[%s/%s] %s order executed: qty=%.6f @ %.0f fee=%.2f id=%s",
                strategy.name, market, order.side,
                result.quantity, result.price, result.fee, result.order_id,
            )

            # Track buy date for next-session sell
            if signal.signal == Signal.BUY:
                self._buy_dates[market] = datetime.now(KST).date()
                if self._telegram:
                    await self._telegram.notify_buy(
                        market=market,
                        price=result.price,
                        quantity=result.quantity,
                        strategy=strategy.name,
                        confidence=signal.confidence,
                    )

            await strategy.on_trade_executed({
                "order_id": result.order_id,
                "market": result.market,
                "side": result.side,
                "price": result.price,
                "quantity": result.quantity,
                "fee": result.fee,
                "strategy": strategy.name,
            })
            await self._record_trade(strategy.name, result)

        else:
            logger.error(
                "[%s/%s] Order failed: %s",
                strategy.name, market, result.error,
            )
            if self._telegram:
                await self._telegram.notify_order_failed(
                    market=market,
                    side=order.side,
                    error=result.error or "Unknown error",
                    strategy=strategy.name,
                )

    # ------------------------------------------------------------------
    # Next-session sell
    # ------------------------------------------------------------------

    async def _execute_next_session_sell(
        self,
        strategy: BaseStrategy,
        market: str,
        portfolio: PortfolioState,
        quantity: float,
        market_data: MarketData,
    ) -> None:
        """Sell an entire position at the next session's open price (current price)."""
        sell_order = OrderRequest(
            market=market,
            side="sell",
            quantity=quantity,
            order_type="market",
            price=market_data.current_price,
        )
        result = await self.executor.execute_order(sell_order)

        if result.success:
            pos = portfolio.positions.get(market, {})
            avg_price = float(pos.get("avg_price", result.price))
            pnl = (result.price - avg_price) * result.quantity - result.fee

            logger.info(
                "[%s] Next-session SELL executed: qty=%.6f @ %.0f pnl=%.2f id=%s",
                market, result.quantity, result.price, pnl, result.order_id,
            )

            if self._telegram:
                await self._telegram.notify_sell(
                    market=market,
                    price=result.price,
                    quantity=result.quantity,
                    pnl=pnl,
                    strategy=strategy.name,
                )

            # Clear buy date so we can re-enter on next signal
            self._buy_dates.pop(market, None)

            await strategy.on_trade_executed({
                "order_id": result.order_id,
                "market": result.market,
                "side": result.side,
                "price": result.price,
                "quantity": result.quantity,
                "fee": result.fee,
                "strategy": strategy.name,
                "pnl": pnl,
            })
            await self._record_trade(strategy.name, result)

        else:
            logger.error("[%s] Next-session SELL failed: %s", market, result.error)
            if self._telegram:
                await self._telegram.notify_order_failed(
                    market=market,
                    side="sell",
                    error=result.error or "Unknown error",
                    strategy=strategy.name,
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_portfolio_state(self) -> PortfolioState:
        """Construct a :class:`PortfolioState` from the executor's current state."""
        try:
            available_balance = await self.executor.get_balance("KRW")
            positions = await self.executor.get_positions()
            position_value = sum(
                p.get("current_value", 0) for p in positions.values()
            )
            total_balance = available_balance + position_value
            return PortfolioState(
                total_balance=total_balance,
                available_balance=available_balance,
                positions=positions,
                peak_balance=total_balance,  # simplified; real impl tracks this
            )
        except Exception as exc:
            logger.error("Failed to build portfolio state: %s", exc)
            return PortfolioState(
                total_balance=0.0,
                available_balance=0.0,
                positions={},
            )

    async def _fetch_market_data(
        self, strategy: BaseStrategy, market: str
    ) -> MarketData | None:
        """Fetch live candles via UpbitClient and assemble :class:`MarketData`.

        Returns ``None`` when no client is configured (backtest callers skip gracefully).
        """
        if self._upbit_client is None:
            return None

        timeframes = strategy.required_timeframes()
        timeframe = timeframes[0] if timeframes else "1d"

        try:
            raw_candles = await self._upbit_client.get_candles(
                market=market,
                timeframe=timeframe,
                count=_CANDLE_FETCH_COUNT,
            )
        except Exception as exc:
            logger.error("Failed to fetch candles for %s/%s: %s", market, timeframe, exc)
            return None

        if not raw_candles:
            logger.warning("No candles returned for %s/%s", market, timeframe)
            return None

        # Upbit returns newest-first; reverse to chronological order
        raw_candles = list(reversed(raw_candles))

        df = pd.DataFrame([
            {
                "date": c.get("candle_date_time_kst", "")[:10],
                "open": float(c.get("opening_price", 0)),
                "high": float(c.get("high_price", 0)),
                "low": float(c.get("low_price", 0)),
                "close": float(c.get("trade_price", 0)),
                "volume": float(c.get("candle_acc_trade_volume", 0)),
            }
            for c in raw_candles
        ])

        indicators = compute_indicators(df, strategy.required_indicators())
        current_price = df["close"].iloc[-1]

        return MarketData(
            market=market,
            candles=df.to_dict(orient="records"),
            current_price=float(current_price),
            indicators=indicators,
        )

    @staticmethod
    def _signal_to_order(signal: TradeSignal, current_price: float = 0.0) -> OrderRequest:
        """Convert a :class:`TradeSignal` to an :class:`OrderRequest`."""
        return OrderRequest(
            market=signal.market,
            side=signal.signal.value,  # "buy" or "sell"
            quantity=signal.suggested_size,
            order_type="market",
            price=current_price,
        )

    async def _record_trade(self, strategy_name: str, result) -> None:
        """Persist a completed trade to the database (no-op if db is None)."""
        if self.db is None:
            return
        try:
            trade = Trade(
                market=result.market,
                side=result.side,
                strategy=strategy_name,
                price=result.price,
                quantity=result.quantity,
                fee=result.fee,
                pnl=None,
                order_id=result.order_id,
                timestamp=datetime.now(timezone.utc),
            )
            async with self.db.get_session() as session:
                session.add(trade)
            logger.debug("Trade recorded: %s %s %s", result.market, result.side, result.order_id)
        except Exception as exc:
            logger.error("Failed to record trade: %s", exc)
