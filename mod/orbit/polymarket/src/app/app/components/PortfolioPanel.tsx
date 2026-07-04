"use client";

// Portfolio panel — at-a-glance view of where the user's money lives.
//
// Two charts in one panel, toggled by the user:
//   PIE  — current split between cash (USDC in the V2 deposit wallet)
//          and open positions (live Polymarket position value).
//   LINE — both values plotted over time so you can see how cash drains
//          into positions, positions resolve back into cash, etc.
//
// Snapshots are appended to localStorage every poll (45s), capped at
// PORTFOLIO_HISTORY_CAP points so the entry never grows unbounded.
// History persists across reloads — first time you open the panel after
// 2 weeks of running you'll see the full 2-week curve.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { fetchPositions } from "../lib/polymarket";

interface DepositWalletInfo {
  depositWallet: string;
  usdcBalance: string;
}

interface PositionLite {
  market: string;
  outcome: string;
  size: number;
  value: number;
  pnlUsd: number;
  avgPrice: number;
  // Forwarded so the SELL ALL button can build a /order/place body
  // without re-fetching positions just for the asset/conditionId.
  tokenId: string;
  conditionId: string;
  currentPrice: number;
  negRisk: boolean;
  // Market resolved → REDEEM (not SELL) is the only cash-out path.
  redeemable: boolean;
}

interface Snapshot {
  t: number;       // unix ms
  liq: number;     // $ in deposit wallet
  pos: number;     // sum of position market values
}

const HISTORY_KEY = "poly_portfolio_history_v1";
// ~12h at 15s cadence. Faster cadence = livelier cash + position numbers
// so a fill or withdrawal shows up within seconds instead of half a minute.
const PORTFOLIO_HISTORY_CAP = 3000;
const POLL_MS = 30_000;

function loadHistory(): Snapshot[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((s) => typeof s?.t === "number") : [];
  } catch {
    return [];
  }
}

function saveHistory(history: Snapshot[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  } catch {}
}

function fmtUsd(v: number): string {
  if (!Number.isFinite(v)) return "$0.00";
  return v >= 1000
    ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
    : `$${v.toFixed(2)}`;
}

function fmtRelTime(now: number, t: number): string {
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 90) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

// ── Pie chart (2 slices) ───────────────────────────────────────────────

function PieChart({ liq, pos }: { liq: number; pos: number }) {
  const total = liq + pos;
  if (total <= 0) {
    return (
      <div className="flex items-center justify-center h-40 text-pixel-muted text-xs">
        No funds yet
      </div>
    );
  }
  const liqPct = (liq / total) * 100;
  const posPct = 100 - liqPct;

  // For a 2-slice pie we just need a single arc. SVG circle + dasharray
  // is way simpler than computing path arc d-strings for one slice.
  const r = 70;
  const c = 2 * Math.PI * r;
  const liqArc = (liq / total) * c;
  const posArc = c - liqArc;

  return (
    <div className="flex items-center gap-6">
      <svg viewBox="0 0 200 200" className="w-44 h-44 -rotate-90">
        {/* Position slice (full circle as background, "filled" with position color) */}
        <circle cx="100" cy="100" r={r} fill="none"
          stroke="#f59e0b" strokeWidth="36" />
        {/* Liquidity slice (overlay arc starting at top, going clockwise) */}
        <circle cx="100" cy="100" r={r} fill="none"
          stroke="#10b981" strokeWidth="36"
          strokeDasharray={`${liqArc} ${posArc}`}
          strokeDashoffset="0" />
      </svg>
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: "#10b981" }} />
          <span className="text-pixel-muted">Cash</span>
          <span className="font-mono">{fmtUsd(liq)}</span>
          <span className="text-pixel-muted text-xs">({liqPct.toFixed(0)}%)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: "#f59e0b" }} />
          <span className="text-pixel-muted">Positions</span>
          <span className="font-mono">{fmtUsd(pos)}</span>
          <span className="text-pixel-muted text-xs">({posPct.toFixed(0)}%)</span>
        </div>
        <div className="border-t border-pixel-border pt-1 text-xs">
          <span className="text-pixel-muted mr-1">Total</span>
          <span className="font-mono">{fmtUsd(total)}</span>
        </div>
      </div>
    </div>
  );
}

