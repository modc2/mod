// The COPY BOOK, client side.
//
// Thin on purpose. Every function here is one call to `/copy/*` on the Rust
// API — the same routes the `pm_copy_*` MCP tools call. There is no local
// mirror of the book, no localStorage copy, no merge: the server owns it, the
// screen renders what the server says, and an agent that changes an allocation
// changes what the next poll renders. That is the whole reason the book lives
// server-side (see api/src/copy.rs).
//
// Bearer auth is attached by the fetch patch in lib/access.ts — every request
// to `/api/polymarket` carries the owner token without a call site knowing.

import { API_BASE } from "./polymarket";
import type { AllocationParams } from "./identityStrat";

/** One leader's live session, as the desk needs it. Null ⇒ never started. */
export interface CopyLive {
  running: boolean;
  /** TEST vs LIVE — the most consequential bit on the row. `autoExecute` is
      the wire spelling; `modeOf()` in lib/tradingMode.ts turns it into the
      word the UI shows. */
  autoExecute: boolean;
  status: string;
  lastCycleAt: number | null;
  nextCycleAt: number | null;
  cycles: number;
  ordersPlaced: number;
  ordersFailed: number;
  volumeMirrored: number;
  balance: number | null;
  accountValue: number | null;
  error: string | null;
  ledger: {
    realized: number;
    volume: number;
    buys: number;
    sells: number;
    redeems: number;
    settled: number;
    lastFillAt: number;
  } | null;
}

/** A row of `/copy/book` — an allocation plus what it's doing. */
export interface CopyBookRow {
  address: string;
  label?: string | null;
  /** Label, or "COPY 0xab…cd". Server-computed so both clients agree. */
  name: string;
  allocationUsd: number;
  enabled: boolean;
  params?: AllocationParams;
  notes?: string | null;
  addedAt: number;
  updatedAt: number;
  /** `copy-<address>` — the engine session key, ledger bucket and backtest
      key, all in one derived string. */
  strategyId: string;
  live: CopyLive | null;
}

export interface CopyBook {
  version: number;
  /** The desk's target size. Advisory: the engine budgets per allocation. */
  bankroll: number;
  updatedAt: number;
  eoa: string | null;
  allocations: CopyBookRow[];
  totals: {
    traders: number;
    enabled: number;
    allocatedUsd: number;
    /** Negative ⇒ over-allocated against the desk's own target. */
    unallocatedUsd: number;
    running: number;
    /** Sessions actually placing orders. `running - executing` are DRY RUN. */
    executing: number;
  };
}

/** The API answers 4xx with `{error}`; surface that text rather than a
    status code, because these errors are written to be read by a person
    ("give them dollars before starting"). */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error((body && (body as { error?: string }).error) || `HTTP ${res.status}`);
  }
  return body as T;
}

function eoaQuery(eoa?: string | null): string {
  return eoa ? `?eoa=${encodeURIComponent(eoa)}` : "";
}

/** The desk. Pass the signed-in wallet to get the live column filled in. */
export function fetchCopyBook(eoa?: string | null): Promise<CopyBook> {
  return call<CopyBook>(`/copy/book${eoaQuery(eoa)}`);
}

export interface UpsertAllocation {
  address: string;
  allocationUsd: number;
  label?: string;
  notes?: string;
  enabled?: boolean;
  /** A PATCH: omitted knobs keep their current value. */
  params?: AllocationParams;
}

/** Add a leader, or change one already in the book. Idempotent by address —
    "copy this trader" twice is one allocation, not two sessions. A running
    session is reconfigured in place (its execution mode is preserved). */
export function upsertAllocation(
  alloc: UpsertAllocation,
  eoa?: string | null,
): Promise<{ ok: boolean; allocation: CopyBookRow; reconfigured: boolean; book: CopyBook }> {
  return call(`/copy/allocations${eoaQuery(eoa)}`, {
    method: "POST",
    body: JSON.stringify(alloc),
  });
}

/** Change ONLY which markets a leader is copied in, leaving their money alone.
 *
 *  `allocationUsd` is required by the route (it is the one field an upsert
 *  cannot infer), so re-posting a gate means re-posting an amount — and the
 *  amount a screen is holding may be a poll behind, or edited locally and
 *  never saved. Both would turn "copy them only in bitcoin" into a silent
 *  resize of the position. So the current row is read back first and its own
 *  figure is echoed: this call can change the gate and nothing else.
 *
 *  An empty `marketQuery` is a VALUE, not an omission — it clears the gate
 *  (params are merged server-side, so a missing key would keep the old one). */
