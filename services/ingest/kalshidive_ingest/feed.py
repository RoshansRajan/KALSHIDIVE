"""Feed abstraction: one interface, a live venue and a synthetic one behind it.

The whole pipeline downstream of this module is identical whether events come
from Kalshi or from a generator. That buys three things: the stack runs
end-to-end with no credentials, tests are deterministic without a network, and
recorded history can be replayed through the same path as live data -- which
is the entire point of persisting ticks.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from kalshidive_schemas import (
    MarketEvent,
    OrderBookDelta,
    OrderBookSnapshot,
    PriceLevel,
    Side,
    Trade,
)


@runtime_checkable
class Feed(Protocol):
    """Anything that yields normalized market events.

    Implementations are responsible for their own reconnection. A Feed that
    raises on a dropped connection has pushed its hardest problem onto every
    caller; one that reconnects internally lets callers treat the stream as
    infinite.
    """

    def stream(self) -> AsyncIterator[MarketEvent]:
        """Yield events until cancelled. Must not terminate on disconnect."""
        ...


class MockFeed:
    """Synthetic feed producing a plausible random-walk book.

    Crucially, this maintains its own view of level sizes and never emits a
    delta that would drive one negative. That constraint is the difference
    between a useful mock and a harmful one: a generator that emits impossible
    states makes the consumer log desync warnings constantly, and a warning
    that fires constantly is a warning nobody reads.

    Failure injection is opt-in via ``drop_rate`` rather than accidental.
    """

    def __init__(
        self,
        market_tickers: list[str] | None = None,
        *,
        interval: float = 0.25,
        snapshot_every: int = 200,
        drop_rate: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self.market_tickers = market_tickers or [
            "KXBTCD-25AUG26-T100000",
            "KXHIGHNY-25AUG26-T90",
        ]
        self.interval = interval
        self.snapshot_every = snapshot_every
        self.drop_rate = drop_rate
        self._rng = random.Random(seed)
        self._seq: dict[str, int] = {t: 0 for t in self.market_tickers}
        self._mid: dict[str, int] = {
            t: self._rng.randint(30, 70) for t in self.market_tickers
        }
        # Target spread per market, random-walked so the agent has a varying
        # liquidity signal to read rather than a constant.
        self._spread: dict[str, int] = {t: 2 for t in self.market_tickers}
        # Mirrors what a consumer's OrderBook should hold: {ticker: {side: {price: size}}}
        self._levels: dict[str, dict[Side, dict[int, int]]] = {
            t: {Side.YES: {}, Side.NO: {}} for t in self.market_tickers
        }

    def _snapshot(self, ticker: str) -> OrderBookSnapshot:
        mid = self._mid[ticker]
        caps = self._caps(mid, self._spread[ticker])
        yes = {
            p: self._rng.randint(50, 3000)
            for p in range(max(1, caps[Side.YES] - 3), caps[Side.YES] + 1)
        }
        no = {
            p: self._rng.randint(50, 3000)
            for p in range(max(1, caps[Side.NO] - 3), caps[Side.NO] + 1)
        }
        self._levels[ticker] = {Side.YES: dict(yes), Side.NO: dict(no)}
        self._seq[ticker] += 1
        return OrderBookSnapshot(
            market_ticker=ticker,
            seq=self._seq[ticker],
            yes=[PriceLevel(price=p, size=s) for p, s in sorted(yes.items())],
            no=[PriceLevel(price=p, size=s) for p, s in sorted(no.items())],
        )

    @staticmethod
    def _caps(mid: int, spread: int = 1) -> dict[Side, int]:
        """Highest permissible price on each side, for a given mid and spread.

        A YES bid at `y` and a NO bid at `n` imply a YES ask of `100 - n`, so
        the realized spread is `100 - n - y`. Capping the two sides so they
        sum to at most `100 - spread` therefore guarantees at least `spread`
        cents between bid and ask.

        The spread is a parameter rather than a constant because pinning both
        sides against each other yields a book whose spread is permanently
        exactly one cent -- which makes the agent's spread-based reasoning
        vacuous, since the signal it is asked to read never varies.
        """
        low = spread // 2
        high = spread - low
        return {Side.YES: mid - low, Side.NO: 100 - mid - high}

    def _is_crossed(self, ticker: str) -> bool:
        yes = self._levels[ticker][Side.YES]
        no = self._levels[ticker][Side.NO]
        if not yes or not no:
            return False
        return max(yes) + max(no) >= 100

    def _price_cap(self, ticker: str, side: Side, mid: int) -> int:
        """Highest price we may quote on ``side`` without crossing.

        The mid-derived cap is not sufficient on its own: levels resting from
        an earlier mid can sit closer to the spread than the current mid
        implies, so a new quote at the mid-derived cap would cross them. The
        binding constraint is the opposite side's current best.
        """
        other = Side.NO if side is Side.YES else Side.YES
        other_levels = self._levels[ticker][other]
        spread = self._spread[ticker]

        cap = self._caps(mid, spread)[side]
        if other_levels:
            cap = min(cap, 100 - spread - max(other_levels))
        return cap

    def _next_delta(self, ticker: str, seq: int) -> OrderBookDelta | None:
        """Pick a delta that keeps the book internally consistent.

        Returns None when no non-crossing price is available, leaving the
        caller to resync with a snapshot.
        """
        mid = self._mid[ticker]
        side = self._rng.choice([Side.YES, Side.NO])
        levels = self._levels[ticker][side]

        cap = self._price_cap(ticker, side, mid)
        if cap < 1:
            return None

        price = max(1, cap - self._rng.randint(0, 3))
        current = levels.get(price, 0)

        if current > 0 and self._rng.random() < 0.45:
            # Remove size, but never more than is actually resting there.
            amount = -self._rng.randint(1, current)
        else:
            amount = self._rng.choice([100, 200, 400, 800])

        levels[price] = current + amount
        if levels[price] == 0:
            del levels[price]

        return OrderBookDelta(
            market_ticker=ticker, seq=seq, side=side, price=price, delta=amount
        )

    async def stream(self) -> AsyncIterator[MarketEvent]:
        for ticker in self.market_tickers:
            yield self._snapshot(ticker)

        tick = 0
        while True:
            await asyncio.sleep(self.interval)
            tick += 1
            ticker = self._rng.choice(self.market_tickers)

            if tick % self.snapshot_every == 0:
                yield self._snapshot(ticker)
                continue

            self._seq[ticker] += 1
            # Simulate a lost message: burn a seq without emitting it, which
            # is precisely what a consumer's gap detection should catch.
            if self.drop_rate and self._rng.random() < self.drop_rate:
                self._seq[ticker] += 1

            seq = self._seq[ticker]

            if self._rng.random() < 0.15:
                yield Trade(
                    market_ticker=ticker,
                    seq=seq,
                    side=Side.YES,
                    price=self._mid[ticker],
                    count=self._rng.randint(1, 200),
                )
                continue

            drift = self._rng.choice([-1, 0, 1])
            self._mid[ticker] = max(5, min(95, self._mid[ticker] + drift))
            if self._rng.random() < 0.1:
                self._spread[ticker] = max(
                    1, min(6, self._spread[ticker] + self._rng.choice([-1, 1]))
                )

            # Levels resting from an older mid can end up crossed once the mid
            # walks past them. A real venue would match them away; we resync
            # with a snapshot instead, which is a legitimate event the
            # consumer already handles -- and far simpler than modelling a
            # matching engine just to keep the demo book sane.
            if self._is_crossed(ticker):
                self._seq[ticker] = seq - 1
                yield self._snapshot(ticker)
                continue

            delta = self._next_delta(ticker, seq)
            if delta is None:
                self._seq[ticker] = seq - 1
                yield self._snapshot(ticker)
                continue

            yield delta
