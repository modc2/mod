"use client";

// The window ladder on a strat card: one strat, backtested over the last
// 1 · 3 · 7 · 14 · 30 days, side by side.
//
// One window is one sample. A strat that's green over 1D and red over every
// window behind it caught a lucky day; one that's green across the row is the
// one worth money. Each cell is the same replay the hub runs (lib/hubReplay.ts,
// net of fees) — the background worker fills the whole row every pass, so
// this component only reads (see `useStratWindows`).

import { HUB_WINDOWS, type WindowRow } from "../lib/hubBacktest";

/** Compact signed dollars — five cells have to fit a 280px card. */
function shortUsd(v: number): string {
  const a = Math.abs(v);
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  const body = a >= 10_000 ? `${(a / 1000).toFixed(0)}k` : a >= 1000 ? `${(a / 1000).toFixed(1)}k` : a >= 100 ? a.toFixed(0) : a.toFixed(1);
  return `${sign}$${body}`;
}

function ago(ts: number): string {
  const m = Math.round((Date.now() - ts) / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

/** The worker's 0-trade notes, said in the user's terms. "no traders to
    copy" describes the roster AT REPLAY TIME — a score-fn strat whose re-rank
    landed since (traderCount > 0 now) is just waiting on the next pass, not
    missing traders. */
function friendlyNote(note: string, traderCount?: number): string {
  if (note === "no traders to copy") {
    return traderCount && traderCount > 0
      ? "roster was empty at last replay — refreshes next pass"
      : "0 traders on this strat — add traders to backtest";
  }
  if (note === "no leader flow in this window") return "leader didn't trade in these windows";
  return note;
}

const VERDICT_CHIP: Record<string, { sym: string; cls: string }> = {
  held:      { sym: "✓", cls: "text-green-400" },
  faded:     { sym: "✗", cls: "text-red-400" },
  "no-edge": { sym: "✗", cls: "text-red-400" },
  stalled:   { sym: "⏸", cls: "text-amber-300" },
  recovered: { sym: "↗", cls: "text-amber-300" },
  untested:  { sym: "?", cls: "text-pixel-gray" },
};

export default function WindowStrip({ row, loading, running, traderCount }: {
  row: WindowRow | undefined;
  /** The worker cache hasn't been read yet. */
  loading?: boolean;
  /** The worker is mid-pass — missing cells are on their way. */
  running?: boolean;
  /** The strat's CURRENT roster size, to caption 0-trade notes honestly. */
  traderCount?: number;
}) {
  // When every window came back 0-trade for the same reason, a row of five
  // "—" cells just looks broken. Say the reason once, where it can be seen —
  // the hover tooltip alone buried it ("i can't see the performance of
  // these tests").
  const cells = HUB_WINDOWS.map((d) => row?.[d]);
  const ranEmpty = cells.filter((bt) => bt && bt.trades === 0 && bt.note);
  if (row && ranEmpty.length === HUB_WINDOWS.length) {
    const note = ranEmpty[0]!.note!;
    const uniform = ranEmpty.every((bt) => bt!.note === note);
    return (
      <div
        className="mx-3 mb-2 rounded-[var(--radius-sm)] px-2 py-1.5"
        style={{ border: "1px solid var(--border)" }}
        title={HUB_WINDOWS.map((d) => `${d}D — 0 trades: ${row[d]?.note ?? "?"}`).join("\n")}
      >
        <div className="text-[8px] font-semibold tracking-[0.14em] text-pixel-gray">BACKTEST 1–30D</div>
        <div className="text-[10px] font-mono text-amber-300/90">
          {friendlyNote(uniform ? note : "0 trades in every window", traderCount)}
        </div>
      </div>
    );
  }
  return (
    <div
      className="grid grid-cols-5 mx-3 mb-2 rounded-[var(--radius-sm)] overflow-hidden"
      style={{ border: "1px solid var(--border)" }}
      title="Backtest over the last 1, 3, 7, 14 and 30 days — net of fees. Green across the row = it held up; green on one window only = luck."
    >
      {HUB_WINDOWS.map((d, i) => {
        const bt = row?.[d];
        const empty = !bt || (bt.trades === 0 && !!bt.note);
        const verdictChip = bt && !empty && bt.forward?.verdict ? VERDICT_CHIP[bt.forward.verdict] : null;
        const tone = !bt || empty ? "text-pixel-gray"
          : bt.pnl > 0 ? "text-green-400" : bt.pnl < 0 ? "text-red-400" : "text-pixel-gray";
        const tip = !bt
          ? `${d}D — not run yet. ${running ? "The background worker is replaying it now." : "The background worker replays every window each pass."}`
          : [
              `${d}-day backtest: ${bt.pnl >= 0 ? "+" : "−"}$${Math.abs(bt.pnl).toFixed(2)} (${bt.roi >= 0 ? "+" : ""}${bt.roi.toFixed(1)}%) on $${bt.capital}`,
              `${bt.trades} trades · ${bt.traders} traders`,
              bt.forward ? `walk-forward: ${bt.forward.verdict}` : null,
              bt.note ?? null,
              bt.stale ? "strat edited since — refreshes next pass" : null,
              `run ${ago(bt.at)}`,
            ].filter(Boolean).join("\n");
        return (
          <div
            key={d}
            title={tip}
            className={`px-1 py-1 text-center ${bt?.stale || bt?.warming ? "opacity-60" : ""}`}
            style={i > 0 ? { borderLeft: "1px solid var(--border)" } : undefined}
          >
            <div className="text-[8px] font-semibold tracking-[0.14em] text-pixel-gray">{d}D</div>
            <div className={`text-[11px] font-mono font-semibold tabular-nums truncate ${tone}`}>
              {bt ? (empty ? "0t" : shortUsd(bt.pnl)) : loading || running ? "…" : "·"}
            </div>
            {bt && !empty && (
              <div className={`text-[9px] font-mono tabular-nums ${tone}`}>
                {bt.roi >= 0 ? "+" : ""}{bt.roi.toFixed(bt.roi !== 0 && Math.abs(bt.roi) < 10 ? 1 : 0)}%
              </div>
            )}
            {verdictChip && (
              <div className={`text-[9px] leading-none ${verdictChip.cls}`}>{verdictChip.sym}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}
