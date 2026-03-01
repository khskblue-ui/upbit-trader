from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Candle(Base):
    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(20))       # e.g. 'KRW-BTC'
    timeframe: Mapped[str] = mapped_column(String(10))    # e.g. '1m', '5m', '1h', '1d'
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("market", "timeframe", "timestamp"),)

    def __repr__(self) -> str:
        return (
            f"Candle(market={self.market!r}, timeframe={self.timeframe!r}, "
            f"timestamp={self.timestamp!r}, close={self.close})"
        )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))          # 'buy' or 'sell'
    strategy: Mapped[str] = mapped_column(String(50))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, nullable=True)
    pnl: Mapped[float] = mapped_column(Float, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return (
            f"Trade(market={self.market!r}, side={self.side!r}, "
            f"strategy={self.strategy!r}, price={self.price}, quantity={self.quantity})"
        )


class StrategyPerformance(Base):
    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy: Mapped[str] = mapped_column(String(50))
    date: Mapped[date] = mapped_column(Date)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_trades: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("strategy", "date"),)

    def __repr__(self) -> str:
        return (
            f"StrategyPerformance(strategy={self.strategy!r}, date={self.date!r}, "
            f"total_pnl={self.total_pnl}, total_trades={self.total_trades})"
        )
