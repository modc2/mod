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
}

interface Snapshot {
  t: number;       // unix ms
  liq: number;     // $ in deposit wallet
  pos: number;     // sum of position market values
}

const HISTORY_KEY = "poly_portfolio_history_v1";
const PORTFOLIO_HISTORY_CAP = 1000; // ~12h at 45s cadence
const POLL_MS = 45_000;

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

export default function PortfolioPanel() {
  const { auth } = useAuth();
  const [mode, setMode] = useState<Mode>("pie");
  const [liq, setLiq] = useState(0);
  const [posValue, setPosValue] = useState(0);
  const [positions, setPositions] = useState<PositionLite[]>([]);
  const [history, setHistory] = useState<Snapshot[]>(() => loadHistory());
  const [lastError, setLastError] = useState<string | null>(null);

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
    if (wallet) {
      try {
        const pos = await fetchPositions(wallet);
        nextPositions = pos.map((p) => ({
          market: p.market,
          outcome: p.outcome,
          size: p.size,
          value: p.value,
          pnlUsd: p.pnlUsd,
        }));
        nextPosVal = nextPositions.reduce((s, p) => s + p.value, 0);
      } catch (e) {
        setLastError(`positions: ${e instanceof Error ? e.message : String(e)}`);
      }
    }

    setLiq(nextLiq);
    setPosValue(nextPosVal);
    setPositions(nextPositions);

    // 3) Snapshot for the time-series view. De-dupe back-to-back identical
    // snapshots (a paused engine shouldn't pollute the curve with thousands
    // of flatlined points).
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
  }, [eoa]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const total = liq + posValue;
  const topPositions = useMemo(
    () => [...positions].sort((a, b) => b.value - a.value).slice(0, 4),
    [positions],
  );

  if (!auth.connected) return null;

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-3">
      {/* Header: title + total + chart toggle */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs uppercase tracking-wide text-pixel-muted">
          Portfolio
        </span>
        <span className="text-base font-mono">{fmtUsd(total)}</span>
        <span className="text-xs text-pixel-muted">
          ({fmtUsd(liq)} cash + {fmtUsd(posValue)} positions)
        </span>
        <div className="ml-auto flex gap-1 border border-pixel-border rounded overflow-hidden">
          <button
            onClick={() => setMode("pie")}
            className={`px-2 py-1 text-xs ${
              mode === "pie" ? "bg-pixel-border-light" : "hover:bg-pixel-border-light/50"
            }`}
          >
            PIE
          </button>
          <button
            onClick={() => setMode("line")}
            className={`px-2 py-1 text-xs ${
              mode === "line" ? "bg-pixel-border-light" : "hover:bg-pixel-border-light/50"
            }`}
          >
            OVER TIME
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

      {/* Top positions list — only show when there's something to see. */}
      {topPositions.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs uppercase tracking-wide text-pixel-muted">
            Top Positions
          </div>
          <div className="space-y-1 text-xs">
            {topPositions.map((p, i) => (
              <div key={i} className="flex items-center justify-between gap-2 border-b border-pixel-border/40 py-1 last:border-0">
                <span className="truncate flex-1" title={p.market}>
                  {p.market}
                  <span className="text-pixel-muted ml-1">· {p.outcome}</span>
                </span>
                <span className="font-mono whitespace-nowrap">{fmtUsd(p.value)}</span>
                <span className={`font-mono whitespace-nowrap text-[10px] ${p.pnlUsd >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {p.pnlUsd >= 0 ? "+" : ""}{fmtUsd(p.pnlUsd)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {lastError && (
        <div className="text-[10px] text-red-400/70 font-mono break-all">{lastError}</div>
      )}
    </div>
  );
}
