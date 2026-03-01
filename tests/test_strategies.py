"""Tests for src/strategy/base.py and src/strategy/registry.py."""

from __future__ import annotations

import pytest

from src.strategy.base import (
    BaseStrategy,
    MarketData,
    Signal,
    StrategyConfig,
    TradeSignal,
)
from src.strategy.registry import (
    STRATEGIES,
    available_strategies,
    create_strategy,
    register,
)


# ---------------------------------------------------------------------------
# Helper: minimal concrete strategy for tests
# ---------------------------------------------------------------------------

class _ConcreteStrategy(BaseStrategy):
    name = "_test_concrete"
    version = "0.1.0"

    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        return TradeSignal(
            signal=Signal.HOLD,
            market=market,
            confidence=0.5,
            reason="test",
        )

    def required_indicators(self) -> list[str]:
        return ["rsi_14"]

    def required_timeframes(self) -> list[str]:
        return ["1h"]


# ---------------------------------------------------------------------------
# BaseStrategy — abstract interface
# ---------------------------------------------------------------------------

class TestBaseStrategyAbstract:
    def test_cannot_instantiate_base_strategy_directly(self):
        with pytest.raises(TypeError):
            BaseStrategy(config=StrategyConfig())  # type: ignore[abstract]

    def test_concrete_subclass_can_be_instantiated(self):
        cfg = StrategyConfig()
        strategy = _ConcreteStrategy(cfg)
        assert strategy.config is cfg

    def test_config_stored_on_instance(self):
        cfg = StrategyConfig(enabled=False, markets=["KRW-ETH"])
        strategy = _ConcreteStrategy(cfg)
        assert strategy.config.enabled is False
        assert "KRW-ETH" in strategy.config.markets

    def test_validate_config_returns_empty_list_by_default(self):
        strategy = _ConcreteStrategy(StrategyConfig())
        assert strategy.validate_config() == []

    async def test_on_startup_runs_without_error(self):
        strategy = _ConcreteStrategy(StrategyConfig())
        await strategy.on_startup()

    async def test_on_shutdown_runs_without_error(self):
        strategy = _ConcreteStrategy(StrategyConfig())
        await strategy.on_shutdown()

    async def test_on_trade_executed_runs_without_error(self):
        strategy = _ConcreteStrategy(StrategyConfig())
        await strategy.on_trade_executed({"order_id": "abc", "status": "done"})


# ---------------------------------------------------------------------------
# @register decorator and registry functions
# ---------------------------------------------------------------------------

class TestRegisterDecorator:
    def setup_method(self):
        """Capture registry state before each test; restore after."""
        self._original = dict(STRATEGIES)

    def teardown_method(self):
        STRATEGIES.clear()
        STRATEGIES.update(self._original)

    def test_register_adds_strategy_to_registry(self):
        @register
        class _Reg(BaseStrategy):
            name = "_reg_test"

            async def generate_signal(self, market, data):
                ...

            def required_indicators(self):
                return []

            def required_timeframes(self):
                return []

        assert "_reg_test" in STRATEGIES
        assert STRATEGIES["_reg_test"] is _Reg

    def test_register_returns_class_unchanged(self):
        class _Ret(BaseStrategy):
            name = "_ret_test"

            async def generate_signal(self, market, data):
                ...

            def required_indicators(self):
                return []

            def required_timeframes(self):
                return []

        result = register(_Ret)
        assert result is _Ret

    def test_create_strategy_returns_correct_instance(self):
        @register
        class _Create(_ConcreteStrategy):
            name = "_create_test"

        cfg = StrategyConfig()
        instance = create_strategy("_create_test", cfg)
        assert isinstance(instance, _Create)
        assert instance.config is cfg

    def test_create_strategy_unknown_name_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            create_strategy("__nonexistent__", StrategyConfig())

    def test_available_strategies_returns_sorted_list(self):
        @register
        class _Z(_ConcreteStrategy):
            name = "_zzz"

        @register
        class _A(_ConcreteStrategy):
            name = "_aaa"

        names = available_strategies()
        # Must be sorted
        assert names == sorted(names)
        assert "_aaa" in names
        assert "_zzz" in names


# ---------------------------------------------------------------------------
# StrategyConfig
# ---------------------------------------------------------------------------

class TestStrategyConfig:
    def test_default_values(self):
        cfg = StrategyConfig()
        assert cfg.enabled is True
        assert cfg.markets == ["KRW-BTC"]

    def test_extra_params_allowed(self):
        cfg = StrategyConfig(rsi_period=14, threshold=0.3)
        assert cfg.rsi_period == 14  # type: ignore[attr-defined]
        assert cfg.threshold == 0.3  # type: ignore[attr-defined]

    def test_custom_markets(self):
        cfg = StrategyConfig(markets=["KRW-ETH", "KRW-XRP"])
        assert cfg.markets == ["KRW-ETH", "KRW-XRP"]

    def test_enabled_false(self):
        cfg = StrategyConfig(enabled=False)
        assert cfg.enabled is False


# ---------------------------------------------------------------------------
# TradeSignal
# ---------------------------------------------------------------------------

class TestTradeSignal:
    def test_valid_buy_signal(self):
        sig = TradeSignal(
            signal=Signal.BUY,
            market="KRW-BTC",
            confidence=0.9,
            reason="test",
        )
        assert sig.signal == Signal.BUY
        assert sig.suggested_size is None
        assert sig.metadata == {}

    def test_valid_sell_signal_with_size(self):
        sig = TradeSignal(
            signal=Signal.SELL,
            market="KRW-ETH",
            confidence=0.7,
            reason="take profit",
            suggested_size=500_000.0,
        )
        assert sig.signal == Signal.SELL
        assert sig.suggested_size == 500_000.0

    def test_serialization_round_trip(self):
        sig = TradeSignal(
            signal=Signal.HOLD,
            market="KRW-BTC",
            confidence=0.5,
            reason="neutral",
            metadata={"indicator": "rsi", "value": 50.0},
        )
        data = sig.model_dump()
        restored = TradeSignal(**data)
        assert restored.signal == Signal.HOLD
        assert restored.metadata["value"] == 50.0

    def test_signal_enum_values(self):
        assert Signal.BUY.value == "buy"
        assert Signal.SELL.value == "sell"
        assert Signal.HOLD.value == "hold"


# ---------------------------------------------------------------------------
# MarketData
# ---------------------------------------------------------------------------

class TestMarketData:
    def test_valid_construction(self, sample_candle_data):
        md = MarketData(
            market="KRW-BTC",
            candles=sample_candle_data,
            current_price=50_000_000.0,
        )
        assert md.market == "KRW-BTC"
        assert len(md.candles) == 50
        assert md.orderbook is None
        assert md.indicators == {}

    def test_with_indicators(self, sample_candle_data):
        md = MarketData(
            market="KRW-BTC",
            candles=sample_candle_data,
            current_price=50_000_000.0,
            indicators={"rsi_14": 35.2, "bb_20_2": {"bb_upper": 51_000_000.0}},
        )
        assert md.indicators["rsi_14"] == 35.2