export async function setAllocationMarketQuery(
  address: string,
  marketQuery: string,
  eoa?: string | null,
): Promise<CopyBook> {
  const addr = address.trim().toLowerCase();
  const book = await fetchCopyBook(eoa);
  const row = book.allocations.find((a) => a.address.toLowerCase() === addr);
  if (!row) throw new Error(`${addr} isn't in the copy book`);
  const res = await upsertAllocation(
    { address: row.address, allocationUsd: row.allocationUsd, params: { marketQuery } },
    eoa,
  );
  return res.book;
}

/** Stop this leader's session and drop them from the book. */
export function removeAllocation(
  address: string,
  eoa?: string | null,
): Promise<{ ok: boolean; removed: boolean; stopped: boolean; book: CopyBook }> {
  return call(`/copy/allocations/${address}${eoaQuery(eoa)}`, { method: "DELETE" });
}

/** Set the desk's target size without touching allocations. */
export function setBankroll(bankroll: number, eoa?: string | null): Promise<CopyBook> {
  return call(`/copy/book${eoaQuery(eoa)}`, {
    method: "POST",
    body: JSON.stringify({ bankroll }),
  });
}

/** Split a bankroll across the enabled leaders. "weighted" keeps the
    proportions you set and rescales them — conviction survives a deposit. */
export function rebalanceBook(
  bankroll: number,
  mode: "equal" | "weighted",
  eoa?: string | null,
): Promise<{ ok: boolean; reconfigured: number; book: CopyBook }> {
  return call(`/copy/rebalance${eoaQuery(eoa)}`, {
    method: "POST",
    body: JSON.stringify({ bankroll, mode }),
  });
}

/** Start copying. `autoExecute` omitted or false ⇒ TEST: the engine computes
    every mirror and places none. Callers must pass `true` deliberately, and
    every UI path that does goes through `confirmGoLive()`. */
export function startCopying(
  eoa: string,
  opts?: { address?: string; autoExecute?: boolean; proxyAddress?: string },
): Promise<{
  ok: boolean;
  mode: "LIVE" | "DRY RUN";
  proxyAddress: string;
  started: { address: string; strategyId: string; capital: number }[];
  book: CopyBook;
}> {
  return call(`/copy/start`, {
    method: "POST",
    body: JSON.stringify({ eoa, ...opts }),
  });
}

/** Flip a RUNNING session between TEST and LIVE in place, without stopping it.
 *
 *  The same `/live/execution` route the strat workspace's mode switch uses —
 *  so "change your mind about real money" is one click on both screens. The
 *  desk used to have no route to it at all: the mode was fixed at start, and
 *  the only way to go live was STOP then start again, which reset the session
 *  and made the two consoles feel like different products. */
export function setCopyExecution(
  eoa: string,
  strategyId: string,
  autoExecute: boolean,
): Promise<{ ok: boolean; autoExecute: boolean }> {
  return call(`/live/execution`, {
    method: "POST",
    body: JSON.stringify({ eoa, strategyId, autoExecute }),
  });
}

/** Stop one leader, or the whole desk when `address` is omitted. */
export function stopCopying(
  eoa: string,
  address?: string,
): Promise<{ ok: boolean; stopped: string[]; book: CopyBook }> {
  return call(`/copy/stop`, {
    method: "POST",
    body: JSON.stringify({ eoa, ...(address ? { address } : {}) }),
  });
}

/** The book as identity strats — what the backtest worker replays. */
export function fetchCopyStrats(): Promise<{ strats: unknown[]; count: number }> {
  return call(`/copy/strats`);
}

// ── Derived reads the desk uses in more than one place ──

/** Realized P&L for a row, or null when the leader has never been run.
    Realized only: open positions are marked, not booked, and a mark reads
    high because leaders sell winners and let losers expire. */
export function realizedPnl(row: CopyBookRow): number | null {
  return row.live?.ledger ? row.live.ledger.realized : null;
}

/** Why a running session might not be trading, in the order worth checking.
    Returns null when there's nothing to warn about. Every string here was a
    real support question first. */
export function stallReason(row: CopyBookRow): string | null {
  const live = row.live;
  if (!live || !live.running) return null;
  if (live.error) return live.error;
  if (!live.autoExecute) {
    return "TEST — computing every mirror, placing none. Flip the switch to LIVE to send them.";
  }
  if (row.allocationUsd <= 0) return "no allocation — nothing to size against";
  if (live.balance !== null && live.balance <= 0) {
    return "trading wallet is empty — deposit before this can fill";
  }
  if (live.cycles > 20 && (!live.ledger || live.ledger.lastFillAt === 0)) {
    return "running, no fill yet — check the leader's flow and the entry gates";
  }
  return null;
}
