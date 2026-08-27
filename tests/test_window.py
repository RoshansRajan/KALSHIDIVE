"""Windowing behavior."""

import time

from kalshidive_analysis.window import Window, WindowBuffer


def tick(seq, ticker="KXTEST-1", **kw):
    base = {
        "type": "delta",
        "market_ticker": ticker,
        "seq": seq,
        "side": "yes",
        "price": 62,
        "delta": 100,
    }
    return {**base, **kw}


def test_window_not_ready_before_interval():
    buf = WindowBuffer(window_seconds=60.0)
    buf.add(tick(1))
    buf.add(tick(2))
    assert buf.ready() == []


def test_window_closes_after_interval():
    buf = WindowBuffer(window_seconds=0.01)
    buf.add(tick(1))
    buf.add(tick(2))
    time.sleep(0.02)

    windows = buf.ready()
    assert len(windows) == 1
    assert windows[0].start_seq == 1
    assert windows[0].end_seq == 2


def test_single_event_window_discarded():
    """One tick is not analyzable; asking anyway yields confident noise."""
    buf = WindowBuffer(window_seconds=0.01)
    buf.add(tick(1))
    time.sleep(0.02)
    assert buf.ready() == []


def test_markets_windowed_independently():
    buf = WindowBuffer(window_seconds=0.01)
    buf.add(tick(1, ticker="A"))
    buf.add(tick(2, ticker="A"))
    buf.add(tick(1, ticker="B"))
    buf.add(tick(2, ticker="B"))
    time.sleep(0.02)

    windows = {w.market_ticker for w in buf.ready()}
    assert windows == {"A", "B"}


def test_buffer_drains_on_close():
    buf = WindowBuffer(window_seconds=0.01)
    buf.add(tick(1))
    buf.add(tick(2))
    time.sleep(0.02)
    assert len(buf.ready()) == 1
    assert buf.ready() == [], "a closed window must not be re-emitted"


def test_summarize_keeps_newest_on_overflow():
    """Recency dominates for a momentum read."""
    w = Window(
        market_ticker="KXTEST-1",
        events=[tick(i) for i in range(1, 101)],
        start_seq=1,
        end_seq=100,
    )
    text = w.summarize(max_events=10)

    assert "90 earlier events omitted" in text
    assert "100 delta" in text
    assert "\n1 delta" not in text


def test_summarize_renders_trades_distinctly():
    w = Window(
        market_ticker="KXTEST-1",
        events=[tick(1), tick(2, type="trade", count=50)],
        start_seq=1,
        end_seq=2,
    )
    text = w.summarize()
    assert "TRADE" in text
    assert "x50" in text
