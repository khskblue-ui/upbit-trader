"""Daily and weekly performance reporter."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.database import Database

logger = logging.getLogger(__name__)


@dataclass
class DailyReport:
    """Aggregated metrics for a single trading day."""

    date: str
    total_trades: int
    buy_trades: int
    sell_trades: int
    win_trades: int
    total_pnl: float
    total_fee: float
    win_rate_pct: float
    strategies: list[str] = field(default_factory=list)


@dataclass
class WeeklyReport:
    """Aggregated metrics for a 7-day period."""

    start_date: str
    end_date: str
    total_trades: int
    win_trades: int
    total_pnl: float
    total_fee: float
    win_rate_pct: float
    best_day_pnl: float
    worst_day_pnl: float
    daily_reports: list[DailyReport] = field(default_factory=list)


class PerformanceReporter:
    """Build daily and weekly reports from the Trade table.

    Args:
        db: Async :class:`Database` instance used to query the trades table.
    """

    def __init__(self, db: "Database") -> None:
        self._db = db

    async def daily_report(self, target_date: date | None = None) -> DailyReport:
        """Build a :class:`DailyReport` for *target_date* (default: today UTC).

        Args:
            target_date: Date to report on. Defaults to today in UTC.

        Returns:
            :class:`DailyReport` populated from the database.
        """
        from sqlalchemy import select, and_
        from src.data.models import Trade

        target_date = target_date or datetime.now(timezone.utc).date()
        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        async with self._db.get_session() as session:
            result = await session.execute(
                select(Trade).where(
                    and_(Trade.timestamp >= day_start, Trade.timestamp < day_end)
                )
            )
            trades = result.scalars().all()

        sell_trades = [t for t in trades if t.side == "sell" and t.pnl is not None]
        win_trades = [t for t in sell_trades if t.pnl > 0]
        total_pnl = sum(t.pnl for t in sell_trades)
        total_fee = sum(t.fee for t in trades)
        strategies = list({t.strategy for t in trades})
        win_rate = len(win_trades) / len(sell_trades) * 100 if sell_trades else 0.0

        return DailyReport(
            date=str(target_date),
            total_trades=len(trades),
            buy_trades=sum(1 for t in trades if t.side == "buy"),
            sell_trades=len(sell_trades),
            win_trades=len(win_trades),
            total_pnl=round(total_pnl, 2),
            total_fee=round(total_fee, 4),
            win_rate_pct=round(win_rate, 2),
            strategies=strategies,
        )

    async def weekly_report(self, end_date: date | None = None) -> WeeklyReport:
        """Build a :class:`WeeklyReport` covering the 7 days ending on *end_date*.

        Args:
            end_date: Last day (inclusive) of the report window.
                      Defaults to today in UTC.

        Returns:
            :class:`WeeklyReport` with per-day breakdown.
        """
        end_date = end_date or datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=6)

        daily_reports: list[DailyReport] = []
        current = start_date
        while current <= end_date:
            report = await self.daily_report(current)
            daily_reports.append(report)
            current += timedelta(days=1)

        total_trades = sum(r.total_trades for r in daily_reports)
        win_trades = sum(r.win_trades for r in daily_reports)
        total_pnl = sum(r.total_pnl for r in daily_reports)
        total_fee = sum(r.total_fee for r in daily_reports)
        sell_trades_total = sum(r.sell_trades for r in daily_reports)
        win_rate = win_trades / sell_trades_total * 100 if sell_trades_total else 0.0
        daily_pnls = [r.total_pnl for r in daily_reports]

        return WeeklyReport(
            start_date=str(start_date),
            end_date=str(end_date),
            total_trades=total_trades,
            win_trades=win_trades,
            total_pnl=round(total_pnl, 2),
            total_fee=round(total_fee, 4),
            win_rate_pct=round(win_rate, 2),
            best_day_pnl=max(daily_pnls) if daily_pnls else 0.0,
            worst_day_pnl=min(daily_pnls) if daily_pnls else 0.0,
            daily_reports=daily_reports,
        )

    @staticmethod
    def format_daily(report: DailyReport) -> str:
        """Format *report* as a human-readable string."""
        lines = [
            f"{'=' * 50}",
            f"일일 보고 [{report.date}]",
            f"{'=' * 50}",
            f"총 거래:    {report.total_trades:>8}  (매수 {report.buy_trades} / 매도 {report.sell_trades})",
            f"승리 거래:  {report.win_trades:>8}",
            f"승률:       {report.win_rate_pct:>7.1f} %",
            f"총 손익:    {report.total_pnl:>+10,.0f} KRW",
            f"총 수수료:  {report.total_fee:>+10,.2f} KRW",
            f"전략:       {', '.join(report.strategies) or 'N/A'}",
            f"{'=' * 50}",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_weekly(report: WeeklyReport) -> str:
        """Format *report* as a human-readable string."""
        lines = [
            f"{'=' * 50}",
            f"주간 보고 [{report.start_date} ~ {report.end_date}]",
            f"{'=' * 50}",
            f"총 거래:    {report.total_trades:>8}",
            f"승리 거래:  {report.win_trades:>8}",
            f"승률:       {report.win_rate_pct:>7.1f} %",
            f"총 손익:    {report.total_pnl:>+10,.0f} KRW",
            f"총 수수료:  {report.total_fee:>+10,.2f} KRW",
            f"최고 하루:  {report.best_day_pnl:>+10,.0f} KRW",
            f"최저 하루:  {report.worst_day_pnl:>+10,.0f} KRW",
            f"{'=' * 50}",
        ]
        return "\n".join(lines)
