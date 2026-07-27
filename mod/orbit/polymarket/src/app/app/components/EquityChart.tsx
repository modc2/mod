"use client";

// EquityChart — the ONE portfolio-over-time line chart, shared by the LIVE
// view (PortfolioPanel) and the BACKTEST panel so both render the exact same
// visual: green Cash line, amber Positions line, faint dashed Total. Both
// pass `markers` (backtest: every simulated trade; LIVE: every real fill +
// auto-redeem) so you can see on the curve which trades moved it, with a
// hover tooltip naming the trade when the marker carries a `label`.
//
// Time navigation: range pills (1H…ALL) window the curve to the last N hours,
// the mouse wheel zooms around the cursor, and dragging pans. The y-axis
// re-fits whatever window is visible, so a zoomed-in stretch fills the full
// height instead of hugging a $0 baseline.

import { useLayoutEffect, useMemo, useRef, useState } from "react";

export type EquitySnapshot = {
  t: number;   // epoch ms
  liq: number; // free cash ($)
  pos: number; // positions value ($)
};

export type EquityMarker = {
  t: number;
  // REDEEM = settled winnings converted to cash (positions ↓, cash ↑).
  side: "BUY" | "SELL" | "REDEEM";
  /** Hover detail — market · outcome. Enables the tooltip when present. */
  label?: string;
  /** Notional ($) the trade moved between cash and positions. */
  usd?: number;
};

const MARKER_COLOR: Record<EquityMarker["side"], string> = {
  BUY: "#10b981",
  SELL: "#f87171",
  REDEEM: "#f59e0b",
};

// Range pills — hours of history to show, anchored to the NEWEST snapshot
// (not wall-clock "now") so the backtest's historical curves window too.
const RANGES: { label: string; hours: number | null }[] = [
  { label: "1H", hours: 1 },
  { label: "6H", hours: 6 },
  { label: "12H", hours: 12 },
  { label: "24H", hours: 24 },
  { label: "3D", hours: 72 },
  { label: "ALL", hours: null },
];

// Tick steps the x-axis picks from so labels land on round clock times.
const TICK_STEPS_MS = [
  60_000, 2 * 60_000, 5 * 60_000, 10 * 60_000, 15 * 60_000, 30 * 60_000,
  3_600_000, 2 * 3_600_000, 3 * 3_600_000, 6 * 3_600_000, 12 * 3_600_000,
  86_400_000, 2 * 86_400_000, 7 * 86_400_000,
];

const MIN_SPAN_MS = 90_000; // zoom floor — ~3 poll intervals

