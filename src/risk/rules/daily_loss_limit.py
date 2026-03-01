from __future__ import annotations

import logging

from src.risk.base import BaseRiskRule, PortfolioState, RiskCheckResult, RiskDecision

logger = logging.getLogger(__name__)


class DailyLossLimitRule(BaseRiskRule):
    """Reject new trades when today's realized loss exceeds the configured threshold.

    Config keys (all optional):
        enabled (bool): Whether the rule is active. Default True.
        max_daily_loss_pct (float): Maximum allowable daily loss as a fraction of
            total_balance (e.g. 0.05 = 5%). Default 0.05.
    """

    name = "daily_loss_limit"
    description = "Halts trading when daily P&L loss exceeds the configured percentage of total balance."

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_daily_loss_pct: float = config.get("max_daily_loss_pct", 0.05)

    async def evaluate(self, signal, portfolio: PortfolioState) -> RiskCheckResult:
        if portfolio.total_balance <= 0:
            return RiskCheckResult(
                decision=RiskDecision.APPROVE,
                rule_name=self.name,
                reason="Total balance is zero; daily loss check skipped.",
            )

        loss_ratio = -portfolio.daily_pnl / portfolio.total_balance  # positive when losing

        if loss_ratio >= self.max_daily_loss_pct:
            reason = (
                f"Daily loss {loss_ratio:.2%} has reached the limit "
                f"{self.max_daily_loss_pct:.2%} of total balance "
                f"(daily_pnl={portfolio.daily_pnl:.2f}, total={portfolio.total_balance:.2f})."
            )
            logger.warning("DailyLossLimitRule triggered: %s", reason)
            return RiskCheckResult(
                decision=RiskDecision.REJECT,
                rule_name=self.name,
                reason=reason,
            )

        return RiskCheckResult(
            decision=RiskDecision.APPROVE,
            rule_name=self.name,
            reason=(
                f"Daily loss {loss_ratio:.2%} within limit {self.max_daily_loss_pct:.2%}."
            ),
        )
