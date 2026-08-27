"""Order book correctness, with emphasis on the failure paths.

The happy path is nearly self-evident; the gap and resync behavior is where
the real risk lives, so that is where the tests concentrate.
"""

import pytest
from kalshidive_ingest.orderbook import (
    BookDesync,
    NegativeSize,
    NoBaseline,
    OrderBook,
    SequenceGap,
)
from kalshidive_schemas import (
    OrderBookDelta,
    OrderBookSnapshot,
    PriceLevel,
    Side,
    Trade,
)

TICKER = "KXTEST-25AUG26-T50"


def snapshot(seq=1, yes=None, no=None):
    # `is None`, not `or`: an explicitly empty side is a meaningful input
    # (a one-sided book), and `or` would silently swap in the default.
    yes = [(60, 100)] if yes is None else yes
    no = [(38, 200)] if no is None else no
    return OrderBookSnapshot(
        market_ticker=TICKER,
        seq=seq,
        yes=[PriceLevel(price=p, size=s) for p, s in yes],
        no=[PriceLevel(price=p, size=s) for p, s in no],
    )


def delta(seq, price, amount, side=Side.YES):
    return OrderBookDelta(
        market_ticker=TICKER, seq=seq, side=side, price=price, delta=amount
    )


def test_snapshot_establishes_baseline():
    book = OrderBook(market_ticker=TICKER)
    assert book.stale is True

    book.apply_snapshot(snapshot(seq=5, yes=[(60, 100), (59, 250)]))

    assert book.stale is False
    assert book.last_seq == 5
    assert book.yes == {60: 100, 59: 250}


def test_snapshot_drops_zero_size_levels():
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(yes=[(60, 100), (59, 0)]))
    assert 59 not in book.yes


def test_delta_adds_and_removes_size():
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(seq=1, yes=[(60, 100)]))

    book.apply_delta(delta(2, 60, 50))
    assert book.yes[60] == 150

    book.apply_delta(delta(3, 60, -150))
    assert 60 not in book.yes, "level emptied to zero should be removed, not kept at 0"


def test_delta_on_stale_book_raises():
    """A book with no snapshot cannot be mutated -- there is no baseline."""
    book = OrderBook(market_ticker=TICKER)
    with pytest.raises(NoBaseline):
        book.apply_delta(delta(2, 60, 50))


def test_sequence_gap_detected_and_marks_stale():
    """The core guarantee: a missed message is caught, not silently absorbed."""
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(seq=1))

    with pytest.raises(SequenceGap) as exc:
        book.apply_delta(delta(3, 60, 50))  # seq 2 never arrived

    assert exc.value.expected == 2
    assert exc.value.got == 3
    assert book.stale is True, "a gapped book must not look valid to callers"


def test_stale_book_recovers_from_snapshot():
    """Snapshots are the repair path, so they must not require continuity."""
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(seq=1))
    with pytest.raises(SequenceGap):
        book.apply_delta(delta(9, 60, 50))
    assert book.stale is True

    book.apply_snapshot(snapshot(seq=20, yes=[(61, 400)]))

    assert book.stale is False
    assert book.yes == {61: 400}
    book.apply_delta(delta(21, 61, -100))
    assert book.yes[61] == 300


def test_negative_size_is_distinct_from_a_gap():
    """Going negative means divergence, not a lost message -- and the
    distinction is what tells you which layer to go debug."""
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(seq=1, yes=[(60, 100)]))

    with pytest.raises(NegativeSize) as exc:
        book.apply_delta(delta(2, 60, -500))

    assert book.stale is True
    assert "diverged" in str(exc.value)
    assert "sequence gap" not in str(exc.value)


def test_all_desyncs_share_a_base_so_callers_catch_one_thing():
    assert issubclass(SequenceGap, BookDesync)
    assert issubclass(NegativeSize, BookDesync)
    assert issubclass(NoBaseline, BookDesync)


def test_trade_advances_sequence_without_touching_levels():
    """A trade changes no resting size but does consume a seq. If the book
    ignores it, the next delta looks like a gap and every execution
    manufactures a spurious resync."""
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(seq=1, yes=[(60, 100)]))

    book.apply_trade(
        Trade(market_ticker=TICKER, seq=2, side=Side.YES, price=60, count=25)
    )

    assert book.last_seq == 2
    assert book.yes == {60: 100}, "a trade must not mutate resting size"

    # The delta right after a trade must not be treated as a gap.
    book.apply_delta(delta(3, 60, 50))
    assert book.yes[60] == 150
    assert book.stale is False


def test_apply_dispatches_every_event_type():
    book = OrderBook(market_ticker=TICKER)
    book.apply(snapshot(seq=1, yes=[(60, 100)]))
    book.apply(Trade(market_ticker=TICKER, seq=2, side=Side.YES, price=60, count=5))
    book.apply(delta(3, 60, 25))

    assert book.last_seq == 3
    assert book.yes[60] == 125


def test_wrong_ticker_rejected():
    book = OrderBook(market_ticker=TICKER)
    other = OrderBookSnapshot(market_ticker="KXOTHER-1", seq=1)
    with pytest.raises(ValueError):
        book.apply_snapshot(other)


def test_best_ask_converts_no_side_to_yes_terms():
    """Buying NO at 38c is selling YES at 62c."""
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(seq=1, yes=[(60, 100)], no=[(38, 200)]))

    assert book.best_bid() == (60, 100)
    assert book.best_ask() == (62, 200)
    assert book.spread() == 2


def test_spread_none_when_one_side_empty():
    book = OrderBook(market_ticker=TICKER)
    book.apply_snapshot(snapshot(seq=1, yes=[(60, 100)], no=[]))
    assert book.best_ask() is None
    assert book.spread() is None
