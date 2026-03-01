"""In-memory position tracker — maintains open positions across the session."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PositionTracker:
    """Track open positions and update on every filled order.

    Positions are stored in memory only; they are not persisted to the database.
    For persistent tracking use the Trade table in the database.
    """

    def __init__(self) -> None:
        # market -> {quantity, avg_price, cost_basis}
        self._positions: dict[str, dict[str, float]] = {}

    # ------------------------------------------------------------------
    # Position mutation
    # ------------------------------------------------------------------

    def on_buy(self, market: str, quantity: float, price: float) -> None:
        """Record a buy fill.

        Adjusts average price using a weighted average when adding to an
        existing position.

        Args:
            market: Market identifier, e.g. "KRW-BTC".
            quantity: Number of coins purchased.
            price: Fill price per coin.
        """
        if market in self._positions:
            pos = self._positions[market]
            old_qty = pos["quantity"]
            old_avg = pos["avg_price"]
            new_qty = old_qty + quantity
            new_avg = (old_qty * old_avg + quantity * price) / new_qty
            pos["quantity"] = new_qty
            pos["avg_price"] = new_avg
            pos["cost_basis"] = new_avg * new_qty
        else:
            self._positions[market] = {
                "quantity": quantity,
                "avg_price": price,
                "cost_basis": quantity * price,
            }
        logger.debug("Position after buy: %s -> %s", market, self._positions[market])

    def on_sell(self, market: str, quantity: float, price: float) -> float:
        """Record a sell fill and return the realised PnL.

        Args:
            market: Market identifier.
            quantity: Number of coins sold.
            price: Fill price per coin.

        Returns:
            Realised PnL (proceeds minus cost basis of the sold portion).
        """
        pos = self._positions.get(market)
        if pos is None:
            logger.warning("on_sell called for unknown position: %s", market)
            return 0.0

        sell_qty = min(quantity, pos["quantity"])
        cost_basis_sold = sell_qty * pos["avg_price"]
        proceeds = sell_qty * price
        pnl = proceeds - cost_basis_sold

        pos["quantity"] -= sell_qty
        pos["cost_basis"] = pos["avg_price"] * pos["quantity"]

        if pos["quantity"] <= 1e-12:
            del self._positions[market]
            logger.debug("Position closed: %s | pnl=%.2f", market, pnl)
        else:
            logger.debug("Position reduced: %s -> %s | pnl=%.2f", market, pos, pnl)

        return pnl

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, market: str) -> dict | None:
        """Return position for *market*, or ``None`` if not held.

        Returns:
            Dict with keys ``quantity``, ``avg_price``, ``cost_basis``, or ``None``.
        """
        return self._positions.get(market)

    def get_all_positions(self) -> dict:
        """Return a shallow copy of all open positions."""
        return {k: dict(v) for k, v in self._positions.items()}

    def has_position(self, market: str) -> bool:
        """Return ``True`` if there is an open position in *market*."""
        pos = self._positions.get(market)
        return pos is not None and pos["quantity"] > 1e-12

    def total_cost_basis(self) -> float:
        """Return the total KRW cost basis of all open positions."""
        return sum(p["cost_basis"] for p in self._positions.values())

    def __repr__(self) -> str:
        return f"PositionTracker({self._positions})"
