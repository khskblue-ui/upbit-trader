"""Backtest performance metrics and report formatting."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.backtest.engine import BacktestResult


@dataclass
class PerformanceMetrics:
    """Key performance metrics computed from a :class:`BacktestResult`."""

    total_return_pct: float        # Total return as percentage
    cagr_pct: float | None         # Compound Annual Growth Rate (%)
    max_drawdown_pct: float        # Maximum drawdown (negative %)
    sharpe_ratio: float | None     # Annualised Sharpe ratio (risk-free = 0)
    total_trades: int              # Completed round-trips (buy+sell pairs)
    win_trades: int                # Number of profitable trades
    lose_trades: int               # Number of losing trades
    win_rate_pct: float            # Win rate (%)
    profit_factor: float | None    # Gross profit / gross loss
    avg_pnl: float                 # Average PnL per completed trade (KRW)
    total_fee: float               # Total fees paid (KRW)


def calculate_metrics(result: BacktestResult) -> PerformanceMetrics:
    """Compute :class:`PerformanceMetrics` from a :class:`BacktestResult`."""
    equity = result.equity_curve
    initial = result.initial_capital
    final = result.final_capital

    total_return_pct = (final - initial) / initial * 100.0 if initial else 0.0
    max_drawdown_pct = _max_drawdown(equity) if equity else 0.0

    # Only completed sell legs count as trades
    sell_trades = [t for t in result.trades if t.side == "sell" and t.pnl is not None]
    total_trades = len(sell_trades)
    win_trades = sum(1 for t in sell_trades if t.pnl > 0)
    lose_trades = total_trades - win_trades
    win_rate_pct = win_trades / total_trades * 100.0 if total_trades else 0.0
    avg_pnl = sum(t.pnl for t in sell_trades) / total_trades if total_trades else 0.0

    gross_profit = sum(t.pnl for t in sell_trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in sell_trades if t.pnl < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    total_fee = sum(t.fee for t in result.trades)

    # CAGR — approximate assuming daily bars
    cagr_pct: float | None = None
    if equity and len(equity) > 1 and initial > 0 and final > 0:
        years = len(equity) / 365.0
        if years > 0:
            cagr_pct = ((final / initial) ** (1.0 / years) - 1.0) * 100.0

    sharpe_ratio = _sharpe_ratio(equity)

    return PerformanceMetrics(
        total_return_pct=round(total_return_pct, 4),
        cagr_pct=round(cagr_pct, 4) if cagr_pct is not None else None,
        max_drawdown_pct=round(max_drawdown_pct, 4),
        sharpe_ratio=round(sharpe_ratio, 4) if sharpe_ratio is not None else None,
        total_trades=total_trades,
        win_trades=win_trades,
        lose_trades=lose_trades,
        win_rate_pct=round(win_rate_pct, 2),
        profit_factor=round(profit_factor, 4) if profit_factor is not None else None,
        avg_pnl=round(avg_pnl, 2),
        total_fee=round(total_fee, 2),
    )


def format_report(result: BacktestResult, metrics: PerformanceMetrics) -> str:
    """Return a human-readable backtest report string."""
    cagr_str = f"{metrics.cagr_pct:>+14.2f} %" if metrics.cagr_pct is not None else "             N/A"
    sharpe_str = f"{metrics.sharpe_ratio:>15.4f}" if metrics.sharpe_ratio is not None else "             N/A"
    pf_str = f"{metrics.profit_factor:>15.4f}" if metrics.profit_factor is not None else "             N/A"

    lines = [
        "=" * 60,
        f"백테스팅 결과: {result.strategy_name}",
        f"마켓: {result.market}",
        f"기간: {result.start_date} ~ {result.end_date}",
        "=" * 60,
        f"초기 자본:       {result.initial_capital:>15,.0f} KRW",
        f"최종 자본:       {result.final_capital:>15,.0f} KRW",
        f"총 수익률:       {metrics.total_return_pct:>+14.2f} %",
        f"연간 수익률:     {cagr_str}",
        f"최대 낙폭(MDD):  {metrics.max_drawdown_pct:>+14.2f} %",
        f"샤프 비율:       {sharpe_str}",
        "-" * 60,
        f"총 거래 횟수:    {metrics.total_trades:>15}",
        f"승리:            {metrics.win_trades:>15}",
        f"패배:            {metrics.lose_trades:>15}",
        f"승률:            {metrics.win_rate_pct:>14.2f} %",
        f"손익비:          {pf_str}",
        f"평균 손익:       {metrics.avg_pnl:>15,.2f} KRW",
        f"총 수수료:       {metrics.total_fee:>15,.2f} KRW",
        "=" * 60,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _max_drawdown(equity: list[float]) -> float:
    """Return the maximum drawdown as a negative percentage."""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        if peak > 0:
            dd = (val - peak) / peak * 100.0
            if dd < max_dd:
                max_dd = dd
    return max_dd


def _sharpe_ratio(equity: list[float]) -> float | None:
    """Annualised Sharpe ratio assuming daily bars and risk-free rate = 0."""
    if len(equity) < 2:
        return None
    returns = [
        (equity[i] - equity[i - 1]) / equity[i - 1]
        for i in range(1, len(equity))
        if equity[i - 1] != 0
    ]
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return (mean_r / std) * math.sqrt(365.0)
