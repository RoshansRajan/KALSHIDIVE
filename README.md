# KalshiDive

Real-time market client for [Kalshi](https://kalshi.com), an exchange for
binary event contracts. Streams live contract and order-book updates over
WebSockets, persists tick-level history in Postgres, and runs an LLM agent
that turns raw order-book activity into schema-validated structured insights.

The stack runs end-to-end with **no credentials** — a synthetic feed stands in
for Kalshi, so you can see the whole pipeline work before signing up for
anything.

```
                                        ┌──────────────────┐
  Kalshi WS ──┐                    ┌───▶│  Postgres        │
  (or MockFeed)│                   │    │  tick history    │
              ▼                    │    └──────────────────┘
        ┌───────────┐  normalized  │
        │  ingest   │──── events ──┤    ┌──────────────────┐   ┌───────────┐
        │  service  │              └───▶│  fanout :8765    │──▶│ dashboard │
        └───────────┘                   └──────────────────┘   └───────────┘
                                                 │                    ▲
                                                 ▼                    │
                                        ┌──────────────────┐          │
                                        │ analysis service │          │
                                        │  Claude → JSON   │──────────┘
                                        └──────────────────┘  fanout :8766
```

## Quick start

```bash
git clone https://github.com/RoshansRajan/KALSHIDIVE.git
cd KALSHIDIVE
cp .env.example .env       # works empty; fill in to go live
docker compose up --build
```

Then open <http://localhost:5173>.

With an empty `.env` you get synthetic market data and no LLM analysis — enough
to watch ticks flow, land in Postgres, and render. Add `ANTHROPIC_API_KEY` to
turn on analysis; add the two `KALSHI_*` values to stream the real exchange.

## Running the tests

```bash
docker build -f Dockerfile.test -t kalshidive-test . && docker run --rm kalshidive-test
```

The suite covers order-book delta application, sequence-gap detection and
recovery, reconnect backoff, windowing, and schema validation. None of it
touches the network.

## How it's put together

| Path | What lives there |
|---|---|
| `packages/schemas/` | Pydantic models. The contract every service shares. |
| `packages/bus/` | Fanout server + reconnecting subscriber. |
| `services/ingest/` | WebSocket client, order book, Postgres writer. |
| `services/analysis/` | Windowing + the Claude agent. |
| `web/` | Vite + React dashboard. |
| `db/migrations/` | Schema, applied on first Postgres start. |

### Surviving dropped sessions

Two independent recovery mechanisms, because they cover different failures:

**Reconnection** handles the socket dying. Exponential backoff with jitter
(`services/ingest/kalshidive_ingest/backoff.py`), capped at 30s. The jitter
matters — when the venue restarts, every client disconnects simultaneously,
and un-jittered clients then reconnect in synchronized waves that turn a blip
into an outage.

**Sequence-gap detection** handles the nastier case: the socket stays open but
a message is lost. Kalshi stamps each update with a per-market sequence
number; the book requires them to be strictly consecutive and marks itself
stale on any gap
(`services/ingest/kalshidive_ingest/orderbook.py`). Without this a single
dropped delta silently corrupts every subsequent read, with nothing raised.

The recorded cursor in `feed_cursor` means a restart resumes where it left
off rather than leaving a hole in history.

### Structured LLM output

`MarketInsight` is a Pydantic model, and the agent calls
`client.messages.parse(output_format=MarketInsight)`. That gives two
independent guarantees:

1. The API constrains decoding to the JSON schema, so no prose preamble, no
   markdown fence, no trailing "Hope this helps!".
2. The SDK validates the decoded JSON against the model anyway — catching
   things a schema describes but decoding doesn't enforce, like
   `0 ≤ confidence ≤ 1`.

Validation failure means the insight is dropped, never partially applied. The
analysis path is advisory: ingest and persistence keep running whether or not
the model is reachable.

### Why tick-level history

`market_ticks` is append-only. Nothing is ever updated in place, and current
book state is never stored as a mutable row — it's derived by replaying ticks.

That's what makes it possible to evaluate a strategy against the order book as
it actually looked at some past moment, rather than against whatever the
latest values happen to be. `TickStore.replay()` feeds recorded ticks through
the same `OrderBook` the live path uses, so historical and live analysis run
identical code.

This is not a property you can add later. Storing only current state discards
the history permanently.

## Configuration

All via environment variables; see `.env.example`. The two that change
behavior most:

- **`KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH`** — both set switches from
  `MockFeed` to the live exchange. Defaulting to mock means a missing key
  gives you a running system with obviously fake data, rather than a crash
  loop or a silent connection failure that looks like a quiet market.
- **`ANTHROPIC_API_KEY`** — absent, analysis logs a warning and every call
  fails, while ingest continues untouched.

## Deployment

See [DEPLOY.md](DEPLOY.md) for the ECS Fargate + RDS topology.

## Status

Bare-bones scaffold. Deliberately absent: order placement, authentication, a
backtesting engine, and migration tooling beyond the initial SQL.
