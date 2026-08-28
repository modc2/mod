"use client";

// TRADER FILTER — the strat param that keeps only the best of the watchlist.
//
// A copy strat's watchlist rots: the trader who was top-10 last month is the
// one bleeding this week, and an equal-weight mirror keeps copying them until
// a human notices. `filter` makes that automatic — every scan, the engine
// ranks the enabled watchlist on the traders' OWN realized returns (inside
// this strat's market slice) and mirrors only the top N.
//
// This card is both the control and the receipt: the same `rankTraders` the
// engine and backtest call renders the live ranking underneath the knobs, so
// "who is my strat actually copying right now" is one glance, not a log dig.
// Ranking here uses the console's `traderStatsMap` (30d FIFO returns) and the
// engine recomputes it per cycle from the same formula — parity-fixture-pinned
// against the Rust `select_top_traders`, so this preview is the live truth.

import { rankTraders, DEFAULT_FILTER_TOP_N } from "../lib/strats/strat";
import { shortAddress } from "@/lib/auth";
import type { TraderFilter, TraderMetric, TraderRoiStats } from "../lib/types";

const METRICS: { key: TraderMetric; label: string; hint: string }[] = [
  { key: "score", label: "SCORE", hint: "P(win) × ROI — expected edge per dollar copied. The same math that ranks candidate trades." },
  { key: "sharpe", label: "SHARPE", hint: "ROI ÷ stdev of per-trade returns — consistency, not size. Needs ≥3 closed trades." },
  { key: "roi", label: "ROI", hint: "Mean realized return per closed trade in the 30d window." },
  { key: "winRate", label: "WIN%", hint: "Laplace-smoothed hit rate — shrinks toward 50% on thin samples." },
];

function fmtScore(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(3);
}

export default function TraderFilterCard({
  filter,
  onPatch,
  watchlist,
  traderStats,
}: {
  /** Current filter, or null when the strat copies every watched trader. */
  filter: TraderFilter | null;
  /** `null` clears the filter; a partial merges into it (turning it on). */
  onPatch: (changes: Partial<TraderFilter> | null) => void;
  /** Enabled watchlist addresses. */
  watchlist: string[];
  /** address (lowercase) → 30d stats, from the console's traderStatsMap. */
  traderStats: Record<string, TraderRoiStats>;
}) {
  const on = filter !== null;
  const metric = filter?.metric ?? "score";
  const topN = filter?.topN === undefined ? DEFAULT_FILTER_TOP_N : filter.topN;
  const rows = on ? rankTraders(watchlist, traderStats, filter) : [];
  const kept = rows.filter((r) => r.kept).length;

  return (
    <div className="w-full space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => onPatch(on ? null : { metric: "score", topN: DEFAULT_FILTER_TOP_N })}
          title={
            on
              ? "Turn the trader filter off — copy every enabled trader on the watchlist"
              : "Turn the trader filter on — copy only the best-scoring traders, re-ranked every scan"
          }
          className={`text-[10px] px-2 py-0.5 rounded border font-mono font-bold tracking-[0.08em] transition-colors ${
            on
              ? "border-green-400 text-green-400 bg-green-400/10"
              : "border-pixel-border text-pixel-gray hover:text-pixel-white"
          }`}
        >
          {on ? "ON" : "OFF"}
        </button>

        {on && (
          <>
            <span className="flex items-center gap-1">
              {METRICS.map((m) => (
                <button
                  key={m.key}
                  onClick={() => onPatch({ metric: m.key })}
                  title={m.hint}
                  className={`text-[10px] px-2 py-0.5 rounded border font-mono transition-colors ${
                    metric === m.key
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </span>

            <span
              className="flex items-center font-mono text-[12px] text-pixel-white"
              title="Copy only this many traders — the top N by the selected metric. 0 = no rank cut (the thresholds below still apply)."
            >
              <span className="text-[10px] text-pixel-gray mr-1">TOP</span>
              <input
                type="text"
                inputMode="numeric"
                value={topN}
                onChange={(e) => {
                  const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                  onPatch({ topN: isNaN(v) ? 0 : v });
                }}
                onFocus={(e) => e.target.select()}
                className="bg-transparent w-8 text-right outline-none border-b border-pixel-border/40 focus:border-green-400"
              />
            </span>

            <span
              className="flex items-center font-mono text-[12px] text-pixel-white"
              title="Hard floor on the metric — a trader inside the top N but below this is cut too. 0 on SCORE means 'must be expected-profitable'. Blank = no floor."
            >
              <span className="text-[10px] text-pixel-gray mr-1">MIN</span>
              <input
                type="text"
                inputMode="decimal"
                placeholder="—"
                value={filter?.minScore ?? ""}
                onChange={(e) => {
                  const raw = e.target.value.trim();
                  const v = raw === "" ? undefined : Number(raw);
                  onPatch({ minScore: v !== undefined && Number.isFinite(v) ? v : undefined });
                }}
                className="bg-transparent w-10 text-right outline-none border-b border-pixel-border/40 focus:border-green-400 placeholder:text-pixel-gray/40"
              />
            </span>

            <span
              className="flex items-center font-mono text-[12px] text-pixel-white"
              title="Ignore traders with fewer closed trades than this in the 30d window — under ~3, the stats are noise rather than skill."
            >
              <span className="text-[10px] text-pixel-gray mr-1">≥CLOSED</span>
              <input
                type="text"
                inputMode="numeric"
                placeholder="0"
                value={filter?.minSamples ?? ""}
                onChange={(e) => {
                  const raw = e.target.value.replace(/[^0-9]/g, "");
                  onPatch({ minSamples: raw === "" ? undefined : parseInt(raw, 10) });
                }}
                className="bg-transparent w-8 text-right outline-none border-b border-pixel-border/40 focus:border-green-400 placeholder:text-pixel-gray/40"
              />
            </span>
          </>
        )}
      </div>

      {on && (
        rows.length === 0 ? (
          <div className="text-[10px] font-mono text-pixel-gray">
            No traders on the watchlist yet — add some and the ranking appears here.
          </div>
        ) : (
          <div className="space-y-1">
            <div className="text-[10px] font-mono text-pixel-gray">
              copying{" "}
              <span className={kept === 0 ? "text-red-400" : "text-green-400"}>{kept}</span>
              {" "}of {rows.length} watched · re-ranked every scan
              {kept === 0 && " — nobody clears the bar, so this strat will not trade"}
            </div>
            <div className="flex flex-wrap gap-1">
              {rows.map((r) => (
                <span
                  key={r.address}
                  title={
                    r.kept
                      ? `#${r.rank} · ${metric} ${fmtScore(r.score)} · ${r.sampleSize} closed trades — COPIED`
                      : `#${r.rank} · ${metric} ${fmtScore(r.score)} · ${r.sampleSize} closed trades — NOT copied: ${r.reason}`
                  }
                  className={`px-1.5 py-0.5 text-[10px] font-mono rounded border tabular-nums ${
                    r.kept
                      ? "border-green-400/50 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray/70 line-through decoration-pixel-gray/40"
                  }`}
                >
                  {r.rank}. {shortAddress(r.address)} {fmtScore(r.score)}
                </span>
              ))}
            </div>
          </div>
        )
      )}
    </div>
  );
}
