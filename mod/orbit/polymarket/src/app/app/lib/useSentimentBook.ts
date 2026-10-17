"use client";

// The React half of lib/marketSentiment.ts.
//
// The gate itself is synchronous — `tradeMatchesFilters` is called from a
// dozen render paths that cannot await. The DATA behind it is not: a mood is
// price history, one CLOB request per outcome token. This hook is the seam:
// it warms the book off a set of trades and hands back the pure lookup the
// gate takes, plus the two numbers a screen must show beside a sentiment
// filter — how much of the flow it can actually read, and what it would keep.
//
// Three restraints, all the same restraint the rest of this console shows
// about upstream budget:
//
//   • NOTHING IS FETCHED unless a sentiment filter is actually on. An idle
//     dimension costs zero requests. `sentimentFilterActive` is the switch.
//   • The refetch key is the FILTER's window (the only dial that changes what
//     history is needed) plus a coarse signature of the trades — their count
//     and newest timestamp. Re-rendering with the same flow does not re-fetch;
//     changing the mood from bullish to bearish does not either, because the
//     same series answers both.
//   • The fetch is cancellable and never leaves a stale book behind a fresh
//     filter.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  emptySentimentBook, sentimentBreakdown, sentimentCoverage, sentimentFilterActive,
  sentimentReject, sentimentWindowHours, warmSentiment,
  type SentimentBook, type SentimentFilter, type SentimentLean, type SentimentSubject,
} from "./marketSentiment";

export interface SentimentBookState {
  book: SentimentBook;
  /** Warming right now. */
  loading: boolean;
  /** Readable trades / total trades, 0–1. 0 when nothing is gated. */
  coverage: number;
  readable: number;
  /** How the sample's trades break down by mood. */
  breakdown: Record<SentimentLean, number>;
  /** Trades the current filter would KEEP, out of the sample. */
  kept: number;
  /** Distinct outcome tokens dropped for budget — they read `unknown`. */
  overBudget: number;
  /** The fetch could not reach as far back as the sample does — trades before
      `book.coversFromMs` read `unknown`. See `MAX_HISTORY_SPAN_MS`. */
  spanCapped: boolean;
}

const IDLE: SentimentBookState = {
  book: emptySentimentBook(),
  loading: false,
  coverage: 0,
  readable: 0,
  breakdown: { bullish: 0, bearish: 0, flat: 0, unknown: 0 },
  kept: 0,
  overBudget: 0,
  spanCapped: false,
};

/** Warm the sentiment book for `trades` under `filter`.
 *
 *  `atMs` is what a LIVE screen passes to read every market as of now; leave it
 *  undefined and each trade is read at its own timestamp, which is what a
 *  replay preview wants. */
export function useSentimentBook(
  trades: SentimentSubject[],
  filter: SentimentFilter | undefined | null,
  opts: { atMs?: number; budget?: number } = {},
): SentimentBookState {
  const active = sentimentFilterActive(filter);
  const windowHours = sentimentWindowHours(filter);

  // Coarse on purpose. The exact array identity changes on every poll tick;
  // what actually invalidates a price series is a new trade or a new window.
  const sig = useMemo(() => {
    if (!active) return "off";
    let newest = 0;
    let n = 0;
    for (const t of trades) {
      if (!t) continue;
      n++;
      const ts = t.timestamp ?? 0;
      if (ts > newest) newest = ts;
    }
    return `${n}|${Math.round(newest / 60_000)}|${windowHours}|${opts.atMs ? Math.round(opts.atMs / 300_000) : 0}`;
  }, [active, trades, windowHours, opts.atMs]);

  const [book, setBook] = useState<SentimentBook>(() => emptySentimentBook());
  const [loading, setLoading] = useState(false);
  const tradesRef = useRef(trades);
  tradesRef.current = trades;

  useEffect(() => {
    if (!active) {
      setBook(emptySentimentBook());
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void warmSentiment(tradesRef.current, {
      filter,
      atMs: opts.atMs,
      budget: opts.budget,
    }).then((b) => {
      if (cancelled) return;
      setBook(b);
      setLoading(false);
    });
    return () => { cancelled = true; };
    // `filter` is deliberately not a dep: swapping bullish↔bearish reads the
    // same series, and `sig` already carries the one dial (window) that does
    // not. Refetching on every mood click would spend a hundred requests to
    // learn nothing.
  }, [sig, active]); // eslint-disable-line react-hooks/exhaustive-deps

  return useMemo(() => {
    if (!active) return IDLE;
    const cov = sentimentCoverage(trades, book);
    const breakdown = sentimentBreakdown(trades, book);
    // What the filter keeps, counted through the SAME reject the engine runs.
    // Not a second copy of the rules — a preview that disagreed with the gate
    // would be worse than no preview.
    let kept = 0;
    for (const t of trades) {
      if (sentimentReject(book.lookup(t), filter) === null) kept++;
    }
    return {
      book,
      loading,
      coverage: cov.fraction,
      readable: cov.readable,
      breakdown,
      kept,
      overBudget: book.overBudget,
      spanCapped: book.spanCapped,
    };
  }, [active, book, loading, trades, filter]);
}
