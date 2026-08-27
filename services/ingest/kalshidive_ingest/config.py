"""Environment-driven configuration.

Credentials come from the environment and never from a file in the repo. The
private key is passed by path rather than by value so it can be mounted as a
secret (ECS/Secrets Manager, a Docker secret, a k8s volume) instead of being
inlined into an environment variable where it lands in `docker inspect` and
process listings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _tickers() -> list[str]:
    raw = os.getenv("KALSHI_MARKET_TICKERS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


@dataclass
class Config:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql://kalshi:kalshi@localhost:5432/kalshidive"
        )
    )
    fanout_host: str = field(default_factory=lambda: os.getenv("FANOUT_HOST", "0.0.0.0"))
    fanout_port: int = field(
        default_factory=lambda: int(os.getenv("FANOUT_PORT", "8765"))
    )
    fanout_url: str = field(
        default_factory=lambda: os.getenv("FANOUT_URL", "ws://localhost:8765")
    )
    market_tickers: list[str] = field(default_factory=_tickers)
    kalshi_api_key_id: str | None = field(
        default_factory=lambda: os.getenv("KALSHI_API_KEY_ID")
    )
    kalshi_private_key_path: str | None = field(
        default_factory=lambda: os.getenv("KALSHI_PRIVATE_KEY_PATH")
    )
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    )
    analysis_window_seconds: float = field(
        default_factory=lambda: float(os.getenv("ANALYSIS_WINDOW_SECONDS", "30"))
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    @property
    def use_live_feed(self) -> bool:
        """Live only when both credentials are present.

        Defaulting to the mock feed means a missing key produces a running
        system with obviously synthetic data, rather than a crash loop or --
        far worse -- a silent connection failure that looks like a quiet
        market.
        """
        return bool(self.kalshi_api_key_id and self.kalshi_private_key_path)
