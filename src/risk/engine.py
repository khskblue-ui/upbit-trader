from __future__ import annotations

import logging

from src.risk.base import BaseRiskRule, PortfolioState, RiskCheckResult, RiskDecision
from src.strategy.base import TradeSignal

logger = logging.getLogger(__name__)


class RiskEngine:
    """Evaluates a trade signal against an ordered list of risk rules.

    Rules are evaluated in order. The first REJECT short-circuits further
    evaluation. MODIFY rules update ``signal.suggested_size`` before passing
    to subsequent rules, so later rules see the adjusted size.
    """

    def __init__(self, rules: list[BaseRiskRule]) -> None:
        self.rules = rules

    async def check(
        self, signal: TradeSignal, portfolio: PortfolioState
    ) -> tuple[RiskDecision, list[RiskCheckResult]]:
        """Run all enabled rules against the signal.

        Args:
            signal: The trade signal to evaluate.
            portfolio: Current portfolio state.

        Returns:
            Tuple of (final_decision, list_of_results). If any rule returns
            REJECT the final decision is REJECT; otherwise APPROVE.
        """
        results: list[RiskCheckResult] = []
        current_signal = signal

        for rule in self.rules:
            if not rule.enabled:
                logger.debug("Skipping disabled rule: %s", rule.name)
                continue

            result = await rule.evaluate(current_signal, portfolio)
            results.append(result)
            logger.debug(
                "Rule %s -> %s: %s", rule.name, result.decision.value, result.reason
            )

            if result.decision == RiskDecision.REJECT:
                logger.warning(
                    "Trade signal REJECTED by rule '%s': %s", rule.name, result.reason
                )
                return RiskDecision.REJECT, results

            if result.decision == RiskDecision.MODIFY and result.modified_size is not None:
                current_signal = current_signal.model_copy(
                    update={"suggested_size": result.modified_size}
                )
                logger.info(
                    "Rule '%s' modified suggested_size to %.6f",
                    rule.name,
                    result.modified_size,
                )

        return RiskDecision.APPROVE, results
