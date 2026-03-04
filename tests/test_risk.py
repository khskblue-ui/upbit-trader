"""Tests for RiskEngine and all risk rules."""

from __future__ import annotations

import pytest

from src.risk.base import PortfolioState, RiskCheckResult, RiskDecision
from src.risk.engine import RiskEngine
from src.risk.rules.consecutive_loss import ConsecutiveLossGuardRule
from src.risk.rules.daily_loss_limit import DailyLossLimitRule
from src.risk.rules.mdd_circuit_breaker import MDDCircuitBreakerRule
from src.risk.rules.position_size import MaxPositionSizeRule
from src.strategy.base import Signal, TradeSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy_signal(size: float | None = 1_000_000.0) -> TradeSignal:
    return TradeSignal(
        signal=Signal.BUY,
        market="KRW-BTC",
        confidence=0.8,
        reason="test",
        suggested_size=size,
    )


def _sell_signal() -> TradeSignal:
    return TradeSignal(
        signal=Signal.SELL,
        market="KRW-BTC",
        confidence=0.8,
        reason="test",
    )


def _portfolio(
    total: float = 10_000_000.0,
    available: float = 8_000_000.0,
    positions: dict | None = None,
    daily_pnl: float = 0.0,
    peak: float = 10_000_000.0,
    consec_losses: int = 0,
) -> PortfolioState:
    return PortfolioState(
        total_balance=total,
        available_balance=available,
        positions=positions or {},
        daily_pnl=daily_pnl,
        peak_balance=peak,
        consecutive_losses=consec_losses,
    )


# ---------------------------------------------------------------------------
# Stub rule helpers
# ---------------------------------------------------------------------------

class _ApproveRule(MaxPositionSizeRule):
    """Always approves — overrides evaluate to return APPROVE."""
    name = "_stub_approve"

    async def evaluate(self, signal, portfolio):
        return RiskCheckResult(
            decision=RiskDecision.APPROVE,
            rule_name=self.name,
            reason="stub approve",
        )


class _RejectRule(MaxPositionSizeRule):
    name = "_stub_reject"

    async def evaluate(self, signal, portfolio):
        return RiskCheckResult(
            decision=RiskDecision.REJECT,
            rule_name=self.name,
            reason="stub reject",
        )


class _ModifyRule(MaxPositionSizeRule):
    name = "_stub_modify"
    _new_size: float = 500_000.0

    async def evaluate(self, signal, portfolio):
        return RiskCheckResult(
            decision=RiskDecision.MODIFY,
            rule_name=self.name,
            reason="stub modify",
            modified_size=self._new_size,
        )


class _SizeCapturingRule(MaxPositionSizeRule):
    """Records the suggested_size it saw on the signal."""
    name = "_stub_capture"
    captured_size: float | None = None

    async def evaluate(self, signal, portfolio):
        _SizeCapturingRule.captured_size = signal.suggested_size
        return RiskCheckResult(
            decision=RiskDecision.APPROVE,
            rule_name=self.name,
            reason="captured",
        )


def _stub(cls, enabled=True):
    rule = cls.__new__(cls)
    rule.config = {"enabled": enabled}
    rule.enabled = enabled
    rule._logger = __import__("logging").getLogger("test")
    return rule


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------

class TestRiskEngine:
    async def test_all_rules_approve_returns_approve(self):
        rules = [_stub(_ApproveRule), _stub(_ApproveRule)]
        engine = RiskEngine(rules)
        decision, results = await engine.check(_buy_signal(), _portfolio())
        assert decision == RiskDecision.APPROVE
        assert len(results) == 2

    async def test_one_reject_returns_reject_and_short_circuits(self):
        rules = [_stub(_ApproveRule), _stub(_RejectRule), _stub(_ApproveRule)]
        engine = RiskEngine(rules)
        decision, results = await engine.check(_buy_signal(), _portfolio())
        assert decision == RiskDecision.REJECT
        # Short-circuit: only 2 results (approve + reject), not 3
        assert len(results) == 2

    async def test_modify_propagates_size_to_next_rule(self):
        modify = _stub(_ModifyRule)
        capture = _stub(_SizeCapturingRule)
        engine = RiskEngine([modify, capture])
        _SizeCapturingRule.captured_size = None

        await engine.check(_buy_signal(size=2_000_000.0), _portfolio())

        assert _SizeCapturingRule.captured_size == _ModifyRule._new_size

    async def test_disabled_rule_is_skipped(self):
        disabled = _stub(_RejectRule, enabled=False)
        approve = _stub(_ApproveRule)
        engine = RiskEngine([disabled, approve])
        decision, results = await engine.check(_buy_signal(), _portfolio())
        # disabled rule produces no result; only the approve rule runs
        assert decision == RiskDecision.APPROVE
        assert len(results) == 1

    async def test_empty_rules_returns_approve(self):
        engine = RiskEngine([])
        decision, results = await engine.check(_buy_signal(), _portfolio())
        assert decision == RiskDecision.APPROVE
        assert results == []


