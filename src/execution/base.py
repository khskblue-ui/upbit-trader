from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class OrderRequest(BaseModel):
    market: str
    side: str  # "buy" / "sell"
    price: float | None = None
    quantity: float | None = None
    order_type: str = "market"  # "market" / "limit"


class OrderResult(BaseModel):
    success: bool
    order_id: str | None = None
    market: str = ""
    side: str = ""
    price: float = 0.0
    quantity: float = 0.0
    fee: float = 0.0
    error: str | None = None


class BaseExecutor(ABC):
    """Abstract base class for order executors (live, paper, backtest)."""

    def __init__(self) -> None:
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    async def execute_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order and return the result.

        Args:
            order: Order parameters including market, side, quantity, price.

        Returns:
            OrderResult with success flag, order_id, fill details, and fee.
        """
        ...

    @abstractmethod
    async def get_balance(self, currency: str = "KRW") -> float:
        """Return available balance for the given currency.

        Args:
            currency: Currency ticker, e.g. "KRW" or "BTC".

        Returns:
            Available balance as a float.
        """
        ...

    @abstractmethod
    async def get_positions(self) -> dict:
        """Return current open positions.

        Returns:
            Dict mapping market -> {quantity, avg_price, current_value}.
        """
        ...
