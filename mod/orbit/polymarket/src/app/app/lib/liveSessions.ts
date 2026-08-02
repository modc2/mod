// Multi-strat live sessions — the client half of the backend's one-engine-
// per-(EOA, strat) registry (api/src/live_engine.rs `session_key`).
//
// A wallet can fund several strats at once: each session budgets against its
// OWN `capital` allocation minus the positions it opened, and tags every fill
// with its `strategyId`, so two strats trading the same deposit wallet keep
// separate books. `/live/sessions` returns all of them — running or merely
// persisted — which is what lets the strat sidebar render every funded strat
// side by side instead of assuming one live strat per wallet.
//
// Callers that act on ONE strat must pass its `strategyId`: `/live/stop` and
// `/live/execution` without it fall back to "the whole wallet", which would
// take the user's other funded strats down with it.

import { DEFAULT_STOP_LOSS, DEFAULT_TAKE_PROFIT, MIN_POLL_MINUTES } from "./strats/strat";
import type { SavedIndex } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";

/** Open position the engine tracks, tagged with the strat that bought it. */
export interface SessionPosition {
  tokenId: string;
  size: number;
  entryPrice: number;
  strategyId?: string;
  openedAt?: number;
}

export interface SessionStratLedger {
  realized?: number;
  volume?: number;
  buys?: number;
  sells?: number;
  redeems?: number;
  lastFillAt?: number;
}

export interface SessionRealizedEvent {
  t: number;
  strategyId: string;
  pnl: number;
  basis: number;
}

/** One `/live/sessions` entry — the same `{running, config, state}` envelope
    `/live/status` serves, plus the strat id it belongs to. */
export interface LiveSession {
  strategyId: string;
  running: boolean;
  config?: {
    strategyId?: string;
    capital?: number;
    autoExecute?: boolean;
    intervalMs?: number;
  };
  state?: {
    status?: string;
    balance?: number | null;
    /** Cash + mark value of the session's positions — what proportional
        copy sizing is a fraction OF (live_engine.rs `AccountValue`). */
    accountValue?: number | null;
    cycleCount?: number;
    totalOrdersPlaced?: number;
    nextCycleAt?: number | null;
    positions?: Record<string, SessionPosition>;
    stratStats?: Record<string, SessionStratLedger>;
    realizedEvents?: SessionRealizedEvent[];
  };
}

/** Each trader's bankroll (positions mark value + free USDC), keyed by
    lowercased address — the denominator proportional copy sizing divides by
    (`copyRatioFor`). Served from the engine's shared cache, so the backtest
    previews the exact ratio the live engine will size with. Missing entries
    (unreadable book) fall back to the volume model. */
export async function fetchTraderBankrolls(addresses: string[]): Promise<Map<string, number>> {
  const out = new Map<string, number>();
  const list = addresses.filter((a) => /^0x[0-9a-fA-F]{40}$/.test(a));
  if (list.length === 0) return out;
  try {
    const qs = encodeURIComponent(list.join(","));
    const res = await fetch(`${API_URL}/live/bankroll?addresses=${qs}`);
    if (!res.ok) return out;
    const j = (await res.json()) as { bankrolls?: Record<string, number> };
    for (const [addr, v] of Object.entries(j.bankrolls || {})) {
      if (Number.isFinite(v)) out.set(addr.toLowerCase(), v);
    }
  } catch {
    // Offline / gate closed — the caller's volume fallback covers it.
  }
  return out;
}

/** Every session this wallet has, running or persisted. Empty on any error —
    the console degrades to "nothing running", never to a thrown render. */
export async function fetchLiveSessions(eoa: string): Promise<LiveSession[]> {
  try {
    const res = await fetch(`${API_URL}/live/sessions?eoa=${encodeURIComponent(eoa)}`);
    if (!res.ok) return [];
    const j = (await res.json()) as { sessions?: LiveSession[] };
    return Array.isArray(j.sessions) ? j.sessions : [];
  } catch {
    return [];
  }
}