# ---------------------------------------------------------------------------
# MaxPositionSizeRule
# ---------------------------------------------------------------------------

class TestMaxPositionSizeRule:
    def _rule(self, **cfg) -> MaxPositionSizeRule:
        defaults = {
            "max_single_asset_ratio": 0.20,
            "max_total_investment_ratio": 0.70,
            "max_concurrent_positions": 5,
        }
        defaults.update(cfg)
        return MaxPositionSizeRule(defaults)

    async def test_sell_signal_always_approved(self):
        rule = self._rule()
        result = await rule.evaluate(_sell_signal(), _portfolio())
        assert result.decision == RiskDecision.APPROVE

    async def test_buy_within_limits_approved(self):
        rule = self._rule()
        # 1M out of 10M total = 10% < 20% limit; 1 position < 5 max
        signal = _buy_signal(size=1_000_000.0)
        result = await rule.evaluate(signal, _portfolio())
        assert result.decision == RiskDecision.APPROVE

    async def test_buy_exceeds_concurrent_positions_rejected(self):
        rule = self._rule(max_concurrent_positions=2)
        positions = {
            "KRW-ETH": {"quantity": 1, "avg_price": 2e6, "current_value": 2e6},
            "KRW-XRP": {"quantity": 100, "avg_price": 1000, "current_value": 100_000},
        }
        portfolio = _portfolio(positions=positions)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT
        assert "concurrent" in result.reason.lower()

    async def test_buy_exceeds_total_investment_ratio_rejected(self):
        rule = self._rule(max_total_investment_ratio=0.30)
        # 7M invested of 10M = 70% > 30% limit
        positions = {"KRW-ETH": {"quantity": 1, "avg_price": 7e6, "current_value": 7e6}}
        portfolio = _portfolio(positions=positions)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT

    async def test_buy_exceeds_single_asset_ratio_modified(self):
        rule = self._rule(max_single_asset_ratio=0.20)
        # Requesting 5M but max allowed is 20% of 10M = 2M
        signal = _buy_signal(size=5_000_000.0)
        result = await rule.evaluate(signal, _portfolio())
        assert result.decision == RiskDecision.MODIFY
        assert result.modified_size is not None
        assert result.modified_size <= 2_000_000.0

    async def test_no_capacity_available_rejects(self):
        rule = self._rule(max_single_asset_ratio=0.20, max_total_investment_ratio=0.10)
        # 1M invested of 10M already = 10%, so no room left
        positions = {"KRW-ETH": {"quantity": 1, "avg_price": 1e6, "current_value": 1e6}}
        portfolio = _portfolio(positions=positions, available=0.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT

    # ------------------------------------------------------------------
    # managed_markets filtering
    # ------------------------------------------------------------------

    async def test_unmanaged_btc_excluded_from_total_invested(self):
        """Manually-held BTC must NOT count toward total_invested when managed_markets is set.

        Scenario mirrors a real account:
          - BTC: 10,000,000 KRW (manually held, NOT in managed_markets)
          - KRW available: 100,000
          - total_balance: 10,100,000

        Without filtering: 10M / 10.1M = 99% > 70% → REJECT
        With managed_markets=[KRW-ETH]: 0 managed positions → 0% → APPROVE
        """
        rule = self._rule(
            managed_markets=["KRW-ETH"],
            max_total_investment_ratio=0.70,
        )
        positions = {
            "KRW-BTC": {"quantity": 0.1, "avg_price": 1e8, "current_value": 10_000_000.0},
        }
        portfolio = _portfolio(
            total=10_100_000.0,
            available=100_000.0,
            positions=positions,
        )
        signal = TradeSignal(
            signal=Signal.BUY,
            market="KRW-ETH",
            confidence=0.8,
            reason="test",
            suggested_size=10_000.0,
        )
        result = await rule.evaluate(signal, portfolio)
        assert result.decision in (RiskDecision.APPROVE, RiskDecision.MODIFY), (
            f"Unmanaged BTC should be excluded; expected APPROVE/MODIFY, "
            f"got REJECT. Reason: {result.reason}"
        )

    async def test_managed_eth_position_still_counts_toward_limit(self):
        """A bot-managed ETH position MUST still count toward total_invested."""
        rule = self._rule(
            managed_markets=["KRW-ETH"],
            max_total_investment_ratio=0.30,
        )
        # ETH (managed) = 5M of total 15.1M = 33.1% > 30% → REJECT
        positions = {
            "KRW-ETH": {"quantity": 1, "avg_price": 5e6, "current_value": 5_000_000.0},
            "KRW-BTC": {"quantity": 0.1, "avg_price": 1e8, "current_value": 10_000_000.0},
        }
        portfolio = _portfolio(total=15_100_000.0, available=100_000.0, positions=positions)
        signal = TradeSignal(
            signal=Signal.BUY,
            market="KRW-ETH",
            confidence=0.8,
            reason="test",
            suggested_size=10_000.0,
        )
        result = await rule.evaluate(signal, portfolio)
        assert result.decision == RiskDecision.REJECT, (
            f"Bot-managed ETH position should still count toward limit. "
            f"Reason: {result.reason}"
        )

    async def test_empty_managed_markets_counts_all_positions(self):
        """When managed_markets is empty, all positions count (backward-compatible)."""
        rule = self._rule(
            managed_markets=[],        # empty = legacy behaviour
            max_total_investment_ratio=0.70,
        )
        positions = {
            "KRW-BTC": {"quantity": 0.1, "avg_price": 1e8, "current_value": 10_000_000.0},
        }
        portfolio = _portfolio(total=10_100_000.0, available=100_000.0, positions=positions)
        signal = TradeSignal(
            signal=Signal.BUY,
            market="KRW-ETH",
            confidence=0.8,
            reason="test",
            suggested_size=10_000.0,
        )
        result = await rule.evaluate(signal, portfolio)
        # BTC counts → 99% > 70% → REJECT (old behaviour preserved)
        assert result.decision == RiskDecision.REJECT, (
            f"With empty managed_markets, BTC should count → REJECT. "
            f"Reason: {result.reason}"
        )

    # ------------------------------------------------------------------
    # safe_available_balance (Fix 2 — fee/slippage buffer)
    # ------------------------------------------------------------------

    async def test_safe_available_balance_caps_order_below_raw_balance(self):
        """max_allowed must be capped at available_balance * 0.999, not raw balance.

        If the requested size exactly equals available_balance, the rule should
        MODIFY it down to safe_available (0.999 × balance) so that the 0.05%
        Upbit fee cannot push the total cost over the available funds.
        """
        rule = self._rule(
            max_single_asset_ratio=1.0,   # disable single-asset cap
            max_total_investment_ratio=1.0,  # disable total-investment cap
            max_concurrent_positions=5,
        )
        available = 1_000_000.0
        # Request exactly the full available balance — should be trimmed by 0.1%
        signal = _buy_signal(size=available)
        portfolio = _portfolio(total=available, available=available)

        result = await rule.evaluate(signal, portfolio)

        # The rule must either MODIFY the size down or APPROVE a size ≤ safe_available
        safe_available = available * 0.999  # 999_000.0
        if result.decision == RiskDecision.MODIFY:
            assert result.modified_size is not None
            assert result.modified_size <= safe_available, (
                f"modified_size {result.modified_size} must be ≤ safe_available {safe_available}"
            )
        else:
            # APPROVE is also acceptable only if requested_size ≤ safe_available
            assert signal.suggested_size <= safe_available or result.decision == RiskDecision.MODIFY

    async def test_safe_available_balance_does_not_reject_reasonable_orders(self):
        """A small order well within limits must still be approved despite the 0.1% trim."""
        rule = self._rule()
        # Request 10% of 10M total — well within all limits
        signal = _buy_signal(size=1_000_000.0)
        result = await rule.evaluate(signal, _portfolio())
        # safe_available = 8_000_000 * 0.999 = 7_992_000 → still far above 1M
        assert result.decision == RiskDecision.APPROVE, (
            f"Small order within limits must be approved; got {result.decision}: {result.reason}"
        )


# ---------------------------------------------------------------------------
# DailyLossLimitRule
# ---------------------------------------------------------------------------

class TestDailyLossLimitRule:
    def _rule(self, max_pct=0.05) -> DailyLossLimitRule:
        return DailyLossLimitRule({"max_daily_loss_pct": max_pct})

    async def test_no_loss_approved(self):
        rule = self._rule()
        portfolio = _portfolio(daily_pnl=0.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE

    async def test_profit_approved(self):
        rule = self._rule()
        portfolio = _portfolio(daily_pnl=500_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE

    async def test_within_loss_limit_approved(self):
        rule = self._rule(max_pct=0.05)
        # 4% loss: daily_pnl = -400_000 on total 10M
        portfolio = _portfolio(daily_pnl=-400_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE

    async def test_exceeds_loss_limit_rejected(self):
        rule = self._rule(max_pct=0.05)
        # 6% loss: daily_pnl = -600_000 on total 10M
        portfolio = _portfolio(daily_pnl=-600_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT
        assert "daily loss" in result.reason.lower()

    async def test_exactly_at_limit_rejected(self):
        rule = self._rule(max_pct=0.05)
        # Exactly 5% loss: daily_pnl = -500_000 on total 10M
        portfolio = _portfolio(daily_pnl=-500_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT

    async def test_zero_total_balance_approved(self):
        rule = self._rule()
        portfolio = _portfolio(total=0.0, daily_pnl=-100.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE


# ---------------------------------------------------------------------------
# MDDCircuitBreakerRule
# ---------------------------------------------------------------------------

class TestMDDCircuitBreakerRule:
    def _rule(self, max_dd=0.15) -> MDDCircuitBreakerRule:
        return MDDCircuitBreakerRule({"max_drawdown_pct": max_dd})

    async def test_no_drawdown_approved(self):
        rule = self._rule()
        portfolio = _portfolio(total=10_000_000.0, peak=10_000_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE

    async def test_within_drawdown_limit_approved(self):
        rule = self._rule(max_dd=0.15)
        # 10% drawdown: current 9M, peak 10M
        portfolio = _portfolio(total=9_000_000.0, peak=10_000_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE

    async def test_exceeds_drawdown_limit_rejected(self):
        rule = self._rule(max_dd=0.15)
        # 20% drawdown: current 8M, peak 10M
        portfolio = _portfolio(total=8_000_000.0, peak=10_000_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT
        assert "drawdown" in result.reason.lower()

    async def test_exactly_at_limit_rejected(self):
        rule = self._rule(max_dd=0.15)
        # Exactly 15% drawdown
        portfolio = _portfolio(total=8_500_000.0, peak=10_000_000.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT

    async def test_zero_peak_balance_skips_check(self):
        rule = self._rule()
        portfolio = _portfolio(total=5_000_000.0, peak=0.0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE
        assert "peak balance" in result.reason.lower()


# ---------------------------------------------------------------------------
# ConsecutiveLossGuardRule
# ---------------------------------------------------------------------------

class TestConsecutiveLossGuardRule:
    def _rule(self, max_losses=3) -> ConsecutiveLossGuardRule:
        return ConsecutiveLossGuardRule({"max_consecutive_losses": max_losses})

    async def test_zero_losses_approved(self):
        rule = self._rule()
        portfolio = _portfolio(consec_losses=0)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE

    async def test_below_limit_approved(self):
        rule = self._rule(max_losses=3)
        portfolio = _portfolio(consec_losses=2)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.APPROVE

    async def test_at_limit_rejected(self):
        rule = self._rule(max_losses=3)
        portfolio = _portfolio(consec_losses=3)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT
        assert "consecutive" in result.reason.lower()

    async def test_above_limit_rejected(self):
        rule = self._rule(max_losses=3)
        portfolio = _portfolio(consec_losses=5)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT

    async def test_custom_limit(self):
        rule = self._rule(max_losses=1)
        portfolio = _portfolio(consec_losses=1)
        result = await rule.evaluate(_buy_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT

    async def test_sell_signal_rejected_when_losses_at_limit(self):
        """ConsecutiveLossGuard applies to all signal types (no sell exemption)."""
        rule = self._rule(max_losses=3)
        portfolio = _portfolio(consec_losses=3)
        result = await rule.evaluate(_sell_signal(), portfolio)
        assert result.decision == RiskDecision.REJECT
