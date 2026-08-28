"use client";

import { useMemo, useRef, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CurveData, CurveEvent, CurvePoint } from "../lib/types";
import { useCurrency, fmtValue, fmtPnlValue } from "../context/CurrencyContext";
import { useThemeColors } from "../context/ThemeContext";
import { fmtCompact, fmtPct } from "../lib/api";
import type { Currency } from "../context/CurrencyContext";

// One series on the plot at a time — never two y-scales. The trade markers
// are the second layer, and they keep gain/loss to themselves, so the line
// takes a neutral accent. All four come off the active skin (recharts
// paints into SVG attributes, where a `var()` isn't dependable).

type Mode = "pnl" | "value" | "market";

const MODES: { key: Mode; label: string; hint: string }[] = [
  { key: "pnl", label: "PNL", hint: "cumulative profit vs the start of the window" },
  { key: "value", label: "VALUE", hint: "portfolio value (stake + free TAO)" },
  { key: "market", label: "MARKET", hint: "PnL from alpha price moves only — deposits stripped out" },
];

const KEY: Record<Mode, keyof CurvePoint> = {
  pnl: "pnl_tao",
  value: "value_tao",
  market: "market_tao",
};

const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false,
  });

const fmtDay = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false,
  });

// Axis ticks stay short — the tooltip carries the precise number.
const fmtAxis = (v: number, currency: Currency, usdPerTao: number | null) =>
  currency === "USD" && usdPerTao && usdPerTao > 0
    ? `$${fmtCompact(v * usdPerTao)}`
    : `${fmtCompact(v)} τ`;

