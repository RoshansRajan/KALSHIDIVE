"""The agent's output contract.

These tests exercise the client-side half of the structured-output guarantee.
They deliberately do NOT call the API -- what is under test is that a
malformed insight is rejected before it can reach Postgres or a subscriber.
"""

import json

import pytest
from kalshidive_schemas import MarketInsight, Signal, StoredInsight
from pydantic import ValidationError


def valid(**overrides):
    base = {
        "signal": "bullish",
        "confidence": 0.71,
        "rationale": "Bids stacked 1,200 at 62c while asks thinned to 800.",
        "horizon_minutes": 15,
    }
    return {**base, **overrides}


def test_valid_insight_parses():
    insight = MarketInsight(**valid())
    assert insight.signal is Signal.BULLISH
    assert insight.confidence == pytest.approx(0.71)


@pytest.mark.parametrize("bad", [-0.1, 1.1, 2.0])
def test_confidence_out_of_range_rejected(bad):
    """A JSON schema can describe these bounds; only validation enforces them."""
    with pytest.raises(ValidationError):
        MarketInsight(**valid(confidence=bad))


def test_unknown_signal_rejected():
    with pytest.raises(ValidationError):
        MarketInsight(**valid(signal="mooning"))


def test_extra_fields_rejected():
    """extra='forbid' is what makes additionalProperties:false meaningful."""
    with pytest.raises(ValidationError):
        MarketInsight(**valid(price_target=95))


def test_missing_field_rejected():
    """No field is optional -- an omitted confidence would disable thresholding."""
    payload = valid()
    del payload["confidence"]
    with pytest.raises(ValidationError):
        MarketInsight(**payload)


def test_horizon_bounds_enforced():
    with pytest.raises(ValidationError):
        MarketInsight(**valid(horizon_minutes=0))
    with pytest.raises(ValidationError):
        MarketInsight(**valid(horizon_minutes=10_000))


def test_rationale_length_capped():
    with pytest.raises(ValidationError):
        MarketInsight(**valid(rationale="x" * 401))


def test_emitted_schema_is_strict():
    """The schema sent to the API must actually constrain the model."""
    schema = MarketInsight.model_json_schema()

    assert schema.get("additionalProperties") is False
    assert set(schema["required"]) == {
        "signal",
        "confidence",
        "rationale",
        "horizon_minutes",
    }
    # Enum values must be inlined somewhere reachable, or the model is free
    # to invent a fourth signal.
    assert "bullish" in json.dumps(schema)


def test_stored_insight_carries_provenance():
    """Provenance is what makes a past call re-examinable."""
    stored = StoredInsight(
        market_ticker="KXTEST-1",
        insight=MarketInsight(**valid()),
        window_start_seq=100,
        window_end_seq=140,
        model="claude-sonnet-5",
    )
    assert stored.window_end_seq > stored.window_start_seq
    assert stored.created_at.tzinfo is not None, "timestamps must be tz-aware"
