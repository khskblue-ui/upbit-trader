from __future__ import annotations

import logging

from src.risk.base import BaseRiskRule, PortfolioState, RiskCheckResult, RiskDecision

logger = logging.getLogger(__name__)


class MDDCircuitBreakerRule(BaseRiskRule):
    """Halt trading when Maximum Drawdown exceeds the configured threshold.

    MDD is computed as ``(peak_balance - total_balance) / peak_balance``.

    Config keys (all optional):
        enabled (bool): Whether the rule is active. Default True.
        max_drawdown_pct (float): Maximum tolerable drawdown as a fraction
            (e.g. 0.15 = 15%). Default 0.15.
    """

    name = "mdd_circuit_breaker"
    description = "Halts all trading when portfolio drawdown from peak exceeds the configured threshold."

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_drawdown_pct: float = config.get("max_drawdown_pct", 0.15)

    async def evaluate(self, signal, portfolio: PortfolioState) -> RiskCheckResult:
        # Can't compute MDD without a valid peak
        if portfolio.peak_balance <= 0:
            return RiskCheckResult(
                decision=RiskDecision.APPROVE,
                rule_name=self.name,
                reason="Peak balance not yet established; MDD check skipped.",
            )

        drawdown = (portfolio.peak_balance - portfolio.total_balance) / portfolio.peak_balance

        if drawdown >= self.max_drawdown_pct:
            reason = (
                f"Maximum drawdown {drawdown:.2%} has breached circuit-breaker threshold "
                f"{self.max_drawdown_pct:.2%} "
                f"(peak={portfolio.peak_balance:.2f}, current={portfolio.total_balance:.2f})."
            )
            logger.critical(
                "MDD CIRCUIT BREAKER TRIGGERED: %s -- ALL trading halted.", reason
            )
            return RiskCheckResult(
                decision=RiskDecision.REJECT,
                rule_name=self.name,
                reason=reason,
            )

        return RiskCheckResult(
            decision=RiskDecision.APPROVE,
            rule_name=self.name,
            reason=f"Drawdown {drawdown:.2%} within threshold {self.max_drawdown_pct:.2%}.",
        )
