"""MockFeed must generate data a real consumer can actually accept.

A mock that emits impossible states makes the consumer log desync warnings
constantly, and a warning that fires constantly is a warning nobody reads.
These tests hold the mock to the same standard as the venue.
"""


from kalshidive_ingest.feed import Feed, MockFeed
from kalshidive_ingest.orderbook import BookDesync, OrderBook
from kalshidive_schemas import Side


async def take(feed, n):
    out = []
    async for event in feed.stream():
        out.append(event)
        if len(out) >= n:
            break
    return out


def test_mockfeed_satisfies_the_protocol():
    assert isinstance(MockFeed(), Feed)


async def test_mockfeed_output_replays_cleanly():
    """The whole point: a clean mock feed produces zero desyncs."""
    feed = MockFeed(["KXTEST-1"], interval=0.0, seed=42)
    events = await take(feed, 300)

    book = OrderBook(market_ticker="KXTEST-1")
    for event in events:
        book.apply(event)  # must not raise

    assert book.stale is False
    assert book.last_seq == events[-1].seq


async def test_mockfeed_sequences_are_consecutive_per_market():
    feed = MockFeed(["A", "B"], interval=0.0, seed=7)
    events = await take(feed, 200)

    for ticker in ("A", "B"):
        seqs = [e.seq for e in events if e.market_ticker == ticker]
        assert seqs == list(range(1, len(seqs) + 1))


async def test_mockfeed_never_drives_a_level_negative():
    feed = MockFeed(["KXTEST-1"], interval=0.0, seed=99)
    events = await take(feed, 400)

    sizes = {"yes": {}, "no": {}}
    for e in events:
        if e.type == "snapshot":
            sizes = {
                "yes": {lvl.price: lvl.size for lvl in e.yes},
                "no": {lvl.price: lvl.size for lvl in e.no},
            }
        elif e.type == "delta":
            side = sizes[e.side.value]
            side[e.price] = side.get(e.price, 0) + e.delta
            assert side[e.price] >= 0, f"level went negative at seq {e.seq}"


async def test_drop_rate_injects_gaps_on_demand():
    """Failure injection must be opt-in -- and must actually work."""
    feed = MockFeed(["KXTEST-1"], interval=0.0, drop_rate=0.5, seed=3)
    events = await take(feed, 120)

    book = OrderBook(market_ticker="KXTEST-1")
    gaps = 0
    for event in events:
        try:
            book.apply(event)
        except BookDesync:
            gaps += 1

    assert gaps > 0, "drop_rate should produce detectable gaps"


async def test_mockfeed_never_produces_a_crossed_book():
    """Ask below bid is impossible on a real venue; a negative spread on the
    dashboard reads as a broken system rather than as fake data."""
    feed = MockFeed(["KXTEST-1"], interval=0.0, seed=11)
    events = await take(feed, 600)

    book = OrderBook(market_ticker="KXTEST-1")
    checked = 0
    for event in events:
        book.apply(event)
        spread = book.spread()
        if spread is not None:
            assert spread >= 1, (
                f"crossed book at seq {event.seq}: "
                f"bid={book.best_bid()} ask={book.best_ask()}"
            )
            checked += 1

    assert checked > 100, "test should actually exercise a populated book"


async def test_caps_guarantee_the_requested_spread():
    """The invariant behind the above, checked across mids and spreads."""
    for mid in range(10, 91):
        for spread in range(1, 7):
            caps = MockFeed._caps(mid, spread)
            assert caps[Side.YES] + caps[Side.NO] <= 100 - spread


async def test_spread_actually_varies():
    """A constant spread makes the agent's liquidity reasoning vacuous."""
    feed = MockFeed(["KXTEST-1"], interval=0.0, seed=5)
    events = await take(feed, 900)

    book = OrderBook(market_ticker="KXTEST-1")
    seen = set()
    for event in events:
        book.apply(event)
        s = book.spread()
        if s is not None:
            seen.add(s)

    assert min(seen) >= 1, f"crossed or locked book: {sorted(seen)}"
    assert len(seen) > 1, f"spread never varied: {sorted(seen)}"
