"""Order manager — tracks pending orders and polls for fills."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.api.upbit_client import UpbitClient

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1.0   # seconds between fill-status polls
_FILL_TIMEOUT = 60.0   # seconds before giving up on a pending order


class OrderManager:
    """Track open orders and wait for them to be filled or cancelled.

    Args:
        client: Authenticated :class:`UpbitClient` used to query order status.
    """

    def __init__(self, client: "UpbitClient") -> None:
        self._client = client
        self._pending: dict[str, dict] = {}  # uuid -> order dict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def wait_for_fill(
        self,
        uuid: str,
        timeout: float = _FILL_TIMEOUT,
    ) -> dict | None:
        """Poll the Upbit API until *uuid* is filled (or timeout expires).

        Args:
            uuid: Order UUID returned by :meth:`LiveExecutor.execute_order`.
            timeout: Maximum seconds to wait.

        Returns:
            The filled order dict, or ``None`` if timeout was reached.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                order = await self._client.get_order(uuid)
                state = order.get("state", "")
                if state == "done":
                    logger.info("Order %s filled: %s", uuid, order)
                    self._pending.pop(uuid, None)
                    return order
                if state == "cancel":
                    logger.warning("Order %s was cancelled.", uuid)
                    self._pending.pop(uuid, None)
                    return None
                logger.debug("Order %s state=%s, waiting...", uuid, state)
            except Exception as exc:
                logger.warning("Error polling order %s: %s", uuid, exc)

            await asyncio.sleep(_POLL_INTERVAL)

        logger.error("Order %s timed out after %.0fs; attempting cancel.", uuid, timeout)
        await self.cancel_order(uuid)
        return None

    async def cancel_order(self, uuid: str) -> dict | None:
        """Cancel *uuid* and remove it from pending tracking.

        Args:
            uuid: Order UUID to cancel.

        Returns:
            Cancelled order dict, or ``None`` on failure.
        """
        try:
            result = await self._client.cancel_order(uuid)
            self._pending.pop(uuid, None)
            logger.info("Order %s cancelled.", uuid)
            return result
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", uuid, exc)
            return None

    async def cancel_all_pending(self) -> None:
        """Cancel every tracked pending order (e.g., on shutdown)."""
        for uuid in list(self._pending.keys()):
            await self.cancel_order(uuid)

    def track(self, uuid: str, order_data: dict) -> None:
        """Register an order for tracking.

        Args:
            uuid: Order UUID.
            order_data: Raw order dict from the Upbit API.
        """
        self._pending[uuid] = order_data

    @property
    def pending_count(self) -> int:
        """Number of currently tracked pending orders."""
        return len(self._pending)
