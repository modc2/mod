"use client";

import { useMemo } from "react";
import type { Backtest } from "../lib/types";
import { shortSs58 } from "../lib/api";

/**
 * BacktestPanel — what the basket in the builder would have done.
 *
 * It re-renders on every edit because StratPicker re-runs the replay on
 * every edit; this component only draws. The curve is a plain SVG polyline
 * (the equity series is already downsampled server-side), and everything
 * that would make the number look better than it is — the traders with no
 * indexed history, the thin window, the missing execution lag — is printed
 * next to it rather than left out.
 */
export default function BacktestPanel({
  backtest,
  loading,
  error,
  days,
  onDays,
  compact,
}: {
  backtest: Backtest | null;
  loading: boolean;
  error: string;
  days: number;
  onDays: (d: number) => void;
  compact?: boolean;
}) {
  const path = useMemo(() => {
    const pts = backtest?.curve || [];
    if (pts.length < 2) return "";
    const xs = pts.map((p) => p.t);
    const ys = pts.map((p) => p.equity_tao);
    const x0 = xs[0];
    const xr = Math.max(1, xs[xs.length - 1] - x0);
    const lo = Math.min(...ys);
    const hi = Math.max(...ys);
    const yr = hi - lo || 1;
    return pts
      .map((p, i) => {
        const x = ((p.t - x0) / xr) * 100;
        const y = 30 - ((p.equity_tao - lo) / yr) * 28 - 1;
        return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
  }, [backtest]);

  const st = backtest?.stats || {};
  const up = (st.total_return_pct ?? 0) >= 0;
  const color = up ? "#4ade80" : "#f87171";

  const stat = (label: string, value: string, title?: string) => (
    <div className="min-w-0" title={title}>
      <div className="text-[9px] uppercase tracking-[2px] text-pixel-gray">{label}</div>
      <div className="font-mono text-[13px] text-pixel-white truncate">{value}</div>
    </div>
  );

  return (
    <div className="border-t-2 border-pixel-border pt-2 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
          Backtest {loading && <span className="text-pixel-gray-light">· running</span>}
        </div>
        <div className="flex gap-1">
          {[1, 7, 30].map((d) => (
            <button
              key={d}
              type="button"
              className={`pixel-btn text-[9px] px-1.5 py-0.5 ${
                days === d ? "border-green-400 text-green-400" : ""
              }`}
              onClick={() => onDays(d)}
            >
              {d}D
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <p className="text-[11px] font-mono text-red-400 break-all">{error}</p>
      ) : !backtest ? (
        <p className="arcade-prose arcade-prose-sm">
          Add traders and this replays the basket over the last {days} day
          {days === 1 ? "" : "s"} — automatically, on every change.
        </p>
      ) : !backtest.ok ? (
        <p className="arcade-prose arcade-prose-sm">{backtest.note}</p>
      ) : (
        <>
          <svg
            viewBox="0 0 100 30"
            preserveAspectRatio="none"
            className="w-full"
            style={{ height: compact ? 56 : 72 }}
          >
            <path d={path} fill="none" stroke={color} strokeWidth="0.6" vectorEffect="non-scaling-stroke" />
          </svg>

          <div className={`grid gap-2 ${compact ? "grid-cols-2" : "grid-cols-4"}`}>
            {stat("Return", `${(st.total_return_pct ?? 0).toFixed(2)}%`,
              "Basket return over the covered window")}
            {stat("On 100τ", `${(st.end_tao ?? 0).toFixed(2)}τ`,
              `${(st.pnl_tao ?? 0) >= 0 ? "+" : ""}${(st.pnl_tao ?? 0).toFixed(2)}τ against the capital you set`)}
            {stat("Max DD", `${(st.max_drawdown_pct ?? 0).toFixed(2)}%`,
              "Deepest drop from a peak inside the window")}
            {stat("Sharpe", st.sharpe == null ? "—" : st.sharpe.toFixed(2),
              "Annualized, from the step returns. Null when the window is too thin to mean anything.")}
          </div>

          <div className="text-[10px] font-mono text-pixel-gray">
            {backtest.covered_hours.toFixed(0)}h of data
            {backtest.covered_hours < backtest.requested_hours - 1 && (
              <> · asked for {backtest.requested_hours}h, the index only goes back this far</>
            )}
            {backtest.thin && <> · too few points for the stats to mean much</>}
          </div>

          {backtest.per_trader.length > 1 && (
            <table className="pixel-table">
              <thead>
                <tr>
                  <th>Trader</th>
                  <th className="num">Share</th>
                  <th className="num">Its return</th>
                  <th className="num" title="Its slice of the basket's PnL — these sum to the basket return">Contribution</th>
                </tr>
              </thead>
              <tbody>
                {backtest.per_trader.slice(0, 8).map((r) => (
                  <tr key={r.ss58}>
                    <td className="font-mono">{r.label || shortSs58(r.ss58)}</td>
                    <td className="num font-mono text-pixel-gray-light">
                      {(r.weight * 100).toFixed(1)}%
                    </td>
                    <td className="num font-mono" style={{ color: r.return_pct >= 0 ? "#4ade80" : "#f87171" }}>
                      {r.return_pct.toFixed(2)}%
                    </td>
                    <td className="num font-mono" style={{ color: r.contribution_pct >= 0 ? "#4ade80" : "#f87171" }}>
                      {r.contribution_pct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {backtest.truncated && (
            <p className="text-[10px] font-mono text-amber-400">
              replayed the {backtest.truncated.kept} heaviest traders ·{" "}
              {backtest.truncated.dropped} left out to keep this instant
            </p>
          )}

          {backtest.skipped.length > 0 && (
            <p className="text-[10px] font-mono text-amber-400">
              {backtest.skipped.length} trader{backtest.skipped.length === 1 ? "" : "s"} skipped
              (no indexed history yet) — their weight went to the rest:{" "}
              {backtest.skipped.slice(0, 4).map((s) => shortSs58(s.ss58)).join(", ")}
              {backtest.skipped.length > 4 ? " …" : ""}
            </p>
          )}

          <p className="arcade-prose arcade-prose-sm">
            Mirrors each trader&apos;s portfolio return at its weight, rebalanced
            every step. No execution lag, slippage or fees — a copied trade
            lands after the leader&apos;s, so live sits under this curve.
          </p>
        </>
      )}
    </div>
  );
}
