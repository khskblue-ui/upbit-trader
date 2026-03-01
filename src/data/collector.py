from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from data.database import Database
from data.models import Candle

if TYPE_CHECKING:
    from api.upbit_client import UpbitClient

logger = logging.getLogger(__name__)

# Upbit REST API returns at most 200 candles per request.
_UPBIT_MAX_CANDLES_PER_REQUEST = 200


class DataCollector:
    """Fetches OHLCV candle data from the Upbit REST API and persists it to the DB."""

    def __init__(self, client: UpbitClient, db: Database) -> None:
        self._client = client
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_historical_candles(
        self,
        market: str,
        timeframe: str,
        count: int = 200,
    ) -> list[Candle]:
        """Fetch *count* historical candles from Upbit and save them to the DB.

        Handles pagination automatically when count > 200.

        Args:
            market: Market code, e.g. ``'KRW-BTC'``.
            timeframe: Candle interval, e.g. ``'1m'``, ``'1h'``, ``'1d'``.
            count: Number of candles to retrieve (may span multiple API calls).

        Returns:
            List of persisted :class:`Candle` ORM objects, ordered oldest-first.
        """
        raw_candles: list[dict[str, Any]] = []
        to_fetch = count
        to_time: str | None = None  # ISO-8601 cursor for pagination

        while to_fetch > 0:
            batch_size = min(to_fetch, _UPBIT_MAX_CANDLES_PER_REQUEST)
            batch = await self._client.get_candles(
                market=market,
                timeframe=timeframe,
                count=batch_size,
                to=to_time,
            )
            if not batch:
                logger.debug(
                    "No more candles returned for %s/%s (cursor=%s).",
                    market,
                    timeframe,
                    to_time,
                )
                break

            raw_candles.extend(batch)
            to_fetch -= len(batch)

            # Upbit returns candles newest-first; the oldest in this batch is last.
            oldest_ts: str = batch[-1].get(
                "candle_date_time_utc", batch[-1].get("timestamp", "")
            )
            to_time = oldest_ts  # next page: fetch candles older than this timestamp

            if len(batch) < batch_size:
                break  # API returned fewer records than requested — no more data

        saved = await self.save_candles(raw_candles, market=market, timeframe=timeframe)
        logger.info(
            "fetch_historical_candles: fetched %d raw records, saved %d new candles "
            "for %s/%s.",
            len(raw_candles),
            saved,
            market,
            timeframe,
        )

        # Return persisted candles from DB ordered oldest-first.
        async with self._db.get_session() as session:
            result = await session.execute(
                select(Candle)
                .where(Candle.market == market, Candle.timeframe == timeframe)
                .order_by(Candle.timestamp.asc())
                .limit(count)
            )
            return list(result.scalars().all())

    async def save_candles(
        self,
        candles: list[dict[str, Any]],
        market: str,
        timeframe: str,
    ) -> int:
        """Convert raw API dicts to :class:`Candle` rows and bulk-insert them.

        Duplicate rows (same market / timeframe / timestamp) are silently skipped
        using a select-then-insert approach for database portability.

        Args:
            candles: List of raw candle dicts from the Upbit REST API.
            market: Market code, e.g. ``'KRW-BTC'``.
            timeframe: Candle interval string.

        Returns:
            Number of newly inserted rows.
        """
        if not candles:
            return 0

        rows = [_api_dict_to_row(c, market, timeframe) for c in candles]
        inserted = 0

        async with self._db.get_session() as session:
            for row in rows:
                # Check if candle already exists (DB-agnostic duplicate handling)
                existing = await session.execute(
                    select(Candle).where(
                        Candle.market == row["market"],
                        Candle.timeframe == row["timeframe"],
                        Candle.timestamp == row["timestamp"],
                    ).limit(1)
                )
                if existing.scalars().first() is None:
                    session.add(Candle(**row))
                    inserted += 1

        logger.debug(
            "save_candles: %d rows provided, %d newly inserted for %s/%s.",
            len(rows),
            inserted,
            market,
            timeframe,
        )
        return inserted

    async def get_latest_candle(self, market: str, timeframe: str) -> Candle | None:
        """Return the most recent :class:`Candle` for the given market/timeframe.

        Args:
            market: Market code, e.g. ``'KRW-BTC'``.
            timeframe: Candle interval string.

        Returns:
            The latest :class:`Candle` or ``None`` if no data exists.
        """
        async with self._db.get_session() as session:
            result = await session.execute(
                select(Candle)
                .where(Candle.market == market, Candle.timeframe == timeframe)
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )
            candle = result.scalars().first()

        if candle is None:
            logger.debug("get_latest_candle: no candle found for %s/%s.", market, timeframe)
        return candle


# ------------------------------------------------------------------
# Module-level utility
# ------------------------------------------------------------------

def _api_dict_to_row(raw: dict[str, Any], market: str, timeframe: str) -> dict[str, Any]:
    """Convert a single Upbit candle API dict to a column-value mapping for SQLAlchemy."""
    # Upbit returns UTC timestamps as ISO-8601 strings, e.g. "2024-01-01T00:00:00".
    ts_str: str = raw.get("candle_date_time_utc") or raw.get("timestamp", "")
    if isinstance(ts_str, str):
        ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    else:
        # Fallback: unix ms timestamp (websocket format)
        ts = datetime.fromtimestamp(int(ts_str) / 1000, tz=timezone.utc)

    return {
        "market": market,
        "timeframe": timeframe,
        "timestamp": ts,
        "open": float(raw.get("opening_price", raw.get("open", 0.0))),
        "high": float(raw.get("high_price", raw.get("high", 0.0))),
        "low": float(raw.get("low_price", raw.get("low", 0.0))),
        "close": float(raw.get("trade_price", raw.get("close", 0.0))),
        "volume": float(raw.get("candle_acc_trade_volume", raw.get("volume", 0.0))),
    }
