// STRAT AUTOPSY — the live-performance ground truth the strat chat reasons over.
//
// The chat's problem answering "why did this lose money?" was never the model,
// it was the evidence: the live context said "cycles: 40,000, orders: 1,600"
// and nothing about outcomes. The two sources that DO know the outcome
// disagree with each other in exactly the way that matters:
//
//   • the engine's stratStats ledger books realized P&L at sell/redeem fills —
//     a position that expires worthless produces NO fill, so a strat bleeding
//     out on 5-minute candles can show a positive ledger while the wallet
//     drains (the movoaev8/mrjg86gf account showed +$19 on a wallet down $870);
//   • the data-api's /closed-positions is settlement truth — every fully
//     exited position with realized P&L computed upstream — but it is
//     WALLET-wide, and several strats share one deposit wallet.
//
// So the autopsy hands the model both, labeled as what they are, plus the
// decompositions a person would actually compute by hand: P&L by entry-price
// band, by market class (sub-hour Up/Down candles vs everything else), the
// monthly trend, the worst individual positions, and the strat's own config
// red flags (no duration gate, no stop, single-order size vs capital).
//
// Closed positions are ~30 upstream pages for a busy wallet, so the fetch is
// disk-cached (stateDir()/autopsy-cache.json, 15-minute TTL) and stale cache
// beats no data when the data-api rate-limits a refresh.

import { readFileSync } from "fs";
import { join } from "path";

import {
  API_BASE, fetchClosedPositions, serverAuthHeaders, setServerAuthToken,
  type ClosedPosition,
} from "../polymarket";
import { writeAtomic } from "./feedStore";
import { mintOwnerToken, ownerAddress, stateDir } from "./ownerToken";

const CACHE_TTL_MS = 15 * 60 * 1000;

const cachePath = () => join(stateDir(), "autopsy-cache.json");

interface CacheFile {
  v: 1;
  wallet: string;
  at: number;
  positions: ClosedPosition[];
}

const num = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

// ── engine session, the pieces the autopsy reads ────────────────

interface RawSession {
  running?: boolean;
  config?: {
    strategyId?: string;
    capital?: number;
    minMinutesToClose?: number | null;
    stopLoss?: number | null;
    maxOrderSize?: number | null;
    intervalMs?: number;
    autoExecute?: boolean;
    traders?: Array<{ enabled?: boolean }>;
  };
  state?: {
    totalOrdersFailed?: number;
    stratStats?: Record<string, {
      realized?: number; fees?: number; buys?: number; sells?: number; redeems?: number;
    }>;
  };
}

async function fetchSessions(eoa: string): Promise<RawSession[]> {
  const res = await fetch(`${API_BASE}/live/sessions?eoa=${encodeURIComponent(eoa)}`, {
    headers: serverAuthHeaders(),
  });
  if (!res.ok) throw new Error(`/live/sessions HTTP ${res.status}`);
  const j = (await res.json()) as { sessions?: RawSession[] };
  return Array.isArray(j.sessions) ? j.sessions : [];
}

async function fetchDepositWallet(eoa: string): Promise<{ wallet: string; usdc: number } | null> {
  const res = await fetch(`${API_BASE}/deposit-wallet/info?eoa=${encodeURIComponent(eoa)}`, {
    headers: serverAuthHeaders(),
  });
  if (!res.ok) return null;
  const j = (await res.json()) as { depositWallet?: string; usdcBalance?: string };
  return j.depositWallet ? { wallet: j.depositWallet, usdc: num(j.usdcBalance) / 1e6 } : null;
}

/** Closed positions for the wallet, through the disk cache. A failed refresh
    serves whatever the cache holds, however old — for a postmortem, last
    hour's truth still beats none. */
async function closedPositions(wallet: string): Promise<ClosedPosition[] | null> {
  let cached: CacheFile | null = null;
  try {
    const raw = JSON.parse(readFileSync(cachePath(), "utf8")) as CacheFile;
    if (raw.wallet === wallet && Array.isArray(raw.positions)) cached = raw;
  } catch {
    // no cache yet
  }
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.positions;
  try {
    const fresh = await fetchClosedPositions(wallet);
    writeAtomic(cachePath(), JSON.stringify({ v: 1, wallet, at: Date.now(), positions: fresh }));
    return fresh;
  } catch {
    return cached ? cached.positions : null;
  }
}

// ── the decomposition ───────────────────────────────────────────

/** Sub-hour candle series ("Bitcoin Up or Down - September 10, 1:30AM-1:45AM").
    The class that loses to copy latency by construction. */
const isCandle = (p: ClosedPosition): boolean => /up or down/i.test(p.market);

interface Bucket { n: number; pnl: number; wins: number; cost: number }

function bucket(ps: ClosedPosition[]): Bucket {
  const b: Bucket = { n: 0, pnl: 0, wins: 0, cost: 0 };
  for (const p of ps) {
    b.n += 1;
    b.pnl += p.realizedPnl;
    if (p.realizedPnl > 0) b.wins += 1;
    b.cost += p.avgPrice * p.totalBought;
  }
  return b;
}

const money = (v: number): string => `${v < 0 ? "-" : "+"}$${Math.abs(v).toFixed(2)}`;

function line(label: string, b: Bucket): string {
  if (b.n === 0) return "";
  const win = ((100 * b.wins) / b.n).toFixed(0);
  return `${label}: ${b.n} positions, ${money(b.pnl)} realized, ${win}% won, $${b.cost.toFixed(0)} deployed.`;
}

