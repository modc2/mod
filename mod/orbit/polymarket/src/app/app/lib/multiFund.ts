// DEPOSIT — funding SEVERAL strats in one action.
//
// Funding a strat has never been a transfer: every strat on a wallet trades
// through the SAME deposit wallet, and a strat's `capital` is the budget its
// engine session sizes against (live_engine.rs budgets each session against
// its own allocation minus the positions it opened). So "deposit $50 into
// FILTER and $50 into BTC SHARPS" is two allocations plus two `/live/start`
// calls against one pot of USDC — which is exactly why it was worth making a
// single screen: done one strat at a time through the LIVE tab, nothing ever
// showed the user the SUM they were committing against the money they have.
//
// Two kinds of row can be funded here, because the console has two places a
// dollar amount lives:
//
//   • STRAT   — a SavedIndex from the strat store. Persist `capital`, then
//               `/live/start` at that number.
//   • COPY    — a COPY DESK allocation (api/src/copy.rs), keyed by the leader's
//               address rather than by a strat id. `/copy/allocations` then
//               `/copy/start` for that one leader.
//
// Both end up as an engine session keyed (eoa, strategyId); the desk's ids are
// `copy-<address>` (see identityStrat.ts). Nothing here invents a third
// funding path — it drives the two that already exist, in a loop, and reports
// each result separately so a partial success reads as one.
//
// AUTO-EXECUTE: an amount above $0 means REAL orders, matching what GO LIVE in
// the LIVE panel does with a funded wallet. This is the module's most
// consequential line — the module memo for this console records "no trades" as
// the #1 support question and a silently-dry-run session as its #1 cause.

import { updateIndex } from "./indexStore";
import { startLiveSessionDetailed, stopLiveSession } from "./liveSessions";
import { startCopying, stopCopying, upsertAllocation } from "./copyBook";
import type { SavedIndex } from "./types";

/** One fundable line: a saved strat, or a leader in the COPY DESK's book. */
export interface FundRow {
  /** Strat id, or the desk's `copy-<address>` — the engine session key either way. */
  id: string;
  name: string;
  kind: "strat" | "copy";
  /** kind === "strat" */
  strat?: SavedIndex;
  /** kind === "copy" — the leader being copied. */
  address?: string;
  /** Allocation the engine is sizing against right now (USD). */
  allocated: number;
  /** Cost basis this strat currently holds in open positions (engine ledger).
      Real money, unlike `allocated`, which is an intention. */
  deployed: number;
  /** Engine running for this row right now. */
  running: boolean;
}

export interface DepositOutcome {
  id: string;
  name: string;
  amountUsd: number;
  ok: boolean;
  error?: string;
}

/** Round to whole cents — the CLOB prices in cents and a budget that carries
    float dust prints as $33.333333333333336. */
