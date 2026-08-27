"""Reconnect delay policy."""

import random

import pytest
from kalshidive_ingest.backoff import backoff_delay


def test_delay_grows_exponentially():
    no_jitter = {"jitter": 0.0}
    assert backoff_delay(0, base=0.5, **no_jitter) == pytest.approx(0.5)
    assert backoff_delay(1, base=0.5, **no_jitter) == pytest.approx(1.0)
    assert backoff_delay(2, base=0.5, **no_jitter) == pytest.approx(2.0)
    assert backoff_delay(3, base=0.5, **no_jitter) == pytest.approx(4.0)


def test_delay_is_capped():
    """Without a cap, attempt 20 would wait ~6 days."""
    assert backoff_delay(20, base=0.5, cap=30.0, jitter=0.0) == pytest.approx(30.0)


def test_jitter_stays_within_bounds():
    rng = random.Random(1234)
    for _ in range(500):
        d = backoff_delay(4, base=0.5, cap=30.0, jitter=0.3, rng=rng)
        assert 8.0 * 0.7 <= d <= 8.0 * 1.3


def test_jitter_actually_varies():
    """Identical delays across clients are what jitter exists to prevent."""
    rng = random.Random(7)
    values = {backoff_delay(3, rng=rng) for _ in range(50)}
    assert len(values) > 40, "jitter should produce distinct delays"


def test_negative_attempt_rejected():
    with pytest.raises(ValueError):
        backoff_delay(-1)
