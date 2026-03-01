from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.strategy.base import BaseStrategy, StrategyConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

STRATEGIES: dict[str, type[BaseStrategy]] = {}


def register(cls: type[BaseStrategy]) -> type[BaseStrategy]:
    """Class decorator to register a strategy by its ``name`` attribute.

    Example::

        @register
        class RSIStrategy(BaseStrategy):
            name = "rsi"
            ...
    """
    STRATEGIES[cls.name] = cls
    logger.debug("Registered strategy: %s", cls.name)
    return cls


def create_strategy(name: str, config: StrategyConfig) -> BaseStrategy:
    """Instantiate a registered strategy by name.

    Args:
        name: Strategy name as declared in ``cls.name``.
        config: ``StrategyConfig`` instance to pass to the constructor.

    Returns:
        Constructed ``BaseStrategy`` instance.

    Raises:
        ValueError: If the strategy name is not registered.
    """
    if name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy: {name}. Available: {list(STRATEGIES.keys())}"
        )
    return STRATEGIES[name](config)


def available_strategies() -> list[str]:
    """Return sorted list of all registered strategy names."""
    return sorted(STRATEGIES.keys())
