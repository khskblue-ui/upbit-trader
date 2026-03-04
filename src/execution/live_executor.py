"""Live order executor — submits real orders to the Upbit API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.execution.base import BaseExecutor, OrderRequest, OrderResult

if TYPE_CHECKING:
    from src.api.upbit_client import UpbitClient

logger = logging.getLogger(__name__)

_UPBIT_FEE_RATE = 0.0005  # 0.05%


class LiveExecutor(BaseExecutor):
    """Execute orders against the live Upbit API.

    Args:
        client: Authenticated :class:`UpbitClient` instance.

    Side and order type translation:
        - ``side="buy", order_type="market"``  → Upbit ``side=bid, ord_type=price``
          (market buy by KRW amount via *price* field)
        - ``side="sell", order_type="market"`` → Upbit ``side=ask, ord_type=market``
          (market sell by coin volume via *volume* field)
        - ``side="buy"/"sell", order_type="limit"`` → ``ord_type=limit``
    """

    def __init__(self, client: "UpbitClient") -> None:
        super().__init__()
        self._client = client

    async def execute_order(self, order: OrderRequest) -> OrderResult:
        """Submit *order* to Upbit and return the result.

        Args:
            order: Order parameters.

        Returns:
            :class:`OrderResult` populated from the API response.
        """
        try:
            upbit_side = "bid" if order.side == "buy" else "ask"

            if order.order_type == "market":
                if order.side == "buy":
                    # Market buy: specify KRW amount via ``price``
                    krw_amount_int = int(order.quantity or 0)
                    if krw_amount_int < 5_000:
                        return OrderResult(
                            success=False,
                            market=order.market,
                            side=order.side,
                            error=f"Order amount {krw_amount_int:,} KRW is below Upbit minimum (5,000 KRW)",
                        )
                    raw = await self._client.create_order(
                        market=order.market,
                        side=upbit_side,
                        price=str(krw_amount_int),
                        ord_type="price",
                    )
                else:
                    # Market sell: specify coin volume via ``volume``
                    qty = order.quantity or 0.0
                    volume = f"{qty:.8f}" if qty else None
                    raw = await self._client.create_order(
                        market=order.market,
                        side=upbit_side,
                        volume=volume,
                        ord_type="market",
                    )
            else:
                # Limit order
                raw = await self._client.create_order(
                    market=order.market,
                    side=upbit_side,
                    volume=str(order.quantity) if order.quantity else None,
                    price=str(int(order.price)) if order.price else None,
                    ord_type="limit",
                )

            fill_price = float(raw.get("price") or raw.get("avg_price") or 0)
            fill_volume = float(raw.get("volume") or raw.get("executed_volume") or 0)
            paid_fee = float(raw.get("paid_fee") or 0)

            logger.info(
                "Order executed: %s %s %s qty=%.6f price=%.0f fee=%.2f uuid=%s",
                order.market, order.side, order.order_type,
                fill_volume, fill_price, paid_fee, raw.get("uuid"),
            )

            return OrderResult(
                success=True,
                order_id=raw.get("uuid"),
                market=order.market,
                side=order.side,
                price=fill_price,
                quantity=fill_volume,
                fee=paid_fee,
            )

        except Exception as exc:
            logger.error("Order failed: %s %s -> %s", order.market, order.side, exc)
            return OrderResult(
                success=False,
                market=order.market,
                side=order.side,
                error=str(exc),
            )

    async def get_balance(self, currency: str = "KRW") -> float:
        """Return the available balance for *currency*.

        Args:
            currency: Currency ticker, e.g. "KRW" or "BTC".

        Returns:
            Available balance as float, or 0.0 if not held.
        """
        accounts = await self._client.get_accounts()
        for account in accounts:
            if account.get("currency") == currency:
                return float(account.get("balance", 0))
        return 0.0

    async def get_positions(self) -> dict:
        """Return all non-KRW holdings as a position dict.

        Returns:
            Dict mapping ``"KRW-<COIN>"`` → ``{quantity, avg_price, current_value}``.
            ``current_value`` is estimated from avg_price (no ticker fetch).
        """
        accounts = await self._client.get_accounts()
        positions: dict = {}
        for account in accounts:
            currency = account.get("currency", "")
            if currency == "KRW":
                continue
            balance = float(account.get("balance", 0))
            avg_buy_price = float(account.get("avg_buy_price", 0))
            if balance <= 0:
                continue
            market = f"KRW-{currency}"
            positions[market] = {
                "quantity": balance,
                "avg_price": avg_buy_price,
                "current_value": balance * avg_buy_price,
            }
        return positions
