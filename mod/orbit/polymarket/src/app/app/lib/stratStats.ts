// Per-strat money-in + live performance. The backend live engine tags every
// open position with the strat that bought it and keeps a per-strat realized
// ledger (stratStats) in its persisted state — /live/status serves both even
// when the engine is stopped. This hook joins that with the deposit wallet's
// live positions feed (current prices) into one map the strat picker and the
// cards browser can render: stratId → { moneyIn, unrealized, realized, … }.

import { useEffect, useRef, useState } from "react";
import { fetchPositions } from "./polymarket";
import { useAuth } from "../context/AuthContext";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";

interface EnginePosition {
  tokenId: string;
  size: number;
  entryPrice: number;
  strategyId?: string;
  openedAt?: number;
}

interface RealizedEvent {
  t: number;
  strategyId: string;
  pnl: number;
  basis: number;
}

interface EngineStratLedger {
  realized?: number;
  volume?: number;
  buys?: number;
  sells?: number;
  redeems?: number;
  lastFillAt?: number;
}

interface LiveStatusResponse {
  running?: boolean;
  state?: {
    balance?: number | null;
    positions?: Record<string, EnginePosition>;
    stratStats?: Record<string, EngineStratLedger>;
    realizedEvents?: RealizedEvent[];
  };
}

export interface StratMoney {
  /** Open cost basis the strat currently has deployed (USDC). */
  moneyIn: number;
  /** Those open positions marked to current prices (entry when unpriced). */
  openValue: number;
  unrealized: number;
  /** Realized PnL from the engine's per-strat ledger (sells + redeems). */
  realized: number;
  totalPnl: number;
  /** totalPnl vs open cost basis; null when nothing is deployed. */
  pnlPct: number | null;
  /** Last-24h PnL: realized fills in the window + unrealized on positions
      opened inside it (engine's rolling realizedEvents feed). */
  pnl24h: number;
  /** pnl24h vs the cost basis those fills/positions touched; null when the
      strat moved no money in the window. */
  roi24h: number | null;
  fills: number;
  openPositions: number;
  lastFillAt: number;
}

export interface StratStatsResult {
  stats: Record<string, StratMoney>;
  /** Deposit wallet's USDC cash — on-chain read via /deposit-wallet/info,
      engine-reported balance as fallback; null until known (render as
      "unknown", never $0). */
  cash: number | null;
}

const num = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

/** Poll per-strat money/performance + wallet cash for the signed-in EOA.
    Returns an empty map / null cash until the first successful read (and for
    users with no engine data). */
