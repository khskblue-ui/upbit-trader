from __future__ import annotations

import asyncio
import logging

from config.settings import Settings, yaml_config_loader


async def main() -> None:
    settings = Settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    logger.info("Upbit trader starting up")
    logger.info("Trading mode: %s", settings.trading_mode)
    logger.info("DB URL: %s", settings.db_url)

    yaml_config = yaml_config_loader()
    strategies = yaml_config.get("strategies", {})
    risk_rules = yaml_config.get("risk_rules", [])
    logger.info("Loaded %d strategies, %d risk rules", len(strategies), len(risk_rules))

    # TODO: initialise and start the trading engine here
    logger.info("Trading engine startup placeholder -- not yet implemented")


if __name__ == "__main__":
    asyncio.run(main())
