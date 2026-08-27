"""Internal broadcast hub.

This is the seam that keeps the services decoupled: ingest publishes without
knowing who listens, and the analysis agent and dashboard subscribe without
knowing where events came from. Adding a third consumer requires no change to
ingest at all.

Delivery is best-effort and non-blocking. A slow subscriber gets its backlog
dropped rather than being allowed to stall the hub -- because the alternative
is that one wedged browser tab applies backpressure all the way up the
pipeline and stops ticks reaching Postgres. Durability is Postgres's job; the
fanout's job is liveness.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve

log = logging.getLogger(__name__)


class Fanout:
    """Broadcasts JSON messages to every connected subscriber."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765, queue_size: int = 256):
        self.host = host
        self.port = port
        self.queue_size = queue_size
        self._subscribers: set[asyncio.Queue] = set()
        self._server = None

    async def start(self) -> None:
        self._server = await serve(self._handle, self.host, self.port)
        log.info("fanout listening on ws://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def publish(self, message: dict[str, Any]) -> None:
        """Queue a message for all subscribers.

        Synchronous and never awaits, so the ingest hot loop is not coupled to
        subscriber speed.
        """
        if not self._subscribers:
            return
        payload = json.dumps(message, default=str)
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest and retry once: for a live order book, the
                # newest state is strictly more useful than the stalest one.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def _handle(self, connection: ServerConnection) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.add(queue)
        log.info("subscriber connected (%d total)", len(self._subscribers))
        try:
            while True:
                payload = await queue.get()
                await connection.send(payload)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._subscribers.discard(queue)
            log.info("subscriber left (%d remain)", len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
