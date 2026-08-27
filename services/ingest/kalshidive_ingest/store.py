"""Postgres persistence for ticks, cursors, and insights.

Writes are batched. At tick granularity a busy market produces events far
faster than round-trip-per-row inserts can absorb, and the bottleneck is round
trips rather than the inserts themselves -- so we accumulate and flush with
``copy_records_to_table``, which is an order of magnitude cheaper than
per-row INSERTs.

The tradeoff is an explicit bounded window of data loss: anything buffered but
unflushed when the process dies is gone. ``flush_interval`` is that window,
and it is a knob rather than a hidden constant for exactly that reason.
"""

from __future__ import annotations

import asyncio
import json
import logging

import asyncpg
from kalshidive_schemas import (
    MarketEvent,
    OrderBookDelta,
    StoredInsight,
    Trade,
)

log = logging.getLogger(__name__)

_TICK_COLUMNS = (
    "market_ticker",
    "seq",
    "event_type",
    "side",
    "price",
    "delta",
    "count",
    "payload",
    "received_at",
)


def _to_row(event: MarketEvent) -> tuple:
    """Flatten an event into a market_ticks row.

    The hot columns (side, price, delta) are promoted out of the JSON so that
    the common queries are plain column reads, while ``payload`` keeps the
    full event for anything we did not anticipate needing. Promoting
    everything would ossify the schema; promoting nothing would make every
    query a JSON traversal.
    """
    side = getattr(event, "side", None)
    return (
        event.market_ticker,
        event.seq,
        event.type,
        side.value if side is not None else None,
        getattr(event, "price", None),
        event.delta if isinstance(event, OrderBookDelta) else None,
        event.count if isinstance(event, Trade) else None,
        json.dumps(event.model_dump(mode="json")),
        event.received_at,
    )


class TickStore:
    """Batched writer with a periodic flush."""

    def __init__(
        self,
        dsn: str,
        *,
        batch_size: int = 500,
        flush_interval: float = 1.0,
    ) -> None:
        self.dsn = dsn
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._pool: asyncpg.Pool | None = None
        self._buffer: list[tuple] = []
        self._cursors: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        self._flusher = asyncio.create_task(self._flush_loop())
        log.info("tick store connected")

    async def close(self) -> None:
        if self._flusher:
            self._flusher.cancel()
            try:
                await self._flusher
            except asyncio.CancelledError:
                pass
        await self.flush()
        if self._pool:
            await self._pool.close()

    async def record(self, event: MarketEvent) -> None:
        async with self._lock:
            self._buffer.append(_to_row(event))
            self._cursors[event.market_ticker] = event.seq
            should_flush = len(self._buffer) >= self.batch_size
        if should_flush:
            await self.flush()

    async def flush(self) -> None:
        """Write buffered ticks and advance cursors in one transaction.

        Ticks and cursors must move together. A cursor ahead of its ticks
        would make a restart skip history that was never actually written --
        the one inconsistency that would quietly corrupt the replay guarantee
        this whole table exists to provide.
        """
        async with self._lock:
            rows, cursors = self._buffer, self._cursors
            self._buffer, self._cursors = [], {}

        if not rows or self._pool is None:
            return

        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.copy_records_to_table(
                    "market_ticks", records=rows, columns=list(_TICK_COLUMNS)
                )
                if cursors:
                    await conn.executemany(
                        """
                        INSERT INTO feed_cursor (market_ticker, last_seq, updated_at)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (market_ticker) DO UPDATE
                          SET last_seq = EXCLUDED.last_seq, updated_at = NOW()
                        """,
                        list(cursors.items()),
                    )
            log.debug("flushed %d ticks", len(rows))
        except Exception:
            log.exception("flush failed; dropped %d ticks", len(rows))

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval)
            await self.flush()

    async def last_seq(self, market_ticker: str) -> int | None:
        """Resume point recorded for this market, if any."""
        if self._pool is None:
            return None
        return await self._pool.fetchval(
            "SELECT last_seq FROM feed_cursor WHERE market_ticker = $1",
            market_ticker,
        )

    async def save_insight(self, stored: StoredInsight) -> None:
        if self._pool is None:
            return
        i = stored.insight
        await self._pool.execute(
            """
            INSERT INTO market_insights (
                market_ticker, signal, confidence, rationale, horizon_minutes,
                window_start_seq, window_end_seq, model, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            stored.market_ticker,
            i.signal.value,
            i.confidence,
            i.rationale,
            i.horizon_minutes,
            stored.window_start_seq,
            stored.window_end_seq,
            stored.model,
            stored.created_at,
        )

    async def replay(
        self,
        market_ticker: str,
        *,
        start_seq: int = 0,
        end_seq: int | None = None,
    ) -> list[dict]:
        """Recorded ticks for one market, in sequence order.

        This is the payoff for append-only storage: feed the result through
        the same OrderBook the live path uses and you have the book exactly as
        it stood, not an approximation of it.
        """
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            """
            SELECT payload FROM market_ticks
            WHERE market_ticker = $1 AND seq >= $2
              AND ($3::BIGINT IS NULL OR seq <= $3)
            ORDER BY seq
            """,
            market_ticker,
            start_seq,
            end_seq,
        )
        return [json.loads(r["payload"]) for r in rows]
