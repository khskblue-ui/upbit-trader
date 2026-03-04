from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class TradeSignal(BaseModel):
    signal: Signal
    market: str
    confidence: float  # 0.0 ~ 1.0
    reason: str
    suggested_size: float | None = None
    metadata: dict = Field(default_factory=dict)


class StrategyConfig(BaseModel):
    enabled: bool = True
    markets: list[str] = ["KRW-BTC"]

    model_config = ConfigDict(extra="allow")


class MarketData(BaseModel):
    """Market data passed to strategies for signal generation."""

    market: str
    candles: list  # list of candle dicts with OHLCV
    current_price: float
    orderbook: dict | None = None
    indicators: dict = {}  # pre-computed indicators like {"rsi_14": 35.2}
    portfolio_balance: float = 0.0  # 실제 사용 가능 KRW 잔액 (TradingEngine이 주입)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class BaseStrategy(ABC):
    name: str
    version: str = "1.0.0"
    description: str = ""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    async def generate_signal(self, market: str, data: MarketData) -> TradeSignal:
        """Generate a trading signal for the given market and data."""
        ...

    @abstractmethod
    def required_indicators(self) -> list[str]:
        """Return list of indicator names this strategy requires (e.g. ['rsi_14', 'bb_20_2'])."""
        ...

    @abstractmethod
    def required_timeframes(self) -> list[str]:
        """Return list of timeframes this strategy requires (e.g. ['1m', '1h', '1d'])."""
        ...

    def validate_config(self) -> list[str]:
        """Validate strategy config. Return list of error messages (empty = valid)."""
        return []

    async def on_startup(self) -> None:
        """Called once when the trading engine starts."""
        self._logger.info("Strategy %s v%s started.", self.name, self.version)

    async def on_shutdown(self) -> None:
        """Called once when the trading engine shuts down."""
        self._logger.info("Strategy %s shutting down.", self.name)

    async def on_trade_executed(self, trade_result: dict) -> None:
        """Called after a trade is executed with the result details."""
        self._logger.debug("Trade executed for strategy %s: %s", self.name, trade_result)
