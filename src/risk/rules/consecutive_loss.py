from __future__ import annotations

import logging

from src.risk.base import BaseRiskRule, PortfolioState, RiskCheckResult, RiskDecision

logger = logging.getLogger(__name__)


class ConsecutiveLossGuardRule(BaseRiskRule):
    """Pause trading after a run of consecutive losing trades.

    Config keys (all optional):
        enabled (bool): Whether the rule is active. Default True.
        max_consecutive_losses (int): Number of consecutive losses allowed before
            trading is halted. Default 3.
    """

    name = "consecutive_loss_guard"
    description = "Rejects new trades when consecutive loss count reaches the configured limit."

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_consecutive_losses: int = int(config.get("max_consecutive_losses", 3))

    async def evaluate(self, signal, portfolio: PortfolioState) -> RiskCheckResult:
        if portfolio.consecutive_losses >= self.max_consecutive_losses:
            reason = (
                f"Consecutive losses ({portfolio.consecutive_losses}) have reached the limit "
                f"({self.max_consecutive_losses}). Trading paused until reset."
            )
            logger.warning("ConsecutiveLossGuardRule triggered: %s", reason)
            return RiskCheckResult(
                decision=RiskDecision.REJECT,
                rule_name=self.name,
                reason=reason,
            )

        return RiskCheckResult(
            decision=RiskDecision.APPROVE,
            rule_name=self.name,
            reason=(
                f"Consecutive losses {portfolio.consecutive_losses} "
                f"below limit {self.max_consecutive_losses}."
            ),
        )
