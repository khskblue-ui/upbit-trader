from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    upbit_access_key: str = ""
    upbit_secret_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    trading_mode: Literal["backtest", "paper", "live"] = "backtest"
    log_level: str = "INFO"
    db_url: str = "sqlite+aiosqlite:///data/trading.db"


def yaml_config_loader(config_dir: Path | str | None = None) -> dict[str, Any]:
    """Load strategies.yaml and risk.yaml from the config directory.

    Args:
        config_dir: Path to directory containing yaml config files.
                    Defaults to src/config/ relative to this file.

    Returns:
        Merged dict with keys 'strategies' and 'risk_rules'.
    """
    if config_dir is None:
        config_dir = Path(__file__).parent

    config_dir = Path(config_dir)

    result: dict[str, Any] = {}

    strategies_path = config_dir / "strategies.yaml"
    if strategies_path.exists():
        with strategies_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data:
                result.update(data)

    risk_path = config_dir / "risk.yaml"
    if risk_path.exists():
        with risk_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data:
                result.update(data)

    return result
