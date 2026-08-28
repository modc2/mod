"use client";

// MY POSITIONS — every position this account has EVER held, current and past,
// each with its own P&L:
//   OPEN   — live holdings, mark-to-market unrealized P&L (from /positions).
//   CLOSED — fully exited positions (sold and/or redeemed), realized P&L
//            computed upstream by the data-api /closed-positions endpoint.
//            Also includes DEAD rows still in /positions: markets resolved
//            against us (or 0¢ dust below the $1 CLOB sell floor) keep their
//            worthless tokens until redeem sweeps them, so the data-api
//            reports them as "open" — economically they are closed losses.
// Open rows lead (biggest first), closed rows follow newest-first, with
// realized / unrealized / net totals pinned in the header so the account's
// full trading record is one glance.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { getOwnerAddress } from "../lib/access";
import {
  fetchPositions,
  fetchClosedPositions,
  ClosedPosition,
} from "../lib/polymarket";
import { PolymarketPosition } from "../lib/types";

type Filter = "all" | "open" | "closed";
type Sort = "recent" | "worst" | "best";
type GroupBy = "entry" | "type";

// ── Leak analysis ───────────────────────────────────────────────────
//
// A per-position list answers "what happened"; it does NOT answer "where
// did the money go" — 1200 rows of ±$3 hide the handful of buckets that
// actually drained the account. These two groupings are the ones that
// have historically found the leak on this book:
//
//   ENTRY — P&L by price paid per share. Cheap longshots look harmless
//           per bet ($2–5) but resolve worthless ~99% of the time, so a
//           few hundred of them silently eat the bankroll. On this
//           account, sub-10¢ entries were 6% of stake and 87% of losses.
//   TYPE  — P&L by market family. Separates a structurally-losing family
//           (lagged copies of 5-minute candle markets, which close before
//           a mirror can ever be in front of the move) from families that
//           are merely noisy.
//
// Both are computed over CLOSED positions only: open marks are opinions,
// realized P&L is cash.

const ENTRY_BANDS: [string, number, number][] = [
  ["under 10¢", 0, 0.1],
  ["10–25¢", 0.1, 0.25],
  ["25–50¢", 0.25, 0.5],
  ["50–75¢", 0.5, 0.75],
  ["75–90¢", 0.75, 0.9],
  ["90¢+", 0.9, 1.01],
];

function entryBand(avgPrice: number): string {
  for (const [label, lo, hi] of ENTRY_BANDS) {
    if (avgPrice >= lo && avgPrice < hi) return label;
  }
  return "90¢+";
}

/// Market family from the title. The Up-or-Down split is by candle length:
/// the title carries the window ("3:30PM-3:35PM ET"), so the gap between
/// the two clock times separates 5-minute candles from hourly+ markets.
/// That distinction matters — sub-hour windows close faster than a copy
/// can land, so they lose structurally rather than from bad selection.
function marketType(title: string): string {
  if (/up or down/i.test(title)) {
    const m = title.match(/(\d{1,2}):(\d{2})\s*([AP]M)\s*-\s*(\d{1,2}):(\d{2})\s*([AP]M)/i);
    if (m) {
      const to24 = (h: string, mer: string) => {
        const hh = Number(h) % 12;
        return /pm/i.test(mer) ? hh + 12 : hh;
      };
      const a = to24(m[1], m[3]) * 60 + Number(m[2]);
      const b = to24(m[4], m[6]) * 60 + Number(m[5]);
      const gap = (b - a + 1440) % 1440;
      if (gap > 0 && gap <= 15) return "Up/Down ≤15min";
      return "Up/Down hourly+";
    }
    return "Up/Down hourly+";
  }
  if (/(LoL:|Counter-Strike|Valorant|Dota|CS2|Rainbow Six|Esports)/i.test(title)) return "Esports";
  if (/(price of|Bitcoin|Ethereum|Solana|XRP)/i.test(title)) return "Crypto price";
  if (/(\bvs\.?\b|Open:|Series|League|Cup|O\/U|Winner)/i.test(title)) return "Sports";
  return "Other";
}

