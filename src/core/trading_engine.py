"""TradingEngine — main orchestration loop connecting data → strategy → risk → execution."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
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

_CANDLE_FETCH_COUNT = 200  # candles to fetch per evaluation cycle (EMA(120) needs ≥125)
KST = timezone(timedelta(hours=9))
_POSITIONS_FILE = Path("data/positions.json")

# Keywords in RiskEngine REJECT reasons that indicate infrastructure/system errors.
# Protective exits (HARD_STOP, ATR_TRAIL) must NOT override these rejections —
# doing so can trigger crashes due to insufficient funds, API maintenance, etc.
# Only *policy* violations (position limits, MDD, daily-loss cap, etc.) are
# overridable; if ANY matched keyword appears in ANY REJECT reason, the override
# is suppressed and the error is logged for manual intervention.
_SYSTEM_ERROR_KEYWORDS = frozenset({
    "insufficient",
    "maintenance",
    "unavailable",
    "connection",
    "timeout",
    "network",
})


def _upbit_session_date(dt: datetime) -> date:
    """Return the Upbit trading session date for the given datetime.

    Upbit's daily candles reset at 09:00 KST (not midnight).
    Times before 09:00 KST are still part of the *previous* session.

    Args:
        dt: Any timezone-aware datetime.

    Returns:
        The session date (KST-based, 09:00 boundary).
    """
    kst = dt.astimezone(KST)
    if kst.hour < 9:
        return (kst - timedelta(days=1)).date()
    return kst.date()


@dataclass
class _PositionInfo:
    """Per-market open position metadata for stop-loss and exit management.

    Attributes:
        entry_price: Fill price at the time of purchase (KRW).
        entry_atr: ATR value at entry — used to set initial ATR-based trailing stop.
        trailing_stop: Dynamic stop price, ratchets up with price. Never decreases.
        hard_stop: Absolute floor price (entry_price × (1 - hard_stop_pct)).
        buy_session: Upbit session date (09:00 KST boundary) when position opened.
        highest_price: Peak price observed since entry.  The trailing stop is
            computed as ``highest_price - atr_trail_mult * atr`` (ATR-based) or
            ``highest_price * (1 - trailing_stop_pct)`` (percent-based) so that it
            always reflects the maximum gain and can only ever move up.
            Initialised to entry_price and updated on every evaluation tick.
        buy_datetime: UTC datetime of the fill.  Used for ``max_hold_hours`` time
            exit (1h strategies).  ``None`` for positions restored from old snapshots
            that pre-date this field.
    """

    entry_price: float
    entry_atr: float
    trailing_stop: float
    hard_stop: float
    buy_session: date
    highest_price: float = 0.0  # peak price since entry; set to entry_price on creation
    buy_datetime: datetime | None = None  # UTC fill time; enables max_hold_hours exit


@dataclass
class _HourlyStats:
    """Accumulated stats for the current 1-hour monitoring window.

    Reset every hour just after the briefing is sent.

    Attributes:
        start_time: When this window began (KST).
        error_count: Total exceptions caught in ``_tick()`` this hour.
        error_messages: Short description of each error (newest last).
        trade_executed: True when at least one buy or sell completed.
        last_hold_reasons: market → last HOLD reason string seen this hour.
            Overwritten on each tick so only the most recent state is kept.
        last_indicators: market → indicator snapshot dict
            (ema_20, ema_60, rsi, current_price, target_price).
    """

    start_time: datetime
    error_count: int = 0
    error_messages: list[str] = field(default_factory=list)
    trade_executed: bool = False
    last_hold_reasons: dict[str, str] = field(default_factory=dict)
    last_indicators: dict[str, dict] = field(default_factory=dict)


class TradingEngine:
    """Orchestrate the full trading loop: data collection → signal → risk → order.

    This class is intentionally a *pure orchestrator*: it holds no trading
    logic itself, delegating all decisions to injected strategy and risk objects.

    Sell algorithm (3-layer exit, checked on every tick):
        Layer 1 — HARD_STOP: current_price <= entry_price × (1 - hard_stop_pct)
            Immediate exit on gap-downs or flash crashes. Always executes
            even if RiskEngine rejects (protective override).
        Layer 2 — ATR_TRAIL: current_price <= trailing_stop
            Trailing stop ratchets up once per new Upbit session (09:00 KST).
            Prevents noise exits within intraday ticks.
        Layer 3 — TIME_EXIT: sessions_held >= max_hold_days
            Forces exit after N trading sessions to avoid dead money.

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
        mode: str = "paper",
        live_executor: BaseExecutor | None = None,
        paper_executor: BaseExecutor | None = None,
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
        self._mode = mode

        # Executor references for mode switching
        self._live_executor: BaseExecutor | None = live_executor or (executor if mode == "live" else None)
        self._paper_executor: BaseExecutor | None = paper_executor or (executor if mode in ("paper", "backtest") else None)

        # Open position metadata: market → _PositionInfo
        # Persisted to _POSITIONS_FILE to survive process restarts.
        self._positions: dict[str, _PositionInfo] = {}

        # Rolling 1-hour stats window for the hourly Telegram briefing.
        self._hourly_stats = _HourlyStats(start_time=datetime.now(KST))

    # ------------------------------------------------------------------
    # Position persistence
    # ------------------------------------------------------------------

    def _save_positions(self) -> None:
        """Persist open positions to JSON snapshot file.

        Called after every position change (buy, sell, trailing update).
        """
        try:
            _POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                market: {
                    **asdict(p),
                    "buy_session": p.buy_session.isoformat(),
                    # Override non-JSON-serializable datetime with ISO string
                    "buy_datetime": p.buy_datetime.isoformat() if p.buy_datetime else None,
                }
                for market, p in self._positions.items()
            }
            _POSITIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.error("Failed to save positions snapshot: %s", exc)

    def _load_positions(self) -> None:
        """Restore positions from JSON snapshot on startup."""
        if not _POSITIONS_FILE.exists():
            logger.info("No positions snapshot found at %s — starting fresh.", _POSITIONS_FILE)
            return
        try:
            raw = json.loads(_POSITIONS_FILE.read_text())
            for market, d in raw.items():
                _raw_dt = d.get("buy_datetime")
                self._positions[market] = _PositionInfo(
                    entry_price=float(d["entry_price"]),
                    entry_atr=float(d["entry_atr"]),
                    trailing_stop=float(d["trailing_stop"]),
                    hard_stop=float(d["hard_stop"]),
                    buy_session=date.fromisoformat(d["buy_session"]),
                    # backward-compat: old snapshots lack highest_price; fall back to entry_price
                    highest_price=float(d.get("highest_price", d["entry_price"])),
                    # backward-compat: old snapshots lack buy_datetime
                    buy_datetime=datetime.fromisoformat(_raw_dt) if _raw_dt else None,
                )
            logger.info(
                "Restored %d open position(s) from snapshot: %s",
                len(self._positions),
                list(self._positions.keys()),
            )
        except Exception as exc:
            logger.error(
                "Failed to restore positions from snapshot (%s): %s — starting fresh.",
                _POSITIONS_FILE, exc,
            )

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def switch_mode(self, new_mode: str) -> None:
        """Switch between paper and live trading mode at runtime.

        Args:
            new_mode: ``"paper"`` or ``"live"``

        Raises:
            ValueError: When the target executor is not available.
        """
        if new_mode not in ("paper", "live"):
            raise ValueError(f"Invalid mode '{new_mode}': must be 'paper' or 'live'")
        if new_mode == self._mode:
            raise ValueError(f"Already in '{new_mode}' mode")
        if new_mode == "live" and self._live_executor is None:
            raise ValueError(
                "Live mode unavailable: UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY not configured"
            )
        if new_mode == "paper" and self._paper_executor is None:
            raise ValueError("Paper executor not available")

        old_mode = self._mode
        self.executor = self._live_executor if new_mode == "live" else self._paper_executor
        self._mode = new_mode
        logger.info("Trading mode switched: %s → %s", old_mode, new_mode)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load persisted positions, then call ``on_startup`` on all strategies."""
        self._load_positions()
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
        """Main event loop — runs until :meth:`stop` is called.

        Launches a background hourly-briefing task alongside the main tick loop.
        Both tasks are cancelled cleanly on exit.
        """
        await self.start()
        briefing_task = asyncio.create_task(
            self._hourly_briefing_loop(), name="hourly-briefing"
        )
        try:
            while self._running:
                await self._tick()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("TradingEngine cancelled.")
        finally:
            briefing_task.cancel()
            try:
                await briefing_task
            except asyncio.CancelledError:
                pass
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
                    self._hourly_stats.error_count += 1
                    self._hourly_stats.error_messages.append(
                        f"[{market}] {str(exc)[:120]}"
                    )

    async def _evaluate(
        self,
        strategy: BaseStrategy,
        market: str,
        portfolio: PortfolioState,
    ) -> None:
        """Evaluate a single strategy/market pair and execute if approved.

        Sell logic (3-layer exit):
          Checks HARD_STOP → ATR_TRAIL → TIME_EXIT before the buy signal check.
          Orphan positions (portfolio holds coin but no _PositionInfo record)
          are recovered conservatively from DB or via fallback defaults.

        Buy logic:
          Ask the strategy for a signal; run through risk engine; execute order.
          Price is passed explicitly so paper/backtest fills are realistic.
        """
        market_data = await self._fetch_market_data(strategy, market)
        if market_data is None:
            return
        market_data.portfolio_balance = portfolio.available_balance  # ← 실잔액 주입

        # ------------------------------------------------------------------
        # Sell check: 3-layer exit algorithm
        # ------------------------------------------------------------------
        pos = portfolio.positions.get(market, {})
        position_qty = float(pos.get("quantity", 0)) if pos else 0.0

        if position_qty > 0:
            if market not in self._positions:
                # Orphan position: coin held but no entry record (e.g. after restart)
                await self._recover_orphan_position(market, position_qty, market_data)

            if market in self._positions:
                pinfo = self._positions[market]
                current_price = market_data.current_price
                current_session = _upbit_session_date(datetime.now(KST))
                sessions_held = (current_session - pinfo.buy_session).days

                # --- Update highest_price tracker (every tick) ---
                # Must happen BEFORE the trailing-stop recalculation so the new
                # high is available when we compute the ratchet.
                needs_save = False
                if current_price > pinfo.highest_price:
                    pinfo.highest_price = current_price
                    needs_save = True

                # --- Update trailing stop based on highest_price (ratchet-up) ---
                # Strategy-aware: percent-based (IMB) updates every tick for fine-grained
                # intraday protection; ATR-based (TFVB) updates once per new Upbit session
                # (09:00 KST) to prevent noise exits within the same trading day.
                _pct_raw = getattr(strategy.config, "trailing_stop_pct", None)
                trailing_stop_pct = float(_pct_raw) if isinstance(_pct_raw, (int, float)) else 0.0

                if trailing_stop_pct > 0:
                    # Percent-based trailing stop — refresh on every tick
                    new_trail = pinfo.highest_price * (1.0 - trailing_stop_pct)
                    if new_trail > pinfo.trailing_stop:
                        pinfo.trailing_stop = new_trail
                        needs_save = True
                        logger.debug(
                            "[%s] Pct trailing stop → %.0f (peak %.0f, pct=%.1f%%)",
                            market, pinfo.trailing_stop, pinfo.highest_price,
                            trailing_stop_pct * 100,
                        )
                elif current_session > pinfo.buy_session:
                    # ATR-based trailing stop — update once per new Upbit session
                    atr = market_data.indicators.get("atr_14") or pinfo.entry_atr
                    atr_trail_mult = float(getattr(strategy.config, "atr_trail_mult", 2.0))
                    new_trail = pinfo.highest_price - atr_trail_mult * atr
                    if new_trail > pinfo.trailing_stop:
                        pinfo.trailing_stop = new_trail
                        needs_save = True
                        logger.debug(
                            "[%s] ATR trailing stop → %.0f (session %s, peak %.0f)",
                            market, pinfo.trailing_stop, current_session, pinfo.highest_price,
                        )

                if needs_save:
                    self._save_positions()

                # --- Determine exit trigger ---
                max_hold_days = int(getattr(strategy.config, "max_hold_days", 5))
                _max_h = getattr(strategy.config, "max_hold_hours", None)
                max_hold_hours_cfg = float(_max_h) if isinstance(_max_h, (int, float)) else None
                exit_reason: str | None = None

                if current_price <= pinfo.hard_stop:
                    exit_reason = (
                        f"HARD_STOP({pinfo.hard_stop:,.0f}): "
                        f"급락/갭다운 — 진입가 대비 "
                        f"{(current_price / pinfo.entry_price - 1) * 100:.1f}%"
                    )
                elif current_price <= pinfo.trailing_stop:
                    trail_label = "PCT_TRAIL" if trailing_stop_pct > 0 else "ATR_TRAIL"
                    exit_reason = (
                        f"{trail_label}({pinfo.trailing_stop:,.0f}): "
                        f"추세 반전 — 트레일링 스탑 터치"
                    )
                elif max_hold_hours_cfg is not None and pinfo.buy_datetime is not None:
                    # Hours-based time exit (IMB 1h strategy)
                    hours_held = (
                        datetime.now(timezone.utc) - pinfo.buy_datetime
                    ).total_seconds() / 3600
                    if hours_held >= max_hold_hours_cfg:
                        exit_reason = (
                            f"TIME_EXIT: {hours_held:.1f}시간 보유 "
                            f"(최대 {max_hold_hours_cfg:.0f}시간)"
                        )
                elif sessions_held >= max_hold_days:
                    exit_reason = (
                        f"TIME_EXIT: {sessions_held}세션 보유 "
                        f"(최대 {max_hold_days}세션, 09:00 KST 기준)"
                    )

                if exit_reason:
                    await self._execute_sell(
                        strategy, market, portfolio,
                        position_qty, market_data, exit_reason,
                    )
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
            # Track the HOLD reason and current indicator snapshot for the hourly briefing.
            self._hourly_stats.last_hold_reasons[market] = signal.reason or ""
            ind_snap: dict = {}
            # Cover both daily TFVB (ema_20/ema_60/atr_14) and 1h IMB (ema_24/ema_120/atr_24)
            for key in ("ema_20", "ema_60", "ema_24", "ema_120", "rsi_14", "atr_14", "atr_24"):
                v = market_data.indicators.get(key)
                if v is not None:
                    # Normalize rsi_NN → "rsi" for display consistency
                    out_key = "rsi" if key.startswith("rsi_") else key
                    ind_snap[out_key] = v
            ind_snap["current_price"] = market_data.current_price
            if signal.metadata and "target_price" in signal.metadata:
                ind_snap["target_price"] = signal.metadata["target_price"]
            if signal.metadata and "rsi" in signal.metadata:
                ind_snap["rsi"] = signal.metadata["rsi"]
            self._hourly_stats.last_indicators[market] = ind_snap
            return

        # ── Real-time: signal detected ──────────────────────────────────
        if self._telegram:
            await self._telegram.notify_signal(
                market=market,
                strategy=strategy.name,
                signal=signal.signal.value,
                confidence=signal.confidence,
                reason=signal.reason or "",
                metadata=signal.metadata or {},
            )

        decision, results = await self.risk_engine.check(signal, portfolio)

        # ── Real-time: risk check result ─────────────────────────────────
        if self._telegram:
            reject_reasons = [r.reason for r in results if r.decision == RiskDecision.REJECT]
            approve_reasons = [r.reason for r in results if r.decision != RiskDecision.REJECT]
            all_reasons = reject_reasons or approve_reasons
            await self._telegram.notify_risk_check(
                market=market,
                strategy=strategy.name,
                decision=decision.value,
                reasons=all_reasons,
            )

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

            # Record position info for 3-layer exit management
            if signal.signal == Signal.BUY:
                # Use the best available ATR indicator (strategy-agnostic)
                entry_atr = (
                    market_data.indicators.get("atr_14")
                    or market_data.indicators.get("atr_24")
                    or 0.0
                )
                hard_stop_pct = float(getattr(strategy.config, "hard_stop_pct", 0.05))
                _pct_raw2 = getattr(strategy.config, "trailing_stop_pct", None)
                _pct = float(_pct_raw2) if isinstance(_pct_raw2, (int, float)) else 0.0
                if _pct > 0:
                    # Percent-based initial trailing stop (IMB strategy)
                    initial_trailing_stop = result.price * (1.0 - _pct)
                else:
                    # ATR-based initial trailing stop (TFVB strategy)
                    atr_trail_mult = float(getattr(strategy.config, "atr_trail_mult", 2.0))
                    initial_trailing_stop = result.price - atr_trail_mult * entry_atr
                self._positions[market] = _PositionInfo(
                    entry_price=result.price,
                    entry_atr=entry_atr,
                    trailing_stop=initial_trailing_stop,
                    hard_stop=result.price * (1.0 - hard_stop_pct),
                    buy_session=_upbit_session_date(datetime.now(KST)),
                    highest_price=result.price,  # start tracking peak from entry fill price
                    buy_datetime=datetime.now(timezone.utc),  # enables max_hold_hours exit
                )
                self._save_positions()
                self._hourly_stats.trade_executed = True

                if self._telegram:
                    await self._telegram.notify_buy(
                        market=market,
                        price=result.price,
                        quantity=result.quantity,
                        strategy=strategy.name,
                        confidence=signal.confidence,
                        reason=signal.reason or "",
                        metadata=signal.metadata,
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
    # 3-layer sell execution
    # ------------------------------------------------------------------

    async def _execute_sell(
        self,
        strategy: BaseStrategy,
        market: str,
        portfolio: PortfolioState,
        quantity: float,
        market_data: MarketData,
        exit_reason: str,
    ) -> None:
        """Execute a sell order with full exit context.

        Sell orders are routed through RiskEngine as a courtesy.
        HARD_STOP and ATR_TRAIL (protective exits) override a REJECT to
        ensure the position is always closed in safety scenarios.

        Args:
            exit_reason: Human-readable exit trigger description (e.g. "HARD_STOP(...)").
        """
        sell_order = OrderRequest(
            market=market,
            side="sell",
            quantity=quantity,
            order_type="market",
            price=market_data.current_price,
        )

        # Protective exits (HARD_STOP, ATR_TRAIL) must always execute
        is_protective = exit_reason.startswith(("HARD_STOP", "ATR_TRAIL"))

        try:
            sell_signal = TradeSignal(
                signal=Signal.SELL,
                market=market,
                confidence=1.0,
                reason=exit_reason,
            )
            decision, results = await self.risk_engine.check(sell_signal, portfolio)
            if decision == RiskDecision.REJECT and not is_protective:
                logger.warning(
                    "[%s] Sell blocked by risk engine (TIME_EXIT): %s", market, exit_reason
                )
                return
            if decision == RiskDecision.REJECT and is_protective:
                # Safety gate: only override *policy* rejections (position limits,
                # MDD cap, daily-loss limit, etc.).  If any REJECT reason contains
                # infrastructure/system-error keywords (insufficient funds, API
                # maintenance …) we must NOT override — blindly executing the order
                # in those conditions would cause a crash or an un-fillable order.
                reject_reasons = [r.reason for r in results if r.decision == RiskDecision.REJECT]
                system_error_hit = any(
                    kw in reason.lower()
                    for reason in reject_reasons
                    for kw in _SYSTEM_ERROR_KEYWORDS
                )
                if system_error_hit:
                    logger.error(
                        "[%s] Protective sell BLOCKED — system/infra error detected in "
                        "REJECT reasons: %s.  Manual intervention required.",
                        market, reject_reasons,
                    )
                    return
                logger.warning(
                    "[%s] Risk engine rejected protective sell (policy violation) — "
                    "overriding for safety.  Reasons: %s",
                    market, reject_reasons,
                )
        except Exception as exc:
            logger.error(
                "[%s] Risk engine error during sell — executing anyway: %s", market, exc
            )

        result = await self.executor.execute_order(sell_order)

        if result.success:
            pos = portfolio.positions.get(market, {})
            avg_price = float(pos.get("avg_price", result.price))
            pnl = (result.price - avg_price) * result.quantity - result.fee

            logger.info(
                "[%s] SELL executed (%s): qty=%.6f @ %.0f pnl=%.2f id=%s",
                market, exit_reason, result.quantity, result.price, pnl, result.order_id,
            )

            if self._telegram:
                await self._telegram.notify_sell(
                    market=market,
                    price=result.price,
                    quantity=result.quantity,
                    pnl=pnl,
                    strategy=strategy.name,
                    exit_reason=exit_reason,
                )

            # Clear position record and persist
            self._positions.pop(market, None)
            self._save_positions()
            self._hourly_stats.trade_executed = True

            await strategy.on_trade_executed({
                "order_id": result.order_id,
                "market": result.market,
                "side": result.side,
                "price": result.price,
                "quantity": result.quantity,
                "fee": result.fee,
                "strategy": strategy.name,
                "pnl": pnl,
                "exit_reason": exit_reason,
            })
            await self._record_trade(strategy.name, result)

        else:
            logger.error("[%s] SELL failed (%s): %s", market, exit_reason, result.error)
            if self._telegram:
                await self._telegram.notify_order_failed(
                    market=market,
                    side="sell",
                    error=result.error or "Unknown error",
                    strategy=strategy.name,
                )

    # ------------------------------------------------------------------
    # Orphan position recovery
    # ------------------------------------------------------------------

    async def _recover_orphan_position(
        self,
        market: str,
        quantity: float,
        market_data: MarketData,
    ) -> None:
        """Handle a position that exists in the portfolio but has no entry record.

        This occurs when the process restarts and the snapshot file is missing
        or corrupt. Recovery attempts:
          1. Query the DB for the most recent buy trade for this market.
          2. If found: reconstruct _PositionInfo from recorded entry price/timestamp.
          3. If not found: apply a conservative fallback — current_price with
             tight stops, and buy_session set to yesterday so TIME_EXIT fires
             at the next evaluation.
        """
        logger.warning(
            "[%s] Orphan position detected (qty=%.6f, no entry record). Attempting recovery.",
            market, quantity,
        )

        recovered = False
        if self.db is not None:
            try:
                recovered = await self._recover_position_from_db(market, market_data)
            except Exception as exc:
                logger.error("[%s] DB recovery failed: %s", market, exc)

        if not recovered:
            # Conservative fallback: tight stops, yesterday's session → TIME_EXIT fires soon
            current_price = market_data.current_price
            atr = market_data.indicators.get("atr_14") or current_price * 0.03
            yesterday_session = _upbit_session_date(datetime.now(KST)) - timedelta(days=1)
            self._positions[market] = _PositionInfo(
                entry_price=current_price,
                entry_atr=atr,
                trailing_stop=current_price * 0.97,   # tight 3% trail
                hard_stop=current_price * 0.95,        # 5% hard floor
                buy_session=yesterday_session,          # forces TIME_EXIT on next tick
                highest_price=current_price,            # conservative: current price as peak
                # Set buy_datetime 25h ago → triggers max_hold_hours exit on next tick
                buy_datetime=datetime.now(timezone.utc) - timedelta(hours=25),
            )
            self._save_positions()
            logger.warning(
                "[%s] Fallback position record created. "
                "Will trigger TIME_EXIT at next evaluation.",
                market,
            )

    async def _recover_position_from_db(
        self,
        market: str,
        market_data: MarketData,
    ) -> bool:
        """Query the trades DB to reconstruct _PositionInfo for *market*.

        Returns:
            True if recovery succeeded, False otherwise.
        """
        try:
            from sqlalchemy import select, desc
            async with self.db.get_session() as session:
                stmt = (
                    select(Trade)
                    .where(Trade.market == market, Trade.side == "buy")
                    .order_by(desc(Trade.timestamp))
                    .limit(1)
                )
                result = await session.execute(stmt)
                last_buy: Trade | None = result.scalar_one_or_none()

            if last_buy is None:
                logger.warning("[%s] No buy trade found in DB for recovery.", market)
                return False

            entry_price = last_buy.price
            atr = market_data.indicators.get("atr_14") or entry_price * 0.03
            buy_session = _upbit_session_date(
                last_buy.timestamp.replace(tzinfo=timezone.utc)
            )

            self._positions[market] = _PositionInfo(
                entry_price=entry_price,
                entry_atr=atr,
                trailing_stop=entry_price - 2.0 * atr,
                hard_stop=entry_price * 0.95,
                buy_session=buy_session,
                highest_price=entry_price,  # conservative: use entry price as peak
                buy_datetime=last_buy.timestamp.replace(tzinfo=timezone.utc),
            )
            self._save_positions()
            logger.info(
                "[%s] Position recovered from DB: entry=%.0f session=%s",
                market, entry_price, buy_session,
            )
            return True

        except Exception as exc:
            logger.error("[%s] Error querying DB for recovery: %s", market, exc)
            return False

    # ------------------------------------------------------------------
    # Hourly briefing
    # ------------------------------------------------------------------

    async def _hourly_briefing_loop(self) -> None:
        """Background task: send a Telegram briefing every hour, then reset stats."""
        while self._running:
            await asyncio.sleep(3600)
            if not self._running:
                break
            await self._send_hourly_briefing()
            # Reset window — start_time anchors to now for the next hour label
            self._hourly_stats = _HourlyStats(start_time=datetime.now(KST))

    async def _send_hourly_briefing(self) -> None:
        """Format and dispatch the hourly Telegram briefing from accumulated stats."""
        if self._telegram is None:
            return
        stats = self._hourly_stats
        now = datetime.now(KST)
        hour_label = (
            f"{stats.start_time.strftime('%H:%M')}~{now.strftime('%H:%M')} KST"
        )
        await self._telegram.notify_hourly_briefing(
            hour_label=hour_label,
            error_count=stats.error_count,
            error_messages=stats.error_messages,
            trade_executed=stats.trade_executed,
            market_hold_reasons=stats.last_hold_reasons,
            market_indicators=stats.last_indicators,
        )

    async def send_briefing_now(self) -> None:
        """Manually trigger a briefing for the current window and reset the stats.

        Called by the Telegram ``/briefing`` command.

        Race-safe design: stats are atomically swapped *before* any ``await``
        so that new ticks accumulate into the fresh window immediately.
        The snapshot is then sent without touching ``self._hourly_stats`` again,
        preventing double-reset if the hourly loop fires concurrently.
        """
        if self._telegram is None:
            return
        # Atomic swap — no await between capture and reset
        old_stats = self._hourly_stats
        self._hourly_stats = _HourlyStats(start_time=datetime.now(KST))

        now = datetime.now(KST)
        hour_label = (
            f"{old_stats.start_time.strftime('%H:%M')}~{now.strftime('%H:%M')} KST"
        )
        await self._telegram.notify_hourly_briefing(
            hour_label=hour_label,
            error_count=old_stats.error_count,
            error_messages=old_stats.error_messages,
            trade_executed=old_stats.trade_executed,
            market_hold_reasons=old_stats.last_hold_reasons,
            market_indicators=old_stats.last_indicators,
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
