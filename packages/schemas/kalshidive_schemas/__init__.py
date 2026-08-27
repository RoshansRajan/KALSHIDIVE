"""Shared event and insight schemas.

Every service in KalshiDive speaks these models and nothing else. The ingest
service produces them, Postgres stores them, the analysis agent consumes them
and produces MarketInsight, and the TypeScript dashboard mirrors them in
web/src/types.ts. Changing a model here is a breaking change everywhere.
"""

from .events import (
    MarketEvent,
    OrderBookDelta,
    OrderBookSnapshot,
    PriceLevel,
    Side,
    Trade,
)
from .insight import MarketInsight, Signal, StoredInsight

__all__ = [
    "MarketEvent",
    "MarketInsight",
    "OrderBookDelta",
    "OrderBookSnapshot",
    "PriceLevel",
    "Side",
    "Signal",
    "StoredInsight",
    "Trade",
]
