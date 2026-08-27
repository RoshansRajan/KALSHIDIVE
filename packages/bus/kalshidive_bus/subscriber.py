"""Reconnecting subscriber for the internal fanout.

Mirrors the reconnect discipline of the Kalshi client, for the same reason:
a consumer that dies when its producer restarts turns every deploy into a
manual restart of everything downstream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets

log = logging.getLogger(__name__)


async def subscribe(
    url: str,
    *,
    base_delay: float = 0.5,
    max_delay: float = 15.0,
) -> AsyncIterator[dict[str, Any]]:
    """Yield decoded messages from a fanout, reconnecting forever."""
    delay = base_delay
    while True:
        try:
            async with websockets.connect(url) as ws:
                log.info("subscribed to %s", url)
                delay = base_delay
                async for raw in ws:
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("undecodable message from %s", url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - subscriber must survive
            log.warning("bus connection to %s lost (%s); retry in %.1fs", url, exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
