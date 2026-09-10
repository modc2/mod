// STRAT PNL HISTORY — the sidecar that makes the strat cards' 7-day curves
// possible at all.
//
// Nothing durable holds a per-strat PnL time series today: the live engine's
// realizedEvents feed is pruned at 48h (live_engine.rs REALIZED_EVENTS_MAX_AGE
// _MS), the portfolio equity curve is wallet-wide localStorage, and stratStats
// is an aggregate ledger with no time axis. So this loop samples the same
// number the sidebar already shows per strat — realized − fees + unrealized —
// every SAMPLE_MS into ~/.mod/polymarket/strat-pnl-history.json, and the
// /api/strat-pnl route serves the last 7 days of it to the cards.
//
// First run seeds the file from the engines' surviving realizedEvents (up to
// 48h of real fills), anchored so each strat's backfilled curve ends at its
// pnl right now — the cards get a real curve on day one instead of a dot.
//
// Owner-scoped on purpose: this is a single-owner console (owner.json), and
// the deposit wallet, sessions and ledger all hang off that one EOA.

import { existsSync, readFileSync } from "fs";
import { join } from "path";

import { API_BASE, fetchPositions, serverAuthHeaders, setServerAuthToken } from "../polymarket";
import { writeAtomic } from "./feedStore";
import { mintOwnerToken, ownerAddress, stateDir } from "./ownerToken";

const SAMPLE_MS = 10 * 60 * 1000;
const KEEP_MS = 30 * 24 * 3600 * 1000;
/** Points per strat the route serves — plenty for a 120px sparkline. */
const SERVE_POINTS = 120;

const historyPath = () => join(stateDir(), "strat-pnl-history.json");

/** One sample: sparse map — only strats that had money/ledger at time t. */
interface PnlRow {
  t: number;
  pnl: Record<string, number>;
}

interface HistoryFile {
  v: 1;
  eoa: string;
  rows: PnlRow[];
}

function readHistory(): HistoryFile | null {
  try {
    const raw = JSON.parse(readFileSync(historyPath(), "utf8")) as HistoryFile;
    return Array.isArray(raw.rows) ? raw : null;
  } catch {
    return null;
  }
}

// ── engine state, read the way stratStats.ts reads it ───────────

interface RawSession {
  strategyId?: string;
  config?: { strategyId?: string };
  state?: {
    positions?: Record<string, { tokenId?: string; size?: number; entryPrice?: number; strategyId?: string }>;
    stratStats?: Record<string, { realized?: number; fees?: number }>;
    realizedEvents?: Array<{ t?: number; strategyId?: string; pnl?: number }>;
  };
}

const num = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

async function fetchSessions(eoa: string): Promise<RawSession[]> {
  const res = await fetch(`${API_BASE}/live/sessions?eoa=${encodeURIComponent(eoa)}`, {
    headers: serverAuthHeaders(),
  });
  if (!res.ok) throw new Error(`/live/sessions HTTP ${res.status}`);
  const j = (await res.json()) as { sessions?: RawSession[] };
  return Array.isArray(j.sessions) ? j.sessions : [];
}

/** Current per-strat total PnL (realized − fees + unrealized), the exact
    number the sidebar's stratStats hook renders. Prices are best-effort —
    an unpriced open position marks at entry, same rule as the client. */
async function currentPnlByStrat(eoa: string, sessions: RawSession[]): Promise<Record<string, number>> {
  const price = new Map<string, number>();
  try {
    const res = await fetch(`${API_BASE}/deposit-wallet/info?eoa=${encodeURIComponent(eoa)}`, {
      headers: serverAuthHeaders(),
    });
    if (res.ok) {
      const info = (await res.json()) as { depositWallet?: string };
      if (info.depositWallet) {
        const live = await fetchPositions(info.depositWallet, { bypassCache: true });
        for (const p of live) if (p.tokenId) price.set(p.tokenId, p.currentPrice);
      }
    }
  } catch {
    // mark at entry
  }

  const pnl: Record<string, number> = {};
  for (const s of sessions) {
    const own = s.strategyId || s.config?.strategyId || "";
    for (const p of Object.values(s.state?.positions ?? {})) {
      const id = p.strategyId || own;
      if (!id) continue;
      const cost = num(p.size) * num(p.entryPrice);
      const cur = p.tokenId ? price.get(p.tokenId) : undefined;
      const value = num(p.size) * (cur !== undefined ? cur : num(p.entryPrice));
      pnl[id] = (pnl[id] ?? 0) + (value - cost);
    }
    for (const [rawId, l] of Object.entries(s.state?.stratStats ?? {})) {
      const id = rawId === "unassigned" && own ? own : rawId;
      pnl[id] = (pnl[id] ?? 0) + num(l.realized) - num(l.fees);
    }
  }
  return pnl;
}

