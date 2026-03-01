from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RiskDecision(Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


class RiskCheckResult(BaseModel):
    decision: RiskDecision
    rule_name: str
    reason: str
    modified_size: float | None = None


class PortfolioState(BaseModel):
    total_balance: float
    available_balance: float
    positions: dict  # market -> {quantity, avg_price, current_value}
    daily_pnl: float = 0.0
    peak_balance: float = 0.0
    consecutive_losses: int = 0


class BaseRiskRule(ABC):
    name: str
    description: str = ""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.enabled: bool = config.get("enabled", True)
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    async def evaluate(self, signal, portfolio: PortfolioState) -> RiskCheckResult:
        """Evaluate the trade signal against this risk rule.

        Args:
            signal: TradeSignal to evaluate.
            portfolio: Current portfolio state.

        Returns:
            RiskCheckResult with decision, reason, and optional modified_size.
        """
        ...
