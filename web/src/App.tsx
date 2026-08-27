import type { BookView, InsightMessage } from "./types";
import { useStream } from "./useStream";

const TICK_URL = import.meta.env.VITE_TICK_URL ?? "ws://localhost:8765";
const INSIGHT_URL = import.meta.env.VITE_INSIGHT_URL ?? "ws://localhost:8766";

const cents = (v: number | null) => (v === null ? "--" : `${v}¢`);
const size = (v: number | null) => (v === null ? "--" : v.toLocaleString());

function Depth({ levels, side }: { levels: [number, number][]; side: "bid" | "ask" }) {
  // Widths are relative to the largest level in view, so the bars stay
  // readable whether the book is 50 deep or 50,000.
  const max = Math.max(1, ...levels.map(([, s]) => s));
  return (
    <div className={`depth depth--${side}`}>
      {levels.length === 0 && <div className="depth__empty">no resting size</div>}
      {levels.map(([price, sz]) => (
        <div className="depth__row" key={price}>
          <span className="depth__bar" style={{ width: `${(sz / max) * 100}%` }} />
          <span className="depth__price">{cents(side === "ask" ? 100 - price : price)}</span>
          <span className="depth__size">{size(sz)}</span>
        </div>
      ))}
    </div>
  );
}

function Insight({ msg }: { msg: InsightMessage | undefined }) {
  if (!msg) {
    return <div className="insight insight--empty">awaiting first analysis window…</div>;
  }
  const { signal, confidence, rationale, horizon_minutes } = msg.insight;
  return (
    <div className={`insight insight--${signal}`}>
      <div className="insight__head">
        <span className="insight__signal">{signal}</span>
        <span className="insight__conf">{(confidence * 100).toFixed(0)}% confidence</span>
        <span className="insight__horizon">{horizon_minutes}m horizon</span>
      </div>
      <p className="insight__rationale">{rationale}</p>
      <div className="insight__meta">
        {msg.model} · seq {msg.window_start_seq}–{msg.window_end_seq}
      </div>
    </div>
  );
}

function Market({ book, insight }: { book: BookView; insight?: InsightMessage }) {
  return (
    <section className="market">
      <header className="market__head">
        <h2>{book.market_ticker}</h2>
        {book.stale && <span className="badge badge--stale">resyncing</span>}
      </header>

      <div className="market__top">
        <div className="quote quote--bid">
          <span className="quote__label">BID</span>
          <span className="quote__price">{cents(book.best_bid)}</span>
          <span className="quote__size">{size(book.best_bid_size)}</span>
        </div>
        <div className="quote quote--spread">
          <span className="quote__label">SPREAD</span>
          <span className="quote__price">{cents(book.spread)}</span>
        </div>
        <div className="quote quote--ask">
          <span className="quote__label">ASK</span>
          <span className="quote__price">{cents(book.best_ask)}</span>
          <span className="quote__size">{size(book.best_ask_size)}</span>
        </div>
      </div>

      <div className="market__books">
        <Depth levels={book.yes} side="bid" />
        <Depth levels={book.no} side="ask" />
      </div>

      <Insight msg={insight} />
    </section>
  );
}

export function App() {
  const { books, insights, connected, tickCount } = useStream(TICK_URL, INSIGHT_URL);
  const markets = Object.values(books).sort((a, b) =>
    a.market_ticker.localeCompare(b.market_ticker),
  );

  return (
    <div className="app">
      <header className="app__head">
        <h1>KalshiDive</h1>
        <div className="app__status">
          <span className={`dot ${connected ? "dot--live" : "dot--down"}`} />
          {connected ? "streaming" : "reconnecting"} · {tickCount.toLocaleString()} ticks
        </div>
      </header>

      {markets.length === 0 ? (
        <p className="app__empty">
          Waiting for the first tick. If this persists, check that the ingest
          service is running and reachable at <code>{TICK_URL}</code>.
        </p>
      ) : (
        <main className="app__grid">
          {markets.map((book) => (
            <Market
              key={book.market_ticker}
              book={book}
              insight={insights[book.market_ticker]}
            />
          ))}
        </main>
      )}
    </div>
  );
}
