from __future__ import annotations

import logging

from src.risk.base import BaseRiskRule, PortfolioState, RiskCheckResult, RiskDecision

logger = logging.getLogger(__name__)

# Safety margin applied to available_balance before using it as a cap on order size.
# Upbit charges a 0.05 % maker/taker fee ON TOP of the order amount for market buys,
# so using the raw available balance can result in "insufficient funds" API errors.
# Multiplying by 0.999 reserves ≈0.1 % as a combined fee + slippage buffer.
_SAFE_BALANCE_RATIO: float = 0.999


class MaxPositionSizeRule(BaseRiskRule):
    """Enforce limits on single-asset allocation, total investment, and concurrent positions.

    Config keys (all optional):
        enabled (bool): Whether the rule is active. Default True.
        max_single_asset_ratio (float): Max fraction of total_balance for one asset. Default 0.20.
        max_total_investment_ratio (float): Max fraction of total_balance across all
            *managed* positions. Default 0.70.
        max_concurrent_positions (int): Max number of open *managed* positions. Default 5.
        managed_markets (list[str]): Whitelist of markets the bot actively manages.
            When non-empty, only positions in this list count toward ``total_invested``
            and concurrent-position checks.  Positions outside the list (e.g. manually-
            held BTC) are completely ignored by this rule.
            When empty (default), ALL portfolio positions are counted (legacy behaviour).

    Example risk.yaml entry::

        - name: max_position_size
          enabled: true
          max_single_asset_ratio: 0.20
          max_total_investment_ratio: 0.70
          max_concurrent_positions: 5
          managed_markets:
            - KRW-ETH
    """

    name = "max_position_size"
    description = "Limits single-asset exposure, total investment ratio, and concurrent position count."

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_single_asset_ratio: float = config.get("max_single_asset_ratio", 0.20)
        self.max_total_investment_ratio: float = config.get("max_total_investment_ratio", 0.70)
        self.max_concurrent_positions: int = int(config.get("max_concurrent_positions", 5))
        # Normalise to a set for O(1) lookup; empty set = "count everything"
        raw_markets = config.get("managed_markets", [])
        self.managed_markets: set[str] = set(raw_markets) if raw_markets else set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _managed_positions(self, portfolio: PortfolioState) -> dict:
        """Return only the positions that this rule manages.

        If ``managed_markets`` is empty, all portfolio positions are returned
        (backward-compatible behaviour).  Otherwise, only positions whose
        market key is in ``managed_markets`` are returned.
        """
        if not self.managed_markets:
            return portfolio.positions
        return {
            market: pos
            for market, pos in portfolio.positions.items()
            if market in self.managed_markets
        }

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def evaluate(self, signal, portfolio: PortfolioState) -> RiskCheckResult:
        from src.strategy.base import Signal

        # Only check BUY signals; SELL/HOLD pass through
        if signal.signal != Signal.BUY:
            return RiskCheckResult(
                decision=RiskDecision.APPROVE,
                rule_name=self.name,
                reason="Non-buy signal; position size check skipped.",
            )

        managed = self._managed_positions(portfolio)

        # --- Check concurrent positions (managed only) ---
        num_positions = len(managed)
        if num_positions >= self.max_concurrent_positions:
            reason = (
                f"Max concurrent positions reached ({num_positions}/{self.max_concurrent_positions})."
            )
            if self.managed_markets:
                reason += f" (managed markets: {sorted(self.managed_markets)})"
            logger.warning(reason)
            return RiskCheckResult(
                decision=RiskDecision.REJECT,
                rule_name=self.name,
                reason=reason,
            )

        # --- Check total investment ratio (managed positions only) ---
        total_invested = sum(
            pos.get("current_value", 0.0) for pos in managed.values()
        )
        total_invested_ratio = (
            total_invested / portfolio.total_balance if portfolio.total_balance > 0 else 0.0
        )
        if total_invested_ratio >= self.max_total_investment_ratio:
            reason = (
                f"Total investment ratio {total_invested_ratio:.1%} exceeds limit "
                f"{self.max_total_investment_ratio:.1%}."
            )
            if self.managed_markets:
                reason += f" (counting managed positions only: {sorted(self.managed_markets)})"
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

        # Reserve a fee+slippage buffer: Upbit charges ~0.05 % on market buys ON TOP
        # of the order amount.  Using the raw available_balance as the cap can cause
        # "insufficient funds" API rejections when the fee pushes the total cost just
        # over the balance.  _SAFE_BALANCE_RATIO (0.999) leaves ≈0.1 % headroom.
        safe_available = portfolio.available_balance * _SAFE_BALANCE_RATIO
        max_allowed = min(max_allowed, safe_available)

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
