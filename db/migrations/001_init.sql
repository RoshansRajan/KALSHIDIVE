-- KalshiDive schema.
--
-- Design note: market_ticks is append-only. We never UPDATE a tick and never
-- store "current book state" as a mutable row. The book at any past moment is
-- derived by replaying ticks up to that point, which is what lets a strategy
-- be evaluated against the order book as it actually looked -- rather than
-- against whatever the latest values happen to be now.
--
-- Storing only current state would make historical evaluation impossible, and
-- that is not a gap you can backfill later: the data is simply gone.

CREATE TABLE IF NOT EXISTS market_ticks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market_ticker TEXT        NOT NULL,
    seq           BIGINT      NOT NULL,
    event_type    TEXT        NOT NULL CHECK (event_type IN ('snapshot','delta','trade')),
    side          TEXT        CHECK (side IN ('yes','no')),
    price         INTEGER     CHECK (price BETWEEN 1 AND 99),
    delta         INTEGER,
    count         INTEGER,
    payload       JSONB       NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The workhorse index: "replay market X from seq N" and "what happened
-- between these two times" are the only two access patterns that matter.
CREATE INDEX IF NOT EXISTS idx_ticks_ticker_seq
    ON market_ticks (market_ticker, seq);
CREATE INDEX IF NOT EXISTS idx_ticks_received
    ON market_ticks (received_at DESC);

-- Resume point per market. On restart the ingest service reads this to know
-- where it left off, so a crash costs a snapshot refresh rather than a gap in
-- recorded history.
CREATE TABLE IF NOT EXISTS feed_cursor (
    market_ticker TEXT PRIMARY KEY,
    last_seq      BIGINT      NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Agent output. window_start_seq/window_end_seq tie each insight back to the
-- exact ticks that produced it, so a past call can be re-examined against the
-- same input that generated it.
CREATE TABLE IF NOT EXISTS market_insights (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market_ticker   TEXT        NOT NULL,
    signal          TEXT        NOT NULL CHECK (signal IN ('bullish','bearish','neutral')),
    confidence      REAL        NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    rationale       TEXT        NOT NULL,
    horizon_minutes INTEGER     NOT NULL,
    window_start_seq BIGINT     NOT NULL,
    window_end_seq   BIGINT     NOT NULL,
    model           TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_insights_ticker_created
    ON market_insights (market_ticker, created_at DESC);
