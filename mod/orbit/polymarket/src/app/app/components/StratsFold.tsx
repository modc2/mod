"use client";

// STRATS — one fold at the bottom of the side panel's COPY tab.
//
// The panel used to stack ALLOCATION (every saved strat) and BENCH (the
// active strat's traders) above the copy book, each with its own header that
// repeated the active strat's name — with the account card's strip, the same
// "COPY 0x14c7…9ccf" printed four times before the first thing you could
// actually do (user, 2026-09-30: "no way i can understand this sidebar").
//
// Strats are the power-user layer (BACKTEST / LIVE run the active one); the
// copy book above is the everyday one. So: ONE header naming the active strat
// once, CLOSED by default, and inside it the allocation list then the active
// strat's traders. The heavy hooks in both only mount while open.

import { useCallback, useEffect, useState } from "react";

import { listStrats, STRAT_UPDATED_EVENT } from "../lib/activeStrat";
import { getActiveIndexId } from "../lib/indexStore";
import IndexBench from "./IndexBench";
import StratBlock from "./StratBlock";

const OPEN_KEY = "poly_strats_fold_open";

export default function StratsFold() {
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState<{ count: number; active: string | null }>({ count: 0, active: null });

  useEffect(() => {
    try { setOpen(localStorage.getItem(OPEN_KEY) === "1"); } catch {}
    const read = () => {
      const all = listStrats();
      const id = getActiveIndexId();
      const active = (id ? all.find((s) => s.id === id) : null) ?? all[0] ?? null;
      setSummary({ count: all.length, active: active?.name ?? null });
    };
    read();
    window.addEventListener(STRAT_UPDATED_EVENT, read);
    return () => window.removeEventListener(STRAT_UPDATED_EVENT, read);
  }, []);

  const toggle = useCallback(() => {
    setOpen((o) => {
      try { localStorage.setItem(OPEN_KEY, o ? "0" : "1"); } catch {}
      return !o;
    });
  }, []);

  return (
    <section style={{ borderTop: "1px solid var(--border)" }}>
      <button
        onClick={toggle}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-pixel-white/[0.04] transition-colors"
        title="Your saved strategies — pick the active one (BACKTEST and LIVE run it), move money between them, edit its traders"
      >
        <span className="text-[10px] font-mono tracking-[0.16em] text-pixel-gray">STRATS</span>
        <span className="min-w-0 flex-1 truncate text-[10.5px] font-mono text-green-400" title={summary.active ? `Active strat: ${summary.active}` : undefined}>
          {summary.active ?? "none yet"}
        </span>
        {summary.count > 1 && (
          <span className="text-[9.5px] font-mono text-pixel-gray shrink-0">+{summary.count - 1} more</span>
        )}
        <span className="text-[9px] text-pixel-gray shrink-0">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <>
          <StratBlock bare />
          <IndexBench bare />
        </>
      )}
    </section>
  );
}
