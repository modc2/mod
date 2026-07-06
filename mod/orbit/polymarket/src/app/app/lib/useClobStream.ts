"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Live price stream over Polymarket's CLOB WebSocket — the ONLY Polymarket
 * feed that actually streams. We use it instead of REST-polling the gamma
 * `markets` endpoint so the live ticker doesn't burn the rate limit (the old
 * 8s poll × every open tab was a steady drip of calls; the data-api 429s are a
 * separate, REST-only problem handled by the backend's hourly cache).
 *
 * Channel: wss://ws-subscriptions-clob.polymarket.com/ws/market
 *   - subscribe with {assets_ids: [...], type: "market"}
 *   - server pushes `book` (full snapshot), `price_change` (level deltas) and
 *     `last_trade_price` events per asset id (the CLOB outcome-token id).
 *
 * We keep a per-asset order book in a ref and derive a midpoint price; state is
 * flushed on a 1s cadence so a burst of deltas can't cause a render storm.
 * No auth needed for the public market channel.
 */

const WS_URL =
  process.env.NEXT_PUBLIC_CLOB_WS_URL ||
  "wss://ws-subscriptions-clob.polymarket.com/ws/market";

const FLUSH_MS = 1000; // throttle state updates to 1/s — plenty for a ticker
const PING_MS = 10_000; // keep-alive; the server drops idle sockets
const MAX_BACKOFF_MS = 30_000;

interface Book {
  bids: Map<string, number>; // price -> size
  asks: Map<string, number>;
  last?: number; // last trade price, used only if the book is one-sided/empty
}

/** Best bid (highest) and best ask (lowest) among levels with size > 0. */
function midpoint(book: Book): number | undefined {
  let bestBid = -Infinity;
  for (const [p, s] of book.bids) if (s > 0) bestBid = Math.max(bestBid, Number(p));
  let bestAsk = Infinity;
  for (const [p, s] of book.asks) if (s > 0) bestAsk = Math.min(bestAsk, Number(p));

  const haveBid = Number.isFinite(bestBid);
  const haveAsk = Number.isFinite(bestAsk);
  if (haveBid && haveAsk) return (bestBid + bestAsk) / 2;
  if (haveBid) return bestBid;
  if (haveAsk) return bestAsk;
  return book.last;
}

function applyLevels(side: Map<string, number>, levels: unknown): void {
  if (!Array.isArray(levels)) return;
  for (const lvl of levels) {
    const l = lvl as Record<string, unknown>;
    const price = String(l.price ?? "");
    if (!price) continue;
    const size = Number(l.size ?? 0);
    if (size > 0) side.set(price, size);
    else side.delete(price); // size 0 = level removed
  }
}

/**
 * Subscribe to live midpoint prices for a set of CLOB token ids.
 * Returns a Map<assetId, price> (price in 0..1) plus a `connected` flag.
 * Re-subscribes whenever the set of ids changes.
 */
export function useClobStream(
  assetIds: string[],
  { pauseWhenHidden = true }: { pauseWhenHidden?: boolean } = {},
) {
  const [prices, setPrices] = useState<Map<string, number>>(new Map());
  const [connected, setConnected] = useState(false);

  // Stable dependency key — only reconnect when the actual id set changes,
  // not on every render that produces a fresh array instance.
  const idsKey = [...assetIds].filter(Boolean).sort().join(",");

  useEffect(() => {
    const ids = idsKey ? idsKey.split(",") : [];
    if (ids.length === 0) {
      setPrices(new Map());
      setConnected(false);
      return;
    }

    const books = new Map<string, Book>();
    const priceRef = new Map<string, number>();
    let dirty = false;
    let ws: WebSocket | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let flushTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let backoff = 1000;
    let closedByUs = false;

    const bookFor = (assetId: string): Book => {
      let b = books.get(assetId);
      if (!b) {
        b = { bids: new Map(), asks: new Map() };
        books.set(assetId, b);
      }
      return b;
    };

    const recompute = (assetId: string) => {
      const mid = midpoint(bookFor(assetId));
      if (mid == null) return;
      if (priceRef.get(assetId) !== mid) {
        priceRef.set(assetId, mid);
        dirty = true;
      }
    };

    const handleEvent = (evt: Record<string, unknown>) => {
      const assetId = String(evt.asset_id ?? evt.assetId ?? "");
      if (!assetId) return;
      const type = String(evt.event_type ?? evt.type ?? "");
      const book = bookFor(assetId);

      if (type === "book") {
        book.bids.clear();
        book.asks.clear();
        applyLevels(book.bids, evt.bids ?? evt.buys);
        applyLevels(book.asks, evt.asks ?? evt.sells);
      } else if (type === "price_change") {
        // Newer payloads batch level updates in `changes`; older ones put a
        // single {price, side, size} at the top level.
        const changes = Array.isArray(evt.changes) ? evt.changes : [evt];
        for (const ch of changes) {
          const c = ch as Record<string, unknown>;
          const side = String(c.side ?? "").toUpperCase();
          const target = side === "SELL" || side === "ASK" ? book.asks : book.bids;
          applyLevels(target, [c]);
        }
      } else if (type === "last_trade_price" || type === "tick_size_change") {
        const p = Number(evt.price);
        if (Number.isFinite(p) && p > 0) book.last = p;
      } else {
        return;
      }
      recompute(assetId);
    };

    const onMessage = (raw: string) => {
      // Non-JSON keep-alive frames ("PONG") are ignored.
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        return;
      }
      const events = Array.isArray(parsed) ? parsed : [parsed];
      for (const e of events) {
        if (e && typeof e === "object") handleEvent(e as Record<string, unknown>);
      }
    };

    const connect = () => {
      if (pauseWhenHidden && typeof document !== "undefined" && document.hidden) return;
      try {
        ws = new WebSocket(WS_URL);
      } catch {
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        backoff = 1000;
        setConnected(true);
        ws?.send(JSON.stringify({ assets_ids: ids, type: "market" }));
        pingTimer = setInterval(() => {
          try {
            ws?.send("PING");
          } catch {
            /* socket closing */
          }
        }, PING_MS);
      };
      ws.onmessage = (ev) => onMessage(typeof ev.data === "string" ? ev.data : "");
      ws.onerror = () => ws?.close();
      ws.onclose = () => {
        setConnected(false);
        if (pingTimer) {
          clearInterval(pingTimer);
          pingTimer = null;
        }
        if (!closedByUs) scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (closedByUs || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    };

    const teardown = () => {
      closedByUs = true;
      if (pingTimer) clearInterval(pingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch {
        /* noop */
      }
      ws = null;
    };

    // Flush accumulated prices to React state at most once per second.
    flushTimer = setInterval(() => {
      if (!dirty) return;
      dirty = false;
      setPrices(new Map(priceRef));
    }, FLUSH_MS);

    const onVisibility = () => {
      if (!pauseWhenHidden) return;
      if (document.hidden) {
        // Drop the socket while backgrounded; reconnect (fresh book) on return.
        closedByUs = true;
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        try {
          ws?.close();
        } catch {
          /* noop */
        }
      } else {
        closedByUs = false;
        if (!ws || ws.readyState === WebSocket.CLOSED) connect();
      }
    };

    connect();
    if (pauseWhenHidden && typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      teardown();
      if (flushTimer) clearInterval(flushTimer);
      if (pauseWhenHidden && typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, [idsKey, pauseWhenHidden]);

  return { prices, connected };
}
