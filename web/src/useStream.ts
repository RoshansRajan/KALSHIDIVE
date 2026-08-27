import { useEffect, useRef, useState } from "react";
import type { BookView, BusMessage, InsightMessage } from "./types";

/**
 * Subscribes to the tick and insight fanouts.
 *
 * State is keyed by market ticker and replaced wholesale per message rather
 * than accumulated. A live order book has no use for its own history in the
 * browser -- that is what Postgres is for -- and keeping an unbounded array
 * of ticks in React state is how a dashboard left open overnight ends up
 * consuming a gigabyte.
 */
export function useStream(tickUrl: string, insightUrl: string) {
  const [books, setBooks] = useState<Record<string, BookView>>({});
  const [insights, setInsights] = useState<Record<string, InsightMessage>>({});
  const [connected, setConnected] = useState(false);
  const [tickCount, setTickCount] = useState(0);

  // Held in a ref so reconnect scheduling never triggers a re-render.
  const timers = useRef<number[]>([]);

  useEffect(() => {
    let closed = false;

    const connect = (url: string, onMessage: (m: BusMessage) => void) => {
      if (closed) return;
      const ws = new WebSocket(url);

      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        try {
          onMessage(JSON.parse(ev.data) as BusMessage);
        } catch {
          // A single malformed frame must not tear down the socket.
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        // Fixed short delay: this is a LAN-local service, so the thundering
        // herd that justifies exponential backoff upstream does not apply.
        timers.current.push(
          window.setTimeout(() => connect(url, onMessage), 1000),
        );
      };
      ws.onerror = () => ws.close();
      return ws;
    };

    connect(tickUrl, (msg) => {
      if (msg.kind !== "tick") return;
      setBooks((prev) => ({ ...prev, [msg.book.market_ticker]: msg.book }));
      setTickCount((n) => n + 1);
    });

    connect(insightUrl, (msg) => {
      if (msg.kind !== "insight") return;
      setInsights((prev) => ({ ...prev, [msg.market_ticker]: msg }));
    });

    return () => {
      closed = true;
      timers.current.forEach(window.clearTimeout);
      timers.current = [];
    };
  }, [tickUrl, insightUrl]);

  return { books, insights, connected, tickCount };
}