// ── Line chart (2 series) ──────────────────────────────────────────────

function LineChart({ history }: { history: Snapshot[] }) {
  if (history.length < 2) {
    return (
      <div className="flex items-center justify-center h-40 text-pixel-muted text-xs text-center px-4">
        Not enough history yet — the panel snapshots every 45s. Come back
        after a few minutes to see the curve.
      </div>
    );
  }
  const W = 600;
  const H = 160;
  const PAD_L = 40;
  const PAD_R = 8;
  const PAD_T = 8;
  const PAD_B = 18;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const t0 = history[0].t;
  const t1 = history[history.length - 1].t;
  const tspan = Math.max(1, t1 - t0);

  let vmax = 0;
  for (const s of history) {
    if (s.liq > vmax) vmax = s.liq;
    if (s.pos > vmax) vmax = s.pos;
    if (s.liq + s.pos > vmax) vmax = s.liq + s.pos;
  }
  if (vmax <= 0) vmax = 1;

  const x = (t: number) => PAD_L + ((t - t0) / tspan) * innerW;
  const y = (v: number) => PAD_T + innerH - (v / vmax) * innerH;

  const liqPath = history.map((s, i) => `${i === 0 ? "M" : "L"}${x(s.t).toFixed(1)},${y(s.liq).toFixed(1)}`).join(" ");
  const posPath = history.map((s, i) => `${i === 0 ? "M" : "L"}${x(s.t).toFixed(1)},${y(s.pos).toFixed(1)}`).join(" ");
  const totalPath = history.map((s, i) => `${i === 0 ? "M" : "L"}${x(s.t).toFixed(1)},${y(s.liq + s.pos).toFixed(1)}`).join(" ");

  // Y-axis ticks at 0, 50%, 100%.
  const yTicks = [0, vmax / 2, vmax];

  return (
    <div className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-44">
        {/* Gridlines + tick labels */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} y1={y(v)} x2={W - PAD_R} y2={y(v)}
              stroke="currentColor" strokeOpacity="0.1" strokeWidth="1" />
            <text x={PAD_L - 4} y={y(v) + 3} fontSize="9" fill="currentColor"
              fillOpacity="0.5" textAnchor="end">
              {fmtUsd(v)}
            </text>
          </g>
        ))}
        {/* Total (faint, behind) */}
        <path d={totalPath} fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.5" strokeDasharray="3 3" />
        {/* Positions */}
        <path d={posPath} fill="none" stroke="#f59e0b" strokeWidth="2" />
        {/* Liquidity */}
        <path d={liqPath} fill="none" stroke="#10b981" strokeWidth="2" />
        {/* X-axis time labels (start/end) */}
        <text x={PAD_L} y={H - 4} fontSize="9" fill="currentColor" fillOpacity="0.5">
          {fmtRelTime(t1, t0)}
        </text>
        <text x={W - PAD_R} y={H - 4} fontSize="9" fill="currentColor"
          fillOpacity="0.5" textAnchor="end">
          now
        </text>
      </svg>
      <div className="flex items-center gap-4 text-xs flex-wrap">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5" style={{ background: "#10b981" }} />
          <span className="text-pixel-muted">Cash</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5" style={{ background: "#f59e0b" }} />
          <span className="text-pixel-muted">Positions</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 border-t border-dashed" />
          <span className="text-pixel-muted">Total</span>
        </span>
        <span className="ml-auto text-pixel-muted">{history.length} points</span>
      </div>
    </div>
  );
}

// ── Main panel ─────────────────────────────────────────────────────────

type Mode = "pie" | "line";

function tickRound(p: number): number {
  if (!Number.isFinite(p)) return 0.01;
  const clamped = Math.max(0.01, Math.min(0.99, p));
  return Math.round(clamped * 100) / 100;
}