export function useStratStats(pollMs = 30_000): StratStatsResult {
  const { auth } = useAuth();
  const address = auth.address;
  const [result, setResult] = useState<StratStatsResult>({ stats: {}, cash: null });
  // Deposit wallet is CREATE2-stable per EOA — resolve once and reuse.
  const walletRef = useRef<{ eoa: string; wallet: string } | null>(null);

  useEffect(() => {
    if (!address) { setResult({ stats: {}, cash: null }); return; }
    let cancelled = false;

    const poll = async () => {
      try {
        // Wallet cash = the deposit wallet's on-chain USDC read, straight from
        // /deposit-wallet/info. The engine's state.balance is a fallback only —
        // it is null on fresh/stopped engines, and a null must render as
        // "unknown", never as $0 (the phantom-$0 bug).
        let cash: number | null = null;
        try {
          const r = await fetch(
            `${API_URL}/deposit-wallet/info?eoa=${encodeURIComponent(address)}`,
            { cache: "no-store" },
          );
          if (r.ok) {
            const info = (await r.json()) as {
              depositWallet?: string;
              /** Stringified base units (1e6 = $1); null = RPC read failed. */
              usdcBalance?: string | null;
            };
            if (info.depositWallet) walletRef.current = { eoa: address, wallet: info.depositWallet };
            if (info.usdcBalance != null) {
              const n = Number(info.usdcBalance);
              if (Number.isFinite(n)) cash = n / 1e6;
            }
          }
        } catch { /* fall through to engine balance */ }

        const res = await fetch(`${API_URL}/live/status?eoa=${encodeURIComponent(address)}`);
        if (!res.ok) {
          // Still publish the fresh cash read; keep the last stats snapshot.
          if (!cancelled) setResult((prev) => ({ stats: prev.stats, cash }));
          return;
        }
        const j = (await res.json()) as LiveStatusResponse;
        if (cash === null && typeof j.state?.balance === "number") cash = j.state.balance;
        const positions = Object.values(j.state?.positions ?? {});
        const ledger = j.state?.stratStats ?? {};
        if (positions.length === 0 && Object.keys(ledger).length === 0) {
          if (!cancelled) setResult({ stats: {}, cash });
          return;
        }

        // Current prices for open positions, via the deposit wallet's live
        // positions feed. Best-effort: unpriced tokens mark at entry.
        const price = new Map<string, number>();
        if (positions.length > 0) {
          try {
            if (walletRef.current?.eoa !== address) {
              const r = await fetch(
                `${API_URL}/deposit-wallet/info?eoa=${encodeURIComponent(address)}`,
                { cache: "no-store" },
              );
              if (r.ok) {
                const info = (await r.json()) as { depositWallet?: string };
                if (info.depositWallet) walletRef.current = { eoa: address, wallet: info.depositWallet };
              }
            }
            const wallet = walletRef.current?.eoa === address ? walletRef.current.wallet : null;
            if (wallet) {
              const live = await fetchPositions(wallet, { bypassCache: true });
              for (const p of live) if (p.tokenId) price.set(p.tokenId, p.currentPrice);
            }
          } catch { /* mark at entry */ }
        }

        const next: Record<string, StratMoney> = {};
        const entryFor = (id: string): StratMoney =>
          (next[id] ??= {
            moneyIn: 0, openValue: 0, unrealized: 0, realized: 0,
            totalPnl: 0, pnlPct: null, pnl24h: 0, roi24h: null,
            fills: 0, openPositions: 0, lastFillAt: 0,
          });

        // 24h window: realized fills inside it + unrealized on positions
        // opened inside it, each with the cost basis they touched so ROI is
        // return-on-capital-moved rather than vs a stale total.
        const cutoff = Date.now() - 24 * 3600 * 1000;
        const basis24h: Record<string, number> = {};

        for (const p of positions) {
          const id = p.strategyId || "unassigned";
          const s = entryFor(id);
          const cost = num(p.size) * num(p.entryPrice);
          const cur = price.get(p.tokenId);
          const value = num(p.size) * (cur !== undefined ? cur : num(p.entryPrice));
          s.moneyIn += cost;
          s.openValue += value;
          s.openPositions += 1;
          if (num(p.openedAt) >= cutoff) {
            s.pnl24h += value - cost;
            basis24h[id] = (basis24h[id] ?? 0) + cost;
          }
        }
        for (const [id, l] of Object.entries(ledger)) {
          const s = entryFor(id);
          s.realized += num(l.realized);
          s.fills += num(l.buys) + num(l.sells) + num(l.redeems);
          s.lastFillAt = Math.max(s.lastFillAt, num(l.lastFillAt));
        }
        for (const ev of j.state?.realizedEvents ?? []) {
          if (num(ev.t) < cutoff) continue;
          const id = ev.strategyId || "unassigned";
          const s = entryFor(id);
          s.pnl24h += num(ev.pnl);
          basis24h[id] = (basis24h[id] ?? 0) + num(ev.basis);
        }
        for (const [id, s] of Object.entries(next)) {
          s.unrealized = s.openValue - s.moneyIn;
          s.totalPnl = s.realized + s.unrealized;
          s.pnlPct = s.moneyIn > 0 ? (s.totalPnl / s.moneyIn) * 100 : null;
          const b = basis24h[id] ?? 0;
          s.roi24h = b > 0 ? (s.pnl24h / b) * 100 : null;
        }
        if (!cancelled) setResult({ stats: next, cash });
      } catch { /* transient — keep last snapshot */ }
    };

    void poll();
    const t = setInterval(poll, pollMs);
    return () => { cancelled = true; clearInterval(t); };
  }, [address, pollMs]);

  return result;
}

/** Compact "$12.50 in · +$1.20" formatting shared by picker + cards. */
export function fmtUsd(n: number): string {
  const abs = Math.abs(n);
  const s = abs >= 100 ? abs.toFixed(0) : abs.toFixed(2);
  return `${n < 0 ? "-" : ""}$${s}`;
}