export default function PnlCurve({
  curve,
  days,
}: {
  curve: CurveData;
  days: number;
}) {
  const skin = useThemeColors();
  const LINE = skin.cyan;      // the series itself — never a P&L colour
  const BUY = skin.lime;
  const SELL = skin.red;
  const SURFACE = skin.panel;  // the ring that keeps markers separable
  const { currency, usdPerTao } = useCurrency();
  const [mode, setMode] = useState<Mode>("pnl");
  const [showTrades, setShowTrades] = useState(true);
  const [activeTs, setActiveTs] = useState<number | null>(null);
  const [pinnedTs, setPinnedTs] = useState<number | null>(null);
  const tapeRef = useRef<HTMLDivElement>(null);

  const key = KEY[mode];
  const points = curve.points;

  // Markers ride the same series the line does, so a dot is always literally
  // on the curve rather than floating next to it.
  const evY = (e: CurveEvent) =>
    mode === "value" ? e.value_tao : mode === "pnl" ? e.pnl_tao : marketAt(e.ts);

  const marketByTs = useMemo(() => {
    const m = new Map<number, number>();
    points.forEach((p) => m.set(p.ts, p.market_tao));
    return m;
  }, [points]);

  function marketAt(ts: number) {
    if (marketByTs.has(ts)) return marketByTs.get(ts) as number;
    // The series is thinned for the chart; snap to the nearest kept point.
    let best = 0;
    let bestD = Infinity;
    points.forEach((p) => {
      const d = Math.abs(p.ts - ts);
      if (d < bestD) { bestD = d; best = p.market_tao; }
    });
    return best;
  }

  const events = curve.events;
  const maxVol = Math.max(
    1e-9,
    ...events.map((e) => Math.abs(e.buy_tao) + Math.abs(e.sell_tao)),
  );

  const scatter = useMemo(() => {
    const mk = (side: "buy" | "sell") =>
      events
        .filter((e) => e.side === side)
        .map((e) => ({ ts: e.ts, y: evY(e), ev: e }));
    return { buys: mk("buy"), sells: mk("sell") };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, mode, points]);

  const pointByTs = useMemo(() => {
    const m = new Map<number, CurvePoint>();
    points.forEach((p) => m.set(p.ts, p));
    return m;
  }, [points]);

  const eventByTs = useMemo(() => {
    const m = new Map<number, CurveEvent>();
    events.forEach((e) => m.set(e.ts, e));
    return m;
  }, [events]);

  if (curve.warming || points.length < 2) {
    return (
      <div className="pixel-panel p-6 text-sm text-pixel-gray">
        <p className="font-display text-lg font-bold text-pixel-white mb-2">
          PnL curve
        </p>
        <p>
          Only {curve.coverage.snapshots} snapshot
          {curve.coverage.snapshots === 1 ? "" : "s"} on record — the curve
          needs at least two. The snapshot loop records this account every{" "}
          {Math.round((curve.coverage.interval_sec || 1800) / 60)} min.
        </p>
      </div>
    );
  }

  const t = curve.totals;
  const focusTs = pinnedTs ?? activeTs;

  const marker = (side: "buy" | "sell") =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (props: any) => {
      const { cx, cy, payload } = props;
      if (cx == null || cy == null) return <g />;
      const e: CurveEvent = payload.ev;
      const vol = Math.abs(e.buy_tao) + Math.abs(e.sell_tao);
      const focused = focusTs === e.ts;
      // 9–15px triangles: over the 8px minimum hit target even at the floor,
      // scaled by how much TAO moved.
      const r = (focused ? 3 : 0) + 5 + 3.5 * Math.sqrt(vol / maxVol);
      const up = side === "buy";
      const pts = up
        ? `${cx},${cy - r} ${cx - r},${cy + r * 0.8} ${cx + r},${cy + r * 0.8}`
        : `${cx},${cy + r} ${cx - r},${cy - r * 0.8} ${cx + r},${cy - r * 0.8}`;
      return (
        <g
          onMouseEnter={() => setActiveTs(e.ts)}
          onMouseLeave={() => setActiveTs(null)}
          onClick={(clickEv) => {
            // The chart's own onClick clears the pin — don't let it undo this.
            clickEv.stopPropagation();
            setPinnedTs(pinnedTs === e.ts ? null : e.ts);
            tapeRef.current
              ?.querySelector<HTMLElement>(`[data-ts="${e.ts}"]`)
              ?.scrollIntoView({ block: "center", behavior: "smooth" });
          }}
          style={{ cursor: "pointer" }}
        >
          {/* 2px surface ring keeps overlapping markers separable */}
          <polygon
            points={pts}
            fill={up ? BUY : SELL}
            stroke={SURFACE}
            strokeWidth={2}
            opacity={focused || focusTs === null ? 1 : 0.35}
          />
        </g>
      );
    };

  return (
    <section className="space-y-3">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-bold">
            PnL curve{" "}
            {/* The coverage caveat takes its own line on a phone — inline it
                wrapped a 20px display heading across three. */}
            <span className="block sm:inline text-pixel-gray font-normal text-[11px] sm:text-sm">
              ({curve.coverage.actual_days.toFixed(1)}d of snapshots
              {curve.coverage.actual_days < days - 0.5 && ` · ${days}d requested`})
            </span>
          </h2>
          <p className="text-[11px] text-pixel-gray">
            {t.trades} inferred trade{t.trades === 1 ? "" : "s"} in{" "}
            {t.events} rebalance{t.events === 1 ? "" : "s"} · every point is a
            real block
          </p>
        </div>
        <div className="rail no-scrollbar -mx-3 px-3 sm:mx-0 sm:px-0 w-full sm:w-auto">
          {MODES.map((m) => (
            <button
              key={m.key}
              title={m.hint}
              onClick={() => setMode(m.key)}
              className={`pixel-btn text-[11px] px-3 py-1 ${
                mode === m.key ? "border-green-400 text-green-400" : "text-pixel-gray-light"
              }`}
            >
              {m.label}
            </button>
          ))}
          <button
            onClick={() => setShowTrades((s) => !s)}
            className={`pixel-btn text-[11px] px-3 py-1 ${
              showTrades ? "text-pixel-white" : "text-pixel-gray"
            }`}
          >
            {showTrades ? "◆ TRADES" : "◇ TRADES"}
          </button>
        </div>
      </div>

      {/* What actually drove the number — trading vs topping up the stake */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {/* Window-explicit: the header stat uses the {days}d baseline, this
            one measures across the snapshots actually on record. */}
        <Metric
          label={`net PnL · ${curve.coverage.actual_days.toFixed(1)}d`}
          tao={t.pnl_tao}
          pct={t.pnl_pct}
        />
        <Metric label="from price moves" tao={t.market_pnl_tao} />
        <Metric label="from stake flows" tao={t.flow_tao} />
        <div className="pixel-panel p-3">
          <p className="text-[10px] tracking-[2px] uppercase text-pixel-gray mb-1">
            traded volume
          </p>
          {/* Bought over sold, stacked in a narrow tile — side by side the
              pair wrapped mid-number. */}
          <p className="font-mono text-[13px] tabular-nums">
            <span className="block sm:inline" style={{ color: BUY }}>
              ▲ {fmtValue(t.buy_tao, currency, usdPerTao)}
            </span>
            <span className="text-pixel-gray mx-1 hidden sm:inline">/</span>
            <span className="block sm:inline" style={{ color: SELL }}>
              ▼ {fmtValue(t.sell_tao, currency, usdPerTao)}
            </span>
          </p>
        </div>
      </div>

      <div className="pixel-panel p-3 pr-4">
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart
              data={points}
              margin={{ top: 12, right: 8, bottom: 4, left: 4 }}
              onClick={() => setPinnedTs(null)}
            >
              <defs>
                <linearGradient id="ct-curve-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={LINE} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={LINE} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="ts"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={fmtDay}
                tickLine={false}
                axisLine={false}
                minTickGap={48}
              />
              <YAxis
                tickFormatter={(v) => fmtAxis(v, currency, usdPerTao)}
                tickLine={false}
                axisLine={false}
                width={74}
                domain={["auto", "auto"]}
              />
              {mode !== "value" && (
                <ReferenceLine y={0} stroke={skin.border} strokeDasharray="3 3" />
              )}
              {focusTs !== null && (
                <ReferenceLine x={focusTs} stroke={skin.fgDim} />
              )}
              <Tooltip
                cursor={{ stroke: skin.fgDim, strokeWidth: 1 }}
                isAnimationActive={false}
                content={(props) => {
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  const { active, label, payload } = props as any;
                  if (!active) return null;
                  // Hovering a trade marker makes recharts report the scatter
                  // item instead of an axis label — fall back to its point.
                  const ts = Number(label ?? payload?.[0]?.payload?.ts);
                  const p = pointByTs.get(ts);
                  if (!p) return null;
                  const ev = eventByTs.get(ts);
                  return (
                    <div className="pixel-panel p-2 text-[11px] font-mono space-y-1 bg-pixel-panel">
                      <p className="text-pixel-gray">{fmtTime(ts)} · block {p.block}</p>
                      <p className="text-pixel-white">
                        {mode === "value" ? "value " : mode === "market" ? "market pnl " : "pnl "}
                        <span className="tabular-nums">
                          {mode === "value"
                            ? fmtValue(p.value_tao, currency, usdPerTao)
                            : fmtPnlValue(p[key] as number, currency, usdPerTao)}
                        </span>
                        {mode === "pnl" && (
                          <span className="text-pixel-gray"> ({fmtPct(p.pnl_pct)})</span>
                        )}
                      </p>
                      {ev && (
                        <div className="pt-1 border-t border-pixel-border space-y-0.5">
                          <p style={{ color: ev.side === "buy" ? BUY : SELL }}>
                            {ev.side === "buy" ? "▲" : "▼"} {ev.legs} leg
                            {ev.legs === 1 ? "" : "s"} · net{" "}
                            {fmtPnlValue(ev.net_tao, currency, usdPerTao)}
                          </p>
                          {ev.top.map((l) => (
                            <p key={l.netuid} className="text-pixel-gray">
                              {l.side === "buy" ? "+" : "−"} SN{l.netuid} {l.subnet_name}{" "}
                              {fmtValue(l.tao_value, currency, usdPerTao)}
                            </p>
                          ))}
                          {ev.legs > ev.top.length && (
                            <p className="text-pixel-gray">
                              +{ev.legs - ev.top.length} more
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  );
                }}
              />
              <Area
                // Stepped, to match the pixel-lattice sparklines — a smooth
                // interpolation is the one curve nothing 8-bit could draw.
                type="stepAfter"
                dataKey={key as string}
                stroke={LINE}
                strokeWidth={2}
                fill="url(#ct-curve-fill)"
                dot={false}
                activeDot={{ r: 3, fill: LINE, stroke: SURFACE, strokeWidth: 2 }}
                isAnimationActive={false}
              />
              {showTrades && (
                <Scatter
                  data={scatter.buys}
                  dataKey="y"
                  shape={marker("buy")}
                  isAnimationActive={false}
                />
              )}
              {showTrades && (
                <Scatter
                  data={scatter.sells}
                  dataKey="y"
                  shape={marker("sell")}
                  isAnimationActive={false}
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 pt-1 text-[10px] text-pixel-gray font-mono">
          <span>
            <span style={{ color: LINE }}>━</span>{" "}
            {mode === "value" ? "portfolio value" : mode === "market" ? "price-move PnL" : "cumulative PnL"}
          </span>
          <span><span style={{ color: BUY }}>▲</span> buy / stake added</span>
          <span><span style={{ color: SELL }}>▼</span> sell / unstaked</span>
          <span className="hidden sm:inline sm:ml-auto">marker size = TAO moved</span>
        </div>
      </div>

      {showTrades && curve.trades.length > 0 && (
        <div className="pixel-panel overflow-hidden">
          <div ref={tapeRef} className="max-h-[320px] overflow-auto">
            <table className="pixel-table" style={{ minWidth: 700 }}>
              <thead className="sticky">
                <tr>
                  <th>Time</th>
                  <th>Side</th>
                  <th>Subnet</th>
                  <th className="num">Alpha</th>
                  <th className="num">Price</th>
                  <th className="num">Value</th>
                  <th className="num">PnL after</th>
                </tr>
              </thead>
              <tbody>
                {curve.trades
                  .slice()
                  .reverse()
                  .map((tr, i) => (
                    <tr
                      key={`${tr.ts}-${tr.netuid}-${i}`}
                      data-ts={tr.ts}
                      onMouseEnter={() => setActiveTs(tr.ts)}
                      onMouseLeave={() => setActiveTs(null)}
                      onClick={() => setPinnedTs(pinnedTs === tr.ts ? null : tr.ts)}
                      style={{
                        cursor: "pointer",
                        background: focusTs === tr.ts ? "var(--table-hover)" : undefined,
                      }}
                    >
                      <td className="font-mono text-[11px] text-pixel-gray whitespace-nowrap">
                        {fmtTime(tr.ts)}
                      </td>
                      <td
                        className="font-mono text-[11px]"
                        style={{ color: tr.side === "buy" ? BUY : SELL }}
                      >
                        {tr.side === "buy" ? "▲ BUY" : "▼ SELL"}
                      </td>
                      <td>
                        <span className="font-mono text-pixel-white">SN{tr.netuid}</span>
                        <span className="text-pixel-gray text-xs ml-2">{tr.subnet_name}</span>
                      </td>
                      <td className="num font-mono">{tr.alpha.toFixed(4)}</td>
                      <td className="num font-mono">{tr.price_tao.toFixed(6)}</td>
                      <td className="num font-mono">
                        {fmtValue(tr.tao_value, currency, usdPerTao)}
                      </td>
                      <td
                        className="num font-mono tabular-nums"
                        style={{ color: tr.pnl_tao >= 0 ? BUY : SELL }}
                      >
                        {fmtPnlValue(tr.pnl_tao, currency, usdPerTao)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="text-[10px] text-pixel-gray leading-relaxed">
        {curve.note}
        {curve.coverage.window_short &&
          " · fewer snapshots than the selected window — showing everything on record"}
      </p>
    </section>
  );
}

function Metric({ label, tao, pct }: { label: string; tao: number; pct?: number }) {
  const { lime: BUY, red: SELL } = useThemeColors();
  const { currency, usdPerTao } = useCurrency();
  return (
    <div className="pixel-panel p-3">
      <p className="text-[10px] tracking-[2px] uppercase text-pixel-gray mb-1">
        {label}
      </p>
      <p
        className="font-mono text-[13px] tabular-nums"
        style={{ color: tao >= 0 ? BUY : SELL }}
      >
        {fmtPnlValue(tao, currency, usdPerTao)}
        {pct !== undefined && (
          <span className="opacity-60 ml-1">({fmtPct(pct)})</span>
        )}
      </p>
    </div>
  );
}
