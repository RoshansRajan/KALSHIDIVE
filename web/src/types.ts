/**
 * Mirrors packages/schemas/kalshidive_schemas/*.py.
 *
 * Hand-maintained rather than generated: the surface is small enough that a
 * codegen step would cost more than it saves. The tradeoff is real though --
 * if you change a Pydantic model, change it here in the same commit, because
 * nothing will catch the drift for you.
 */

export type Side = "yes" | "no";
export type Signal = "bullish" | "bearish" | "neutral";

export interface MarketEvent {
  type: "snapshot" | "delta" | "trade";
  market_ticker: string;
  seq: number;
  received_at: string;
  side?: Side;
  price?: number;
  delta?: number;
  count?: number;
}

/** Top-of-book summary computed by ingest, so the browser never replays deltas. */
export interface BookView {
  market_ticker: string;
  best_bid: number | null;
  best_bid_size: number | null;
  best_ask: number | null;
  best_ask_size: number | null;
  spread: number | null;
  /** True when a sequence gap left the book unreliable. Surfaced, not hidden. */
  stale: boolean;
  yes: [number, number][];
  no: [number, number][];
}

export interface MarketInsight {
  signal: Signal;
  confidence: number;
  rationale: string;
  horizon_minutes: number;
}

export interface TickMessage {
  kind: "tick";
  event: MarketEvent;
  book: BookView;
}

export interface InsightMessage {
  kind: "insight";
  market_ticker: string;
  insight: MarketInsight;
  window_start_seq: number;
  window_end_seq: number;
  model: string;
  created_at: string;
}

export type BusMessage = TickMessage | InsightMessage;