export default function PortfolioPanel({ strategyId }: { strategyId?: string }) {
  const { auth } = useAuth();
  // Default to the over-time PnL curve (like the backtest's PnL curve) so the
  // chart leads MY TRADES; PIE is a click away for the cash/positions split.
  const [mode, setMode] = useState<Mode>("line");
  const [liq, setLiq] = useState(0);
  const [posValue, setPosValue] = useState(0);
  const [positions, setPositions] = useState<PositionLite[]>([]);
  const [history, setHistory] = useState<Snapshot[]>(() => loadHistory());
  const [lastError, setLastError] = useState<string | null>(null);
  const [selling, setSelling] = useState(false);
  const [sellStatus, setSellStatus] = useState<string | null>(null);
  const [redeeming, setRedeeming] = useState(false);
  const [redeemStatus, setRedeemStatus] = useState<string | null>(null);

  const eoa = auth.address;

  const refresh = useCallback(async () => {
    if (!eoa) return;
    let nextLiq = 0;
    let nextPosVal = 0;
    let nextPositions: PositionLite[] = [];

    // 1) Liquidity from the deposit wallet
    let wallet: string | null = null;
    try {
      const r = await fetch(
        `/api/polymarket/deposit-wallet/info?eoa=${eoa}`,
        { cache: "no-store" },
      );
      if (r.ok) {
        const j = (await r.json()) as DepositWalletInfo;
        wallet = j.depositWallet;
        nextLiq = Number(j.usdcBalance) / 1_000_000;
      }
    } catch (e) {
      setLastError(`liquidity: ${e instanceof Error ? e.message : String(e)}`);
    }

    // 2) Positions for the deposit wallet — that's where trades land in V2.
    // The data-api intermittently returns an EMPTY positions list under load
    // (and the proxy can serve that empty as a cached HIT) — so a "successful"
    // /positions call is NOT proof you hold nothing. We therefore treat the
    // light /value endpoint as the authoritative TOTAL and only let the
    // detailed list drive the total when the list actually has rows.
    let valueTotal: number | null = null; // authoritative total from /value
    let listOk = false;                    // /positions call succeeded (maybe empty)
    if (wallet) {
      // 2a) Authoritative TOTAL positions value — one light call that tends to
      // survive rate-limiting even when the heavier /positions list is empty.
      try {
        const vr = await fetch(`/api/polymarket/?endpoint=value&user=${wallet}`, { cache: "no-store" });
        if (vr.ok) {
          const vj = await vr.json();
          const v = Array.isArray(vj) ? Number(vj[0]?.value) : Number(vj?.value);
          if (Number.isFinite(v)) valueTotal = v;
        }
      } catch { /* keep going; list below may still populate */ }

      // 2b) Per-position breakdown for the list + pie (heavier; best-effort).
      try {
        const pos = await fetchPositions(wallet, { bypassCache: true });
        nextPositions = pos.map((p) => ({
          market: p.market,
          outcome: p.outcome,
          size: p.size,
          value: p.value,
          pnlUsd: p.pnlUsd,
          avgPrice: p.avgPrice,
          tokenId: p.tokenId,
          conditionId: p.conditionId,
          currentPrice: p.currentPrice,
          negRisk: p.negRisk,
          redeemable: p.redeemable,
        }));
        listOk = true;
      } catch (e) {
        setLastError(`positions: ${e instanceof Error ? e.message : String(e)}`);
      }
    }

    // Resolve the headline total. Prefer the detailed sum ONLY when the list
    // has rows; otherwise an empty/throttled list must NOT zero a real /value
    // total (the phantom-$0 bug). Genuine zero = list returned empty AND /value
    // also ~0.
    const listSum = nextPositions.reduce((s, p) => s + p.value, 0);
    let posValueOk = false;
    if (listOk && nextPositions.length > 0) {
      nextPosVal = listSum;
      posValueOk = true;
    } else if (valueTotal != null) {
      nextPosVal = valueTotal;
      posValueOk = true;
    } else if (listOk) {
      nextPosVal = 0; // empty list and no /value reading → truly flat
      posValueOk = true;
    }

    // Cash is a reliable on-chain RPC read, so always update it.
    setLiq(nextLiq);
    if (posValueOk) setPosValue(nextPosVal);
    // Only replace the breakdown list when it has rows, or when /value confirms
    // a genuine zero — so a stale-empty list never wipes a populated breakdown.
    if (listOk && (nextPositions.length > 0 || (valueTotal != null && valueTotal < 0.01))) {
      setPositions(nextPositions);
    }

    // 3) Snapshot for the time-series view — only on a real positions total, so
    // a rate-limited tick can't punch a false $0 dip into the P&L curve.
    // De-dupe back-to-back identical snapshots (a paused engine shouldn't
    // pollute the curve with thousands of flatlined points).
    if (posValueOk) {
      const snap: Snapshot = { t: Date.now(), liq: nextLiq, pos: nextPosVal };
      setHistory((prev) => {
        const last = prev[prev.length - 1];
        if (last && Math.abs(last.liq - snap.liq) < 0.001 && Math.abs(last.pos - snap.pos) < 0.001) {
          return prev;
        }
        const next = [...prev, snap].slice(-PORTFOLIO_HISTORY_CAP);
        saveHistory(next);
        return next;
      });
    }
  }, [eoa]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const total = liq + posValue;

  // Unrealized P&L across every open position (mark-to-market vs avg entry),
  // with the cost basis so we can show it as a % too.
  const unrealized = useMemo(
    () => positions.reduce((s, p) => s + p.pnlUsd, 0),
    [positions],
  );
  const costBasis = useMemo(
    () => positions.reduce((s, p) => s + p.avgPrice * p.size, 0),
    [positions],
  );
  const unrealizedPct = costBasis > 0 ? (unrealized / costBasis) * 100 : 0;

  // Session performance: equity now vs the oldest snapshot we hold.
  const sessionDelta = useMemo(() => {
    if (history.length < 2) return null;
    const first = history[0];
    const start = first.liq + first.pos;
    if (!Number.isFinite(start)) return null;
    return { delta: total - start, since: first.t, start };
  }, [history, total]);

  // ── Rotation queue ──
  // Mirror the live engine's forward-EP ranking so the user can see which
  // positions get sold first when cash is freed to fund a new buy. Forward
  // EP = max(0, entryEP − realized P&L) — the engine sorts ascending and
  // rotates the cheapest out first. Positions the engine didn't open (no
  // stored entry) have forward EP 0 → "free" to rotate. Read straight from
  // the engine's persisted ledger so this never drifts from the real logic.
  const rotationQueue = useMemo(() => {
    let ledger: Record<string, { entryEP: number; mirrorNotional: number }> = {};
    if (strategyId) {
      try {
        const raw = localStorage.getItem(`poly_copy_positionep_${strategyId}`);
        if (raw) ledger = JSON.parse(raw) || {};
      } catch {}
    }
    return positions
      .filter((p) => p.size > 0)
      .map((p) => {
        const key = `${p.conditionId.toLowerCase()}:${(p.outcome || "Yes").toLowerCase()}`;
        const stored = ledger[key];
        const forwardEP = stored ? Math.max(0, stored.entryEP - p.pnlUsd) : 0;
        return { ...p, forwardEP, tracked: !!stored };
      })
      .sort((a, b) => a.forwardEP - b.forwardEP || b.pnlUsd - a.pnlUsd);
  }, [positions, strategyId]);

  if (!auth.connected) return null;

  return (
    <div className="pixel-panel p-3 space-y-3">
      {/* Header: equity headline + cash/positions/unrealized-P&L breakdown +
          session performance + chart toggle. EQUITY (cash + mark-to-market
          positions) leads — free capital alone is not the account's worth. */}
      <div className="flex items-center gap-x-4 gap-y-1.5 flex-wrap">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-pixel-muted">
            Equity
          </span>
          <span className="text-[19px] font-mono font-semibold text-pixel-white">{fmtUsd(total)}</span>
        </div>
        <div className="flex items-baseline gap-1.5" title="Free USDC in the trading wallet">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Cash</span>
          <span className="text-[13px] font-mono">{fmtUsd(liq)}</span>
        </div>
        <div className="flex items-baseline gap-1.5" title="Mark-to-market value of all open positions">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Positions</span>
          <span className="text-[13px] font-mono text-amber-400">{fmtUsd(posValue)}</span>
        </div>
        <div
          className="flex items-baseline gap-1.5"
          title="Unrealized P&L across all open positions (current price vs avg entry)"
        >
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">P&L</span>
          <span className={`text-[13px] font-mono font-semibold ${unrealized >= 0 ? "text-green-400" : "text-red-400"}`}>
            {unrealized >= 0 ? "+" : "-"}{fmtUsd(Math.abs(unrealized))}
            {costBasis > 0 && (
              <span className="opacity-75"> ({unrealizedPct >= 0 ? "+" : ""}{unrealizedPct.toFixed(1)}%)</span>
            )}
          </span>
        </div>
        {sessionDelta && (
          <div
            className="flex items-baseline gap-1.5"
            title={`Equity change since the oldest snapshot (${fmtUsd(sessionDelta.start)} ${fmtRelTime(Date.now(), sessionDelta.since)})`}
          >
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">
              Δ {fmtRelTime(Date.now(), sessionDelta.since).replace(" ago", "")}
            </span>
            <span className={`text-[13px] font-mono ${sessionDelta.delta >= 0 ? "text-green-400" : "text-red-400"}`}>
              {sessionDelta.delta >= 0 ? "+" : "-"}{fmtUsd(Math.abs(sessionDelta.delta))}
            </span>
          </div>
        )}
        <div className="ml-auto flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          <button
            onClick={() => setMode("line")}
            className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
              mode === "line" ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
            }`}
          >
            OVER TIME
          </button>
          <button
            onClick={() => setMode("pie")}
            className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
              mode === "pie" ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
            }`}
          >
            PIE
          </button>
        </div>
      </div>

      {/* Chart area */}
      <div className="bg-pixel-bg border border-pixel-border rounded p-3">
        {mode === "pie" ? (
          <PieChart liq={liq} pos={posValue} />
        ) : (
          <LineChart history={history} />
        )}
      </div>

      {/* OPEN POSITIONS — every holding with its live mark-to-market P&L,
          listed in the engine's rotation order (lowest forward-EP first =
          sold first to fund new buys). Totals pinned in the footer. */}
      {rotationQueue.length > 0 && (
        <div className="bg-pixel-bg border border-pixel-border rounded-[var(--radius)] overflow-hidden">
          <div className="px-2 py-1.5 border-b border-pixel-border flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-pixel-muted">
              Open positions · {rotationQueue.length}
            </span>
            <span className="text-[9.5px] text-pixel-muted" title="Rows are in rotation order — the engine sells the lowest forward expected profit first to fund new buys.">
              rotation order · ▸ = sold first
            </span>
          </div>
          <div className="grid grid-cols-[16px_minmax(0,1fr)_44px_84px_64px_108px] items-center gap-x-3 px-2 py-1 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-pixel-muted border-b border-pixel-border/40">
            <span />
            <span>Market</span>
            <span className="text-right">Size</span>
            <span className="text-right" title="Average entry price → current price">Avg→Cur</span>
            <span className="text-right">Value</span>
            <span className="text-right" title="Unrealized P&L (current price vs avg entry)">P&L</span>
          </div>
          <div className="max-h-[220px] overflow-y-auto">
            {rotationQueue.map((p, i) => {
              const basis = p.avgPrice * p.size;
              const pct = basis > 0 ? (p.pnlUsd / basis) * 100 : 0;
              const curPrice = p.size > 0 ? p.value / p.size : p.currentPrice;
              return (
                <div
                  key={`${p.conditionId}-${p.outcome}-${i}`}
                  className="grid grid-cols-[16px_minmax(0,1fr)_44px_84px_64px_108px] items-center gap-x-3 px-2 py-1 text-[11px] font-mono border-b border-pixel-border/30 last:border-b-0 hover:bg-pixel-white/[0.03]"
                >
                  <span
                    className={`text-center ${i === 0 ? "text-red-400 font-bold" : "text-pixel-muted"}`}
                    title={i === 0 ? "Next to be sold when cash is freed" : `#${i + 1} in rotation order`}
                  >
                    {i === 0 ? "▸" : i + 1}
                  </span>
                  <span className="truncate min-w-0 text-pixel-white" title={`${p.market} · ${p.outcome}${p.tracked ? ` · fwd EP $${p.forwardEP.toFixed(2)}` : " · not opened by the engine (rotates first)"}`}>
                    {p.market}
                    <span className={p.outcome.toLowerCase() === "no" ? "text-red-400/80" : "text-green-400/80"}> · {p.outcome}</span>
                    {p.redeemable && (
                      <span className="ml-1.5 text-[9px] px-1 py-px rounded-full border border-amber-400/50 text-amber-400 font-sans font-semibold tracking-wide" title="Market resolved — cash out via REDEEM, not SELL">
                        REDEEM
                      </span>
                    )}
                  </span>
                  <span className="text-right text-pixel-muted">{p.size.toFixed(1)}</span>
                  <span className="text-right text-pixel-muted" title="avg entry → current">
                    {Math.round(p.avgPrice * 100)}¢→{Math.round(curPrice * 100)}¢
                  </span>
                  <span className="text-right text-pixel-white">{fmtUsd(p.value)}</span>
                  <span className={`text-right ${p.pnlUsd >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {p.pnlUsd >= 0 ? "+" : "-"}${Math.abs(p.pnlUsd).toFixed(2)}
                    <span className="opacity-70"> {pct >= 0 ? "+" : ""}{pct.toFixed(0)}%</span>
                  </span>
                </div>
              );
            })}
          </div>
          {/* Totals */}
          <div className="grid grid-cols-[16px_minmax(0,1fr)_44px_84px_64px_108px] items-center gap-x-3 px-2 py-1.5 text-[11px] font-mono border-t border-pixel-border bg-pixel-white/[0.02]">
            <span />
            <span className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-pixel-muted font-sans">Total</span>
            <span />
            <span />
            <span className="text-right text-pixel-white font-semibold">{fmtUsd(posValue)}</span>
            <span className={`text-right font-semibold ${unrealized >= 0 ? "text-green-400" : "text-red-400"}`}>
              {unrealized >= 0 ? "+" : "-"}${Math.abs(unrealized).toFixed(2)}
            </span>
          </div>
        </div>
      )}

      {/* REDEEM → CASH — settled markets have no order book, so a SELL can't
          cash them out ("invalid token id"). Their winning tokens must be
          REDEEMED to USDC on-chain. Shown only when resolved positions are
          held; lists how many and how much is claimable. */}
      {positions.some((p) => p.redeemable) && (() => {
        const red = positions.filter((p) => p.redeemable);
        const claimable = red.reduce((s, p) => s + p.value, 0);
        return (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] text-pixel-muted whitespace-nowrap">
              {red.length} settled · ~{fmtUsd(claimable)} to claim
            </span>
            <button
              onClick={async () => {
                if (!eoa) return;
                if (
                  !confirm(
                    `Redeem ${red.length} settled position(s) (~${fmtUsd(claimable)} → cash)?\n\nConverts winning tokens to USDC on-chain (gasless) and wraps it into your trading balance. SELL can't cash these out — the markets have already resolved.`,
                  )
                ) {
                  return;
                }
                setRedeeming(true);
                setRedeemStatus(`redeeming ${red.length} settled position(s)…`);
                try {
                  const r = await fetch("/api/polymarket/redeem", {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ eoa }),
                  });
                  const j = (await r.json().catch(() => ({}))) as {
                    conditions?: number;
                    valueRedeemed?: number;
                    skipped?: number;
                    error?: string;
                  };
                  if (r.ok) {
                    const v = Number(j.valueRedeemed ?? 0);
                    const skipped = Number(j.skipped ?? 0);
                    setRedeemStatus(
                      `redeemed ${j.conditions ?? 0} market(s) · ~${fmtUsd(v)} → cash${skipped ? ` · ${skipped} skipped` : ""}`,
                    );
                    // Curves redraw from the post-redeem state.
                    try { localStorage.removeItem(HISTORY_KEY); } catch {}
                    setHistory([]);
                  } else {
                    setRedeemStatus(null);
                    setLastError(`redeem: ${j.error ?? `HTTP ${r.status}`}`);
                  }
                } catch (e) {
                  setRedeemStatus(null);
                  setLastError(`redeem: ${e instanceof Error ? e.message : String(e)}`);
                }
                setRedeeming(false);
                setTimeout(refresh, 6_000);
              }}
              disabled={redeeming}
              className="text-[10px] px-2 py-0.5 bg-emerald-700/80 hover:bg-emerald-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold rounded"
              title="Redeem resolved-market winnings to USDC (gasless, on-chain). A SELL can't cash these out because settled markets have no order book."
            >
              {redeeming ? "REDEEMING…" : "REDEEM → CASH"}
            </button>
          </div>
        );
      })()}

      {redeemStatus && (
        <div className={`text-xs font-mono ${redeeming ? "text-amber-400" : "text-emerald-400"}`}>
          {redeemStatus}
        </div>
      )}

      {/* SELL ALL → CASH — the per-position "Top Positions" list was removed
          for a sleeker panel; the bulk-liquidate action stays. */}
      {posValue > 0 && (
        <div className="flex justify-end">
            <button
              onClick={async () => {
                if (!eoa) return;
                // Resolved (redeemable) markets have no order book — a SELL
                // there always bounces ("invalid token id"). Exclude them;
                // they cash out through REDEEM → CASH instead.
                const all = positions.filter((p) => p.tokenId && p.size > 0 && !p.redeemable);
                if (all.length === 0) return;
                if (
                  !confirm(
                    `Sell ALL ${all.length} positions (~${fmtUsd(posValue)} → cash)?\n\nUses market-aggressive limit orders (current price). Some may partially fill if liquidity is thin.`,
                  )
                ) {
                  return;
                }
                setSelling(true);
                setSellStatus(`selling ${all.length} positions…`);
                let ok = 0;
                let fail = 0;
                for (let i = 0; i < all.length; i++) {
                  const p = all[i];
                  setSellStatus(`selling ${i + 1}/${all.length} · ${p.market.slice(0, 28)}…`);
                  try {
                    // Sell at current price - 1¢ to be aggressive against
                    // existing bids. FAK so we don't end up with stuck
                    // limits if the book gaps away.
                    const sellPrice = tickRound(Math.max(0.01, p.currentPrice - 0.01));
                    const body = {
                      eoa,
                      creds: { apiKey: "u", secret: "u", passphrase: "u" },
                      args: {
                        tokenId: p.tokenId,
                        side: "SELL",
                        price: sellPrice,
                        size: Math.round(p.size * 100) / 100,
                        feeRateBps: 0,
                        expiration: 0,
                        signatureType: 3,
                        orderType: "FAK",
                        negRisk: p.negRisk,
                        // Backend ignores this and derives the V2 deposit
                        // wallet from `eoa` itself, but the field is
                        // required by PlaceOrderArgs.
                        maker: "0x0000000000000000000000000000000000000000",
                      },
                    };
                    const r = await fetch("/api/polymarket/order/place", {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify(body),
                    });
                    if (r.ok) {
                      const j = (await r.json()) as { success?: boolean; errorMsg?: string };
                      if (j.success === false) {
                        fail++;
                        if (j.errorMsg) setLastError(`${p.market.slice(0, 28)}: ${j.errorMsg}`);
                      } else ok++;
                    } else {
                      fail++;
                      const detail = await r.text().catch(() => "");
                      setLastError(`${p.market.slice(0, 28)}: HTTP ${r.status} ${detail.slice(0, 120)}`);
                    }
                  } catch (e) {
                    fail++;
                    setLastError(`${p.market.slice(0, 28)}: ${e instanceof Error ? e.message : String(e)}`);
                  }
                }
                setSellStatus(`sold ${ok} ✓ · ${fail} failed`);
                setSelling(false);
                // Reset portfolio history so the curves redraw from the
                // post-sell state instead of dragging the old positions
                // wedge across the time axis.
                if (ok > 0) {
                  try { localStorage.removeItem(HISTORY_KEY); } catch {}
                  setHistory([]);
                }
                setTimeout(refresh, 4_000);
              }}
              disabled={selling || posValue <= 0}
              className="text-[10px] px-2 py-0.5 bg-red-700/80 hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold rounded"
              title="Aggressive market-priced FAK sells against every position. Anything that doesn't fill at the bid gets killed (no resting limits)."
            >
              {selling ? "SELLING…" : "SELL ALL → CASH"}
            </button>
        </div>
      )}

      {sellStatus && (
        <div className={`text-xs font-mono ${selling ? "text-amber-400" : "text-green-400"}`}>
          {sellStatus}
        </div>
      )}
      {lastError && (
        <div className="text-[10px] text-red-400/70 font-mono break-all">{lastError}</div>
      )}
    </div>
  );
}
