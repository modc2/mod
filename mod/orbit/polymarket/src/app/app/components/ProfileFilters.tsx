"use client";

// THE FILTER RAIL — the same market gate the board was found with, still on
// screen after you open a trader.
//
// FindTraders ranks the leaderboard inside ONE market type ("who is good at
// BITCOIN"), and hands the winning row's profile that query as `?mq=`. The
// profile honored it but only showed it as a chip with an ✕: you could clear
// the gate, never change it, and the side/price/size dimensions lived behind a
// FILTERS toggle that started closed. So "expand into the trader" quietly
// became a different screen from the one you were just looking at — no market
// presets, no bands, nothing you could turn.
//
// This rail is that control surface, rendered ALWAYS and stuck to the top of
// the viewport, so the answer to "which slice of this trader am I reading?" is
// never more than a glance away — and every dimension of it is adjustable in
// place. It narrows the whole profile (stats, curve, tables) AND the copy
// simulator below, which is the point: the numbers you decide on are the
// numbers under a gate you can see.

import { useEffect, useState } from "react";

import { CATEGORIES, type CategorySlug } from "../lib/polymarket";
import { MARKET_TYPES, matchPreset } from "../lib/marketTypes";
import TradeFilterBar, { type TradeFilterBarState } from "./TradeFilterBar";

interface Props {
  /** The topic gate (FiltersContext `marketQuery`, ?mq=). */
  marketQuery: string;
  /** Supplied ⇒ the presets and the topic box can SET it, not just clear it. */
  onMarketQueryChange?: (q: string) => void;
  category: CategorySlug;
  onCategoryChange?: (c: CategorySlug) => void;
  bar: TradeFilterBarState;
  /** How the gate landed on this trader's tape — shown inline so a filter that
      hides most of a profile can't read as a trader who barely trades. */
  matched: number;
  total: number;
  /** Distance from the top of the viewport, in px. The page raises it while
      the sync strip is on screen so the two don't stack on top of each other. */
  stickyTopPx?: number;
}

const OPEN_KEY = "poly8bit_profile_filters_open";

export default function ProfileFilters({
  marketQuery,
  onMarketQueryChange,
  category,
  onCategoryChange,
  bar,
  matched,
  total,
  stickyTopPx = 56,
}: Props) {
  // The detail rows (side / price / size / keyword / category) can be folded
  // away for vertical room — but they default to OPEN and the topic row above
  // them never folds. A filter you can't see is the bug this file exists for.
  const [open, setOpen] = useState(true);
  useEffect(() => {
    try {
      if (localStorage.getItem(OPEN_KEY) === "0") setOpen(false);
    } catch {}
  }, []);
  const toggleOpen = () => {
    setOpen((o) => {
      try { localStorage.setItem(OPEN_KEY, o ? "0" : "1"); } catch {}
      return !o;
    });
  };

  // Local mirror of the topic box so typing doesn't re-run the whole profile
  // on every keystroke — committed on Enter or blur.
  const [topic, setTopic] = useState(marketQuery);
  useEffect(() => setTopic(marketQuery), [marketQuery]);
  const commitTopic = () => {
    const next = topic.trim();
    if (next !== marketQuery.trim()) onMarketQueryChange?.(next);
  };

  const preset = matchPreset(marketQuery);
  const editable = !!onMarketQueryChange;
  const activeCount =
    bar.count + (category ? 1 : 0) + (marketQuery.trim() ? 1 : 0);
  const narrowing = matched < total;

  const clearAll = () => {
    bar.clear();
    onCategoryChange?.("");
    onMarketQueryChange?.("");
  };

  return (
    <div
      className="pixel-panel sticky z-20 bg-pixel-black/95 backdrop-blur-sm"
      style={{ top: stickyTopPx }}
    >
      {/* ── Row 1: the market gate. Always visible. ── */}
      <div className="flex items-center gap-1.5 flex-wrap px-3 py-2">
        <span className="text-[12px] text-pixel-gray tracking-wider w-14 shrink-0">TOPIC</span>
        {MARKET_TYPES.map((m) => {
          const on = preset?.label === m.label;
          return (
            <button
              key={m.label}
              disabled={!editable}
              onClick={() => onMarketQueryChange?.(on ? "" : m.query)}
              title={`${m.hint} — matches: ${m.query}`}
              className={`pixel-btn text-[12px] px-2 py-0.5 transition-colors ${
                on
                  ? "border-green-400 text-green-400 bg-green-400/10"
                  : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white disabled:hover:text-pixel-gray"
              }`}
            >
              {m.label}
            </button>
          );
        })}
        <input
          type="text"
          value={topic}
          disabled={!editable}
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitTopic();
            if (e.key === "Escape") setTopic(marketQuery);
          }}
          onBlur={commitTopic}
          placeholder="or type one — bitcoin, btc"
          title={
            "The gate this profile is read under, and the gate a copy would run " +
            "under. OR across commas, AND within a phrase."
          }
          className="pixel-input-sm w-52 text-[12px] font-mono"
        />
        {marketQuery.trim() && (
          <button
            onClick={() => onMarketQueryChange?.("")}
            title="Every market this trader traded — no gate"
            className="pixel-btn text-[12px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
          >
            ✕ ANY
          </button>
        )}

        <span className="flex-1" />

        {/* What the gate did to this tape — the honest count. */}
        <span className="font-mono text-[12px] text-pixel-gray tracking-wider">
          {narrowing ? (
            <>
              <span className="text-green-400">{matched.toLocaleString()}</span>
              {`/${total.toLocaleString()} TRADES`}
            </>
          ) : (
            `${total.toLocaleString()} TRADES`
          )}
        </span>
        {activeCount > 0 && (
          <button
            onClick={clearAll}
            className="text-[12px] text-pixel-gray hover:text-pixel-white tracking-wider"
            title="Drop every filter — this trader's whole record"
          >
            CLEAR ALL
          </button>
        )}
        <button
          onClick={toggleOpen}
          title={open ? "Fold the side / price / size rows away" : "Show every filter dimension"}
          className={`pixel-btn text-[12px] px-2 py-0.5 flex items-center gap-1.5 ${
            bar.count > 0 || category
              ? "border-green-400 text-green-400"
              : "border-pixel-border text-pixel-gray hover:text-pixel-white"
          }`}
        >
          {open ? "▾" : "▸"} FILTERS
          {bar.count + (category ? 1 : 0) > 0 && (
            <span className="text-[11px] bg-green-400/20 text-green-400 px-1 py-px border border-green-400/40">
              {bar.count + (category ? 1 : 0)}
            </span>
          )}
        </button>
      </div>

      {/* ── Rows 2+: the per-trade dimensions. The SAME bar the TRADES tape
             uses, so a slice means the same thing on both screens. ── */}
      <div className="border-t-2 border-pixel-border">
        <TradeFilterBar
          bar={bar}
          open={open}
          embedded
          category={category}
          onCategoryChange={onCategoryChange}
        />
      </div>
    </div>
  );
}
