"use client";

// MY POSITIONS — every position this account has EVER held, current and past,
// each with its own P&L:
//   OPEN   — live holdings, mark-to-market unrealized P&L (from /positions).
//   CLOSED — fully exited positions (sold and/or redeemed), realized P&L
//            computed upstream by the data-api /closed-positions endpoint.
// Open rows lead (biggest first), closed rows follow newest-first, with
// realized / unrealized / net totals pinned in the header so the account's
// full trading record is one glance.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import {
  fetchPositions,
  fetchClosedPositions,
  ClosedPosition,
} from "../lib/polymarket";
import { PolymarketPosition } from "../lib/types";

type Filter = "all" | "open" | "closed";

interface Row {
  key: string;
  status: "open" | "closed";
  market: string;
  outcome: string;
  size: number;        // open: current shares · closed: total shares bought
  avgPrice: number;    // average entry
  curPrice: number;    // open: live mark · closed: settlement/exit price
  value: number;       // open: mark-to-market value · closed: 0 (exited)
  pnl: number;         // open: unrealized · closed: realized
  redeemable: boolean;
  ts: number;          // closed: close time (ms) · open: 0
}

function fmtUsd(v: number): string {
  if (!Number.isFinite(v)) return "$0.00";
  return v >= 1000
    ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
    : `$${v.toFixed(2)}`;
}

function fmtWhen(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 90) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

const POLL_MS = 60_000;

