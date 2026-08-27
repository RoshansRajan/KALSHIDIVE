"""The analysis agent's output contract.

This is the only shape the LLM is permitted to emit. Two independent
guarantees keep it machine-parsable:

1. Server-side, the JSON schema derived from this model constrains decoding,
   so the model cannot emit a stray prose preamble or a trailing "Hope this
   helps!".
2. Client-side, Pydantic validates the result anyway -- bounds on confidence,
   membership in the Signal enum, length caps on free text.

Belt and braces is deliberate. The schema constrains structure but cannot
enforce that confidence lands in [0, 1]; validation catches what decoding
cannot, and downstream consumers get to assume both hold.

``extra="forbid"`` maps to ``additionalProperties: false`` in the emitted
schema, which is what makes the constraint strict rather than advisory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Signal(StrEnum):
    """Directional read on the YES side of the contract."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class MarketInsight(BaseModel):
    """A single structured read on one market over one window of ticks.

    Every field is required. Optional fields invite the model to omit the
    inconvenient ones, and a nullable confidence is worse than no confidence
    at all because it silently disables downstream thresholding.
    """

    model_config = ConfigDict(extra="forbid")

    signal: Signal = Field(
        description="Direction implied by the order-book activity in this window."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0 = no view, 1 = maximum conviction. Calibrate honestly.",
    )
    rationale: str = Field(
        max_length=400,
        description=(
            "One or two sentences citing the specific order-book behavior that "
            "drove the signal. Reference concrete numbers from the window."
        ),
    )
    horizon_minutes: int = Field(
        ge=1,
        le=1440,
        description="How long this read should be considered valid.",
    )


class StoredInsight(BaseModel):
    """A MarketInsight plus the provenance needed to evaluate it later.

    The agent never produces this -- we attach it after the fact. Keeping the
    model's output free of bookkeeping fields means the schema it sees stays
    small, and a small schema is one the model fills in accurately.
    """

    market_ticker: str
    insight: MarketInsight
    window_start_seq: int
    window_end_seq: int
    model: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
