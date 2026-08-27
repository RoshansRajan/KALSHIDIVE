"""Normalized market events.

Kalshi's wire format is terse and positional (``[[price, size], ...]``). We
normalize at the edge so that nothing downstream has to know what Kalshi's
JSON looks like -- swapping in a different venue means writing a new adapter,
not touching the order book, the store, or the agent.

Prices are integer cents (1-99). Kalshi contracts settle at $0 or $1, so a
price is literally a probability in percent, and integers avoid float drift
when applying thousands of deltas to the same level.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Side(StrEnum):
    """Which side of a binary contract an order rests on."""

    YES = "yes"
    NO = "no"


class PriceLevel(BaseModel):
    """A single resting level in the book."""

    price: int = Field(ge=1, le=99, description="Cents; also implied probability.")
    size: int = Field(ge=0, description="Contracts resting at this price.")


def _now() -> datetime:
    return datetime.now(UTC)


class _BaseEvent(BaseModel):
    """Fields every market event carries.

    ``seq`` is Kalshi's per-subscription sequence number and is the backbone of
    replay: a gap between consecutive seq values means we silently missed a
    delta and our book is now wrong. See OrderBook.apply_delta.
    """

    market_ticker: str
    seq: int = Field(ge=0)
    received_at: datetime = Field(default_factory=_now)


class OrderBookSnapshot(_BaseEvent):
    """Full book state. Resets any local book to a known-good baseline."""

    type: Literal["snapshot"] = "snapshot"
    yes: list[PriceLevel] = Field(default_factory=list)
    no: list[PriceLevel] = Field(default_factory=list)


class OrderBookDelta(_BaseEvent):
    """Incremental change to one price level.

    ``delta`` is signed: positive adds contracts, negative removes them. It is
    a change in size, not a new absolute size.
    """

    type: Literal["delta"] = "delta"
    side: Side
    price: int = Field(ge=1, le=99)
    delta: int


class Trade(_BaseEvent):
    """An execution."""

    type: Literal["trade"] = "trade"
    side: Side
    price: int = Field(ge=1, le=99)
    count: int = Field(gt=0)


MarketEvent = Annotated[
    OrderBookSnapshot | OrderBookDelta | Trade,
    Field(discriminator="type"),
]