/** Stop ONE funded strat. Omitting `strategyId` stops every session on the
    wallet — only do that from an explicit "stop everything" control. */
export async function stopLiveSession(eoa: string, strategyId?: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/live/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(strategyId ? { eoa, strategyId } : { eoa }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/** Strat ids with a RUNNING engine right now. */
export function runningStrategyIds(sessions: LiveSession[]): Set<string> {
  return new Set(sessions.filter((s) => s.running).map((s) => s.strategyId));
}

/** Fund and start ONE strat's backend session at `capital` USDC.
 *
 *  This is the backend engine only — the durable one that survives tab close.
 *  The in-browser `CopyEngine` stays attached to whichever strat the LIVE tab
 *  is showing; a wallet's other funded strats run server-side. Params mirror
 *  LivePanel's start config so a strat trades the same whether it was armed
 *  from the LIVE tab or funded from the sidebar.
 *
 *  `autoExecute` follows the console's existing rule — an allocation above $0
 *  means real orders, $0 means nothing to trade with. Omitting the flag would
 *  strand the session in DRY RUN, which reads as "I funded it and it never
 *  traded".
 *
 *  `inheritExecution` drops the flag from the payload so the backend keeps the
 *  session's current mode (`live_start` treats an absent `autoExecute` as
 *  "unchanged"). Re-posting a config to APPLY AN EDIT must use it: a user who
 *  deliberately parked a funded session in DRY RUN would otherwise find it
 *  placing real orders just because they retimed the sync.
 */
export async function startLiveSession(
  eoa: string,
  strat: SavedIndex,
  capital: number,
  opts?: { inheritExecution?: boolean },
): Promise<boolean> {
  // Clamp to the engine's rate-limit floor here too — this path starts a
  // session without the LIVE panel, so nothing else would catch a strat
  // carrying a stale sub-30s cadence.
  const pollMin = Math.max(
    MIN_POLL_MINUTES,
    strat.livePollMinutes ?? strat.rebalanceMinutes ?? MIN_POLL_MINUTES,
  );
  const body = {
    eoa,
    strategyId: strat.id,
    address: eoa,
    traders: strat.traders
      .filter((t) => t.enabled !== false)
      .map((t) => ({ address: t.address, weight: t.weight ?? 1, enabled: true })),
    capital,
    intervalMs: Math.round(pollMin * 60_000),
    minOrderSize: strat.minTrade ?? 5,
    maxSlippageBps: 300,
    maxOpenPositions: strat.maxOpenPositions ?? 10,
    maxPerCycle: strat.maxPerCycle ?? 3,
    stopLoss: strat.stopLoss ?? DEFAULT_STOP_LOSS,
    takeProfit: strat.takeProfit ?? DEFAULT_TAKE_PROFIT,
    backtestDays: strat.backtestDays ?? 3,
    ...(opts?.inheritExecution ? {} : { autoExecute: capital > 0 }),
    ...(strat.maxTrade !== undefined && { maxOrderSize: strat.maxTrade }),
    // Proportional-copy fidelity + short-dated/stale mirror gates. Only sent
    // when the strat sets them — the engine's own defaults (2×, 60m, 300s)
    // are the source of truth otherwise.
    ...(strat.maxUpscale !== undefined && { maxUpscale: strat.maxUpscale }),
    ...(strat.minMinutesToClose !== undefined && { minMinutesToClose: strat.minMinutesToClose }),
    ...(strat.maxTradeAgeSec !== undefined && { maxTradeAgeSec: strat.maxTradeAgeSec }),
    ...(strat.marketQuery && { marketQuery: strat.marketQuery }),
    ...(strat.tradeFilters && { tradeFilters: strat.tradeFilters }),
    ...(strat.momentum && { momentum: strat.momentum }),
  };
  try {
    const res = await fetch(`${API_URL}/live/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}
