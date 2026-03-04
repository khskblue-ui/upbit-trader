"""Tests for monitoring and notification modules."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monitoring.reporter import DailyReport, PerformanceReporter, WeeklyReport


# ---------------------------------------------------------------------------
# DailyReport / WeeklyReport dataclasses
# ---------------------------------------------------------------------------


class TestDailyReport:
    def test_default_strategies_empty(self):
        report = DailyReport(
            date="2024-01-01",
            total_trades=0,
            buy_trades=0,
            sell_trades=0,
            win_trades=0,
            total_pnl=0.0,
            total_fee=0.0,
            win_rate_pct=0.0,
        )
        assert report.strategies == []

    def test_fields_stored_correctly(self):
        report = DailyReport(
            date="2024-06-15",
            total_trades=10,
            buy_trades=5,
            sell_trades=5,
            win_trades=3,
            total_pnl=12345.0,
            total_fee=50.0,
            win_rate_pct=60.0,
            strategies=["trend_filtered_breakout"],
        )
        assert report.date == "2024-06-15"
        assert report.total_trades == 10
        assert report.buy_trades == 5
        assert report.sell_trades == 5
        assert report.win_trades == 3
        assert report.total_pnl == pytest.approx(12345.0)
        assert report.total_fee == pytest.approx(50.0)
        assert report.win_rate_pct == pytest.approx(60.0)
        assert report.strategies == ["trend_filtered_breakout"]


class TestWeeklyReport:
    def test_default_daily_reports_empty(self):
        report = WeeklyReport(
            start_date="2024-01-01",
            end_date="2024-01-07",
            total_trades=0,
            win_trades=0,
            total_pnl=0.0,
            total_fee=0.0,
            win_rate_pct=0.0,
            best_day_pnl=0.0,
            worst_day_pnl=0.0,
        )
        assert report.daily_reports == []

    def test_all_fields_stored(self):
        report = WeeklyReport(
            start_date="2024-01-01",
            end_date="2024-01-07",
            total_trades=50,
            win_trades=30,
            total_pnl=500_000.0,
            total_fee=250.0,
            win_rate_pct=60.0,
            best_day_pnl=200_000.0,
            worst_day_pnl=-50_000.0,
        )
        assert report.total_trades == 50
        assert report.win_trades == 30
        assert report.best_day_pnl == pytest.approx(200_000.0)
        assert report.worst_day_pnl == pytest.approx(-50_000.0)


# ---------------------------------------------------------------------------
# PerformanceReporter.format_daily
# ---------------------------------------------------------------------------


class TestFormatDaily:
    def _make_report(self, **kwargs) -> DailyReport:
        defaults = dict(
            date="2024-03-01",
            total_trades=8,
            buy_trades=4,
            sell_trades=4,
            win_trades=3,
            total_pnl=50_000.0,
            total_fee=25.0,
            win_rate_pct=75.0,
            strategies=["trend_filtered_breakout"],
        )
        defaults.update(kwargs)
        return DailyReport(**defaults)

    def test_contains_date(self):
        report = self._make_report()
        text = PerformanceReporter.format_daily(report)
        assert "2024-03-01" in text

    def test_contains_pnl(self):
        report = self._make_report(total_pnl=50_000.0)
        text = PerformanceReporter.format_daily(report)
        assert "50,000" in text

    def test_contains_win_rate(self):
        report = self._make_report(win_rate_pct=75.0)
        text = PerformanceReporter.format_daily(report)
        assert "75.0" in text

    def test_contains_strategy_name(self):
        report = self._make_report(strategies=["trend_filtered_breakout"])
        text = PerformanceReporter.format_daily(report)
        assert "trend_filtered_breakout" in text

    def test_no_strategies_shows_na(self):
        report = self._make_report(strategies=[])
        text = PerformanceReporter.format_daily(report)
        assert "N/A" in text

    def test_separator_lines_present(self):
        report = self._make_report()
        text = PerformanceReporter.format_daily(report)
        assert "=" * 10 in text

    def test_negative_pnl_has_minus(self):
        report = self._make_report(total_pnl=-10_000.0)
        text = PerformanceReporter.format_daily(report)
        assert "-" in text

    def test_returns_string(self):
        report = self._make_report()
        result = PerformanceReporter.format_daily(report)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# PerformanceReporter.format_weekly
# ---------------------------------------------------------------------------


class TestFormatWeekly:
    def _make_report(self, **kwargs) -> WeeklyReport:
        defaults = dict(
            start_date="2024-03-01",
            end_date="2024-03-07",
            total_trades=40,
            win_trades=25,
            total_pnl=300_000.0,
            total_fee=150.0,
            win_rate_pct=62.5,
            best_day_pnl=120_000.0,
            worst_day_pnl=-30_000.0,
        )
        defaults.update(kwargs)
        return WeeklyReport(**defaults)

    def test_contains_date_range(self):
        report = self._make_report()
        text = PerformanceReporter.format_weekly(report)
        assert "2024-03-01" in text
        assert "2024-03-07" in text

    def test_contains_pnl(self):
        report = self._make_report(total_pnl=300_000.0)
        text = PerformanceReporter.format_weekly(report)
        assert "300,000" in text

    def test_contains_best_day(self):
        report = self._make_report(best_day_pnl=120_000.0)
        text = PerformanceReporter.format_weekly(report)
        assert "120,000" in text

    def test_contains_worst_day(self):
        report = self._make_report(worst_day_pnl=-30_000.0)
        text = PerformanceReporter.format_weekly(report)
        assert "-30,000" in text or "30,000" in text

    def test_win_rate_in_output(self):
        report = self._make_report(win_rate_pct=62.5)
        text = PerformanceReporter.format_weekly(report)
        assert "62.5" in text

    def test_returns_string(self):
        report = self._make_report()
        result = PerformanceReporter.format_weekly(report)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# PerformanceReporter.daily_report (with mocked DB)
# ---------------------------------------------------------------------------


class TestPerformanceReporterDB:
    def _make_trade(self, side="sell", pnl=1000.0, fee=5.0, strategy="test"):
        t = MagicMock()
        t.side = side
        t.pnl = pnl
        t.fee = fee
        t.strategy = strategy
        return t

    async def test_daily_report_no_trades(self):
        db = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))
        db.get_session = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False)))

        reporter = PerformanceReporter(db)
        report = await reporter.daily_report(date(2024, 1, 1))

        assert report.total_trades == 0
        assert report.total_pnl == 0.0
        assert report.win_rate_pct == 0.0
        assert isinstance(report, DailyReport)

    async def test_daily_report_with_winning_sells(self):
        sell_win = self._make_trade(side="sell", pnl=5000.0, fee=10.0)
        sell_loss = self._make_trade(side="sell", pnl=-1000.0, fee=5.0)
        buy = self._make_trade(side="buy", pnl=None, fee=8.0)

        db = MagicMock()
        session = AsyncMock()
        trades = [sell_win, sell_loss, buy]
        session.execute = AsyncMock(
            return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: trades))
        )
        db.get_session = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
        )

        reporter = PerformanceReporter(db)
        report = await reporter.daily_report(date(2024, 1, 1))

        assert report.total_trades == 3
        assert report.buy_trades == 1
        assert report.sell_trades == 2
        assert report.win_trades == 1
        assert report.total_pnl == pytest.approx(4000.0)
        assert report.win_rate_pct == pytest.approx(50.0)

    async def test_daily_report_win_rate_100(self):
        sells = [self._make_trade(side="sell", pnl=1000.0) for _ in range(3)]

        db = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: sells))
        )
        db.get_session = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
        )

        reporter = PerformanceReporter(db)
        report = await reporter.daily_report(date(2024, 1, 1))
        assert report.win_rate_pct == pytest.approx(100.0)

    async def test_daily_report_strategies_deduplicated(self):
        t1 = self._make_trade(side="sell", strategy="trend_filtered_breakout")
        t2 = self._make_trade(side="buy", strategy="trend_filtered_breakout")

        db = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [t1, t2]))
        )
        db.get_session = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
        )

        reporter = PerformanceReporter(db)
        report = await reporter.daily_report(date(2024, 1, 1))
        assert report.strategies.count("trend_filtered_breakout") == 1

    async def test_weekly_report_spans_7_days(self):
        """weekly_report should call daily_report exactly 7 times."""
        db = MagicMock()
        reporter = PerformanceReporter(db)

        call_count = []
        original = reporter.daily_report

        async def counting_daily(target_date=None):
            call_count.append(target_date)
            return DailyReport(
                date=str(target_date),
                total_trades=0, buy_trades=0, sell_trades=0,
                win_trades=0, total_pnl=0.0, total_fee=0.0, win_rate_pct=0.0,
            )

        reporter.daily_report = counting_daily
        report = await reporter.weekly_report(date(2024, 1, 7))

        assert len(call_count) == 7
        assert isinstance(report, WeeklyReport)
        assert report.start_date == "2024-01-01"
        assert report.end_date == "2024-01-07"

    async def test_weekly_report_aggregates_pnl(self):
        db = MagicMock()
        reporter = PerformanceReporter(db)

        day_pnls = [1000.0, -500.0, 2000.0, 300.0, -100.0, 800.0, 400.0]

        async def mock_daily(target_date=None):
            idx = (target_date - date(2024, 1, 1)).days
            pnl = day_pnls[idx] if idx < len(day_pnls) else 0.0
            return DailyReport(
                date=str(target_date),
                total_trades=2, buy_trades=1, sell_trades=1,
                win_trades=1 if pnl > 0 else 0,
                total_pnl=pnl, total_fee=5.0, win_rate_pct=50.0,
            )

        reporter.daily_report = mock_daily
        report = await reporter.weekly_report(date(2024, 1, 7))

        assert report.total_pnl == pytest.approx(sum(day_pnls))
        assert report.best_day_pnl == pytest.approx(max(day_pnls))
        assert report.worst_day_pnl == pytest.approx(min(day_pnls))


# ---------------------------------------------------------------------------
# TelegramNotifier
# ---------------------------------------------------------------------------


class TestTelegramNotifier:
    def test_disabled_when_no_token(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="", chat_id="123")
        assert notifier._enabled is False

    def test_disabled_when_no_chat_id(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="")
        assert notifier._enabled is False

    def test_enabled_with_valid_params(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        assert notifier._enabled is True

    def test_disabled_flag_overrides(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123", enabled=False)
        assert notifier._enabled is False

    async def test_send_returns_false_when_disabled(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="", chat_id="")
        result = await notifier.send("hello")
        assert result is False

    async def test_notify_buy_calls_send(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        result = await notifier.notify_buy("KRW-BTC", 50_000_000.0, 0.001, "test_strat", 0.85)
        notifier.send.assert_called_once()
        args = notifier.send.call_args[0][0]
        assert "KRW-BTC" in args
        assert "50,000,000" in args
        assert "test_strat" in args
        assert result is True

    async def test_notify_sell_shows_pnl(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_sell("KRW-ETH", 3_000_000.0, 1.0, 100_000.0, "trend_filtered_breakout")
        args = notifier.send.call_args[0][0]
        assert "KRW-ETH" in args
        assert "100,000" in args

    async def test_notify_sell_negative_pnl_uses_red(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_sell("KRW-BTC", 50_000_000.0, 0.001, -5_000.0, "test")
        args = notifier.send.call_args[0][0]
        assert "🔴" in args

    async def test_notify_error_critical_flag(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_error("disk full", critical=True)
        args = notifier.send.call_args[0][0]
        assert "🚨" in args

    async def test_notify_error_non_critical(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_error("minor issue", critical=False)
        args = notifier.send.call_args[0][0]
        assert "⚠️" in args

    async def test_notify_mdd_warning_content(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_mdd_warning(8.5, 10.0)
        args = notifier.send.call_args[0][0]
        assert "8.50" in args
        assert "10.00" in args

    async def test_notify_daily_report_positive_pnl(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_daily_report("2024-01-01", 50_000.0, 8, 75.0, 1_000_000.0)
        args = notifier.send.call_args[0][0]
        assert "📈" in args
        assert "50,000" in args

    async def test_notify_daily_report_negative_pnl(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_daily_report("2024-01-01", -10_000.0, 5, 40.0, 900_000.0)
        args = notifier.send.call_args[0][0]
        assert "📉" in args

    async def test_notify_system_start(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_system_start("backtest")
        args = notifier.send.call_args[0][0]
        assert "backtest" in args
        assert "✅" in args

    async def test_notify_system_stop_with_reason(self):
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
        notifier.send = AsyncMock(return_value=True)

        await notifier.notify_system_stop("MDD limit exceeded")
        args = notifier.send.call_args[0][0]
        assert "MDD limit exceeded" in args
        assert "🛑" in args

    async def test_send_http_error_returns_false(self):
        """When httpx raises, send() should return False not raise."""
        import httpx
        from src.notification.telegram_bot import TelegramNotifier

        notifier = TelegramNotifier(bot_token="REALTOKEN", chat_id="123")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
            mock_client_cls.return_value = mock_client

            result = await notifier.send("test message")
        assert result is False


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_creates_log_files(self, tmp_path: Path):
        from src.monitoring.logger import setup_logging

        setup_logging(level="DEBUG", log_dir=tmp_path, enable_console=False)

        root = logging.getLogger()
        handler_files = {
            getattr(h, "baseFilename", None)
            for h in root.handlers
            if hasattr(h, "baseFilename")
        }
        names = {Path(p).name for p in handler_files if p}
        assert "system.log" in names
        assert "error.log" in names

    def test_trading_logger_has_handler(self, tmp_path: Path):
        from src.monitoring.logger import setup_logging

        setup_logging(level="INFO", log_dir=tmp_path, enable_console=False)

        trading_logger = logging.getLogger("src.execution")
        # Should have at least the trading handler attached
        assert len(trading_logger.handlers) >= 1

    def test_console_handler_added_when_enabled(self, tmp_path: Path):
        import logging as std_logging
        from src.monitoring.logger import setup_logging

        setup_logging(level="INFO", log_dir=tmp_path, enable_console=True)

        root = std_logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, std_logging.StreamHandler)
                           and not isinstance(h, std_logging.handlers.RotatingFileHandler)]
        assert len(stream_handlers) >= 1

    def test_no_console_handler_when_disabled(self, tmp_path: Path):
        import logging as std_logging
        from src.monitoring.logger import setup_logging

        setup_logging(level="INFO", log_dir=tmp_path, enable_console=False)

        root = std_logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, std_logging.StreamHandler)
                           and not isinstance(h, std_logging.handlers.RotatingFileHandler)]
        assert len(stream_handlers) == 0

    def test_error_handler_level_is_warning(self, tmp_path: Path):
        from src.monitoring.logger import setup_logging

        setup_logging(level="DEBUG", log_dir=tmp_path, enable_console=False)

        root = logging.getLogger()
        error_handlers = [
            h for h in root.handlers
            if hasattr(h, "baseFilename") and "error.log" in getattr(h, "baseFilename", "")
        ]
        assert error_handlers
        assert error_handlers[0].level == logging.WARNING

    def test_log_dir_created(self, tmp_path: Path):
        from src.monitoring.logger import setup_logging

        new_dir = tmp_path / "sublogs"
        setup_logging(level="INFO", log_dir=new_dir, enable_console=False)
        assert new_dir.exists()

    def test_get_logger_returns_logger(self):
        from src.monitoring.logger import get_logger

        lg = get_logger("test.module")
        assert isinstance(lg, logging.Logger)
        assert lg.name == "test.module"
