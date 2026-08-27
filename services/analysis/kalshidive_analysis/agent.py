"""The LLM market-analysis agent.

Structured output here rests on two independent guarantees:

1. ``client.messages.parse(output_format=MarketInsight)`` sends the model a
   JSON schema derived from the Pydantic model, and the API constrains
   decoding to satisfy it. The model cannot emit a prose preamble, a
   markdown fence, or a trailing pleasantry.
2. The SDK validates the decoded JSON against the same model before handing
   it back, so bounds like ``0 <= confidence <= 1`` -- which a JSON schema
   describes but decoding does not enforce -- are checked too.

Note there is no ``temperature`` here. Sampling parameters were removed in the
Claude 4.6+ family; passing ``temperature=0`` to Sonnet 5 returns a 400 rather
than being ignored, so the reflex of pinning temperature for determinism is
now an outright bug.
"""

from __future__ import annotations

import logging

import anthropic
from kalshidive_schemas import MarketInsight, StoredInsight

from .window import Window

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a market microstructure analyst for Kalshi, a binary event-contract \
exchange. Contracts settle at $1.00 (YES resolves true) or $0.00 (NO), so a \
price in cents is directly an implied probability in percent: 62c means the \
market prices the event at 62%.

You receive a window of raw order-book events for a single market:
  - `delta <side> <price>c <+/-size>` -- resting size changed at that level
  - `TRADE <side> <price>c x<count>`  -- an execution
  - `snapshot`                        -- full book state

Judge only what the ticks support. Specific evidence:
  - Trades lifting the offer repeatedly -> buying pressure.
  - Bids stacking below the mid while asks thin out -> upward drift.
  - A tightening spread -> rising confidence; a widening one -> uncertainty.

Two rules that matter more than any signal you find:
  - Calibrate honestly. A quiet window is `neutral` with low confidence. \
Reserve confidence above 0.7 for windows where the evidence is genuinely \
one-sided.
  - Cite concrete numbers from the window in your rationale -- prices, sizes, \
counts. A rationale that would read identically for any market is not a \
rationale.

You are producing an analytical read, not trading advice.\
"""


class AnalysisAgent:
    """Turns a window of ticks into one validated MarketInsight."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        *,
        client: anthropic.AsyncAnthropic | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = client or anthropic.AsyncAnthropic()

    async def analyze(self, window: Window) -> StoredInsight | None:
        """Analyze one window. Returns None if no valid insight was produced.

        Failure is a dropped insight, never an exception that reaches the
        caller. The analysis path is strictly advisory -- ingest and
        persistence must keep running whether or not the model is reachable,
        so an API outage degrades the system rather than stopping it.
        """
        prompt = (
            f"Market: {window.market_ticker}\n"
            f"Sequence range: {window.start_seq}-{window.end_seq}\n"
            f"Events in window: {len(window.events)}\n\n"
            f"{window.summarize()}"
        )

        for attempt in (1, 2):
            try:
                response = await self._client.messages.parse(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_format=MarketInsight,
                )
                insight = response.parsed_output
                if insight is None:
                    raise ValueError("model returned no parsable output")

                return StoredInsight(
                    market_ticker=window.market_ticker,
                    insight=insight,
                    window_start_seq=window.start_seq,
                    window_end_seq=window.end_seq,
                    model=self.model,
                )

            except anthropic.RateLimitError:
                # Backing off inside the request path would stall the window
                # loop. Windows keep closing regardless, so skipping this one
                # is cheaper than delaying every one behind it.
                log.warning("rate limited on %s; skipping window", window.market_ticker)
                return None
            except anthropic.APIStatusError as exc:
                log.error("API error %s: %s", exc.status_code, exc.message)
                return None
            except (anthropic.APIConnectionError, ValueError) as exc:
                if attempt == 1:
                    log.warning("analysis attempt 1 failed (%s); retrying", exc)
                    continue
                log.error("analysis failed for %s: %s", window.market_ticker, exc)
                return None

        return None