function fmtUsd(v: number): string {
  if (!Number.isFinite(v)) return "$0.00";
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  return a >= 1000
    ? `${sign}$${a.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
    : `${sign}$${a.toFixed(2)}`;
}

function fmtPct(v: number): string {
  if (!Number.isFinite(v)) return "0%";
  const a = Math.abs(v);
  const s = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${s}${a >= 100 ? a.toFixed(0) : a >= 10 ? a.toFixed(1) : a.toFixed(2)}%`;
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

function fmtClock(t: number, withDate: boolean): string {
  const d = new Date(t);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return withDate ? `${d.getMonth() + 1}/${d.getDate()} ${hm}` : hm;
}

export default function EquityChart({
  history,
  markers,
  markerLine = "total",
  highlightT,
  onHoverMarker,
  emptyHint,
  timeControls = true,
  defaultUnit = "$",
}: {
  history: EquitySnapshot[];
  /** Trade markers drawn on the curve (every trade that moved it). */
  markers?: EquityMarker[];
  /** Which line the markers sit on. "total" (backtest default) — or "pos"
      for LIVE, where a fill moves the Positions line while Total stays put,
      so that's where cause-and-effect is actually visible. */
  markerLine?: "total" | "pos";
  /** Timestamp to pin a crosshair at (feed-row hover/selection). */
  highlightT?: number | null;
  /** Fires with the nearest marker's timestamp as the mouse moves (null on leave). */
  onHoverMarker?: (t: number | null) => void;
  emptyHint?: string;
  /** Range pills + wheel-zoom + drag-pan. On everywhere by default. */
  timeControls?: boolean;
  /** Starting unit for the $/% toggle. "%" = TWR PnL curve (flows removed). */
  defaultUnit?: "$" | "%";
}) {
  // Hovered marker index for the built-in tooltip (labelled markers only).
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const lastHoverIdx = useRef<number | null>(null);

  // Time window state. `rangeHours` = pill selection (null = ALL);
  // `view` = explicit zoom/pan window that overrides the pill until RESET.
  // With no view and no pill the window tracks the live edge automatically.
  const [rangeHours, setRangeHours] = useState<number | null>(null);
  const [view, setView] = useState<{ a: number; b: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startX: number; a: number; b: number; moved: boolean } | null>(null);

  // The viewBox width tracks the container's real pixel width so 1 SVG unit
  // = 1 CSS px: full-bleed chart, crisp text (see git history for the why).
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [chartW, setChartW] = useState(600);
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setChartW(Math.max(320, Math.round(w)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ── $ vs % ──
  // "%" plots a TIME-WEIGHTED return: per-interval growth factors chained
  // together, with external cash flows (deposits/withdrawals) removed — so
  // the curve is pure trading performance, unmoved by funding the wallet.
  const [unit, setUnit] = useState<"$" | "%">(defaultUnit);

  // Flow detection needs no chain data — it falls out of the snapshot shape:
  //   trade/redeem  → cash and positions swap, TOTAL stays ~flat
  //   mark move     → positions only, cash untouched
  //   external flow → cash AND total jump together by the same amount
  // So: take the cash delta trades can't explain (markers carry each fill's
  // notional), and only call it a flow if the total moved with it. That last
  // gate also kills the false positives — a backend auto-redeem we have no
  // marker for, or a fill whose timestamp lands one interval off, both move
  // cash without moving total.
  const twr = useMemo(() => {
    const factors: number[] = new Array(history.length).fill(1);
    const flows: { t: number; usd: number; i: number }[] = [];
    if (history.length < 2) return { factors, flows };
    const ms = (markers ?? [])
      .filter((m) => m.usd != null)
      .sort((a, b) => a.t - b.t);
    let mi = 0;
    let fac = 1;
    for (let i = 1; i < history.length; i++) {
      const a = history[i - 1];
      const b = history[i];
      const e0 = a.liq + a.pos;
      const e1 = b.liq + b.pos;
      // Cash the markers say trades moved during (a.t, b.t].
      let tradeCash = 0;
      while (mi < ms.length && ms[mi].t <= b.t) {
        if (ms[mi].t > a.t) {
          tradeCash += ms[mi].side === "BUY" ? -(ms[mi].usd as number) : (ms[mi].usd as number);
        }
        mi++;
      }
      const cand = b.liq - a.liq - tradeCash; // cash move trades can't explain
      const dTot = e1 - e0;
      const thresh = Math.max(1, 0.005 * Math.max(1, e0));
      let flow = 0;
      if (Math.abs(cand) >= thresh && cand * dTot > 0 && Math.abs(dTot) >= 0.5 * Math.abs(cand)) {
        flow = cand;
        flows.push({ t: b.t, usd: flow, i });
      }
      // End-of-interval convention: the flow hadn't earned anything yet, so
      // it's subtracted from the ending equity before computing the return.
      // Sub-$1 base = no meaningful denominator (fresh wallet pre-deposit).
      if (e0 >= 1) {
        const r = (e1 - flow - e0) / e0;
        fac *= 1 + Math.max(-0.95, r);
      }
      factors[i] = fac;
    }
    return { factors, flows };
  }, [history, markers]);

  if (history.length < 2) {
    return (
      <div className="flex items-center justify-center h-40 text-pixel-muted text-xs text-center px-4">
        {emptyHint ??
          "Not enough history yet — the panel snapshots every 45s. Come back after a few minutes to see the curve."}
      </div>
    );
  }
  const W = chartW;
  const H = 210;
  const PAD_L = 46;
  const PAD_R = 8;
  const PAD_T = 10;
  const PAD_B = 20;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const tFirst = history[0].t;
  const tLast = history[history.length - 1].t;

  // Resolve the visible window: explicit zoom > range pill > everything.
  let w0 = tFirst;
  let w1 = tLast;
  if (view) {
    w0 = view.a;
    w1 = view.b;
  } else if (rangeHours != null) {
    w0 = Math.max(tFirst, tLast - rangeHours * 3_600_000);
  }
  if (w1 - w0 < MIN_SPAN_MS) w0 = Math.max(tFirst, w1 - MIN_SPAN_MS);
  const tspan = Math.max(1, w1 - w0);

  // Points inside the window, plus one neighbor on each side so the lines
  // run through the edges instead of stopping at the first inside point.
  // The clip-path below trims the overhang.
  let loIdx = history.findIndex((s) => s.t >= w0);
  if (loIdx === -1) loIdx = history.length - 1;
  let hiIdx = history.length - 1;
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].t <= w1) { hiIdx = i; break; }
  }
  const vStart = Math.max(0, loIdx - 1); // global index of visible[0]
  const visible = history.slice(vStart, Math.min(history.length, hiIdx + 2));
  const inWindow = history.slice(loIdx, hiIdx + 1);

  // % mode rebases to the first point in the visible window, so a 24H view
  // reads "PnL % over these 24h" — same convention as the range pills.
  const pctMode = unit === "%";
  const baseFac = twr.factors[Math.min(loIdx, twr.factors.length - 1)] || 1;
  const pctAt = (gi: number) =>
    ((twr.factors[Math.min(Math.max(0, gi), twr.factors.length - 1)] || 1) / baseFac - 1) * 100;

  // Y-scale fits ONLY what's on screen (with padding) — that's what makes a
  // zoomed window legible. Values can go negative in backtest previews.
  let vmax = -Infinity;
  let vmin = Infinity;
  const useWindow = inWindow.length >= 2;
  if (pctMode) {
    const fitLo = useWindow ? loIdx : vStart;
    const fitHi = useWindow ? hiIdx : vStart + visible.length - 1;
    for (let gi = fitLo; gi <= fitHi; gi++) {
      const p = pctAt(gi);
      if (p > vmax) vmax = p;
      if (p < vmin) vmin = p;
    }
  } else {
    const fitPoints = useWindow ? inWindow : visible;
    for (const s of fitPoints) {
      const tot = s.liq + s.pos;
      if (s.liq > vmax) vmax = s.liq;
      if (s.pos > vmax) vmax = s.pos;
      if (tot > vmax) vmax = tot;
      if (s.liq < vmin) vmin = s.liq;
      if (s.pos < vmin) vmin = s.pos;
      if (tot < vmin) vmin = tot;
    }
  }
  if (!Number.isFinite(vmax) || !Number.isFinite(vmin)) { vmin = 0; vmax = 1; }
  const rawSpan = vmax - vmin;
  const padV = rawSpan > 0 ? rawSpan * 0.07 : Math.max(1, Math.abs(vmax) * 0.05);
  vmin -= padV;
  vmax += padV;
  const vspan = vmax - vmin;

  const x = (t: number) => PAD_L + ((t - w0) / tspan) * innerW;
  const y = (v: number) => PAD_T + innerH - ((v - vmin) / vspan) * innerH;

  const liqPath = pctMode ? "" : visible.map((s, i) => `${i === 0 ? "M" : "L"}${x(s.t).toFixed(1)},${y(s.liq).toFixed(1)}`).join(" ");
  const posPath = pctMode ? "" : visible.map((s, i) => `${i === 0 ? "M" : "L"}${x(s.t).toFixed(1)},${y(s.pos).toFixed(1)}`).join(" ");
  const totalPath = pctMode ? "" : visible.map((s, i) => `${i === 0 ? "M" : "L"}${x(s.t).toFixed(1)},${y(s.liq + s.pos).toFixed(1)}`).join(" ");
  const pctPath = !pctMode ? "" : visible.map((s, i) => `${i === 0 ? "M" : "L"}${x(s.t).toFixed(1)},${y(pctAt(vStart + i)).toFixed(1)}`).join(" ");

  // Y-axis: 5 evenly spaced ticks across the fitted range.
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => vmin + f * vspan);

  // X-axis: round clock-time ticks. Pick the smallest step that yields ≤6.
  const step = TICK_STEPS_MS.find((s) => tspan / s <= 6) ?? TICK_STEPS_MS[TICK_STEPS_MS.length - 1];
  const withDate = tspan > 22 * 3_600_000;
  const xTicks: number[] = [];
  for (let t = Math.ceil(w0 / step) * step; t <= w1; t += step) xTicks.push(t);

  // Markers inside the window only — off-screen dots would pile on the edges.
  const visMarkers = (markers ?? []).filter((m) => m.t >= w0 && m.t <= w1);

  // Nearest snapshot to a timestamp — markers/highlights sit ON the lines,
  // and in the backtest every trade emits a snapshot at its exact ts.
  const nearestIdx = (t: number): number => {
    let best = 0;
    let bestDist = Infinity;
    for (let i = 0; i < history.length; i++) {
      const d = Math.abs(history[i].t - t);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    return best;
  };

  const hlIdx = highlightT != null && highlightT >= w0 && highlightT <= w1 ? nearestIdx(highlightT) : null;
  const hl = hlIdx != null ? history[hlIdx] : null;

  // Markers sit on the line a trade actually moves (see `markerLine`);
  // in % mode there's only one line, so everything rides the PnL curve.
  const markerY = (gi: number) => {
    if (pctMode) return y(pctAt(gi));
    const s = history[gi];
    return markerLine === "pos" ? y(s.pos) : y(s.liq + s.pos);
  };

  // Detected deposits/withdrawals inside the window — drawn in % mode so the
  // user can see exactly which flows were factored out of the curve.
  const visFlows = pctMode ? twr.flows.filter((f) => f.t >= w0 && f.t <= w1) : [];

  const clampWindow = (a: number, b: number): { a: number; b: number } => {
    let span = Math.max(MIN_SPAN_MS, b - a);
    span = Math.min(span, tLast - tFirst || MIN_SPAN_MS);
    if (a < tFirst) { a = tFirst; b = a + span; }
    if (b > tLast) { b = tLast; a = b - span; }
    if (a < tFirst) a = tFirst;
    return { a, b };
  };

  // Mouse handlers — assigned per-render via ref callback so closures always
  // see the current window. Wheel = zoom around the cursor, drag = pan,
  // plain move = nearest-marker hover/tooltip.
  const svgRef = (el: SVGSVGElement | null) => {
    if (!el) return;

    el.onwheel = !timeControls ? null : (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (((e.clientX - rect.left) / rect.width) * W - PAD_L) / innerW));
      const tAt = w0 + frac * tspan;
      const f = e.deltaY > 0 ? 1.3 : 1 / 1.3;
      const next = clampWindow(tAt - (tAt - w0) * f, tAt + (w1 - tAt) * f);
      // Fully zoomed out again → drop the explicit view so live-follow resumes.
      if (next.a <= tFirst && next.b >= tLast) setView(null);
      else setView(next);
    };

    el.onmousedown = !timeControls ? null : (e: MouseEvent) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, a: w0, b: w1, moved: false };
      const onMove = (ev: MouseEvent) => {
        const d = dragRef.current;
        if (!d) return;
        const dx = ev.clientX - d.startX;
        if (Math.abs(dx) > 3 && !d.moved) { d.moved = true; setDragging(true); }
        if (!d.moved) return;
        const rect = el.getBoundingClientRect();
        const dt = -(dx / rect.width) * (W / innerW) * (d.b - d.a);
        setView(clampWindow(d.a + dt, d.b + dt));
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        dragRef.current = null;
        setDragging(false);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    };

    if (!markers || markers.length === 0) { el.onmousemove = null; el.onmouseleave = null; return; }
    const setHover = (idx: number | null) => {
      if (lastHoverIdx.current !== idx) {
        lastHoverIdx.current = idx;
        setHoverIdx(idx);
      }
    };
    const onMove = (e: MouseEvent) => {
      if (dragRef.current?.moved) { setHover(null); return; }
      const rect = el.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / rect.width) * W;
      if (mx < PAD_L || mx > W - PAD_R || visMarkers.length === 0) { onHoverMarker?.(null); setHover(null); return; }
      let bestIdx = 0, bestDist = Infinity;
      for (let i = 0; i < visMarkers.length; i++) {
        const d = Math.abs(x(visMarkers[i].t) - mx);
        if (d < bestDist) { bestDist = d; bestIdx = i; }
      }
      onHoverMarker?.(visMarkers[bestIdx].t);
      // Tooltip only when the pointer is actually near the dot — a bare
      // "nearest" would pin a tooltip from across the chart.
      setHover(bestDist <= 14 ? bestIdx : null);
    };
    const onLeave = () => { onHoverMarker?.(null); setHover(null); };
    el.onmousemove = onMove;
    el.onmouseleave = onLeave;
  };

  const hovered = hoverIdx != null ? visMarkers[hoverIdx] : null;
  const zoomed = view != null;

  return (
    <div className="space-y-2">
      {timeControls && (
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex gap-px border border-pixel-border rounded-full overflow-hidden">
            {RANGES.map((r) => {
              const active = !zoomed && rangeHours === r.hours;
              return (
                <button
                  key={r.label}
                  onClick={() => { setRangeHours(r.hours); setView(null); }}
                  className={`px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.08em] transition-colors ${
                    active ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
                  }`}
                  title={r.hours == null ? "Full history" : `Last ${r.label.toLowerCase()} of history`}
                >
                  {r.label}
                </button>
              );
            })}
          </div>
          <div className="flex gap-px border border-pixel-border rounded-full overflow-hidden">
            {(["$", "%"] as const).map((u) => (
              <button
                key={u}
                onClick={() => setUnit(u)}
                className={`px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.08em] transition-colors ${
                  unit === u ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
                }`}
                title={
                  u === "%"
                    ? "Time-weighted PnL % — deposits and withdrawals are detected and removed, so the curve is trading performance only"
                    : "Raw account value in dollars (cash / positions / total)"
                }
              >
                {u === "%" ? "PNL %" : "$"}
              </button>
            ))}
          </div>
          {zoomed && (
            <button
              onClick={() => setView(null)}
              className="px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.08em] rounded-full border border-amber-400/50 text-amber-400 hover:bg-amber-400/10 transition-colors"
              title="Back to the selected range (and live-follow)"
            >
              ZOOMED · RESET
            </button>
          )}
          <span className="ml-auto text-[9px] text-pixel-muted hidden sm:inline">
            scroll = zoom · drag = pan
          </span>
        </div>
      )}
      <div className="relative" ref={wrapRef}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full block select-none"
        style={{ height: H, cursor: timeControls ? (dragging ? "grabbing" : "crosshair") : undefined }}
      >
        <defs>
          <clipPath id="eq-clip">
            <rect x={PAD_L} y={0} width={innerW} height={H} />
          </clipPath>
        </defs>
        {/* Gridlines + tick labels */}
        {yTicks.map((v, i) => (
          <g key={`y${i}`}>
            <line x1={PAD_L} y1={y(v)} x2={W - PAD_R} y2={y(v)}
              stroke="currentColor" strokeOpacity="0.08" strokeWidth="1" />
            <text x={PAD_L - 5} y={y(v) + 3} fontSize="9.5" fill="currentColor"
              fillOpacity="0.55" textAnchor="end">
              {pctMode ? fmtPct(v) : fmtUsd(v)}
            </text>
          </g>
        ))}
        {xTicks.map((t, i) => (
          <g key={`x${i}`}>
            <line x1={x(t)} y1={PAD_T} x2={x(t)} y2={H - PAD_B}
              stroke="currentColor" strokeOpacity="0.06" strokeWidth="1" />
            <text x={x(t)} y={H - 6} fontSize="9" fill="currentColor"
              fillOpacity="0.55" textAnchor="middle">
              {fmtClock(t, withDate)}
            </text>
          </g>
        ))}
        <g clipPath="url(#eq-clip)">
          {pctMode ? (
            <>
              {/* 0% baseline for orientation */}
              {vmin < 0 && vmax > 0 && (
                <line x1={PAD_L} y1={y(0)} x2={W - PAD_R} y2={y(0)}
                  stroke="currentColor" strokeOpacity="0.25" strokeWidth="1" strokeDasharray="2 3" />
              )}
              {/* Detected flows — the deposits/withdrawals removed from the
                  curve, shown as diamonds so the exclusion is auditable. */}
              {visFlows.map((f, i) => {
                const cx = x(f.t);
                const cy = y(pctAt(f.i));
                return (
                  <g key={`f${i}`}>
                    <line x1={cx} y1={PAD_T} x2={cx} y2={H - PAD_B}
                      stroke="#94a3b8" strokeOpacity="0.3" strokeWidth="1" strokeDasharray="2 3" />
                    <path
                      d={`M${cx},${cy - 5} L${cx + 5},${cy} L${cx},${cy + 5} L${cx - 5},${cy} Z`}
                      fill={f.usd > 0 ? "#94a3b8" : "none"}
                      stroke="#94a3b8"
                      strokeWidth="1.2"
                    >
                      <title>
                        {`${f.usd > 0 ? "Deposit" : "Withdrawal"} ${f.usd > 0 ? "+" : "−"}${fmtUsd(Math.abs(f.usd))} — excluded from the PnL curve`}
                      </title>
                    </path>
                  </g>
                );
              })}
              {/* Time-weighted PnL % */}
              <path d={pctPath} fill="none" stroke="#38bdf8" strokeWidth="2" />
            </>
          ) : (
            <>
              {/* Total (faint, behind) */}
              <path d={totalPath} fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.5" strokeDasharray="3 3" />
              {/* Positions */}
              <path d={posPath} fill="none" stroke="#f59e0b" strokeWidth="2" />
              {/* Liquidity */}
              <path d={liqPath} fill="none" stroke="#10b981" strokeWidth="2" />
            </>
          )}
          {/* Trade markers — BUY green (positions ↑), SELL red (positions ↓),
              REDEEM amber (settled positions ↓ → cash ↑) */}
          {visMarkers.map((m, i) => (
            <circle
              key={i}
              cx={x(m.t)}
              cy={markerY(nearestIdx(m.t))}
              r={i === hoverIdx ? 4.5 : 3}
              fill={MARKER_COLOR[m.side]}
              fillOpacity="0.9"
              stroke="#000"
              strokeWidth="0.5"
            />
          ))}
          {/* Highlight crosshair (feed-row hover / pinned trade) */}
          {hl && hlIdx != null && (
            <>
              <line x1={x(hl.t)} y1={PAD_T} x2={x(hl.t)} y2={H - PAD_B}
                stroke="currentColor" strokeOpacity="0.4" strokeWidth="1" strokeDasharray="3,3" />
              <circle cx={x(hl.t)} cy={pctMode ? y(pctAt(hlIdx)) : y(hl.liq + hl.pos)} r={4.5} fill="#fff" stroke="#000" strokeWidth="1.5" />
            </>
          )}
        </g>
      </svg>
      {/* Tooltip for the hovered marker — names the trade and how it moved
          the positions line, so the dots answer "what caused this bump". */}
      {hovered && (
        <div
          className="absolute top-0 -translate-x-1/2 pointer-events-none z-10 px-2 py-1 rounded border border-pixel-border bg-pixel-bg/95 text-[10px] font-mono whitespace-nowrap shadow-lg"
          style={{ left: `${Math.min(88, Math.max(12, (x(hovered.t) / W) * 100))}%` }}
        >
          <span className="font-bold" style={{ color: MARKER_COLOR[hovered.side] }}>
            {hovered.side}
          </span>
          {hovered.usd != null && (
            <span style={{ color: MARKER_COLOR[hovered.side] }}>
              {" "}{hovered.side === "BUY" ? "+" : "−"}{fmtUsd(hovered.usd)} positions
              {hovered.side === "REDEEM" ? " → cash" : ""}
            </span>
          )}
          {hovered.label && (
            <span className="text-pixel-muted"> · {hovered.label.slice(0, 52)}</span>
          )}
          <span className="text-pixel-muted opacity-70"> · {fmtRelTime(Date.now(), hovered.t)}</span>
        </div>
      )}
      </div>
      <div className="flex items-center gap-4 text-xs flex-wrap">
        {pctMode ? (
          <>
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-0.5" style={{ background: "#38bdf8" }} />
              <span className="text-pixel-muted" title="Time-weighted return: per-interval growth chained together with deposits/withdrawals removed — the curve moves only when trading gains or loses.">
                PnL % · flows excluded
              </span>
            </span>
            {twr.flows.length > 0 && (() => {
              const dep = twr.flows.filter((f) => f.usd > 0).reduce((s, f) => s + f.usd, 0);
              const wd = twr.flows.filter((f) => f.usd < 0).reduce((s, f) => s - f.usd, 0);
              return (
                <span
                  className="flex items-center gap-1.5"
                  title="External transfers detected in the history and factored out of the curve (diamonds mark them on the chart)."
                >
                  <span className="inline-block w-2 h-2 rotate-45" style={{ background: "#94a3b8" }} />
                  <span className="text-pixel-muted">
                    {twr.flows.length} flow{twr.flows.length === 1 ? "" : "s"}
                    {dep > 0 ? ` · +${fmtUsd(dep)} in` : ""}
                    {wd > 0 ? ` · −${fmtUsd(wd)} out` : ""}
                  </span>
                </span>
              );
            })()}
          </>
        ) : (
          <>
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
          </>
        )}
        {markers && markers.length > 0 && (
          <span className="flex items-center gap-1.5" title="Dots mark each trade on the curve — BUY (positions up), SELL (positions down), REDEEM (settled positions cashed out). Hover a dot for the trade.">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: MARKER_COLOR.BUY }} />
            <span className="inline-block w-2 h-2 rounded-full -ml-1" style={{ background: MARKER_COLOR.SELL }} />
            {markers.some((m) => m.side === "REDEEM") && (
              <span className="inline-block w-2 h-2 rounded-full -ml-1" style={{ background: MARKER_COLOR.REDEEM }} />
            )}
            <span className="text-pixel-muted">
              {visMarkers.length === markers.length
                ? `${markers.length} trades`
                : `${visMarkers.length} of ${markers.length} trades in view`}
            </span>
          </span>
        )}
        <span className="ml-auto text-pixel-muted">
          {inWindow.length < history.length
            ? `${inWindow.length} of ${history.length} points · ${fmtRelTime(w1, w0)} → ${w1 >= tLast ? "now" : fmtRelTime(tLast, w1)}`
            : `${history.length} points`}
        </span>
      </div>
    </div>
  );
}
