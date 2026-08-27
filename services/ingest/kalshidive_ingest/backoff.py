"""Reconnection delay policy.

Split out from the client so it can be tested without a socket. Reconnect
logic is exactly the code that is hardest to exercise in integration tests
(you would have to actually break a network) and cheapest to verify in
isolation, so it lives behind a pure function.
"""

from __future__ import annotations

import random


def backoff_delay(
    attempt: int,
    *,
    base: float = 0.5,
    cap: float = 30.0,
    jitter: float = 0.3,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before reconnect attempt ``attempt`` (0-indexed).

    Exponential growth capped at ``cap``, then multiplied by a random factor
    in ``[1 - jitter, 1 + jitter]``.

    The jitter is not cosmetic. When a venue restarts, every client
    disconnects at the same instant; without jitter they all reconnect in
    lockstep and hammer the endpoint in synchronized waves, which is how a
    brief blip becomes a sustained outage. Randomizing spreads the retries.
    """
    if attempt < 0:
        raise ValueError("attempt must be non-negative")

    raw = min(base * (2**attempt), cap)
    r = rng or random
    return raw * (1.0 + r.uniform(-jitter, jitter))
