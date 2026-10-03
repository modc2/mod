"use client";

// "$N on this trader" — the backtest panel on the trader page.
//
// The server does the replay (/trader/:addr/backtest) and, more importantly,
// grades its own data: every result arrives with pass/warn/fail checks on
// history coverage, the equity basis the scaling divides by, fills
// truncation, sample freshness and whether $N outsizes the book. This panel
// renders the checks ABOVE the fold with the numbers, because a backtest
// whose data checks failed is not a smaller answer — it is not an answer.
//
// Two models are shown and named: the equity curve (what your money would
// have felt, unrealised included) and the realised fills mirror (what
// actually closed, the live engine's convention). They legitimately differ
// on any wallet holding risk overnight; the `agreement` check names the gap.

import { useEffect, useRef, useState } from "react";
import { backtestTrader, fmtPnl, fmtUsd, TraderBacktest } from "../lib/api";

const H = 160;
const PAD = { t: 10, r: 12, b: 20, l: 52 };

export default function BacktestPanel({ addr, days }: { addr: string; days: number }) {
  const [capital, setCapital] = useState(1000);
  const [data, setData] = useState<TraderBacktest | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce the capital input — a user typing "25000" is five requests
  // without this, and the server answer for 2, 25 and 250 is noise.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      let alive = true;
      setLoading(true);
      setErr(null);
      backtestTrader(addr, capital, days)
        .then((d) => { if (alive) setData(d); })
        .catch((e) => { if (alive) setErr(e.message ?? String(e)); })
        .finally(() => { if (alive) setLoading(false); });
      return () => { alive = false; };
    }, 350);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [addr, capital, days]);

  return (
    <div className="panel">
      <div className="px-4 py-2 border-b border-border text-[11px] uppercase tracking-wider text-muted flex items-center justify-between">
        <span>backtest · your money on this trader ({days}d)</span>
        {data && data.available && (
          <span className={`text-[11px] normal-case tracking-normal ${data.ok ? "text-muted" : "text-warn"}`}>
            {data.ok ? "data checks passed" : "data checks FAILED — do not trust these numbers"}
          </span>
        )}
      </div>

      <div className="px-4 py-3 flex items-center gap-3 flex-wrap">
        <label className="text-[11px] uppercase tracking-wider text-muted" htmlFor="bt-capital">
          if I had put
        </label>
        <div className="flex items-center gap-1">
          <span className="text-muted text-sm">$</span>
          <input
            id="bt-capital"
            type="number"
            min={1}
            value={capital}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v > 0) setCapital(v);
            }}
            className="num w-28 bg-white/[0.04] border border-border rounded px-2 py-1 text-sm text-ink outline-none focus:border-accent/50"
          />
        </div>
        <span className="text-[11px] text-muted">
          on this trader {days} day{days === 1 ? "" : "s"} ago
          {data?.source === "combined" && " · combined account (no perp-only curve published)"}
        </span>
        {loading && <span className="text-[11px] text-muted">running…</span>}
      </div>

      {err && <div className="px-4 pb-3 text-xs text-loss">{err}</div>}

      {data && !data.available && (
        <div className="px-4 pb-3 text-xs text-muted">
          can&apos;t backtest this wallet — {data.note}
        </div>
      )}

      {data && data.available && (
        <>
          {/* The verdicts first. A number under a failed check is a trap. */}
          <Checks checks={data.checks} />

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-4 pb-3">
            <Stat
              label="you'd have now"
              value={fmtUsd(data.final_value)}
              tone={data.pnl >= 0 ? "win" : "loss"}
              sub={`from ${fmtUsd(data.capital)}`}
            />
            <Stat
              label="net pnl"
              value={fmtPnl(data.pnl)}
              tone={data.pnl >= 0 ? "win" : "loss"}
              sub={`${data.roi_pct >= 0 ? "+" : ""}${data.roi_pct.toFixed(1)}% over ${days}d`}
            />
            <Stat
              label="worst moment"
              value={data.max_drawdown > 0 ? `−${fmtUsd(data.max_drawdown)}` : "—"}
              tone={data.max_drawdown > 0 ? "warn" : undefined}
              sub={
                data.max_drawdown > 0
                  ? `${data.max_drawdown_pct.toFixed(1)}% below the peak on the way`
                  : "never below its peak"
              }
              title="Deepest peak-to-trough fall of YOUR equity inside the window — what you would have been down had you checked at the worst time."
            />
            <Stat
              label="realised mirror"
              value={data.mirror ? fmtPnl(data.mirror.net_pnl) : "—"}
              tone={data.mirror ? (data.mirror.net_pnl >= 0 ? "win" : "loss") : undefined}
              sub={
                data.mirror
                  ? `${data.mirror.fills} fills · ${fmtUsd(data.mirror.fees)} fees${data.mirror.truncated ? " · tape truncated" : ""}`
                  : "no fills data"
              }
              title="Closed fills only, scaled by your capital over the trader's equity — the live copy engine's convention. Differs from the curve on any book holding risk overnight."
            />
          </div>

          {data.points.length >= 2 && <EquityCurve data={data} />}

          <div className="px-4 py-2 text-[10px] text-muted border-t border-border">
            Scaled against {fmtUsd(data.basis_equity)} of trader equity at the window open; the trader
            made {fmtPnl(data.trader_window_pnl)} on that book. A replay of the past is not a forecast.
          </div>
        </>
      )}
    </div>
  );
}

