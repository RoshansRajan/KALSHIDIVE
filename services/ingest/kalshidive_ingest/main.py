"""Ingest entrypoint.

Pipeline: Feed -> OrderBook (validate) -> TickStore (persist) -> Fanout
(broadcast). Each stage is independently testable and none of them knows
about the others' internals.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from kalshidive_bus import Fanout
from kalshidive_schemas import OrderBookSnapshot

from .config import Config
from .feed import MockFeed
from .kalshi_ws import KalshiAuth, KalshiFeed
from .orderbook import BookDesync, OrderBook
from .store import TickStore

log = logging.getLogger("kalshidive.ingest")


def build_feed(cfg: Config):
    """Pick the live feed or the mock, based on whether credentials exist."""
    if not cfg.use_live_feed:
        log.warning(
            "KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH unset -- using MockFeed. "
            "Data is synthetic."
        )
        return MockFeed(cfg.market_tickers or None)

    with open(cfg.kalshi_private_key_path) as fh:
        pem = fh.read()
    auth = KalshiAuth(cfg.kalshi_api_key_id, pem)
    log.info("using live Kalshi feed for %s", ", ".join(cfg.market_tickers))
    return KalshiFeed(cfg.market_tickers, auth=auth)


class Ingest:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = TickStore(cfg.database_url)
        self.fanout = Fanout(cfg.fanout_host, cfg.fanout_port)
        self.books: dict[str, OrderBook] = {}
        self._resnapshot_needed: set[str] = set()

    def _book(self, ticker: str) -> OrderBook:
        if ticker not in self.books:
            self.books[ticker] = OrderBook(market_ticker=ticker)
        return self.books[ticker]

    def _apply(self, event) -> None:
        """Keep the local book current, tolerating gaps.

        A gap is logged and the book marked stale; we do NOT drop the event
        from storage. The recorded history should reflect what the venue
        actually sent, including the messy parts -- filtering at write time
        would hide the gap from anyone analyzing the data later.
        """
        book = self._book(event.market_ticker)
        try:
            book.apply(event)
            if isinstance(event, OrderBookSnapshot):
                self._resnapshot_needed.discard(event.market_ticker)
        except BookDesync as gap:
            if event.market_ticker not in self._resnapshot_needed:
                log.warning("%s -- awaiting snapshot to resync", gap)
                self._resnapshot_needed.add(event.market_ticker)

    def _broadcast(self, event) -> None:
        book = self._book(event.market_ticker)
        bid, ask = book.best_bid(), book.best_ask()
        self.fanout.publish(
            {
                "kind": "tick",
                "event": event.model_dump(mode="json"),
                "book": {
                    "market_ticker": book.market_ticker,
                    "best_bid": bid[0] if bid else None,
                    "best_bid_size": bid[1] if bid else None,
                    "best_ask": ask[0] if ask else None,
                    "best_ask_size": ask[1] if ask else None,
                    "spread": book.spread(),
                    "stale": book.stale,
                    "yes": sorted(book.yes.items(), reverse=True)[:5],
                    "no": sorted(book.no.items(), reverse=True)[:5],
                },
            }
        )

    async def run(self) -> None:
        await self.store.connect()
        await self.fanout.start()
        feed = build_feed(self.cfg)

        log.info("ingest running")
        async for event in feed.stream():
            self._apply(event)
            await self.store.record(event)
            self._broadcast(event)

    async def shutdown(self) -> None:
        log.info("shutting down; flushing buffered ticks")
        await self.store.close()
        await self.fanout.stop()


async def amain() -> None:
    cfg = Config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    ingest = Ingest(cfg)
    task = asyncio.create_task(ingest.run())

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    done, _ = await asyncio.wait(
        [task, asyncio.create_task(stop.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    task.cancel()
    # Flush before exiting: buffered ticks are real data, and a clean SIGTERM
    # from ECS is the one shutdown we can actually handle gracefully.
    await ingest.shutdown()
    for d in done:
        if d is task and not d.cancelled() and d.exception():
            raise d.exception()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
