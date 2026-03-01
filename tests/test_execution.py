"""Tests for src/execution/base.py — OrderRequest, OrderResult, BaseExecutor."""

from __future__ import annotations

import pytest

from src.execution.base import BaseExecutor, OrderRequest, OrderResult


# ---------------------------------------------------------------------------
# OrderRequest model
# ---------------------------------------------------------------------------

class TestOrderRequest:
    def test_valid_market_buy(self):
        order = OrderRequest(market="KRW-BTC", side="buy")
        assert order.market == "KRW-BTC"
        assert order.side == "buy"
        assert order.order_type == "market"
        assert order.price is None
        assert order.quantity is None

    def test_valid_limit_sell(self):
        order = OrderRequest(
            market="KRW-ETH",
            side="sell",
            price=2_000_000.0,
            quantity=0.5,
            order_type="limit",
        )
        assert order.order_type == "limit"
        assert order.price == 2_000_000.0
        assert order.quantity == 0.5

    def test_default_order_type_is_market(self):
        order = OrderRequest(market="KRW-XRP", side="buy")
        assert order.order_type == "market"

    def test_serialization_round_trip(self):
        order = OrderRequest(
            market="KRW-BTC",
            side="buy",
            quantity=0.001,
            order_type="market",
        )
        data = order.model_dump()
        restored = OrderRequest(**data)
        assert restored.market == order.market
        assert restored.quantity == order.quantity

    def test_price_and_quantity_optional(self):
        order = OrderRequest(market="KRW-BTC", side="sell")
        assert order.price is None
        assert order.quantity is None


# ---------------------------------------------------------------------------
# OrderResult model
# ---------------------------------------------------------------------------

class TestOrderResult:
    def test_successful_result(self):
        result = OrderResult(
            success=True,
            order_id="ord_123",
            market="KRW-BTC",
            side="buy",
            price=50_000_000.0,
            quantity=0.001,
            fee=25_000.0,
        )
        assert result.success is True
        assert result.order_id == "ord_123"
        assert result.error is None

    def test_failed_result(self):
        result = OrderResult(
            success=False,
            error="Insufficient balance",
        )
        assert result.success is False
        assert result.error == "Insufficient balance"
        assert result.order_id is None

    def test_default_values(self):
        result = OrderResult(success=True)
        assert result.market == ""
        assert result.side == ""
        assert result.price == 0.0
        assert result.quantity == 0.0
        assert result.fee == 0.0
        assert result.order_id is None
        assert result.error is None

    def test_serialization_round_trip(self):
        result = OrderResult(
            success=True,
            order_id="ord_456",
            market="KRW-ETH",
            side="sell",
            price=2_000_000.0,
            quantity=1.0,
            fee=1_000.0,
        )
        data = result.model_dump()
        restored = OrderResult(**data)
        assert restored.order_id == result.order_id
        assert restored.fee == result.fee


# ---------------------------------------------------------------------------
# BaseExecutor — abstract interface
# ---------------------------------------------------------------------------

class TestBaseExecutorAbstract:
    def test_cannot_instantiate_base_executor_directly(self):
        with pytest.raises(TypeError):
            BaseExecutor()  # type: ignore[abstract]

    def test_concrete_subclass_can_be_instantiated(self):
        class _ConcreteExecutor(BaseExecutor):
            async def execute_order(self, order: OrderRequest) -> OrderResult:
                return OrderResult(success=True, market=order.market, side=order.side)

            async def get_balance(self, currency: str = "KRW") -> float:
                return 10_000_000.0

            async def get_positions(self) -> dict:
                return {}

        executor = _ConcreteExecutor()
        assert executor is not None

    async def test_execute_order_returns_order_result(self):
        class _PaperExecutor(BaseExecutor):
            async def execute_order(self, order: OrderRequest) -> OrderResult:
                return OrderResult(
                    success=True,
                    market=order.market,
                    side=order.side,
                    price=order.price or 0.0,
                    quantity=order.quantity or 0.0,
                )

            async def get_balance(self, currency: str = "KRW") -> float:
                return 5_000_000.0

            async def get_positions(self) -> dict:
                return {"KRW-BTC": {"quantity": 0.01, "avg_price": 50_000_000.0}}

        executor = _PaperExecutor()
        order = OrderRequest(market="KRW-BTC", side="buy", quantity=0.001)
        result = await executor.execute_order(order)

        assert isinstance(result, OrderResult)
        assert result.success is True
        assert result.market == "KRW-BTC"

    async def test_get_balance_returns_float(self):
        class _BalanceExecutor(BaseExecutor):
            async def execute_order(self, order):
                return OrderResult(success=True)

            async def get_balance(self, currency: str = "KRW") -> float:
                return 7_500_000.0

            async def get_positions(self) -> dict:
                return {}

        executor = _BalanceExecutor()
        balance = await executor.get_balance("KRW")
        assert isinstance(balance, float)
        assert balance == 7_500_000.0

    async def test_get_positions_returns_dict(self):
        class _PositionsExecutor(BaseExecutor):
            async def execute_order(self, order):
                return OrderResult(success=True)

            async def get_balance(self, currency: str = "KRW") -> float:
                return 0.0

            async def get_positions(self) -> dict:
                return {
                    "KRW-BTC": {
                        "quantity": 0.1,
                        "avg_price": 50_000_000.0,
                        "current_value": 5_000_000.0,
                    }
                }

        executor = _PositionsExecutor()
        positions = await executor.get_positions()
        assert isinstance(positions, dict)
        assert "KRW-BTC" in positions
        assert positions["KRW-BTC"]["quantity"] == 0.1