/** Dot + word + sentence, never color alone. Fail first — the reader must
 *  trip over it before reaching any number below. */
function Checks({ checks }: { checks: TraderBacktest["checks"] }) {
  const order = { fail: 0, warn: 1, pass: 2 } as Record<string, number>;
  const sorted = [...checks].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
  const cls = (s: string) =>
    s === "pass" ? "text-win" : s === "warn" ? "text-warn" : "text-loss";
  return (
    <div className="px-4 pb-3">
      <div className="text-[10px] uppercase tracking-wider text-muted mb-1">data checks</div>
      <div className="space-y-0.5">
        {sorted.map((c) => (
          <div key={c.name} className="flex items-baseline gap-2 text-[11px]">
            <span className={`num shrink-0 w-36 whitespace-nowrap ${cls(c.status)}`}>
              ● {c.status} · {c.name}
            </span>
            <span className="text-muted">{c.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, tone, sub, title }: {
  label: string;
  value: string;
  tone?: "win" | "loss" | "warn";
  sub?: string;
  title?: string;
}) {
  const toneClass =
    tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : tone === "warn" ? "text-warn" : "";
  return (
    <div className="rounded border border-border bg-white/[0.02] p-3" title={title}>
      <div className="stat">{label}</div>
      <div className={`text-lg num mt-1 ${toneClass}`}>{value}</div>
      {sub && <div className="text-[10px] mt-0.5 num text-muted">{sub}</div>}
    </div>
  );
}

/** Your equity over the window: dep-free SVG, baseline at the deposit. */
function EquityCurve({ data }: { data: TraderBacktest }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const pts = data.points;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  let lo = Math.min(...pts.map((p) => p[1]), data.capital);
  let hi = Math.max(...pts.map((p) => p[1]), data.capital);
  if (hi === lo) { hi += 1; lo -= 1; }
  const padV = (hi - lo) * 0.08;
  lo -= padV; hi += padV;

  const iw = Math.max(0, w - PAD.l - PAD.r);
  const ih = H - PAD.t - PAD.b;
  const X = (t: number) => PAD.l + ((t - t0) / (t1 - t0 || 1)) * iw;
  const Y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * ih;

  const line = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const fmtDay = (ms: number) =>
    new Date(ms).toLocaleDateString([], { month: "short", day: "numeric" });

  return (
    <div ref={wrapRef} className="px-0" role="img"
      aria-label={`your equity over the window, ending at ${fmtUsd(data.final_value)}`}>
      {w > 0 && (
        <svg width={w} height={H} className="block">
          {/* the deposit line — everything above it is profit */}
          <line x1={PAD.l} x2={w - PAD.r} y1={Y(data.capital)} y2={Y(data.capital)}
            stroke="var(--chart-zero)" strokeWidth={1} strokeDasharray="3 3" />
          <text x={PAD.l - 8} y={Y(data.capital) + 3} textAnchor="end" fontSize={10}
            className="fill-muted num">{fmtUsd(data.capital)}</text>
          <path d={line} fill="none" stroke="var(--chart-line)" strokeWidth={2}
            strokeLinejoin="round" strokeLinecap="round" />
          <circle cx={X(t1)} cy={Y(pts[pts.length - 1][1])} r={4}
            fill="var(--chart-line)" stroke="var(--chart-surface)" strokeWidth={2} />
          <text x={X(t1)} y={Y(pts[pts.length - 1][1]) - 8} textAnchor="end" fontSize={10}
            className={pts[pts.length - 1][1] >= data.capital ? "fill-win num" : "fill-loss num"}>
            {fmtUsd(pts[pts.length - 1][1])}
          </text>
          <text x={PAD.l} y={H - 6} fontSize={10} className="fill-muted">{fmtDay(t0)}</text>
          <text x={w - PAD.r} y={H - 6} textAnchor="end" fontSize={10} className="fill-muted">{fmtDay(t1)}</text>
        </svg>
      )}
    </div>
  );
}
