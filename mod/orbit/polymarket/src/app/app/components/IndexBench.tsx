"use client";

// THE BENCH — the active strat's traders, as a side-panel block.
//
// The board's + ADD button writes straight into the active strat, but the
// only trace the column showed was a "· 8T" count on the strat row. The bench
// IS the index you're building — who's on it, what each one SCORES right now,
// and the way off it belong next to the strat list, not behind a card hunt
// on a 2,000-row board.
//
// Each row prints the trader's CURRENT board score off the score bus —
// the exact number the TOP TRADERS board ranks on, published after every
// rank pass (lib/scoreBus.ts). "—" means the board hasn't scored them
// (not streamed yet, or the user's score FUNCTION hid them); the row's
// tooltip says which. Rows sort by that score, best first, so the bench
// answers "who is carrying this index" at a glance.
//
// Mutations ride the same path the board's + ADD uses: updateIndex with
// re-normalized equal weights + announceStratChange, so the board's card
// state, the strat row's count and this list can't disagree.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { ensureActiveStrat, announceStratChange, STRAT_UPDATED_EVENT } from "../lib/activeStrat";
import { updateIndex } from "../lib/indexStore";
import { shortAddress } from "@/lib/auth";
import { formatScore } from "../lib/scoreFormula";
import { useScoreBoard, boardScoreFor } from "../lib/scoreBus";
import type { SavedIndex } from "../lib/types";

const OPEN_KEY = "poly_bench_open";

export default function IndexBench() {
  const [strat, setStrat] = useState<SavedIndex | null>(null);
  const [open, setOpen] = useState(true);
  const board = useScoreBoard();

  useEffect(() => {
    try { setOpen(localStorage.getItem(OPEN_KEY) !== "0"); } catch {}
    const read = () => setStrat(ensureActiveStrat());
    read();
    window.addEventListener(STRAT_UPDATED_EVENT, read);
    return () => window.removeEventListener(STRAT_UPDATED_EVENT, read);
  }, []);

  const setOpenPersisted = useCallback((next: boolean) => {
    setOpen(next);
    try { localStorage.setItem(OPEN_KEY, next ? "1" : "0"); } catch {}
  }, []);

  const mutate = useCallback((fn: (s: SavedIndex) => SavedIndex["traders"]) => {
    const idx = ensureActiveStrat();
    updateIndex(idx.id, { traders: fn(idx), updatedAt: Date.now() });
    announceStratChange();
  }, []);

  const remove = useCallback((address: string) => {
    const a = address.toLowerCase();
    mutate((idx) => {
      const rest = idx.traders.filter((t) => t.address.toLowerCase() !== a);
      const w = rest.length > 0 ? 1 / rest.length : 1;
      return rest.map((t) => ({ ...t, weight: w }));
    });
  }, [mutate]);

  const toggle = useCallback((address: string) => {
    const a = address.toLowerCase();
    mutate((idx) => idx.traders.map((t) =>
      t.address.toLowerCase() === a ? { ...t, enabled: t.enabled === false } : t,
    ));
  }, [mutate]);

  if (!strat || strat.traders.length === 0) return null;

  const rows = [...strat.traders].sort((a, b) => {
    const sa = boardScoreFor(board, a.address);
    const sb = boardScoreFor(board, b.address);
    return (typeof sb === "number" ? sb : Number.NEGATIVE_INFINITY)
      - (typeof sa === "number" ? sa : Number.NEGATIVE_INFINITY);
  });
  const onBench = strat.traders.filter((t) => t.enabled !== false).length;

  return (
    <section style={{ borderTop: "1px solid var(--border)" }}>
      <button
        onClick={() => setOpenPersisted(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-pixel-white/[0.04] transition-colors"
        title={`The traders on "${strat.name}" — every + ADD on the board lands here. BACKTEST replays this bench; LIVE copies it.`}
      >
        <span className="text-[11px] font-semibold tracking-[0.2em] text-pixel-white">BENCH</span>
        <span className="ml-auto flex items-center gap-2 min-w-0 shrink">
          <span className="text-[10px] font-mono text-pixel-gray truncate" title={`Active strat: ${strat.name}`}>
            {onBench}{onBench !== strat.traders.length ? `/${strat.traders.length}` : ""} on {strat.name}
          </span>
          <span className={`text-[9px] text-pixel-gray transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
        </span>
      </button>

      {open && (
        <div className="px-3 pb-2 space-y-1">
          <div className="flex items-baseline font-mono text-[8.5px] tracking-[0.12em] text-pixel-gray/80">
            <span>{board.label}{board.days > 0 ? ` · ${board.days}D` : ""} — the board&apos;s current score</span>
          </div>
          {rows.map((t) => {
            const off = t.enabled === false;
            const sc = boardScoreFor(board, t.address);
            const scText = typeof sc === "number" ? formatScore(sc) : "—";
            const scCls = typeof sc !== "number"
              ? "text-pixel-gray"
              : sc > 0 ? "text-green-400" : sc < 0 ? "text-red-400" : "text-pixel-gray-light";
            const scTitle = sc === undefined
              ? "No board score yet — they weren't in the last rank pass (sync, or widen the board's filters)"
              : sc === null
                ? "Your score FUNCTION hides this trader (returned null/None)"
                : `${board.label} = ${scText} over the board's ${board.days}D window`;
            return (
              <div key={t.address} className={`flex items-center gap-1.5 font-mono text-[10px] ${off ? "opacity-50" : ""}`}>
                <Link
                  href={`/traders/${t.address}`}
                  className="text-pixel-gray-light hover:text-pixel-green normal-case shrink-0"
                  title={t.address}
                >
                  {shortAddress(t.address)}
                </Link>
                <span className="flex-1" />
                <span className={`tabular-nums ${scCls}`} title={scTitle}>{scText}</span>
                <button
                  onClick={() => toggle(t.address)}
                  className={`text-[8.5px] tracking-[0.08em] px-1 shrink-0 ${off ? "text-pixel-gray hover:text-green-400" : "text-green-400/80 hover:text-amber-400"}`}
                  title={off ? "Benched — click to copy them again" : "Copying — click to bench them without removing"}
                >
                  {off ? "OFF" : "ON"}
                </button>
                <button
                  onClick={() => remove(t.address)}
                  className="text-pixel-gray hover:text-red-400 text-[11px] leading-none px-0.5 shrink-0"
                  title={`Remove from "${strat.name}" — the remaining bench re-weights evenly`}
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
