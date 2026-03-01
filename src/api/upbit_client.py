import asyncio
import logging
from typing import Any

import httpx

from .auth import create_jwt_token

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.upbit.com/v1"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.5  # seconds


class UpbitClient:
    """Async HTTP client for the Upbit REST API.

    Supports authenticated and public endpoints with rate limiting
    (via asyncio.Semaphore) and exponential-backoff retries.

    Usage::

        async with UpbitClient(access_key, secret_key) as client:
            accounts = await client.get_accounts()
    """

    def __init__(self, access_key: str, secret_key: str) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = httpx.AsyncClient(base_url=_BASE_URL, timeout=10.0)
        # Upbit rate limits: 10 req/s for market data, 8 req/s for orders
        self._market_sem = asyncio.Semaphore(10)
        self._order_sem = asyncio.Semaphore(8)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "UpbitClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        await self._client.aclose()
        logger.debug("UpbitClient closed.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        authenticated: bool = False,
        is_order: bool = False,
    ) -> Any:
        """Execute an HTTP request with rate limiting and retry logic.

        Args:
            method: HTTP method ("GET", "POST", "DELETE").
            path: URL path relative to the base URL (e.g. "/accounts").
            params: Query parameters (GET) or form body (POST/DELETE).
            authenticated: Whether to attach a JWT Authorization header.
            is_order: Use the order semaphore (8 req/s) instead of the
                      market semaphore (10 req/s).

        Returns:
            Parsed JSON response (dict or list).

        Raises:
            httpx.HTTPStatusError: On non-2xx responses after all retries.
            httpx.RequestError: On network-level failures after all retries.
        """
        sem = self._order_sem if is_order else self._market_sem
        headers: dict[str, str] = {}

        if authenticated:
            token = create_jwt_token(
                self._access_key,
                self._secret_key,
                query_params=params if method == "GET" else None,
            )
            headers["Authorization"] = token

        for attempt in range(_MAX_RETRIES):
            try:
                async with sem:
                    if method == "GET":
                        response = await self._client.get(
                            path, params=params, headers=headers
                        )
                    elif method == "POST":
                        response = await self._client.post(
                            path, data=params, headers=headers
                        )
                    elif method == "DELETE":
                        response = await self._client.delete(
                            path, params=params, headers=headers
                        )
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")

                response.raise_for_status()
                return response.json()

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                if attempt == _MAX_RETRIES - 1:
                    logger.error(
                        "Request failed after %d retries: %s %s -> %s",
                        _MAX_RETRIES,
                        method,
                        path,
                        exc,
                    )
                    raise

                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Request error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Public market endpoints
    # ------------------------------------------------------------------

    async def get_ticker(self, markets: list[str]) -> list[dict]:
        """Return ticker information for the given markets.

        Args:
            markets: Market codes, e.g. ["KRW-BTC", "KRW-ETH"].

        Returns:
            List of ticker dicts from the Upbit API.
        """
        params = {"markets": ",".join(markets)}
        return await self._request("GET", "/ticker", params=params)

    async def get_orderbook(self, markets: list[str]) -> list[dict]:
        """Return order book (depth) data for the given markets.

        Args:
            markets: Market codes, e.g. ["KRW-BTC"].

        Returns:
            List of orderbook dicts from the Upbit API.
        """
        params = {"markets": ",".join(markets)}
        return await self._request("GET", "/orderbook", params=params)

    async def get_candles(
        self,
        market: str,
        timeframe: str = "1m",
        count: int = 200,
        to: str | None = None,
    ) -> list[dict]:
        """Return OHLCV candle data.

        Args:
            market: Market code, e.g. "KRW-BTC".
            timeframe: Candle unit.  Minute-based values: "1m", "3m", "5m",
                       "10m", "15m", "30m", "60m", "240m".  Daily: "1d".
                       Also accepts: "1h" (=60m), "4h" (=240m), "1w", "1M".
            count: Number of candles to return (max 200).
            to: Optional ISO-8601 datetime cursor; fetch candles before this time.

        Returns:
            List of candle dicts from the Upbit API.
        """
        _timeframe_map: dict[str, tuple[str, int]] = {
            "1m": ("minutes", 1), "3m": ("minutes", 3), "5m": ("minutes", 5),
            "10m": ("minutes", 10), "15m": ("minutes", 15), "30m": ("minutes", 30),
            "60m": ("minutes", 60), "1h": ("minutes", 60), "240m": ("minutes", 240),
            "4h": ("minutes", 240), "1d": ("days", 0), "1w": ("weeks", 0),
            "1M": ("months", 0),
        }

        if timeframe not in _timeframe_map:
            raise ValueError(
                f"Invalid timeframe '{timeframe}'. "
                f"Supported: {sorted(_timeframe_map)}"
            )

        unit_type, unit_value = _timeframe_map[timeframe]
        if unit_type == "minutes":
            path = f"/candles/minutes/{unit_value}"
        elif unit_type == "days":
            path = "/candles/days"
        elif unit_type == "weeks":
            path = "/candles/weeks"
        else:
            path = "/candles/months"

        params: dict[str, Any] = {"market": market, "count": count}
        if to:
            params["to"] = to

        return await self._request("GET", path, params=params)

    # ------------------------------------------------------------------
    # Authenticated account / order endpoints
    # ------------------------------------------------------------------

    async def get_accounts(self) -> list[dict]:
        """Return account balance information.

        Returns:
            List of account dicts (one per currency held).
        """
        return await self._request("GET", "/accounts", authenticated=True)

    async def create_order(
        self,
        market: str,
        side: str,
        volume: str | None = None,
        price: str | None = None,
        ord_type: str = "limit",
    ) -> dict:
        """Place a new order.

        Args:
            market: Market code, e.g. "KRW-BTC".
            side: "bid" (buy) or "ask" (sell).
            volume: Order volume (required for limit/market ask orders).
            price: Order price (required for limit/market bid orders).
            ord_type: "limit", "price" (market buy), or "market" (market sell).

        Returns:
            Order dict returned by the Upbit API.
        """
        params: dict[str, Any] = {"market": market, "side": side, "ord_type": ord_type}
        if volume is not None:
            params["volume"] = volume
        if price is not None:
            params["price"] = price

        logger.info("Creating %s order: market=%s, side=%s", ord_type, market, side)
        return await self._request(
            "POST", "/orders", params=params, authenticated=True, is_order=True
        )

    async def get_order(self, uuid: str) -> dict:
        """Fetch details of a single order by UUID.

        Args:
            uuid: Order UUID returned by :meth:`create_order`.

        Returns:
            Order dict from the Upbit API.
        """
        params = {"uuid": uuid}
        return await self._request(
            "GET", "/order", params=params, authenticated=True, is_order=True
        )

    async def cancel_order(self, uuid: str) -> dict:
        """Cancel an open order by UUID.

        Args:
            uuid: Order UUID to cancel.

        Returns:
            Cancelled order dict from the Upbit API.
        """
        params = {"uuid": uuid}
        logger.info("Cancelling order: %s", uuid)
        return await self._request(
            "DELETE", "/order", params=params, authenticated=True, is_order=True
        )
