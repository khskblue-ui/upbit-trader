import asyncio
import json
import logging
import uuid
from collections.abc import Callable, AsyncGenerator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

_WS_URL = "wss://api.upbit.com/websocket/v1"
_PING_INTERVAL = 30.0   # seconds between PING frames
_MAX_RECONNECT_DELAY = 60.0  # cap for exponential backoff
_RECONNECT_BASE_DELAY = 1.0


class UpbitWebSocket:
    """Async WebSocket client for the Upbit real-time data feed.

    Subscribes to one or more data types (ticker, trade, orderbook) for the
    given markets, with automatic reconnection and PING/PONG keepalive.

    Usage::

        ws = UpbitWebSocket(markets=["KRW-BTC"], types=["ticker"])
        await ws.run(callback=my_handler)

    Or as an async generator::

        ws = UpbitWebSocket(markets=["KRW-BTC", "KRW-ETH"], types=["trade"])
        await ws.connect()
        async for message in ws.receive():
            process(message)
        await ws.close()
    """

    def __init__(
        self,
        markets: list[str],
        types: list[str] | None = None,
    ) -> None:
        self._markets = markets
        self._types = types if types is not None else ["ticker"]
        self._ws: Any = None  # websockets.WebSocketClientProtocol
        self._closed = False
        self._reconnect_delay = _RECONNECT_BASE_DELAY

    # ------------------------------------------------------------------
    # Subscription helpers
    # ------------------------------------------------------------------

    def _build_subscription(self) -> str:
        """Build the JSON subscription message required by Upbit.

        Format::

            [
              {"ticket": "<unique_id>"},
              {"type": "ticker", "codes": ["KRW-BTC"]},
              ...
            ]
        """
        ticket = str(uuid.uuid4())
        payload: list[dict] = [{"ticket": ticket}]
        for data_type in self._types:
            payload.append({"type": data_type, "codes": self._markets})
        return json.dumps(payload)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish the WebSocket connection and send the subscription."""
        logger.info(
            "Connecting to Upbit WebSocket: markets=%s, types=%s",
            self._markets,
            self._types,
        )
        self._ws = await websockets.connect(
            _WS_URL,
            ping_interval=_PING_INTERVAL,
            ping_timeout=10,
        )
        subscription = self._build_subscription()
        await self._ws.send(subscription)
        logger.debug("Subscription sent: %s", subscription)
        self._reconnect_delay = _RECONNECT_BASE_DELAY  # reset on success

    async def receive(self) -> AsyncGenerator[dict, None]:
        """Async generator that yields decoded messages from the feed.

        Automatically reconnects on disconnection unless :meth:`close` has
        been called.

        Yields:
            Parsed JSON message as a dict.
        """
        while not self._closed:
            if self._ws is None:
                await self._try_reconnect()
                continue

            try:
                raw = await self._ws.recv()
                # Upbit sends binary frames; decode if needed
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                message: dict = json.loads(raw)
                yield message

            except ConnectionClosed as exc:
                if self._closed:
                    return
                logger.warning("WebSocket connection closed: %s", exc)
                self._ws = None
                await self._try_reconnect()

            except Exception as exc:  # noqa: BLE001
                logger.error("Unexpected WebSocket error: %s", exc)
                if self._closed:
                    return
                self._ws = None
                await self._try_reconnect()

    async def close(self) -> None:
        """Gracefully shut down the WebSocket connection."""
        self._closed = True
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
            logger.info("UpbitWebSocket closed.")

    async def run(self, callback: Callable[[dict], Any]) -> None:
        """Main event loop: receive messages and invoke *callback* for each.

        Blocks until :meth:`close` is called or an unrecoverable error occurs.

        Args:
            callback: Async or sync callable that receives one dict argument
                      per message.  If it is a coroutine function it will be
                      awaited; otherwise it is called synchronously.
        """
        if self._ws is None:
            await self.connect()

        logger.info("UpbitWebSocket run loop started.")
        async for message in self.receive():
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(message)
                else:
                    callback(message)
            except Exception as exc:  # noqa: BLE001
                logger.error("Callback raised an exception: %s", exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _try_reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        if self._closed:
            return

        delay = min(self._reconnect_delay, _MAX_RECONNECT_DELAY)
        logger.info("Reconnecting in %.1f seconds…", delay)
        await asyncio.sleep(delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)

        try:
            await self.connect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reconnection attempt failed: %s", exc)
            self._ws = None
