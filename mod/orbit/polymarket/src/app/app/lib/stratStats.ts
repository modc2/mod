// Per-strat money-in + live performance. The backend live engine tags every
// open position with the strat that bought it and keeps a per-strat realized
// ledger (stratStats) in its persisted state — /live/sessions serves that for
// EVERY session the wallet has, running or stopped. This hook merges them and
// joins the result with the deposit wallet's live positions feed (current
// prices) into one map the strat picker and the cards browser can render:
// stratId → { moneyIn, unrealized, realized, … }.
//
// Merging across sessions is what makes several concurrently-funded strats
// add up: each runs its own engine, so each holds only its own slice.

import { useEffect, useRef, useState } from "react";
import { fetchPositions } from "./polymarket";
import { useAuth } from "../context/AuthContext";
import { fetchLiveSessions, runningStrategyIds, type SessionStratLedger } from "./liveSessions";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";

export interface StratMoney {
  /** Open cost basis the strat currently has deployed (USDC). */
  moneyIn: number;
  /** Those open positions marked to current prices (entry when unpriced). */
  openValue: number;
  unrealized: number;
  /** Realized PnL from the engine's per-strat ledger (sells + redeems),
      GROSS — before `fees`. */
  realized: number;
  /** Polymarket taker fees this strat has paid. Real money, and the reason
      `totalPnl` is not `realized + unrealized`: it is
      `realized - fees + unrealized`. */
  fees: number;
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
  /** Strat ids with a RUNNING backend engine right now. Several strats can be
      funded and live at the same time. */
  running: Set<string>;
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
  const [result, setResult] = useState<StratStatsResult>({ stats: {}, cash: null, running: new Set() });
  // Deposit wallet is CREATE2-stable per EOA — resolve once and reuse.
  const walletRef = useRef<{ eoa: string; wallet: string } | null>(null);

  useEffect(() => {
    if (!address) { setResult({ stats: {}, cash: null, running: new Set() }); return; }
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

        // Every session the wallet has, not just one: several strats can be
        // funded and running at once, each with its own engine state. Merging
        // them is what makes the sidebar's per-strat money add up.
        const sessions = await fetchLiveSessions(address);
        const running = runningStrategyIds(sessions);
        if (cash === null) {
          const bal = sessions.map((s) => s.state?.balance).find((b) => typeof b === "number");
          if (typeof bal === "number") cash = bal;
        }
        // A session IS one strat (the registry is keyed (eoa, strategyId)), so
        // its positions belong to it even when the position record carries no
        // tag — positions opened before per-fill tagging, or by a code path
        // that forgot it, would otherwise vanish into "unassigned" and the
        // strat would render as holding nothing while it holds money.
        const positions = sessions.flatMap((s) =>
          Object.values(s.state?.positions ?? {}).map((p) => ({
            ...p,
            strategyId: p.strategyId || s.strategyId || s.config?.strategyId,
          })),
        );
        const ledger: Record<string, SessionStratLedger[]> = {};
        for (const s of sessions) {
          const own = s.strategyId || s.config?.strategyId;
          for (const [id, l] of Object.entries(s.state?.stratStats ?? {})) {
            // Same reasoning as positions: an untagged ledger inside a
            // session is that session's strat.
            const key = id === "unassigned" && own ? own : id;
            (ledger[key] ??= []).push(l);
          }
        }
        const realizedEvents = sessions.flatMap((s) =>
          (s.state?.realizedEvents ?? []).map((e) => ({
            ...e,
            strategyId: e.strategyId || s.strategyId || s.config?.strategyId || "",
          })),
        );
        if (positions.length === 0 && Object.keys(ledger).length === 0) {
          if (!cancelled) setResult({ stats: {}, cash, running });
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
            moneyIn: 0, openValue: 0, unrealized: 0, realized: 0, fees: 0,
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
        for (const [id, ledgers] of Object.entries(ledger)) {
          const s = entryFor(id);
          for (const l of ledgers) {
            s.realized += num(l.realized);
            s.fees += num(l.fees);
            s.fills += num(l.buys) + num(l.sells) + num(l.redeems);
            s.lastFillAt = Math.max(s.lastFillAt, num(l.lastFillAt));
          }
        }
        for (const ev of realizedEvents) {
          if (num(ev.t) < cutoff) continue;
          const id = ev.strategyId || "unassigned";
          const s = entryFor(id);
          s.pnl24h += num(ev.pnl);
          basis24h[id] = (basis24h[id] ?? 0) + num(ev.basis);
        }
        for (const [id, s] of Object.entries(next)) {
          s.unrealized = s.openValue - s.moneyIn;
          // Fees are money that left the wallet — the strat's P&L is what it
          // won MINUS what it paid Polymarket to win it.
          s.totalPnl = s.realized - s.fees + s.unrealized;
          s.pnlPct = s.moneyIn > 0 ? (s.totalPnl / s.moneyIn) * 100 : null;
          const b = basis24h[id] ?? 0;
          s.roi24h = b > 0 ? (s.pnl24h / b) * 100 : null;
        }
        if (!cancelled) setResult({ stats: next, cash, running });
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