interface Bucket {
  label: string;
  legs: number;
  staked: number;
  pnl: number;
  wins: number;
}

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
  ts: number;          // closed: close time (ms) · open + dead-unsettled: 0
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
  const [sort, setSort] = useState<Sort>("recent");
  const [groupBy, setGroupBy] = useState<GroupBy | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Prefer the signed-in owner (funded wallet) over the connected wallet.
  const eoa = getOwnerAddress() ?? auth.address;

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
    // A held position is DEAD (closed loss, not open) when its mark is ~0¢:
    // either the market resolved (redeemable) with our side at zero, or the
    // tokens are 0¢ dust worth less than the $1 CLOB minimum — unsellable
    // either way, so the loss is final even before redeem sweeps the tokens.
    const isDead = (p: PolymarketPosition, mark: number) =>
      p.redeemable ? mark < 0.05 : mark <= 0.005 && p.value < 1;

    const held = open
      .filter((p) => p.size > 0)
      .map((p) => {
        const mark = p.size > 0 ? p.value / p.size : p.currentPrice;
        const dead = isDead(p, mark);
        return {
          key: `o:${p.conditionId}:${p.outcome}`,
          status: dead ? ("closed" as const) : ("open" as const),
          market: p.market,
          outcome: p.outcome,
          size: p.size,
          avgPrice: p.avgPrice,
          curPrice: mark,
          value: p.value,
          pnl: p.pnlUsd,
          redeemable: !dead && p.redeemable,
          ts: 0,
        };
      });
    const openRows: Row[] = held
      .filter((r) => r.status === "open")
      .sort((a, b) => b.value - a.value);
    // Dead rows lead the closed section — they're the freshest outcomes even
    // though the data-api hasn't settled them into /closed-positions yet.
    const deadRows: Row[] = held
      .filter((r) => r.status === "closed")
      .sort((a, b) => a.pnl - b.pnl);
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
    return [...openRows, ...deadRows, ...closedRows];
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

  // Realized-only buckets — see the leak-analysis note above.
  const buckets = useMemo<Bucket[]>(() => {
    if (!groupBy) return [];
    const by = new Map<string, Bucket>();
    for (const r of rows) {
      if (r.status !== "closed") continue;
      const label = groupBy === "entry" ? entryBand(r.avgPrice) : marketType(r.market);
      let b = by.get(label);
      if (!b) {
        b = { label, legs: 0, staked: 0, pnl: 0, wins: 0 };
        by.set(label, b);
      }
      b.legs += 1;
      b.staked += r.avgPrice * r.size;
      b.pnl += r.pnl;
      if (r.pnl > 0) b.wins += 1;
    }
    const out = [...by.values()];
    // Entry bands read as a price ladder; families read worst-first so the
    // biggest drain is the top line.
    if (groupBy === "entry") {
      const order = ENTRY_BANDS.map(([l]) => l);
      out.sort((a, b) => order.indexOf(a.label) - order.indexOf(b.label));
    } else {
      out.sort((a, b) => a.pnl - b.pnl);
    }
    return out;
  }, [rows, groupBy]);

  const worstBucketPnl = useMemo(
    () => Math.max(1e-9, ...buckets.map((b) => Math.abs(b.pnl))),
    [buckets],
  );

  const visible = useMemo(() => {
    const v = rows.filter((r) => filter === "all" || r.status === filter);
    if (sort === "worst") return [...v].sort((a, b) => a.pnl - b.pnl);
    if (sort === "best") return [...v].sort((a, b) => b.pnl - a.pnl);
    return v;
  }, [rows, filter, sort]);

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

      {/* Sort + leak breakdown controls */}
      <div className="flex items-center gap-x-3 gap-y-1.5 flex-wrap">
        <span className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Sort</span>
        <div className="flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          {([
            ["recent", "RECENT"],
            ["worst", "WORST"],
            ["best", "BEST"],
          ] as [Sort, string][]).map(([s, label]) => (
            <button
              key={s}
              onClick={() => setSort(s)}
              className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
                sort === s ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-pixel-muted ml-1" title="Group realized P&L to find which buckets drained the account">
          Where it went
        </span>
        <div className="flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          {([
            ["entry", "BY ENTRY"],
            ["type", "BY TYPE"],
          ] as [GroupBy, string][]).map(([g, label]) => (
            <button
              key={g}
              onClick={() => setGroupBy(groupBy === g ? null : g)}
              className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
                groupBy === g ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Leak breakdown — realized P&L grouped, with a magnitude bar so the
          dominant drain is visible without reading the numbers. */}
      {groupBy && (
        <div className="bg-pixel-bg border border-pixel-border rounded-[var(--radius)] overflow-hidden">
          <div className="grid grid-cols-[minmax(0,1fr)_44px_74px_84px_56px_52px] items-center gap-x-3 px-2 py-1 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-pixel-muted border-b border-pixel-border/40">
            <span>{groupBy === "entry" ? "Entry price paid" : "Market type"}</span>
            <span className="text-right">Legs</span>
            <span className="text-right" title="Total cost basis deployed into this bucket">Staked</span>
            <span className="text-right">Realized</span>
            <span className="text-right" title="Realized P&L ÷ staked">ROI</span>
            <span className="text-right">Win</span>
          </div>
          {buckets.length === 0 && (
            <div className="px-2 py-3 text-center text-[11px] text-pixel-muted">
              No closed positions yet.
            </div>
          )}
          {buckets.map((b) => (
            <div
              key={b.label}
              className="grid grid-cols-[minmax(0,1fr)_44px_74px_84px_56px_52px] items-center gap-x-3 px-2 py-1 text-[11px] font-mono border-b border-pixel-border/30 last:border-b-0"
            >
              <span className="truncate min-w-0 text-pixel-white flex items-center gap-1.5">
                <span
                  className={`inline-block h-1.5 rounded-sm ${b.pnl >= 0 ? "bg-green-400/70" : "bg-red-400/70"}`}
                  style={{ width: `${Math.round((Math.abs(b.pnl) / worstBucketPnl) * 46) + 2}px` }}
                />
                {b.label}
              </span>
              <span className="text-right text-pixel-muted">{b.legs}</span>
              <span className="text-right text-pixel-muted">{fmtUsd(b.staked)}</span>
              <span className={`text-right ${b.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                {b.pnl >= 0 ? "+" : "-"}${Math.abs(b.pnl).toFixed(2)}
              </span>
              <span className={`text-right ${b.pnl >= 0 ? "text-green-400/80" : "text-red-400/80"}`}>
                {b.staked > 0 ? `${((b.pnl / b.staked) * 100).toFixed(0)}%` : "—"}
              </span>
              <span className="text-right text-pixel-muted">
                {b.legs > 0 ? `${Math.round((b.wins / b.legs) * 100)}%` : "—"}
              </span>
            </div>
          ))}
        </div>
      )}

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
                      title={r.ts === 0 ? "Market resolved against you — tokens are worthless; auto-redeem will sweep them" : r.curPrice >= 0.999 ? "Resolved in your favor / fully exited" : r.curPrice <= 0.001 ? "Resolved against you / fully exited" : "Fully exited"}
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
                  {r.status === "open" ? "live" : r.ts > 0 ? fmtWhen(r.ts) : "ended"}
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
