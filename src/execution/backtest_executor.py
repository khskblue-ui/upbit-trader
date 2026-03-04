"""Paper / backtest executor — virtual order fills without real API calls."""

from __future__ import annotations

import logging
import uuid as _uuid_lib

from src.execution.base import BaseExecutor, OrderRequest, OrderResult
from src.execution.position_tracker import PositionTracker

logger = logging.getLogger(__name__)

_UPBIT_FEE_RATE = 0.0005   # 0.05%
_SLIPPAGE_RATE = 0.0005    # 0.05% (conservative, network-realistic)


class BacktestExecutor(BaseExecutor):
    """Simulate order execution for paper trading and backtesting.

    Orders are filled immediately at the *current_price* with a small
    slippage and Upbit's standard fee applied.

    Args:
        initial_capital: Starting KRW balance.
        current_price_fn: Optional async callable ``(market) -> float`` that
            returns the latest price.  When ``None`` the order's ``price``
            field is used directly as the fill price.
        fee_rate: Fee per order (default 0.05 %).
        slippage_rate: Simulated slippage per order (default 0.01 %).
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        fee_rate: float = _UPBIT_FEE_RATE,
        slippage_rate: float = _SLIPPAGE_RATE,
    ) -> None:
        super().__init__()
        self._krw_balance = initial_capital
        self._fee_rate = fee_rate
        self._slippage_rate = slippage_rate
        self._tracker = PositionTracker()

    # ------------------------------------------------------------------
    # BaseExecutor implementation
    # ------------------------------------------------------------------

    async def execute_order(self, order: OrderRequest) -> OrderResult:
        """Simulate an order fill.

        The fill price is taken from ``order.price`` (limit) or estimated
        from ``order.quantity`` for market orders, with slippage applied.

        Args:
            order: Order parameters.

        Returns:
            :class:`OrderResult` with simulated fill details.
        """
        fill_price = float(order.price or 0)

        if order.side == "buy":
            exec_price = fill_price * (1.0 + self._slippage_rate)
            krw_amount = order.quantity or self._krw_balance * 0.95
            krw_amount = min(krw_amount, self._krw_balance)

            if krw_amount < 5_000:
                return OrderResult(
                    success=False,
                    market=order.market,
                    side=order.side,
                    error="Insufficient balance (< 5,000 KRW minimum)",
                )

            fee = krw_amount * self._fee_rate
            coins = (krw_amount - fee) / exec_price if exec_price > 0 else 0

            self._krw_balance -= krw_amount
            self._tracker.on_buy(order.market, coins, exec_price)

            order_id = str(_uuid_lib.uuid4())
            logger.debug(
                "[PAPER] BUY %s qty=%.6f @ %.0f fee=%.2f balance=%.0f",
                order.market, coins, exec_price, fee, self._krw_balance,
            )
            return OrderResult(
                success=True,
                order_id=order_id,
                market=order.market,
                side=order.side,
                price=exec_price,
                quantity=coins,
                fee=fee,
            )

        else:  # sell
            exec_price = fill_price * (1.0 - self._slippage_rate)
            pos = self._tracker.get_position(order.market)
            sell_qty = order.quantity or (pos["quantity"] if pos else 0)

            if sell_qty <= 0 or not self._tracker.has_position(order.market):
                return OrderResult(
                    success=False,
                    market=order.market,
                    side=order.side,
                    error="No position to sell",
                )

            proceeds = sell_qty * exec_price
            fee = proceeds * self._fee_rate
            net = proceeds - fee

            self._tracker.on_sell(order.market, sell_qty, exec_price)
            self._krw_balance += net

            order_id = str(_uuid_lib.uuid4())
            logger.debug(
                "[PAPER] SELL %s qty=%.6f @ %.0f fee=%.2f balance=%.0f",
                order.market, sell_qty, exec_price, fee, self._krw_balance,
            )
            return OrderResult(
                success=True,
                order_id=order_id,
                market=order.market,
                side=order.side,
                price=exec_price,
                quantity=sell_qty,
                fee=fee,
            )

    async def get_balance(self, currency: str = "KRW") -> float:
        """Return the virtual KRW balance (other currencies not tracked).

        Args:
            currency: Only "KRW" is supported.

        Returns:
            Current virtual KRW balance.
        """
        if currency == "KRW":
            return self._krw_balance
        pos = self._tracker.get_position(f"KRW-{currency}")
        return pos["quantity"] if pos else 0.0

    async def get_positions(self) -> dict:
        """Return all virtual open positions.

        Returns:
            Dict matching the :class:`LiveExecutor` format:
            ``market -> {quantity, avg_price, current_value}``.
        """
        positions = {}
        for market, pos in self._tracker.get_all_positions().items():
            positions[market] = {
                "quantity": pos["quantity"],
                "avg_price": pos["avg_price"],
                "current_value": pos["cost_basis"],
            }
        return positions
