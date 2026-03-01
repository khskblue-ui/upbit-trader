"""Tests for src/data/database.py and src/data/models.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.data.database import Database
from src.data.models import Candle, Trade


# ---------------------------------------------------------------------------
# Fixture: in-memory async SQLite database
# ---------------------------------------------------------------------------

@pytest.fixture
async def db() -> Database:
    """Create and initialise an in-memory SQLite database, yield it, then close."""
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init()
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

class TestDatabaseInit:
    async def test_init_creates_tables(self, db: Database):
        """After init(), get_session() must work and candles table must exist."""
        async with db.get_session() as session:
            # A query against the table proves it was created
            result = await session.execute(select(Candle))
            assert result.scalars().all() == []

    async def test_get_session_before_init_raises(self):
        uninitialised = Database("sqlite+aiosqlite:///:memory:")
        with pytest.raises(RuntimeError, match="init"):
            async with uninitialised.get_session() as _:
                pass

    async def test_close_is_idempotent(self, db: Database):
        await db.close()
        await db.close()  # second close must not raise


# ---------------------------------------------------------------------------
# Candle model — insert and query
# ---------------------------------------------------------------------------

def _make_candle(
    market: str = "KRW-BTC",
    timeframe: str = "1h",
    ts: datetime | None = None,
    close: float = 50_000_000.0,
) -> Candle:
    if ts is None:
        ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return Candle(
        market=market,
        timeframe=timeframe,
        timestamp=ts,
        open=close - 50_000,
        high=close + 100_000,
        low=close - 100_000,
        close=close,
        volume=1.5,
    )


class TestCandleModel:
    async def test_insert_and_query_candle(self, db: Database):
        candle = _make_candle()
        async with db.get_session() as session:
            session.add(candle)

        async with db.get_session() as session:
            result = await session.execute(select(Candle))
            rows = result.scalars().all()

        assert len(rows) == 1
        assert rows[0].market == "KRW-BTC"
        assert rows[0].timeframe == "1h"
        assert rows[0].close == 50_000_000.0
        assert rows[0].volume == 1.5

    async def test_candle_repr(self, db: Database):
        candle = _make_candle()
        async with db.get_session() as session:
            session.add(candle)

        async with db.get_session() as session:
            row = (await session.execute(select(Candle))).scalars().first()
        assert "KRW-BTC" in repr(row)
        assert "1h" in repr(row)

    async def test_multiple_candles_different_timestamps(self, db: Database):
        ts1 = datetime(2024, 1, 1, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        async with db.get_session() as session:
            session.add(_make_candle(ts=ts1, close=50_000_000.0))
            session.add(_make_candle(ts=ts2, close=51_000_000.0))

        async with db.get_session() as session:
            rows = (await session.execute(select(Candle))).scalars().all()
        assert len(rows) == 2

    async def test_unique_constraint_candle(self, db: Database):
        """Inserting two candles with the same (market, timeframe, timestamp) must fail."""
        ts = datetime(2024, 1, 1, 0, tzinfo=timezone.utc)
        async with db.get_session() as session:
            session.add(_make_candle(ts=ts))

        with pytest.raises((IntegrityError, Exception)):
            async with db.get_session() as session:
                session.add(_make_candle(ts=ts))  # duplicate

    async def test_query_by_market(self, db: Database):
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        async with db.get_session() as session:
            session.add(_make_candle(market="KRW-BTC", ts=ts))
            session.add(
                _make_candle(
                    market="KRW-ETH",
                    ts=ts,
                    close=2_000_000.0,
                )
            )

        async with db.get_session() as session:
            result = await session.execute(
                select(Candle).where(Candle.market == "KRW-ETH")
            )
            rows = result.scalars().all()

        assert len(rows) == 1
        assert rows[0].market == "KRW-ETH"


# ---------------------------------------------------------------------------
# Trade model — insert and query
# ---------------------------------------------------------------------------

def _make_trade(
    market: str = "KRW-BTC",
    side: str = "buy",
    price: float = 50_000_000.0,
    quantity: float = 0.01,
) -> Trade:
    return Trade(
        market=market,
        side=side,
        strategy="rsi_strategy",
        price=price,
        quantity=quantity,
        fee=0.0005,
        pnl=None,
        order_id="ord_abc123",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestTradeModel:
    async def test_insert_and_query_trade(self, db: Database):
        async with db.get_session() as session:
            session.add(_make_trade())

        async with db.get_session() as session:
            rows = (await session.execute(select(Trade))).scalars().all()

        assert len(rows) == 1
        t = rows[0]
        assert t.market == "KRW-BTC"
        assert t.side == "buy"
        assert t.strategy == "rsi_strategy"
        assert t.price == 50_000_000.0
        assert t.quantity == 0.01
        assert t.order_id == "ord_abc123"

    async def test_trade_pnl_nullable(self, db: Database):
        async with db.get_session() as session:
            session.add(_make_trade())

        async with db.get_session() as session:
            row = (await session.execute(select(Trade))).scalars().first()
        assert row.pnl is None

    async def test_trade_repr(self, db: Database):
        async with db.get_session() as session:
            session.add(_make_trade())

        async with db.get_session() as session:
            row = (await session.execute(select(Trade))).scalars().first()
        assert "KRW-BTC" in repr(row)
        assert "buy" in repr(row)

    async def test_multiple_trades_same_market(self, db: Database):
        async with db.get_session() as session:
            session.add(_make_trade(side="buy"))
            session.add(
                Trade(
                    market="KRW-BTC",
                    side="sell",
                    strategy="rsi_strategy",
                    price=51_000_000.0,
                    quantity=0.01,
                    fee=0.0005,
                    pnl=10_000.0,
                    order_id="ord_def456",
                    timestamp=datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc),
                )
            )

        async with db.get_session() as session:
            rows = (await session.execute(select(Trade))).scalars().all()
        assert len(rows) == 2
        sides = {r.side for r in rows}
        assert sides == {"buy", "sell"}
