"use client";

// How deep the index is, fetched once for the whole tab.
//
// Both the front page and the board draw the same window rail, and both want
// to say how much history is behind each button. Fetching per component gave
// two requests on every mount and two different answers while one was in
// flight, so the promise lives at module scope and every hook shares it.

import { useEffect, useState } from "react";
import { fetchCoverage } from "./api";
import type { Coverage } from "./types";

const TTL_MS = 5 * 60_000;

let cached: Coverage | null = null;
let cachedAt = 0;
let inflight: Promise<Coverage> | null = null;
const listeners = new Set<(c: Coverage) => void>();

function load(): Promise<Coverage> {
  if (inflight) return inflight;
  inflight = fetchCoverage()
    .then((c) => {
      cached = c;
      cachedAt = Date.now();
      listeners.forEach((fn) => fn(c));
      return c;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function useCoverage(): Coverage | null {
  const [cov, setCov] = useState<Coverage | null>(cached);

  useEffect(() => {
    listeners.add(setCov);
    if (!cached || Date.now() - cachedAt > TTL_MS) load().catch(() => {});
    return () => {
      listeners.delete(setCov);
    };
  }, []);

  return cov;
}

/** What a horizon is actually backed by — null when coverage hasn't loaded. */
export function windowCoverage(cov: Coverage | null, days: number) {
  if (!cov) return null;
  if (days === 0)
    return { days: 0, covered: cov.priced, pct: 100, ok: cov.priced > 0 };
  return (
    cov.windows.find((w) => w.days === days) ?? {
      days,
      // Nothing measured for a horizon the server doesn't offer: fall back to
      // the depth, which is the one thing we always know.
      covered: days <= cov.depth_days ? cov.priced : 0,
      pct: days <= cov.depth_days ? 100 : 0,
      ok: days <= cov.depth_days,
    }
  );
}
