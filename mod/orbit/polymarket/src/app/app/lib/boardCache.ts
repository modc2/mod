// Snapshot cache for the TOP TRADERS board — the last server page per view.
//
// The server already answers board reads from its own memory+disk cache, but
// the browser still opened every visit on an empty table and a spinner while
// that round-trip (or, after the activator slept the API, a full pipeline
// re-run) happened. This stores the last page-0 response per view identity —
// window + sort + every filter that changes what the server would return — so
// a reload paints the board it last showed IMMEDIATELY, and the normal fetch
// replaces it underneath. Stale-while-revalidate: the snapshot carries the
// server's real `syncedAt`, so the staleness chip tells the truth about age
// rather than making old rows look fresh.
//
// localStorage is ONE origin shared by every module on the host, so writes
// are quota-guarded (never throw), snapshots are few (MAX_SNAPSHOTS), small
// (rows are the compact TopTrader shape, ~12-point curves), and size-capped.

import { TopTrader } from "./polymarket";

const PREFIX = "poly_board_";
// A snapshot is a first paint, not a source of truth — but even a day-old
// board is a better landing than an empty one, and the revalidate replaces it
// within a second on a warm server. Past this it's dead weight on the quota.
const TTL_MS = 24 * 60 * 60 * 1000;
// One per recently-viewed tuple (30D/ROI, 30D/VOLUME, 7D/ROI, …); beyond
// this the oldest goes. Keeps worst-case footprint to a few hundred KB.
const MAX_SNAPSHOTS = 6;
// Refuse to persist a pathological payload rather than evict the rest of the
// shared origin for it.
const MAX_BYTES = 300 * 1024;

export interface BoardSnapshot {
  traders: TopTrader[];
  total: number;
  activityDropped: number;
  /** Where the SERVER said the payload came from when it was fetched. */
  source: "memory" | "disk" | "fresh";
  /** Wall-clock ms of the server's last Polymarket pull — real source age. */
  syncedAt: number | null;
  /** Wall-clock ms when this snapshot was written client-side. */
  savedAt: number;
}

/** Stable key for a board view. Every field that changes what the server
    would return for page 0 must be in `parts` — two views that differ in any
    of them must never share a snapshot. */
export function boardKey(parts: Record<string, unknown>): string {
  const stable = Object.keys(parts)
    .sort()
    .map((k) => `${k}=${String(parts[k])}`)
    .join("&");
  return `${PREFIX}${stable}`;
}

export function loadBoardSnapshot(key: string): BoardSnapshot | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const snap = JSON.parse(raw) as BoardSnapshot;
    if (!Array.isArray(snap.traders) || typeof snap.savedAt !== "number") return null;
    if (Date.now() - snap.savedAt > TTL_MS) {
      localStorage.removeItem(key);
      return null;
    }
    return snap;
  } catch {
    return null;
  }
}

// `marketTitles` is ~95% of a page's bytes (hundreds of titles per trader,
// ~630KB for 50 rows) and the hydrated first paint never reads it — the warm
// path renders rows as-is, and the client-side filter that does read titles
// only runs against the streamed dataset. Keep a taste of it for safety,
// drop the rest: 50 rows land at ~60KB.
const MAX_TITLES = 12;

export function saveBoardSnapshot(
  key: string,
  snap: Omit<BoardSnapshot, "savedAt">,
): void {
  if (typeof window === "undefined") return;
  let payload: string;
  try {
    payload = JSON.stringify({
      ...snap,
      traders: snap.traders.map((t) => ({
        ...t,
        marketTitles: (t.marketTitles ?? []).slice(0, MAX_TITLES),
      })),
      savedAt: Date.now(),
    });
  } catch {
    return;
  }
  if (payload.length > MAX_BYTES) return;
  try {
    localStorage.setItem(key, payload);
    prune();
  } catch {
    // Shared-origin quota — drop our own snapshots first, then retry once.
    // A failed write costs nothing but the fast paint on next load.
    try {
      prune(0);
      localStorage.setItem(key, payload);
    } catch {
      // truly full — give up
    }
  }
}

/** Drop expired snapshots, and the oldest beyond `keep`. */
function prune(keep: number = MAX_SNAPSHOTS): void {
  const now = Date.now();
  const entries: Array<{ key: string; savedAt: number }> = [];
  const drop: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (!key || !key.startsWith(PREFIX)) continue;
    try {
      const raw = localStorage.getItem(key);
      const savedAt = raw ? (JSON.parse(raw) as BoardSnapshot).savedAt : 0;
      if (!savedAt || now - savedAt > TTL_MS) drop.push(key);
      else entries.push({ key, savedAt });
    } catch {
      drop.push(key);
    }
  }
  entries.sort((a, b) => b.savedAt - a.savedAt);
  for (const e of entries.slice(keep)) drop.push(e.key);
  for (const k of drop) localStorage.removeItem(k);
}