export default function PositionsHistoryPanel() {
  const { auth } = useAuth();
  const [open, setOpen] = useState<PolymarketPosition[]>([]);
  const [closed, setClosed] = useState<ClosedPosition[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState<string | null>(null);

  const eoa = auth.address;

  const refresh = useCallback(async () => {
    if (!eoa) return;
    // Resolve the V2 deposit wallet — that's where trades live, not the EOA.
    let wallet: string | null = null;
    try {
      const r = await fetch(`/api/polymarket/deposit-wallet/info?eoa=${eoa}`, {
        cache: "no-store",
      });
      if (r.ok) wallet = ((await r.json()) as { depositWallet?: string }).depositWallet ?? null;
    } catch { /* handled below */ }
    if (!wallet) {
      setError("could not resolve deposit wallet");
      return;
    }
    setError(null);

    const [openRes, closedRes] = await Promise.all([
      fetchPositions(wallet, { bypassCache: true }).catch(() => null),
      fetchClosedPositions(wallet).catch(() => null),
    ]);
    // Only overwrite on a successful read — the data-api intermittently
    // returns errors/empties under load and a blip must not blank the record.
    if (openRes) setOpen(openRes);
    if (closedRes && closedRes.length > 0) setClosed(closedRes);
    if (openRes || closedRes) setLoaded(true);
  }, [eoa]);

  useEffect(() => {
    // Account switch → drop the previous account's rows immediately.
    setOpen([]);
    setClosed([]);
    setLoaded(false);
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const rows = useMemo<Row[]>(() => {
    const openRows: Row[] = open
      .filter((p) => p.size > 0)
      .map((p) => ({
        key: `o:${p.conditionId}:${p.outcome}`,
        status: "open" as const,
        market: p.market,
        outcome: p.outcome,
        size: p.size,
        avgPrice: p.avgPrice,
        curPrice: p.size > 0 ? p.value / p.size : p.currentPrice,
        value: p.value,
        pnl: p.pnlUsd,
        redeemable: p.redeemable,
        ts: 0,
      }))
      .sort((a, b) => b.value - a.value);
    const closedRows: Row[] = closed
      .map((p) => ({
        key: `c:${p.conditionId}:${p.outcome}:${p.timestamp}`,
        status: "closed" as const,
        market: p.market,
        outcome: p.outcome,
        size: p.totalBought,
        avgPrice: p.avgPrice,
        curPrice: p.curPrice,
        value: 0,
        pnl: p.realizedPnl,
        redeemable: false,
        ts: p.timestamp,
      }))
      .sort((a, b) => b.ts - a.ts);
    return [...openRows, ...closedRows];
  }, [open, closed]);

  const unrealized = useMemo(
    () => rows.reduce((s, r) => s + (r.status === "open" ? r.pnl : 0), 0),
    [rows],
  );
  const realized = useMemo(
    () => rows.reduce((s, r) => s + (r.status === "closed" ? r.pnl : 0), 0),
    [rows],
  );
  const openCount = rows.filter((r) => r.status === "open").length;
  const closedCount = rows.length - openCount;
  const net = realized + unrealized;

  const visible = rows.filter((r) => filter === "all" || r.status === filter);

  if (!auth.connected) return null;

  return (
    <div className="pixel-panel p-3 space-y-3">
      {/* Header: title + lifetime P&L totals + status filter */}
      <div className="flex items-center gap-x-4 gap-y-1.5 flex-wrap">
        <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-pixel-muted">
          My positions · current &amp; past
        </span>
        <div className="flex items-baseline gap-1.5" title="Realized P&L across every closed position (sold or redeemed)">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Realized</span>
          <span className={`text-[13px] font-mono font-semibold ${realized >= 0 ? "text-green-400" : "text-red-400"}`}>
            {realized >= 0 ? "+" : "-"}{fmtUsd(Math.abs(realized))}
          </span>
        </div>
        <div className="flex items-baseline gap-1.5" title="Unrealized P&L across open positions (mark-to-market vs avg entry)">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Unrealized</span>
          <span className={`text-[13px] font-mono ${unrealized >= 0 ? "text-green-400" : "text-red-400"}`}>
            {unrealized >= 0 ? "+" : "-"}{fmtUsd(Math.abs(unrealized))}
          </span>
        </div>
        <div className="flex items-baseline gap-1.5" title="Realized + unrealized">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Net</span>
          <span className={`text-[13px] font-mono font-semibold ${net >= 0 ? "text-green-400" : "text-red-400"}`}>
            {net >= 0 ? "+" : "-"}{fmtUsd(Math.abs(net))}
          </span>
        </div>
        <div className="ml-auto flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          {([
            ["all", `ALL ${rows.length}`],
            ["open", `OPEN ${openCount}`],
            ["closed", `CLOSED ${closedCount}`],
          ] as [Filter, string][]).map(([f, label]) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
                filter === f ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="bg-pixel-bg border border-pixel-border rounded-[var(--radius)] overflow-hidden">
        <div className="grid grid-cols-[52px_minmax(0,1fr)_48px_84px_64px_100px_64px] items-center gap-x-3 px-2 py-1 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-pixel-muted border-b border-pixel-border/40">
          <span>Status</span>
          <span>Market</span>
          <span className="text-right" title="Open: shares held · Closed: total shares bought">Size</span>
          <span className="text-right" title="Average entry price → current mark (open) / settlement price (closed)">Avg→Cur</span>
          <span className="text-right" title="Mark-to-market value (open positions)">Value</span>
          <span className="text-right" title="Open: unrealized · Closed: realized">P&amp;L</span>
          <span className="text-right">When</span>
        </div>
        <div className="max-h-[320px] overflow-y-auto">
          {visible.length === 0 && (
            <div className="px-2 py-4 text-center text-xs text-pixel-muted">
              {loaded
                ? "No positions yet — this account hasn't traded."
                : "Loading positions…"}
            </div>
          )}
          {visible.map((r) => {
            const basis = r.avgPrice * r.size;
            const pct = basis > 0 ? (r.pnl / basis) * 100 : 0;
            return (
              <div
                key={r.key}
                className="grid grid-cols-[52px_minmax(0,1fr)_48px_84px_64px_100px_64px] items-center gap-x-3 px-2 py-1 text-[11px] font-mono border-b border-pixel-border/30 last:border-b-0 hover:bg-pixel-white/[0.03]"
              >
                <span>
                  {r.status === "open" ? (
                    <span className="text-[9px] px-1 py-px rounded-full border border-amber-400/50 text-amber-400 font-sans font-semibold tracking-wide">
                      OPEN
                    </span>
                  ) : (
                    <span
                      className={`text-[9px] px-1 py-px rounded-full border font-sans font-semibold tracking-wide ${
                        r.pnl >= 0
                          ? "border-green-400/40 text-green-400/90"
                          : "border-red-400/40 text-red-400/90"
                      }`}
                      title={r.curPrice >= 0.999 ? "Resolved in your favor / fully exited" : r.curPrice <= 0.001 ? "Resolved against you / fully exited" : "Fully exited"}
                    >
                      {r.pnl >= 0 ? "WON" : "LOST"}
                    </span>
                  )}
                </span>
                <span className="truncate min-w-0 text-pixel-white" title={`${r.market} · ${r.outcome}`}>
                  {r.market}
                  <span className={r.outcome.toLowerCase() === "no" ? "text-red-400/80" : "text-green-400/80"}> · {r.outcome}</span>
                  {r.redeemable && (
                    <span className="ml-1.5 text-[9px] px-1 py-px rounded-full border border-amber-400/50 text-amber-400 font-sans font-semibold tracking-wide" title="Market resolved — cash out via REDEEM">
                      REDEEM
                    </span>
                  )}
                </span>
                <span className="text-right text-pixel-muted">{r.size.toFixed(1)}</span>
                <span className="text-right text-pixel-muted">
                  {Math.round(r.avgPrice * 100)}¢→{Math.round(r.curPrice * 100)}¢
                </span>
                <span className="text-right text-pixel-white">
                  {r.status === "open" ? fmtUsd(r.value) : "—"}
                </span>
                <span className={`text-right ${r.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {r.pnl >= 0 ? "+" : "-"}${Math.abs(r.pnl).toFixed(2)}
                  <span className="opacity-70"> {pct >= 0 ? "+" : ""}{pct.toFixed(0)}%</span>
                </span>
                <span className="text-right text-pixel-muted text-[10px]">
                  {r.status === "open" ? "live" : fmtWhen(r.ts)}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {error && (
        <div className="text-[10px] text-red-400/70 font-mono break-all">{error}</div>
      )}
    </div>
  );
}