export function usd(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Split `totalUsd` into `n` cent-exact parts that sum back to the total.
    Remainder cents go to the earliest rows, the same rule
    `equalWeightTraders` uses for weights. */
export function evenSplit(totalUsd: number, n: number): number[] {
  if (n <= 0) return [];
  const cents = Math.max(0, Math.round(totalUsd * 100));
  const base = Math.floor(cents / n);
  const extra = cents - base * n;
  return Array.from({ length: n }, (_, i) => (base + (i < extra ? 1 : 0)) / 100);
}

/** What this deposit may commit, in dollars.
 *
 *  Free wallet USDC PLUS the cost basis the selected rows already hold: money
 *  a strat has in open positions is that strat's, so re-arming a funded strat
 *  at its current size must not read as over-allocating. Every other running
 *  strat's money is deliberately NOT in the budget — it is committed to them.
 *
 *  null when the wallet balance is unknown (gate closed, RPC down): the panel
 *  then warns instead of blocking, because "unknown" is not "zero" — the same
 *  rule the sidebar's cash readout follows.
 */
export function fundingBudget(cash: number | null, selected: FundRow[]): number | null {
  if (cash === null) return null;
  return usd(selected.reduce((sum, r) => sum + Math.max(0, r.deployed), cash));
}

/** Fund one row: persist the allocation, then start (or reconfigure) its
    engine session at that number. */
async function depositOne(eoa: string, row: FundRow, amountUsd: number): Promise<DepositOutcome> {
  const base = { id: row.id, name: row.name, amountUsd };
  try {
    if (row.kind === "copy") {
      const address = row.address;
      if (!address) return { ...base, ok: false, error: "row has no leader address" };
      await upsertAllocation({ address, allocationUsd: amountUsd, enabled: true }, eoa);
      // Per-leader start: omitting the address would (re)start the WHOLE desk,
      // including leaders this deposit never touched.
      await startCopying(eoa, { address, autoExecute: amountUsd > 0 });
      return { ...base, ok: true };
    }

    const strat = row.strat;
    if (!strat) return { ...base, ok: false, error: "row has no strat" };
    // The typed number is the user's allocation whether or not the engine
    // accepts it — persist it first so a refused start still leaves the strat
    // carrying the size they asked for (it is also the BACKTEST tab's sim
    // capital). `liveEnabled` is only set once the engine says yes: the flag
    // is what every other surface reads as "this is armed".
    updateIndex(strat.id, { capital: amountUsd, updatedAt: Date.now() });
    const res = await startLiveSessionDetailed(eoa, { ...strat, capital: amountUsd }, amountUsd);
    if (res.ok) updateIndex(strat.id, { liveEnabled: true, updatedAt: Date.now() });
    return { ...base, ok: res.ok, error: res.error };
  } catch (e) {
    return { ...base, ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export interface AdjustOutcome {
  ok: boolean;
  /** The allocation the row carries after the adjustment (USD). */
  allocated: number;
  /** True when the adjustment took the session down (withdrew to $0). */
  stopped?: boolean;
  error?: string;
}

/** Move one row's allocation by `deltaUsd` — positive is a DEPOSIT, negative a
 *  WITHDRAW. Built for the single-trader case ("put $50 behind this trader /
 *  take $50 back out"), but the mechanics are row-kind generic.
 *
 *  The two verbs are deliberately NOT symmetric:
 *
 *   • DEPOSIT re-arms at the new size with REAL orders, exactly like
 *     `depositOne` — funding is arming, and a deposit that silently left the
 *     session in DRY RUN is this console's #1 support question.
 *   • WITHDRAW only ever shrinks the budget. It preserves a running session's
 *     execution mode (`inheritExecution` / the desk's in-place reconfigure),
 *     never resumes a stopped one, and withdrawing the whole allocation stops
 *     the session — a $0 budget has nothing to size against.
 *
 *  Money already in open positions is untouched either way: the allocation is
 *  the engine's budget, not a transfer, so withdrawing below the deployed
 *  basis just stops new buys — cash frees as positions exit.
 */
export async function adjustFunding(
  eoa: string,
  row: FundRow,
  deltaUsd: number,
): Promise<AdjustOutcome> {
  const next = usd(Math.max(0, (row.allocated ?? 0) + deltaUsd));
  try {
    if (row.kind === "copy") {
      const address = row.address;
      if (!address) return { ok: false, allocated: row.allocated, error: "row has no leader address" };
      if (deltaUsd > 0) {
        await upsertAllocation({ address, allocationUsd: next, enabled: true }, eoa);
        await startCopying(eoa, { address, autoExecute: next > 0 });
        return finish({ ok: true, allocated: next });
      }
      // Withdraw: stop BEFORE persisting $0 so the engine never runs a cycle
      // against an empty budget; a partial withdraw reconfigures the running
      // session in place (upsert preserves its execution mode).
      const stopped = next <= 0 && row.running;
      if (stopped) await stopCopying(eoa, address);
      await upsertAllocation({ address, allocationUsd: next }, eoa);
      return finish({ ok: true, allocated: next, stopped });
    }

    const strat = row.strat;
    if (!strat) return { ok: false, allocated: row.allocated, error: "row has no strat" };
    // Persist first, same rule as depositOne: the typed adjustment is the
    // user's allocation whether or not the engine accepts the restart.
    updateIndex(strat.id, { capital: next, updatedAt: Date.now() });
    if (deltaUsd > 0) {
      const res = await startLiveSessionDetailed(eoa, { ...strat, capital: next }, next);
      if (res.ok) updateIndex(strat.id, { liveEnabled: true, updatedAt: Date.now() });
      return finish({ ok: res.ok, allocated: next, error: res.error });
    }
    if (next <= 0) {
      if (row.running) await stopLiveSession(eoa, strat.id);
      updateIndex(strat.id, { liveEnabled: false, updatedAt: Date.now() });
      return finish({ ok: true, allocated: 0, stopped: row.running });
    }
    if (row.running) {
      const res = await startLiveSessionDetailed(
        eoa,
        { ...strat, capital: next },
        next,
        { inheritExecution: true },
      );
      return finish({ ok: res.ok, allocated: next, error: res.error });
    }
    // Stopped strat: the smaller allocation is saved; nothing to reconfigure.
    return finish({ ok: true, allocated: next });
  } catch (e) {
    return { ok: false, allocated: row.allocated, error: e instanceof Error ? e.message : String(e) };
  }
}

/** Repaint every mounted consumer on the way out — same event depositInto fires. */
function finish(out: AdjustOutcome): AdjustOutcome {
  if (typeof window !== "undefined") window.dispatchEvent(new Event("strat-updated"));
  return out;
}

/** Fund every row in the plan, one after the other.
 *
 *  Sequential on purpose: each start spawns an engine that immediately walks
 *  its watchlist's trade history, and firing ten at once is how a wallet earns
 *  a 429 from the data-api before a single mirror is placed. Ten strats cost a
 *  few seconds; a rate-limited fleet costs the first cycle of every one.
 *
 *  Never throws — a row that fails comes back as `{ok: false, error}` so the
 *  panel can report "3 armed, 2 refused, here's why" rather than losing the
 *  successes to one exception.
 */
export async function depositInto(
  eoa: string,
  plan: { row: FundRow; amountUsd: number }[],
): Promise<DepositOutcome[]> {
  const out: DepositOutcome[] = [];
  for (const { row, amountUsd } of plan) {
    out.push(await depositOne(eoa, row, usd(amountUsd)));
  }
  // Repaint every mounted consumer (sidebar rows, hub cards, LIVE panel) —
  // the strat store changed under them.
  if (typeof window !== "undefined") window.dispatchEvent(new Event("strat-updated"));
  return out;
}
