"""Local order book reconstruction with sequence-gap detection.

The book is derived state: a snapshot establishes a baseline and deltas mutate
it. That works right up until one delta goes missing, at which point every
subsequent read is wrong and *nothing raises an error*. A silently-wrong book
is worse than a disconnect, because a disconnect is at least observable.

So the invariant enforced here is: seq numbers must be strictly consecutive.
Anything else marks the book stale and demands a fresh snapshot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kalshidive_schemas import MarketEvent, OrderBookDelta, OrderBookSnapshot, Side, Trade

log = logging.getLogger(__name__)


class BookDesync(Exception):
    """The local book can no longer be trusted; a fresh snapshot is required.

    All three subclasses are recoverable conditions rather than bugs, and
    callers handle them identically (discard, resnapshot). They stay distinct
    because they mean genuinely different things operationally: a steady
    trickle of SequenceGap says the network is lossy, while NegativeSize says
    our view and the venue's have diverged -- and a message that conflates
    them sends you debugging the wrong layer.
    """

    def __init__(self, market_ticker: str, message: str) -> None:
        self.market_ticker = market_ticker
        super().__init__(f"{market_ticker}: {message}")


class SequenceGap(BookDesync):
    """A delta's seq did not directly follow the last applied seq."""

    def __init__(self, market_ticker: str, expected: int, got: int) -> None:
        self.expected = expected
        self.got = got
        super().__init__(
            market_ticker, f"sequence gap, expected {expected}, got {got}"
        )


class NoBaseline(BookDesync):
    """A delta arrived before any snapshot established a baseline."""

    def __init__(self, market_ticker: str, got: int) -> None:
        self.got = got
        super().__init__(
            market_ticker,
            f"delta seq {got} arrived with no valid snapshot to build on",
        )


class NegativeSize(BookDesync):
    """A delta would drive a level below zero.

    The seq was correct, so nothing was dropped in transit -- our accounting
    and the venue's have genuinely diverged, which is a more serious signal
    than a lost message.
    """

    def __init__(
        self, market_ticker: str, side: Side, price: int, resulting: int
    ) -> None:
        self.side = side
        self.price = price
        self.resulting = resulting
        super().__init__(
            market_ticker,
            f"delta would take {side.value} {price}c to {resulting}; "
            f"book has diverged from the venue",
        )


@dataclass
class OrderBook:
    """One market's book.

    Levels are held as ``{price_cents: size}`` dicts rather than sorted lists.
    Deltas address a single price, so dict access keeps application O(1); we
    only pay for sorting when someone actually asks for the top of book, which
    is far rarer than applying a delta.
    """

    market_ticker: str
    yes: dict[int, int] = field(default_factory=dict)
    no: dict[int, int] = field(default_factory=dict)
    last_seq: int | None = None
    stale: bool = True

    def _side(self, side: Side) -> dict[int, int]:
        return self.yes if side is Side.YES else self.no

    def apply_snapshot(self, snap: OrderBookSnapshot) -> None:
        """Reset to a known-good baseline.

        A snapshot always wins. It needs no sequence check precisely because
        it *is* the recovery mechanism -- requiring continuity here would make
        the book unrecoverable after the gap it exists to repair.
        """
        if snap.market_ticker != self.market_ticker:
            raise ValueError(
                f"snapshot for {snap.market_ticker} applied to "
                f"{self.market_ticker}"
            )
        self.yes = {lvl.price: lvl.size for lvl in snap.yes if lvl.size > 0}
        self.no = {lvl.price: lvl.size for lvl in snap.no if lvl.size > 0}
        self.last_seq = snap.seq
        self.stale = False

    def apply_delta(self, delta: OrderBookDelta) -> None:
        """Apply one incremental change.

        Raises SequenceGap if this delta does not directly follow the last
        applied event. The book is marked stale before raising, so a caller
        that swallows the exception still cannot mistake the book for valid.
        """
        if delta.market_ticker != self.market_ticker:
            raise ValueError(
                f"delta for {delta.market_ticker} applied to {self.market_ticker}"
            )
        if self.stale or self.last_seq is None:
            raise NoBaseline(self.market_ticker, delta.seq)

        expected = self.last_seq + 1
        if delta.seq != expected:
            self.stale = True
            raise SequenceGap(self.market_ticker, expected, delta.seq)

        levels = self._side(delta.side)
        new_size = levels.get(delta.price, 0) + delta.delta

        if new_size < 0:
            # The venue and our book disagree about reality. Trust neither.
            self.stale = True
            raise NegativeSize(self.market_ticker, delta.side, delta.price, new_size)

        if new_size == 0:
            levels.pop(delta.price, None)
        else:
            levels[delta.price] = new_size

        self.last_seq = delta.seq

    def apply_trade(self, trade: Trade) -> None:
        """Advance the sequence for an execution.

        A trade changes no resting size, so this mutates nothing -- but it
        does consume a sequence number. Skipping it would leave last_seq
        behind by one and make the very next delta look like a gap, so every
        execution would manufacture a spurious resync. Any event carrying a
        seq must be observed, whether or not it changes the book.
        """
        if trade.market_ticker != self.market_ticker:
            raise ValueError(
                f"trade for {trade.market_ticker} applied to {self.market_ticker}"
            )
        if self.stale or self.last_seq is None:
            raise NoBaseline(self.market_ticker, trade.seq)

        expected = self.last_seq + 1
        if trade.seq != expected:
            self.stale = True
            raise SequenceGap(self.market_ticker, expected, trade.seq)

        self.last_seq = trade.seq

    def apply(self, event: MarketEvent) -> None:
        """Observe any sequenced event. The single entry point callers should use."""
        if isinstance(event, OrderBookSnapshot):
            self.apply_snapshot(event)
        elif isinstance(event, OrderBookDelta):
            self.apply_delta(event)
        elif isinstance(event, Trade):
            self.apply_trade(event)
        else:  # pragma: no cover - the union is closed
            raise TypeError(f"unhandled event type: {type(event).__name__}")

    def best_bid(self) -> tuple[int, int] | None:
        """Highest YES price with resting size, as (price, size)."""
        if not self.yes:
            return None
        price = max(self.yes)
        return price, self.yes[price]

    def best_ask(self) -> tuple[int, int] | None:
        """Cheapest YES-equivalent ask, derived from the NO side.

        Kalshi quotes both sides in their own terms, but buying NO at price p
        is economically selling YES at (100 - p). Converting here means the
        rest of the system reasons about a single YES-denominated book instead
        of constantly flipping between the two conventions.
        """
        if not self.no:
            return None
        no_price = max(self.no)
        return 100 - no_price, self.no[no_price]

    def spread(self) -> int | None:
        """Ask minus bid in cents, or None if either side is empty."""
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return ask[0] - bid[0]
