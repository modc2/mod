"use client";

// SCORE STRATS — one row per score function, each one a strat in MY STRATS:
// "copy the top N this function ranks". The row is the rule's control +
// report card: its N, who it picked, and its OUT-OF-SAMPLE backtest (picked
// on the board from N days ago, traded the N days since). Clicking a row
// makes its strat the active one — BACKTEST/LIVE read that.
//
// The work lives in lib/useScoreStrats.ts (loop) and lib/scoreStrats.ts
// (rank / materialize / grade); this file only draws it.

import { useMemo } from "react";

import { fmtUsd } from "../lib/stratStats";
import { scoreStratId, SCORE_STRAT_MAX_TOP_N, SCORE_STRAT_TEST_WINDOWS } from "../lib/scoreStrats";
import { useScoreStrats } from "../lib/useScoreStrats";
import type { SavedIndex } from "../lib/types";
import Sparkline from "./Sparkline";

function when(ts: number): string {
  return new Date(ts).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function ScoreStratsPanel({
  indexes, activeId, select, owner, running,
}: {
  indexes: SavedIndex[];
  activeId: string | null;
  select: (id: string) => void;
  owner: string | null;
  running: Set<string>;
}) {
  const { listings, settings, rows, progress, busy, rerank, grade, setTopN, setTestDays, restore } =
    useScoreStrats(owner, running);
  const byId = useMemo(() => new Map(indexes.map((i) => [i.id, i])), [indexes]);

  // Graded rules first, best out-of-sample ROI on top; ungraded keep shelf order.
  const ordered = useMemo(() => {
    const roi = (id: string) => byId.get(id)?.scoreFn?.oos?.roi;
    return listings
      .map((l, i) => ({ l, i, id: scoreStratId(l) }))
      .sort((a, b) => {
        const ra = roi(a.id), rb = roi(b.id);
        if (ra == null && rb == null) return a.i - b.i;
        if (ra == null) return 1;
        if (rb == null) return -1;
        return rb - ra;
      });
  }, [listings, byId]);

  const days = settings.testDays;

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-1.5 px-1">
        <span className="text-[9.5px] font-mono text-pixel-gray">TEST</span>
        <div className="flex border border-pixel-border rounded-full overflow-hidden">
          {SCORE_STRAT_TEST_WINDOWS.map((d) => (
            <button
              key={d}
              onClick={() => setTestDays(d)}
              disabled={busy}
              className={`px-2 py-0.5 text-[9.5px] font-mono font-semibold ${
                days === d ? "bg-pixel-border-light text-pixel-white" : "text-pixel-gray hover:bg-pixel-border-light/50"
              }`}
              title={`Pick on the board from ${d}d ago, then trade the ${d} day${d === 1 ? "" : "s"} since`}
            >
              {d}D
            </button>
          ))}
        </div>
        <button
          onClick={() => void rerank()}
          disabled={busy}
          className={`px-2 py-0.5 rounded-full border border-pixel-border text-[9.5px] font-mono font-semibold text-pixel-gray hover:text-green-400 hover:border-green-400/50 ${busy ? "opacity-40" : ""}`}
          title="Re-rank today's board under every score function and refresh each strat's traders. Strats running live are left alone."
        >
          ↻ RE-RANK
        </button>
        <button
          onClick={() => void grade()}
          disabled={busy}
          className={`px-2 py-0.5 rounded-full border border-green-400/50 text-[9.5px] font-mono font-semibold text-green-400 hover:bg-green-400/10 ${busy ? "opacity-40" : ""}`}
          title={`Backtest every score strat over the last ${days}d, with traders picked on the board as it stood ${days}d ago`}
        >
          ▶ BACKTEST {days}D
        </button>
        <span className="text-[9.5px] font-mono text-amber-300/80 truncate">{progress ?? ""}</span>
      </div>
      <div className="px-1 text-[9px] font-mono leading-snug text-pixel-gray/80">
        Each score function is a strat that copies the traders it ranks highest, sized to your capital.
        The backtest picks them on the board from {days} day{days === 1 ? "" : "s"} ago and then trades the days
        since, so the picks never saw the days they are graded on.
      </div>

      <div className="flex flex-col gap-0.5">
        {ordered.map(({ l, id }) => {
          const strat = byId.get(id);
          const meta = strat?.scoreFn;
          const oos = meta?.oos;
          const row = rows[l.id];
          const dismissed = settings.dismissed.includes(id);
          const topN = settings.topN[l.id] ?? meta?.topN ?? 5;
          const isActive = activeId === id;
          const live = running.has(id);
          return (
            <div
              key={id}
              onClick={() => strat && select(id)}
              className={`flex items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 transition-colors ${
                strat ? "cursor-pointer" : ""
              } ${isActive ? "bg-green-400/[0.07] ring-1 ring-green-400/30" : "hover:bg-pixel-white/[0.04] ring-1 ring-pixel-border/60"}`}
            >
              <span className="flex-1 min-w-0">
                <span className={`block truncate text-[11.5px] font-mono font-semibold ${isActive ? "text-green-400" : dismissed ? "text-pixel-gray" : "text-pixel-white"}`}>
                  {l.name}
                  {!l.builtin && <span className="ml-1.5 text-[8.5px] tracking-[0.1em] text-cyan-400">COMMUNITY</span>}
                  {live && <span className="ml-1.5 text-[8.5px] tracking-[0.1em] text-green-400">LIVE</span>}
                </span>
                <span className="block truncate text-[9.5px] font-mono text-pixel-gray">
                  {dismissed
                    ? "deleted from your strats"
                    : row?.kind === "busy" ? <span className="text-amber-300/80">{row.what}…</span>
                    : row?.kind === "error" ? <span className="text-red-400/90" title={row.error}>{row.error}</span>
                    : row?.kind === "skipped" ? row.why
                    : strat
                      ? `${strat.traders.length} trader${strat.traders.length === 1 ? "" : "s"} · ranked ${meta ? when(meta.rankedAt) : "—"}${
                          strat.traders.length < topN ? ` · only ${strat.traders.length} passed its gates` : ""}`
                      : "not ranked yet"}
                </span>
              </span>

              {!dismissed && (
                <span className="shrink-0 flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
                  <span className="text-[9px] font-mono text-pixel-gray mr-0.5">TOP</span>
                  <button
                    onClick={() => setTopN(l, topN - 1)}
                    disabled={busy || topN <= 1}
                    className="w-4 text-[11px] font-mono text-pixel-gray hover:text-pixel-white disabled:opacity-30"
                    title="Copy one fewer"
                  >
                    −
                  </button>
                  <span className="w-5 text-center text-[11px] font-mono font-semibold tabular-nums text-pixel-white">{topN}</span>
                  <button
                    onClick={() => setTopN(l, topN + 1)}
                    disabled={busy || topN >= SCORE_STRAT_MAX_TOP_N}
                    className="w-4 text-[11px] font-mono text-pixel-gray hover:text-pixel-white disabled:opacity-30"
                    title="Copy one more"
                  >
                    +
                  </button>
                </span>
              )}

              {oos && oos.curve.length >= 2 && (
                <span className="shrink-0 text-pixel-gray" title={`Equity over the ${oos.testDays}d test`}>
                  <Sparkline data={oos.curve} width={56} height={18} />
                </span>
              )}
              <span
                className="shrink-0 w-[92px] text-right"
                title={oos
                  ? `Picked ${oos.picks} on the board of ${when(oos.scanId * 1000)}, traded the ${oos.testDays}d since · $${oos.capital} paper · ${oos.trades} trades${oos.note ? ` · ${oos.note}` : ""}`
                  : "Not backtested yet"}
              >
                {oos ? (
                  <>
                    <span className={`block text-[11px] font-mono font-semibold tabular-nums ${oos.pnl > 0 ? "text-green-400" : oos.pnl < 0 ? "text-red-400" : "text-pixel-gray"}`}>
                      {oos.pnl >= 0 ? "+" : ""}{fmtUsd(oos.pnl)} <span className="text-[9px] font-normal">{oos.roi >= 0 ? "+" : ""}{oos.roi.toFixed(1)}%</span>
                    </span>
                    <span className="block text-[9px] font-mono text-pixel-gray tabular-nums">
                      {oos.testDays}D · {oos.trades} trades{oos.testDays !== days ? " · old window" : ""}
                    </span>
                    {(oos.markedUsd ?? 0) > Math.max(10, Math.abs(oos.pnl) * 0.5) && (
                      <span
                        className="block text-[9px] font-mono text-amber-300/90 tabular-nums"
                        title={`${fmtUsd(oos.markedUsd ?? 0)} of exit value is open positions priced at the last trade, not at a known result — those markets haven't resolved yet, so this number can still move a lot either way`}
                      >
                        {fmtUsd(oos.markedUsd ?? 0)} unresolved
                      </span>
                    )}
                  </>
                ) : (
                  <span className="text-[9.5px] font-mono text-pixel-gray/60">{dismissed ? "" : "no backtest"}</span>
                )}
              </span>

              {dismissed && (
                <button
                  onClick={(e) => { e.stopPropagation(); restore(l); }}
                  disabled={busy}
                  className="shrink-0 px-1.5 py-0.5 rounded border border-pixel-border text-[9px] font-mono font-semibold text-pixel-gray hover:text-green-400 hover:border-green-400/60"
                  title="Make this score function's strat again"
                >
                  RESTORE
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
