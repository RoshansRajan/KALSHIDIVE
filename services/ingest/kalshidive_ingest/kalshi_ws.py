"""Live Kalshi WebSocket client.

Two responsibilities, kept separate on purpose:

1. Translate Kalshi's wire format into our normalized events (``_parse``).
2. Keep the socket alive forever (``stream``), so callers never see a
   disconnect -- only a stream that occasionally pauses.

Auth uses RSA-PSS request signing: Kalshi issues an API key ID and a private
key, and each connection signs ``timestamp + method + path``. The signature is
computed per connection attempt because the timestamp is part of the signed
payload -- a cached signature would be rejected as replayed after its window.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import websockets
from kalshidive_schemas import (
    MarketEvent,
    OrderBookDelta,
    OrderBookSnapshot,
    PriceLevel,
    Side,
    Trade,
)

from .backoff import backoff_delay

log = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
_SIGNED_PATH = "/trade-api/ws/v2"


class KalshiAuth:
    """Signs connection headers with an RSA private key.

    Import of ``cryptography`` is deferred to construction so that the mock
    path -- the one that runs with no credentials -- never needs the
    dependency present at import time.
    """

    def __init__(self, api_key_id: str, private_key_pem: str) -> None:
        from cryptography.hazmat.primitives import serialization

        self.api_key_id = api_key_id
        self._key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None
        )

    def headers(self, method: str = "GET", path: str = _SIGNED_PATH) -> dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}".encode()
        signature = self._key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }


class KalshiFeed:
    """Reconnecting client for Kalshi's orderbook_delta and trade channels."""

    def __init__(
        self,
        market_tickers: list[str],
        *,
        auth: KalshiAuth | None = None,
        url: str = DEFAULT_WS_URL,
        ping_interval: float = 10.0,
    ) -> None:
        self.market_tickers = market_tickers
        self.auth = auth
        self.url = url
        self.ping_interval = ping_interval
        self._cmd_id = 0

    def _subscribe_cmd(self) -> str:
        self._cmd_id += 1
        return json.dumps(
            {
                "id": self._cmd_id,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta", "trade"],
                    "market_tickers": self.market_tickers,
                },
            }
        )

    @staticmethod
    def _parse(raw: str) -> MarketEvent | None:
        """Translate one wire message, or None if it is not a market event.

        Unknown message types are ignored rather than raising. Venues add
        message types without warning, and a client that crashes on an
        unrecognized type is a client that breaks on someone else's deploy.
        """
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("undecodable frame: %.120s", raw)
            return None

        msg_type = payload.get("type")
        seq = payload.get("seq")
        msg = payload.get("msg") or {}

        if msg_type == "error":
            log.error("kalshi error frame: %s", msg)
            return None
        if seq is None or not msg:
            return None

        ticker = msg.get("market_ticker")
        if not ticker:
            return None

        if msg_type == "orderbook_snapshot":
            return OrderBookSnapshot(
                market_ticker=ticker,
                seq=seq,
                yes=[PriceLevel(price=p, size=s) for p, s in msg.get("yes", [])],
                no=[PriceLevel(price=p, size=s) for p, s in msg.get("no", [])],
            )

        if msg_type == "orderbook_delta":
            return OrderBookDelta(
                market_ticker=ticker,
                seq=seq,
                side=Side(msg["side"]),
                price=msg["price"],
                delta=msg["delta"],
            )

        if msg_type == "trade":
            return Trade(
                market_ticker=ticker,
                seq=seq,
                side=Side(msg.get("taker_side", "yes")),
                price=msg["yes_price"],
                count=msg["count"],
            )

        return None

    async def stream(self) -> AsyncIterator[MarketEvent]:
        """Yield events forever, reconnecting through any failure.

        Note that ``attempt`` resets only after a connection produces at least
        one message. Resetting on connect alone would defeat the backoff
        entirely against a venue that accepts sockets and immediately closes
        them -- a common failure mode during maintenance, and one that turns
        into a tight reconnect loop if you count connection as success.
        """
        attempt = 0
        while True:
            try:
                headers = self.auth.headers() if self.auth else {}
                async with websockets.connect(
                    self.url,
                    additional_headers=headers,
                    ping_interval=self.ping_interval,
                ) as ws:
                    await ws.send(self._subscribe_cmd())
                    log.info("subscribed to %s", ", ".join(self.market_tickers))
                    got_message = False

                    async for raw in ws:
                        if not got_message:
                            got_message = True
                            attempt = 0
                        event = self._parse(raw)
                        if event is not None:
                            yield event

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - stream must never die
                delay = backoff_delay(attempt)
                log.warning(
                    "feed disconnected (%s); reconnecting in %.1fs", exc, delay
                )
                await asyncio.sleep(delay)
                attempt += 1
