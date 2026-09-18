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

/** Fired after a copy-book write made OUTSIDE a useCopyBook instance (the
    sidebar's SELECTION tray commits with raw upserts) — every mounted
    useCopyBook re-reads on it, so the desk shows the new rows without
    waiting out its 15s poll. */
export const COPY_BOOK_CHANGED_EVENT = "poly-copy-book-changed";

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
    /** GROSS — exit proceeds minus cost basis, before `fees`. */
    realized: number;
    /** Polymarket taker fees this session paid, at each market's own rate.
        Real money out of the wallet; `realized - fees` is what it kept. */
    fees: number;
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

/** A session RUNNING on this wallet that is not a row of the book — a strat
    started from an older console, a candle bot. It spends the same USDC the
    rows do, so the desk shows it (and can stop it) rather than reporting
    "none running" over a wallet that is placing orders. */
export interface OtherSession {
  strategyId: string;
  running: boolean;
  autoExecute: boolean;
  capital: number | null;
  marketQuery: string | null;
  /** True for an originating (candle/momentum) strat — no leaders. */
  momentum: boolean;
  /** Enabled leader addresses. */
  traders: string[];
  status: string;
  cycles: number;
  ordersPlaced: number;
  balance: number | null;
  error: string | null;
  realized: number | null;
  fees: number | null;
  lastFillAt: number | null;
}

export interface CopyBook {
  version: number;
  /** The desk's target size. Advisory: the engine budgets per allocation. */
  bankroll: number;
  updatedAt: number;
  eoa: string | null;
  allocations: CopyBookRow[];
  /** Running outside the book. Absent from an API older than this field. */
  sessions?: OtherSession[];
  totals: {
    traders: number;
    enabled: number;
    allocatedUsd: number;
    /** Negative ⇒ over-allocated against the desk's own target. */
    unallocatedUsd: number;
    running: number;
    /** Sessions actually placing orders. `running - executing` are DRY RUN. */
    executing: number;
    otherRunning?: number;
    otherExecuting?: number;
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

/** Stop a session by its engine id — the route for the sessions the book
    does NOT own (`CopyBook.sessions`). The same `/live/stop` the strat
    workspace used; `/copy/stop` only knows addresses. */
export function stopSession(eoa: string, strategyId: string): Promise<{ ok: boolean }> {
  return call(`/live/stop`, {
    method: "POST",
    body: JSON.stringify({ eoa, strategyId }),
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

// ── Wallet-signed actions — the trustless path ──
//
// LOAD (put $ behind a trader), REMOVE (take $ back off), DROP (out of the
// book) can each be authorized by an EIP-191 signature from the owner wallet
// over the EXACT action, instead of by the session token alone. The server
// builds the message (the client never constructs the bytes it signs — same
// rule as sign-in), the wallet popup shows the human sentence ("LOAD $25.00
// INTO 0xab…"), and the server recovers the signer, requires it to BE the
// owner, enforces a 10-minute freshness window and single use, and appends a
// verifiable receipt. See api/src/copy_actions.rs.

export type SignedCopyAction = "load" | "remove" | "drop";

export interface CopyActionReceipt {
  action: SignedCopyAction;
  trader: string;
  amountUsd: number | null;
  wallet: string;
  beforeUsd: number | null;
  afterUsd: number | null;
  stoppedSession: boolean;
  timestamp: number;
  executedAt: number;
  signature: string;
  digest: string;
}

/** True when a browser wallet is there to sign — the precondition for the
    trustless path. Without one the desk falls back to token-authorized writes
    (and says so in the confirm). */
export function canSignActions(): boolean {
  return typeof window !== "undefined" && !!(window as { ethereum?: unknown }).ethereum;
}

/** Full signed flow: challenge → personal_sign → execute. Wallet errors come
    out in the desk's vocabulary (Reject → WalletDeclinedError, from
    lib/walletConfirm.ts); returns the receipt and the fresh book. */
export async function signedCopyAction(
  action: SignedCopyAction,
  trader: string,
  amountUsd: number | null,
  eoa: string,
): Promise<{ ok: boolean; receipt: CopyActionReceipt; book: CopyBook }> {
  const ch = await call<{ message: string; timestamp: number }>(`/copy/signed/challenge`, {
    method: "POST",
    body: JSON.stringify({ action, trader, amountUsd, eoa }),
  });
  const { signAsOwner } = await import("./walletConfirm");
  const { signature } = await signAsOwner(ch.message);
  return call(`/copy/signed/execute`, {
    method: "POST",
    body: JSON.stringify({
      action, trader, amountUsd, eoa,
      timestamp: ch.timestamp, signature,
    }),
  });
}

/** The audit trail — every signed action with its signature, verifiable by
    anyone who rebuilds the message and recovers the signer. */
export function fetchCopyReceipts(
  eoa?: string | null,
): Promise<{ receipts: CopyActionReceipt[]; count: number }> {
  return call(`/copy/signed/receipts${eoaQuery(eoa)}`);
}

// ── Derived reads the desk uses in more than one place ──

/** Realized P&L for a row, or null when the leader has never been run.
    Realized only: open positions are marked, not booked, and a mark reads
    high because leaders sell winners and let losers expire. */
export function realizedPnl(row: CopyBookRow): number | null {
  return row.live?.ledger ? row.live.ledger.realized : null;
}

/** What this row actually KEPT: realized P&L minus the taker fees it paid to
    get it. `realizedPnl` above is the gross — the two differ by real money, so
    anything reporting performance should use this one. */
export function netRealizedPnl(row: CopyBookRow): number | null {
  const l = row.live?.ledger;
  return l ? l.realized - (l.fees ?? 0) : null;
}

/** Why a running session might not be trading, in the order worth checking.
    Returns null when there's nothing to warn about. Every string here was a
    real support question first. */
export function stallReason(row: CopyBookRow): string | null {
  const live = row.live;
  if (!live || !live.running) return null;
  if (live.error) return live.error;
  if (!live.autoExecute) {
    return "PAPER — computing every mirror, placing none. Flip the switch to REAL to send them.";
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