/** Seed rows from the surviving realizedEvents (≤48h), each strat's curve
    anchored to END at its current pnl — point at event t = now − everything
    realized after t. Unrealized drift inside the window is unknowable from
    what survived, and a realized-anchored curve is the honest approximation. */
function backfillRows(sessions: RawSession[], nowPnl: Record<string, number>, now: number): PnlRow[] {
  const events = sessions.flatMap((s) => {
    const own = s.strategyId || s.config?.strategyId || "";
    return (s.state?.realizedEvents ?? [])
      .map((e) => ({ t: num(e.t), id: e.strategyId || own, pnl: num(e.pnl) }))
      .filter((e) => e.t > 0 && e.id !== "");
  }).sort((a, b) => a.t - b.t);
  if (events.length === 0) return [];

  // Walk newest→oldest subtracting each event's pnl from the anchor.
  const anchor: Record<string, number> = { ...nowPnl };
  const rows: PnlRow[] = [];
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.t >= now) continue;
    rows.unshift({ t: ev.t, pnl: { [ev.id]: anchor[ev.id] ?? 0 } });
    anchor[ev.id] = (anchor[ev.id] ?? 0) - ev.pnl;
  }
  return rows;
}

// ── the sample ──────────────────────────────────────────────────

let sampling = false;

export async function sampleStratPnl(): Promise<void> {
  if (sampling) return;
  sampling = true;
  try {
    const eoa = ownerAddress();
    const token = mintOwnerToken();
    if (!eoa || !token) return; // unconfigured gate — correctly do nothing
    setServerAuthToken(token);

    const sessions = await fetchSessions(eoa);
    const nowPnl = await currentPnlByStrat(eoa, sessions);
    const now = Date.now();

    let file = readHistory();
    if (!file || file.eoa !== eoa) {
      file = { v: 1, eoa, rows: backfillRows(sessions, nowPnl, now) };
    }
    if (Object.keys(nowPnl).length > 0) {
      file.rows.push({ t: now, pnl: nowPnl });
    }
    file.rows = file.rows.filter((r) => r.t >= now - KEEP_MS);
    writeAtomic(historyPath(), JSON.stringify(file));
  } catch {
    // Rust API down / RPC blip — the next tick samples again.
  } finally {
    sampling = false;
  }
}

// ── the reader (route-facing) ───────────────────────────────────

export interface StratPnlSeries {
  /** stratId → [t, pnl] points, oldest → newest, thinned to SERVE_POINTS. */
  series: Record<string, Array<[number, number]>>;
  days: number;
}

export function readStratPnlSeries(days = 7): StratPnlSeries {
  const file = readHistory();
  const out: Record<string, Array<[number, number]>> = {};
  if (file) {
    const cutoff = Date.now() - days * 24 * 3600 * 1000;
    for (const row of file.rows) {
      if (row.t < cutoff) continue;
      for (const [id, v] of Object.entries(row.pnl)) {
        (out[id] ??= []).push([row.t, v]);
      }
    }
    for (const [id, pts] of Object.entries(out)) {
      if (pts.length > SERVE_POINTS) {
        const step = (pts.length - 1) / (SERVE_POINTS - 1);
        out[id] = Array.from({ length: SERVE_POINTS }, (_, i) => pts[Math.round(i * step)]);
      }
    }
  }
  return { series: out, days };
}

// ── the loop ────────────────────────────────────────────────────

let ticker: ReturnType<typeof setInterval> | null = null;

export function startStratPnlLoop(): void {
  if (ticker) return;
  // First sample soon after boot (the Rust API needs a beat to come up), but
  // only wait when there's no history yet — a restart shouldn't gap the curve.
  const first = setTimeout(() => void sampleStratPnl(), existsSync(historyPath()) ? 5_000 : 20_000);
  first.unref?.();
  ticker = setInterval(() => void sampleStratPnl(), SAMPLE_MS);
  ticker.unref?.();
}
