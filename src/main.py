"""upbit-trader entry point.

Usage:
    uv run python -m src.main                 # uses TRADING_MODE from .env
    uv run python -m src.main --mode backtest
    uv run python -m src.main --mode paper
    uv run python -m src.main --mode live
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from src.config.settings import Settings, yaml_config_loader
from src.monitoring.logger import setup_logging


# ---------------------------------------------------------------------------
# Risk rule registry
# ---------------------------------------------------------------------------

def _build_risk_rules(rule_configs: list[dict]):
    """Instantiate risk rule objects from YAML config dicts."""
    from src.risk.rules import (
        ConsecutiveLossGuardRule,
        DailyLossLimitRule,
        MaxPositionSizeRule,
        MDDCircuitBreakerRule,
    )

    _RULE_MAP = {
        "max_position_size": MaxPositionSizeRule,
        "daily_loss_limit": DailyLossLimitRule,
        "mdd_circuit_breaker": MDDCircuitBreakerRule,
        "consecutive_loss_guard": ConsecutiveLossGuardRule,
    }

    rules = []
    for cfg in rule_configs:
        name = cfg.get("name", "")
        cls = _RULE_MAP.get(name)
        if cls is None:
            logging.getLogger(__name__).warning("Unknown risk rule '%s'; skipping.", name)
            continue
        rules.append(cls(cfg))
    return rules


# ---------------------------------------------------------------------------
# Strategy factory
# ---------------------------------------------------------------------------

def _build_strategies(strategy_configs: list[dict]):
    """Instantiate strategy objects from YAML config dicts."""
    # Import concrete strategy modules so they self-register via @register
    import src.strategy.volatility_breakout       # noqa: F401
    import src.strategy.rsi_bollinger             # noqa: F401
    import src.strategy.macd_momentum             # noqa: F401
    import src.strategy.trend_filtered_breakout   # noqa: F401

    from src.strategy.registry import create_strategy
    from src.strategy.base import StrategyConfig

    strategies = []
    for cfg in strategy_configs:
        name = cfg.get("name", "")
        params = cfg.get("params", {})
        markets = cfg.get("markets", ["KRW-BTC"])
        enabled = cfg.get("enabled", True)
        timeframe = cfg.get("timeframe", "1d")

        try:
            strategy_cfg = StrategyConfig(
                enabled=enabled,
                markets=markets,
                timeframe=timeframe,
                **params,
            )
            strategy = create_strategy(name, strategy_cfg)
            strategies.append(strategy)
            logging.getLogger(__name__).info(
                "Strategy '%s' loaded (enabled=%s, markets=%s)", name, enabled, markets
            )
        except (ValueError, Exception) as exc:
            logging.getLogger(__name__).error("Failed to load strategy '%s': %s", name, exc)

    return strategies


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(settings: Settings, mode: str) -> None:
    logger = logging.getLogger(__name__)

    # -- Data directory --
    db_path = settings.db_url.replace("sqlite+aiosqlite:///", "")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # -- Database --
    from src.data.database import Database
    db = Database(settings.db_url)
    await db.init()
    logger.info("Database initialised: %s", settings.db_url)

    # -- Telegram --
    from src.notification.telegram_bot import TelegramNotifier
    telegram = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    # -- Upbit client (live / paper only) --
    upbit_client = None
    if mode in ("live", "paper"):
        if not settings.upbit_access_key or not settings.upbit_secret_key:
            logger.error(
                "UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY are required for '%s' mode. "
                "Set them in .env or use --mode backtest.",
                mode,
            )
            await db.close()
            sys.exit(1)
        from src.api.upbit_client import UpbitClient
        upbit_client = UpbitClient(settings.upbit_access_key, settings.upbit_secret_key)
        logger.info("UpbitClient initialised (mode=%s)", mode)

    # -- Load YAML config --
    config_dir = Path(__file__).parent / "config"
    yaml_config = yaml_config_loader(config_dir)
    strategy_configs = yaml_config.get("strategies", [])
    risk_configs = yaml_config.get("risk_rules", [])

    # -- Build strategies --
    strategies = _build_strategies(strategy_configs)
    if not strategies:
        logger.warning("No strategies configured. Check src/config/strategies.yaml.")

    # -- Build risk engine --
    from src.risk.engine import RiskEngine
    risk_rules = _build_risk_rules(risk_configs)
    risk_engine = RiskEngine(risk_rules)
    logger.info("RiskEngine initialised with %d rules.", len(risk_rules))

    # -- Executors (always create paper; create live only if API keys present) --
    from src.execution.backtest_executor import BacktestExecutor
    initial_capital = 1_000_000.0  # 100만원 가상 자본
    paper_executor = BacktestExecutor(initial_capital=initial_capital)
    logger.info("BacktestExecutor: initial_capital=%.0f KRW", initial_capital)

    live_executor = None
    if upbit_client is not None:
        from src.execution.live_executor import LiveExecutor
        live_executor = LiveExecutor(upbit_client)
        logger.info("LiveExecutor ready (mode=%s)", mode)

    # Active executor depends on current mode
    executor = live_executor if mode == "live" else paper_executor

    # -- Trading engine --
    from src.core.trading_engine import TradingEngine
    poll_interval = 60.0 if mode == "live" else 5.0
    engine = TradingEngine(
        strategies=strategies,
        executor=executor,
        risk_engine=risk_engine,
        db=db,
        poll_interval=poll_interval,
        upbit_client=upbit_client,
        telegram=telegram,
        mode=mode,
        live_executor=live_executor,
        paper_executor=paper_executor,
    )

    # -- Graceful shutdown --
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown(sig_name: str) -> None:
        logger.info("Received %s — shutting down gracefully...", sig_name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig.name)

    # -- Telegram command handler --
    from src.notification.command_handler import TelegramCommandHandler
    command_handler = TelegramCommandHandler(
        notifier=telegram,
        engine=engine,
        executor=executor,
        strategies=strategies,
        mode=mode,
        stop_callback=stop_event.set,
        authorized_chat_id=settings.telegram_chat_id,
    )

    # -- Start --
    await telegram.notify_system_start(mode)
    logger.info("Trading engine starting in '%s' mode.", mode)
    logger.info("Telegram command handler started. Send /help to @GustjdBot for commands.")

    # Run engine + command handler concurrently
    try:
        engine_task = asyncio.create_task(engine.run())
        command_task = asyncio.create_task(command_handler.run())

        await stop_event.wait()

        engine_task.cancel()
        command_task.cancel()
        try:
            await asyncio.gather(engine_task, command_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
    finally:
        if upbit_client:
            await upbit_client.close()
        await db.close()
        await telegram.notify_system_stop("Shutdown requested")
        logger.info("upbit-trader stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upbit Automated Trader")
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live"],
        default=None,
        help="Trading mode (overrides TRADING_MODE in .env)",
    )
    args = parser.parse_args()

    # Settings reads from .env automatically
    settings = Settings()
    mode = args.mode or settings.trading_mode

    setup_logging(level=settings.log_level, enable_console=True)
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("  upbit-trader  |  mode: %s", mode)
    logger.info("=" * 50)

    asyncio.run(run(settings, mode))


if __name__ == "__main__":
    main()
