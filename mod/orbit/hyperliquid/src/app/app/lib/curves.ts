"use client";

// One PnL curve per wallet, fetched once and shared by everything that draws
// one — the trader cards, the table's hover cell, anything after them.
//
// A curve costs Hyperliquid one `portfolio` call, and the board can be five
// thousand wallets long. So the rules here are about restraint rather than
// speed:
//
//   * **One cache, keyed by wallet + window.** A 1d curve and a 30d curve are
//     different drawings of the same account, so both are worth holding; the
//     same drawing asked for twice is not.
//   * **One request per wallet in flight.** A card and a hover asking at the
//     same moment share the same promise.
//   * **Pages, not floods.** Cards ask for the wallets they are showing, in
//     batches the API bounds, and never for the rows below the fold.
//   * **A failure is an answer.** Upstream trouble becomes `available: false`
//     plus a sentence, because a card that throws looks like a broken trader
//     rather than a busy exchange.

import { useEffect, useState } from "react";
import { fetchTraderCurve, fetchTraderCurves, type TraderCurve } from "./api";

type Cached = { at: number; curve: TraderCurve };

const cache = new Map<string, Cached>();
const pending = new Map<string, Promise<TraderCurve>>();

/** How long a successful curve stands. The API caches Hyperliquid's portfolio
 *  payload for 5 minutes upstream, so a shorter TTL here would only re-fetch
 *  the same bytes. */
const TTL_MS = 5 * 60_000;
/** Failures stand for seconds, not minutes — a rate limit clears, and a curve
 *  that says "try again in a moment" must mean it. */
const FAIL_TTL_MS = 20_000;
/** Single-curve fetches in flight at once (the hover path). Hyperliquid
 *  answers /info per IP; three keeps a fast scroll from turning into a 429
 *  storm that punishes the whole board. */
const MAX_INFLIGHT = 3;
/** Wallets per batch request. Under the API's own `max_batch` (60) so a page
 *  is never truncated, and small enough that cards fill in visible waves
 *  instead of all at once after a long wait. */
export const BATCH = 30;

let inflight = 0;
const waiting: (() => void)[] = [];

function acquire(): Promise<void> {
  return new Promise((resolve) => {
    if (inflight < MAX_INFLIGHT) { inflight++; resolve(); return; }
    waiting.push(() => { inflight++; resolve(); });
  });
}
function release() {
  inflight--;
  waiting.shift()?.();
}

const key = (address: string, days: number) => `${address.toLowerCase()}:${days}`;

/** A failure wearing the same shape as an answer, so every consumer has one
 *  code path: `available: false` plus a sentence. */
export function unavailable(address: string, days: number, note: string): TraderCurve {
  return {
    address, days, period: "", points: [], start_ms: 0, end_ms: 0,
    pnl: 0, high: 0, low: 0, max_drawdown: 0, max_drawdown_pct: 0,
    available: false, note,
  };
}

/** A curve already in hand, or null. Lets a component paint on its first
 *  frame instead of flashing a skeleton for something it already has. */
export function fresh(address: string, days: number): TraderCurve | null {
  const k = key(address, days);
  const hit = cache.get(k);
  if (!hit) return null;
  const ttl = hit.curve.available ? TTL_MS : FAIL_TTL_MS;
  if (Date.now() - hit.at > ttl) { cache.delete(k); return null; }
  return hit.curve;
}

function store(curve: TraderCurve, days: number) {
  cache.set(key(curve.address, days), { at: Date.now(), curve });
}

/** One wallet's curve — the hover path. Deduped, cached, and rate-limited. */
export function loadCurve(address: string, days: number): Promise<TraderCurve> {
  const k = key(address, days);
  const hit = fresh(address, days);
  if (hit) return Promise.resolve(hit);
  const already = pending.get(k);
  if (already) return already;

  const p = acquire()
    .then(() => fetchTraderCurve(address, days))
    .catch((e: any) => unavailable(address, days, e?.message ?? "could not load this curve"))
    .then((curve) => {
      release();
      // Trust the wallet we asked about, not the one the payload names — an
      // error shape may carry neither, and the cache key must still match.
      const c = { ...curve, address };
      store(c, days);
      pending.delete(k);
      return c;
    });
  pending.set(k, p);
  return p;
}

/**
 * A page of curves in one request.
 *
 * Wallets already cached or already in flight are dropped before the call, so
 * re-sorting a board that is fully drawn costs nothing at all. The whole page
 * failing (the request itself, not one wallet) resolves to a note per wallet
 * rather than rejecting: the cards keep their numbers and say why the picture
 * is missing.
 */
export async function loadCurves(addresses: string[], days: number): Promise<TraderCurve[]> {
  const want = addresses.filter((a) => !fresh(a, days) && !pending.has(key(a, days)));
  const settled: TraderCurve[] = [];
  for (const a of addresses) {
    const hit = fresh(a, days);
    if (hit) settled.push(hit);
  }
  if (want.length === 0) return settled;

  const req = fetchTraderCurves(want, days)
    .then((r) => r.curves)
    .catch((e: any) => want.map((a) =>
      unavailable(a, days, e?.message ?? "could not load these curves")));

  // Register the whole page as in flight so a hover landing mid-batch waits
  // on it instead of opening a second request for the same wallet.
  const byAddr = req.then((curves) => {
    const m = new Map(curves.map((c) => [c.address.toLowerCase(), c]));
    return m;
  });
  for (const a of want) {
    const p = byAddr
      .then((m) => m.get(a.toLowerCase()) ?? unavailable(a, days, "hyperliquid returned no curve for this wallet"))
      .finally(() => pending.delete(key(a, days)));
    pending.set(key(a, days), p);
  }

  const curves = await req;
  for (const c of curves) store(c, days);
  return [...settled, ...curves];
}

/**
 * Curves for the wallets currently on screen.
 *
 * Fetches in [`BATCH`]-sized pages and publishes each page as it lands, so a
 * long list draws top-down instead of staying blank until the last wallet
 * answers. Changing the sort or the filter re-runs against the cache and
 * usually resolves without a request.
 */
export function useCurves(addresses: string[], days: number, enabled = true): Record<string, TraderCurve> {
  const list = addresses.join(",");
  const [curves, setCurves] = useState<Record<string, TraderCurve>>({});

  useEffect(() => {
    if (!enabled) return;
    const addrs = list ? list.split(",") : [];
    // Paint what is already in hand on this frame — a re-sort of a drawn
    // board must not blink every card back to a skeleton.
    const have: Record<string, TraderCurve> = {};
    for (const a of addrs) {
      const hit = fresh(a, days);
      if (hit) have[a.toLowerCase()] = hit;
    }
    setCurves(have);

    let alive = true;
    (async () => {
      for (let i = 0; i < addrs.length; i += BATCH) {
        if (!alive) return;
        const page = addrs.slice(i, i + BATCH).filter((a) => !fresh(a, days));
        if (page.length === 0) continue;
        const got = await loadCurves(page, days);
        if (!alive) return;
        setCurves((m) => {
          const next = { ...m };
          for (const c of got) next[c.address.toLowerCase()] = c;
          return next;
        });
      }
    })();
    return () => { alive = false; };
  }, [list, days, enabled]);

  return curves;
}
