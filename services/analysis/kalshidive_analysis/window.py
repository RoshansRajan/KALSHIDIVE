"""Tick windowing.

The agent reasons about a span of activity, not a single delta -- one delta
carries no information ("size at 62 went up by 100" means nothing without
context), while thirty seconds of them has shape: direction, momentum,
whether the spread is widening or tightening.

Windows are per-market. Interleaving two markets in one prompt invites the
model to correlate things that have no relationship.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class Window:
    """A closed span of ticks ready for analysis."""

    market_ticker: str
    events: list[dict]
    start_seq: int
    end_seq: int

    def summarize(self, max_events: int = 60) -> str:
        """Render the window as compact text for the prompt.

        Deliberately terse. Feeding raw JSON burns tokens on punctuation and
        repeated key names, and the model reads a dense table at least as well
        -- often better, because the signal is not buried in syntax.

        When a window overflows we keep the newest events: recency dominates
        for a momentum read, and the oldest ticks are the least relevant to
        where the book is heading.
        """
        events = self.events[-max_events:]
        lines = []
        for e in events:
            kind = e.get("type")
            if kind == "delta":
                lines.append(
                    f"{e['seq']} delta {e['side']} {e['price']}c {e['delta']:+d}"
                )
            elif kind == "trade":
                lines.append(
                    f"{e['seq']} TRADE {e['side']} {e['price']}c x{e['count']}"
                )
            elif kind == "snapshot":
                yes = ", ".join(
                    f"{lvl['price']}c x{lvl['size']}" for lvl in e.get("yes", [])
                )
                lines.append(f"{e['seq']} snapshot yes=[{yes}]")

        dropped = len(self.events) - len(events)
        header = f"({dropped} earlier events omitted)\n" if dropped > 0 else ""
        return header + "\n".join(lines)


class WindowBuffer:
    """Accumulates ticks per market and closes windows on a time interval."""

    def __init__(self, window_seconds: float = 30.0, max_events: int = 500) -> None:
        self.window_seconds = window_seconds
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_events))
        self._opened: dict[str, float] = {}

    def add(self, event: dict) -> None:
        ticker = event.get("market_ticker")
        if not ticker:
            return
        self._events[ticker].append(event)
        self._opened.setdefault(ticker, time.monotonic())

    def ready(self) -> list[Window]:
        """Close and return any windows whose interval has elapsed.

        Windows with fewer than two events are discarded rather than analyzed.
        A single tick gives the model nothing to reason about, and asking it
        anyway produces confident noise -- which is more damaging than no
        insight at all, because it looks identical to a real signal.
        """
        now = time.monotonic()
        closed: list[Window] = []

        for ticker, opened_at in list(self._opened.items()):
            if now - opened_at < self.window_seconds:
                continue

            events = list(self._events[ticker])
            self._events[ticker].clear()
            del self._opened[ticker]

            if len(events) < 2:
                continue

            closed.append(
                Window(
                    market_ticker=ticker,
                    events=events,
                    start_seq=events[0]["seq"],
                    end_seq=events[-1]["seq"],
                )
            )
        return closed