function decompose(ps: ClosedPosition[]): string[] {
  const all = bucket(ps);
  const out = [line(`Whole wallet, all strats, full history`, all)];

  out.push(line(`  of that, sub-hour Up/Down candle markets`, bucket(ps.filter(isCandle))));
  out.push(line(`  everything else`, bucket(ps.filter((p) => !isCandle(p)))));

  // The two bands that tell the latency story: entries under 15¢ are mirrors
  // of an already-decided candle (the copied trader's edge was being early;
  // the copy arrives after the move), and 40–60¢ is the coin-flip band where
  // P&L ≈ −(spread + slippage + fees) no matter who you copy.
  out.push(line(`  entries under 15c (buying the already-decided side)`, bucket(ps.filter((p) => p.avgPrice < 0.15))));
  out.push(line(`  entries 40-60c (coin flips at fair value)`, bucket(ps.filter((p) => p.avgPrice >= 0.4 && p.avgPrice < 0.6))));

  // Trend: is it still bleeding, or did a fix land?
  const months = new Map<string, Bucket>();
  for (const p of ps) {
    const k = new Date(p.timestamp).toISOString().slice(0, 7);
    const b = months.get(k) ?? { n: 0, pnl: 0, wins: 0, cost: 0 };
    b.n += 1; b.pnl += p.realizedPnl; if (p.realizedPnl > 0) b.wins += 1;
    months.set(k, b);
  }
  const trend = [...months.entries()].sort().slice(-4)
    .map(([k, b]) => `${k} ${money(b.pnl)} over ${b.n}`).join("; ");
  if (trend) out.push(`By month: ${trend}.`);

  const worst = [...ps].sort((a, b) => a.realizedPnl - b.realizedPnl).slice(0, 3)
    .filter((p) => p.realizedPnl < 0)
    .map((p) => `${money(p.realizedPnl)} on "${p.market.slice(0, 60)}" (${p.totalBought.toFixed(0)} sh @ ${(p.avgPrice * 100).toFixed(0)}c)`);
  if (worst.length) out.push(`Worst single positions: ${worst.join(" · ")}.`);

  return out.filter(Boolean);
}

/** Config settings that are themselves findings. Stated as facts, not advice —
    the model decides what to make of them for the question actually asked. */
function redFlags(s: RawSession, hasCandleFlow: boolean): string[] {
  const c = s.config ?? {};
  const flags: string[] = [];
  const gate = num(c.minMinutesToClose);
  if (gate < 60 && hasCandleFlow) {
    flags.push(`minMinutesToClose is ${gate} — sub-hour candle markets are NOT filtered out, and the wallet's candle flow is where copy latency loses by construction (poll floor 30s vs 5-minute market lifetime).`);
  }
  if (num(c.stopLoss) === 0) flags.push(`stopLoss is 0 — losers are never cut, they ride to $0.`);
  const cap = num(c.capital);
  const maxOrd = num(c.maxOrderSize);
  if (cap > 0 && maxOrd >= cap * 0.4) {
    flags.push(`maxOrderSize $${maxOrd} is ${Math.round((100 * maxOrd) / cap)}% of $${cap} capital — one market can absorb near half the book (this wallet's worst single losses were ~$100 five-minute candles).`);
  }
  const failed = num(s.state?.totalOrdersFailed);
  if (failed > 500) flags.push(`${failed} failed orders this session — usually an empty wallet being retried forever.`);
  return flags;
}

/** Ledger vs settlement truth. The ledger only books P&L at a fill, so
    `buys − sells − redeems` positions closed without ever being booked. */
function ledgerLine(s: RawSession, stratId: string): string | undefined {
  const l = s.state?.stratStats?.[stratId];
  if (!l) return undefined;
  const buys = num(l.buys); const booked = num(l.sells) + num(l.redeems);
  const base = `This strat's engine ledger: ${money(num(l.realized))} realized after ${buys} buys / ${num(l.sells)} sells / ${num(l.redeems)} redeems, $${num(l.fees).toFixed(2)} fees.`;
  if (buys > booked * 2 && buys - booked > 50) {
    return `${base} CAUTION: ${buys - booked} more buys than exits were ever booked — positions that expire worthless produce no fill and no ledger entry, so this realized figure overstates reality. Trust the wallet-wide settlement numbers above for direction.`;
  }
  return base;
}

// ── the context block ───────────────────────────────────────────

/** The LIVE PERFORMANCE section of the strat-chat prompt, or undefined when
    the deployment has no owner/engine or nothing has ever traded. Never
    throws — a chat without an autopsy is degraded, not broken. */
export async function autopsyContext(stratId: string): Promise<string | undefined> {
  try {
    const eoa = ownerAddress();
    const token = mintOwnerToken();
    if (!eoa || !token) return undefined;
    setServerAuthToken(token);

    const [sessions, dep] = await Promise.all([fetchSessions(eoa), fetchDepositWallet(eoa)]);
    if (!dep) return undefined;
    const ps = await closedPositions(dep.wallet);
    if (!ps || ps.length === 0) return undefined;

    const mine = sessions.find((s) => s.config?.strategyId === stratId);
    const others = sessions.filter((s) => s.config?.strategyId !== stratId && s.running);

    const out: string[] = [
      `Settlement truth from the exchange (data-api closed positions). The deposit wallet is SHARED by every strat session, so wallet-wide numbers include ${others.length} other running session(s) — attribute with care, but the wallet does not lie about the total.`,
      ...decompose(ps),
      `Wallet cash right now: $${dep.usdc.toFixed(2)}.`,
    ];
    if (mine) {
      const ll = ledgerLine(mine, stratId);
      if (ll) out.push(ll);
      out.push(...redFlags(mine, ps.some(isCandle)));
    }
    return out.join("\n");
  } catch {
    return undefined;
  }
}
