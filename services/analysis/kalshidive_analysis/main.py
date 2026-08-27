"""Analysis entrypoint.

Pipeline: bus (ticks) -> WindowBuffer -> AnalysisAgent -> Postgres + bus
(insights).

The service publishes on its own port rather than back into the ingest
fanout. Fanouts here are one-way broadcasts, and keeping them one-way avoids
the loop where an analysis message re-enters the tick stream it was derived
from.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from kalshidive_bus import Fanout, subscribe
from kalshidive_ingest.config import Config
from kalshidive_ingest.store import TickStore

from .agent import AnalysisAgent
from .window import WindowBuffer

log = logging.getLogger("kalshidive.analysis")


class Analysis:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = TickStore(cfg.database_url)
        self.buffer = WindowBuffer(window_seconds=cfg.analysis_window_seconds)
        self.agent = AnalysisAgent(model=cfg.anthropic_model)
        self.fanout = Fanout(
            cfg.fanout_host, int(os.getenv("INSIGHT_FANOUT_PORT", "8766"))
        )

    async def _consume(self) -> None:
        """Feed incoming ticks into the window buffer."""
        async for message in subscribe(self.cfg.fanout_url):
            if message.get("kind") == "tick":
                self.buffer.add(message["event"])

    async def _analyze_loop(self) -> None:
        """Close due windows and analyze them.

        Windows are analyzed concurrently via gather. Sequentially, a slow
        call on one market would delay every other market's analysis behind
        it, and these calls are independent -- there is no reason for one
        market to wait on another.
        """
        while True:
            await asyncio.sleep(1.0)
            windows = self.buffer.ready()
            if not windows:
                continue

            log.info("analyzing %d window(s)", len(windows))
            results = await asyncio.gather(
                *(self.agent.analyze(w) for w in windows),
                return_exceptions=True,
            )

            for window, result in zip(windows, results, strict=True):
                if isinstance(result, BaseException):
                    log.error("analysis raised for %s: %s", window.market_ticker, result)
                    continue
                if result is None:
                    continue

                await self.store.save_insight(result)
                self.fanout.publish(
                    {
                        "kind": "insight",
                        "market_ticker": result.market_ticker,
                        "insight": result.insight.model_dump(mode="json"),
                        "window_start_seq": result.window_start_seq,
                        "window_end_seq": result.window_end_seq,
                        "model": result.model,
                        "created_at": result.created_at.isoformat(),
                    }
                )
                log.info(
                    "%s -> %s (%.2f): %s",
                    result.market_ticker,
                    result.insight.signal.value,
                    result.insight.confidence,
                    result.insight.rationale,
                )

    async def run(self) -> None:
        await self.store.connect()
        await self.fanout.start()
        log.info(
            "analysis running (model=%s, window=%.0fs)",
            self.cfg.anthropic_model,
            self.cfg.analysis_window_seconds,
        )
        await asyncio.gather(self._consume(), self._analyze_loop())

    async def shutdown(self) -> None:
        await self.store.close()
        await self.fanout.stop()


async def amain() -> None:
    cfg = Config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    if not os.getenv("ANTHROPIC_API_KEY"):
        log.warning(
            "ANTHROPIC_API_KEY is unset -- every analysis call will fail. "
            "Ingest and persistence are unaffected."
        )

    analysis = Analysis(cfg)
    task = asyncio.create_task(analysis.run())

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await asyncio.wait(
        [task, asyncio.create_task(stop.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    task.cancel()
    await analysis.shutdown()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
