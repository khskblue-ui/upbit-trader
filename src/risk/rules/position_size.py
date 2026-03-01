from __future__ import annotations

import logging

from src.risk.base import BaseRiskRule, PortfolioState, RiskCheckResult, RiskDecision

logger = logging.getLogger(__name__)


class MaxPositionSizeRule(BaseRiskRule):
    """Enforce limits on single-asset allocation, total investment, and concurrent positions.

    Config keys (all optional):
        enabled (bool): Whether the rule is active. Default True.
        max_single_asset_ratio (float): Max fraction of total_balance for one asset. Default 0.20.
        max_total_investment_ratio (float): Max fraction of total_balance across all positions. Default 0.70.
        max_concurrent_positions (int): Max number of open positions. Default 5.
    """

    name = "max_position_size"
    description = "Limits single-asset exposure, total investment ratio, and concurrent position count."

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_single_asset_ratio: float = config.get("max_single_asset_ratio", 0.20)
        self.max_total_investment_ratio: float = config.get("max_total_investment_ratio", 0.70)
        self.max_concurrent_positions: int = int(config.get("max_concurrent_positions", 5))

    async def evaluate(self, signal, portfolio: PortfolioState) -> RiskCheckResult:
        from src.strategy.base import Signal

        # Only check BUY signals; SELL/HOLD pass through
        if signal.signal != Signal.BUY:
            return RiskCheckResult(
                decision=RiskDecision.APPROVE,
                rule_name=self.name,
                reason="Non-buy signal; position size check skipped.",
            )

        # --- Check concurrent positions ---
        num_positions = len(portfolio.positions)
        if num_positions >= self.max_concurrent_positions:
            reason = (
                f"Max concurrent positions reached ({num_positions}/{self.max_concurrent_positions})."
            )
            logger.warning(reason)
            return RiskCheckResult(
                decision=RiskDecision.REJECT,
                rule_name=self.name,
                reason=reason,
            )

        # --- Check total investment ratio ---
        total_invested = sum(
            pos.get("current_value", 0.0) for pos in portfolio.positions.values()
        )
        total_invested_ratio = (
            total_invested / portfolio.total_balance if portfolio.total_balance > 0 else 0.0
        )
        if total_invested_ratio >= self.max_total_investment_ratio:
            reason = (
                f"Total investment ratio {total_invested_ratio:.1%} exceeds limit "
                f"{self.max_total_investment_ratio:.1%}."
            )
            logger.warning(reason)
            return RiskCheckResult(
                decision=RiskDecision.REJECT,
                rule_name=self.name,
                reason=reason,
            )

        # --- Determine requested trade size ---
        requested_size = signal.suggested_size
        if requested_size is None:
            # Default to max single-asset ratio of total balance
            requested_size = self.max_single_asset_ratio * portfolio.total_balance

        max_allowed = self.max_single_asset_ratio * portfolio.total_balance

        # Also cap so total investment stays within limit
        remaining_capacity = (
            self.max_total_investment_ratio * portfolio.total_balance - total_invested
        )
        max_allowed = min(max_allowed, remaining_capacity)
        max_allowed = min(max_allowed, portfolio.available_balance)

        if requested_size <= max_allowed:
            return RiskCheckResult(
                decision=RiskDecision.APPROVE,
                rule_name=self.name,
                reason=f"Requested size {requested_size:.2f} within limits.",
            )

        # Modify size down to the max allowed
        if max_allowed <= 0:
            return RiskCheckResult(
                decision=RiskDecision.REJECT,
                rule_name=self.name,
                reason=f"No capacity available. Max allowed: {max_allowed:.2f}.",
            )

        reason = (
            f"Requested size {requested_size:.2f} exceeds limit {max_allowed:.2f}; "
            f"reducing to {max_allowed:.2f}."
        )
        logger.info(reason)
        return RiskCheckResult(
            decision=RiskDecision.MODIFY,
            rule_name=self.name,
            reason=reason,
            modified_size=max_allowed,
        )
