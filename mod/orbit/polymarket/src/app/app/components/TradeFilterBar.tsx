"use client";

// The tape FILTERS bar — side / entry-price band / USD size band / keywords,
// plus (optionally) the global category buckets. Shared by the TRADES tape and
// a trader's profile so "slice the flow" means the same thing in both places.
//
// The side/price/size dimensions are the exact gate strats copy flow through
// (lib/tradeFilters.ts); keywords are a tape-only OR-match on market title +
// outcome (UI convenience, deliberately NOT part of the Rust-mirrored
// TradeFilters gate).

import { useCallback, useMemo, useState } from "react";
import { CATEGORIES, type CategorySlug } from "../lib/polymarket";
import { tradeFiltersActive, tradeMatchesFilters, describeTradeFilters } from "../lib/tradeFilters";
import type { TradeFilters } from "../lib/types";

/** Everything the bar needs to render + everything a caller needs to filter.
    Built by `useTradeFilterBar`, passed straight back into `<TradeFilterBar>`. */
export interface TradeFilterBarState {
  sides: "both" | "buy" | "sell";
  setSides: (s: "both" | "buy" | "sell") => void;
  minPriceStr: string; setMinPriceStr: (v: string) => void;
  maxPriceStr: string; setMaxPriceStr: (v: string) => void;
  minSizeStr: string; setMinSizeStr: (v: string) => void;
  maxSizeStr: string; setMaxSizeStr: (v: string) => void;
  keywords: string[];
  kwInput: string; setKwInput: (v: string) => void;
  addKeyword: (raw: string) => void;
  removeKeyword: (k: string) => void;
  /** Derived strat-shaped filter object (prices in 0–1, sizes in USD). */
  tf: TradeFilters;
  /** True when a side/price/size dimension is actually constraining. */
  tfActive: boolean;
  /** True when anything in the bar is narrowing (incl. keywords). */
  active: boolean;
  /** How many dimensions are on — for the FILTERS button badge. */
  count: number;
  clear: () => void;
  /** The gate itself. Only applies the strat filter when a dimension is set,
      so an all-defaults TradeFilters shows the tape whole. */
  matches: (t: { side: "BUY" | "SELL"; price: number; size: number; market: string; outcome?: string }) => boolean;
  /** Keyword-only match — for rows that aren't fills (positions have no
      side/price/size to gate on). */
  matchesText: (hay: string) => boolean;
  /** Human summary of what's on — for the collapsed chip row. */
  describe: () => string;
}

export function useTradeFilterBar(initialKeywords: string[] = []): TradeFilterBarState {
  const [sides, setSides] = useState<"both" | "buy" | "sell">("both");
  const [minPriceStr, setMinPriceStr] = useState(""); // cents, 0–100
  const [maxPriceStr, setMaxPriceStr] = useState("");
  const [minSizeStr, setMinSizeStr] = useState("");   // USD notional
  const [maxSizeStr, setMaxSizeStr] = useState("");
  const [keywords, setKeywords] = useState<string[]>(initialKeywords);
  const [kwInput, setKwInput] = useState("");

  const addKeyword = useCallback((raw: string) => {
    const k = raw.trim().toLowerCase();
    if (!k) return;
    setKeywords((prev) => (prev.includes(k) ? prev : [...prev, k]));
    setKwInput("");
  }, []);
  const removeKeyword = useCallback(
    (k: string) => setKeywords((prev) => prev.filter((x) => x !== k)),
    [],
  );

  const tf = useMemo<TradeFilters>(() => {
    const num = (s: string) => {
      const n = Number(s);
      return s.trim() !== "" && Number.isFinite(n) && n >= 0 ? n : undefined;
    };
    return {
      sides,
      minPrice: num(minPriceStr) != null ? num(minPriceStr)! / 100 : undefined,
      maxPrice: num(maxPriceStr) != null ? num(maxPriceStr)! / 100 : undefined,
      minNotional: num(minSizeStr),
      maxNotional: num(maxSizeStr),
    };
  }, [sides, minPriceStr, maxPriceStr, minSizeStr, maxSizeStr]);

  const tfActive = tradeFiltersActive(tf);
  const count =
    (sides !== "both" ? 1 : 0) +
    (tf.minPrice != null || tf.maxPrice != null ? 1 : 0) +
    (tf.minNotional != null || tf.maxNotional != null ? 1 : 0) +
    (keywords.length > 0 ? 1 : 0);

  const clear = useCallback(() => {
    setSides("both");
    setMinPriceStr(""); setMaxPriceStr("");
    setMinSizeStr(""); setMaxSizeStr("");
    setKeywords([]); setKwInput("");
  }, []);

  const matches = useCallback(
    (t: { side: "BUY" | "SELL"; price: number; size: number; market: string; outcome?: string }) => {
      if (tfActive && !tradeMatchesFilters(t, tf)) return false;
      if (keywords.length > 0) {
        const hay = `${t.market} ${t.outcome ?? ""}`.toLowerCase();
        if (!keywords.some((k) => hay.includes(k))) return false;
      }
      return true;
    },
    [tf, tfActive, keywords],
  );

  const matchesText = useCallback(
    (hay: string) => {
      if (keywords.length === 0) return true;
      const h = hay.toLowerCase();
      return keywords.some((k) => h.includes(k));
    },
    [keywords],
  );

  const describe = useCallback(() => {
    const parts: string[] = [];
    const d = describeTradeFilters(tf);
    if (d) parts.push(d);
    if (keywords.length > 0) parts.push(`“${keywords.join("” “")}”`);
    return parts.join(" · ");
  }, [tf, keywords]);

  return {
    sides, setSides,
    minPriceStr, setMinPriceStr,
    maxPriceStr, setMaxPriceStr,
    minSizeStr, setMinSizeStr,
    maxSizeStr, setMaxSizeStr,
    keywords, kwInput, setKwInput, addKeyword, removeKeyword,
    tf, tfActive, active: tfActive || keywords.length > 0, count, clear, matches, matchesText, describe,
  };
}

const KEYWORD_PRESETS = ["bitcoin", "ethereum", "solana", "trump", "fed", "nba"];

/** The FILTERS toggle — lives in the header/tab row next to whatever it filters. */
export function TradeFilterToggle({
  open, onToggle, count,
}: { open: boolean; onToggle: () => void; count: number }) {
  return (
    <button
      onClick={onToggle}
      title="Slice this flow by side, entry price, size, keyword or category"
      className={`pixel-btn text-[12px] px-2.5 py-1 flex items-center gap-1.5 ${
        open || count > 0
          ? "border-green-400 text-green-400"
          : "border-pixel-border text-pixel-gray hover:text-pixel-white"
      }`}
    >
      FILTERS
      {count > 0 && (
        <span className="text-[11px] bg-green-400/20 text-green-400 px-1 py-px border border-green-400/40">
          {count}
        </span>
      )}
    </button>
  );
}

interface Props {
  bar: TradeFilterBarState;
  open: boolean;
  /** Category buckets render only when a setter is supplied (the slug is the
      global filter, shared with TRADERS/MARKETS). */
  category?: CategorySlug;
  onCategoryChange?: (c: CategorySlug) => void;
  /** Inside an existing panel: no border/background of its own. */
  embedded?: boolean;
}

export default function TradeFilterBar({ bar, open, category = "", onCategoryChange, embedded = false }: Props) {
  const totalCount = bar.count + (category ? 1 : 0);
  const clearAll = () => {
    bar.clear();
    onCategoryChange?.("");
  };

  // Collapsed — keep whatever is narrowing the tape visible as a chip row.
  if (!open) {
    if (!bar.active) return null;
    return (
      <div className={`flex items-center gap-2 flex-wrap font-mono text-[11px] ${embedded ? "px-3 py-2 border-b-2 border-pixel-border" : ""}`}>
        <span className="pixel-badge border-green-400/60 text-green-400">{bar.describe()}</span>
        <button onClick={clearAll} className="text-pixel-gray hover:text-pixel-white">CLEAR</button>
      </div>
    );
  }

  return (
    <div className={embedded ? "px-3 py-2.5 space-y-2.5 border-b-2 border-pixel-border" : "pixel-panel px-3 py-2.5 space-y-2.5"}>
      {/* Side */}
      <div className="flex items-center gap-1.5 flex-wrap font-mono">
        <span className="text-[12px] text-pixel-gray tracking-wider shrink-0 mr-1 w-14">SIDE</span>
        {(["both", "buy", "sell"] as const).map((s) => (
          <button
            key={s}
            onClick={() => bar.setSides(s)}
            className={`pixel-btn text-[12px] px-2 py-0.5 transition-colors ${
              bar.sides === s
                ? "border-green-400 text-green-400 bg-green-400/10"
                : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
            }`}
          >
            {s === "both" ? "BOTH" : s === "buy" ? "BUY" : "SELL"}
          </button>
        ))}
      </div>
      {/* Price band (¢) + size band ($) */}
      <div className="flex items-center gap-4 flex-wrap font-mono">
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-pixel-gray tracking-wider shrink-0 mr-1 w-14">PRICE ¢</span>
          <input
            type="number" min={0} max={100} placeholder="0"
            value={bar.minPriceStr} onChange={(e) => bar.setMinPriceStr(e.target.value)}
            className="pixel-input-sm w-16 text-[12px]"
          />
          <span className="text-pixel-gray text-[12px]">–</span>
          <input
            type="number" min={0} max={100} placeholder="100"
            value={bar.maxPriceStr} onChange={(e) => bar.setMaxPriceStr(e.target.value)}
            className="pixel-input-sm w-16 text-[12px]"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-pixel-gray tracking-wider shrink-0 mr-1">SIZE $</span>
          <input
            type="number" min={0} placeholder="0"
            value={bar.minSizeStr} onChange={(e) => bar.setMinSizeStr(e.target.value)}
            className="pixel-input-sm w-20 text-[12px]"
          />
          <span className="text-pixel-gray text-[12px]">–</span>
          <input
            type="number" min={0} placeholder="∞"
            value={bar.maxSizeStr} onChange={(e) => bar.setMaxSizeStr(e.target.value)}
            className="pixel-input-sm w-20 text-[12px]"
          />
        </div>
      </div>
      {/* Keywords — free-form OR-match on market title + outcome. Finer than
          the category buckets: "bitcoin" ≠ the whole Crypto bucket. */}
      <div className="flex items-start gap-1.5 flex-wrap font-mono">
        <span className="text-[12px] text-pixel-gray tracking-wider shrink-0 mr-1 w-14 pt-1">KEYWORD</span>
        <div className="flex items-center gap-1.5 flex-wrap min-w-0">
          {bar.keywords.map((k) => (
            <button
              key={k}
              onClick={() => bar.removeKeyword(k)}
              title="Remove keyword"
              className="pixel-btn text-[12px] px-2 py-0.5 border-green-400 text-green-400 bg-green-400/10 hover:border-red-400 hover:text-red-400"
            >
              {k} ✕
            </button>
          ))}
          <input
            type="text"
            placeholder="bitcoin, fed, nba…"
            value={bar.kwInput}
            onChange={(e) => {
              // Comma commits the chip mid-typing, same as Enter.
              if (e.target.value.includes(",")) {
                e.target.value.split(",").forEach(bar.addKeyword);
              } else bar.setKwInput(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") bar.addKeyword(bar.kwInput);
              else if (e.key === "Backspace" && bar.kwInput === "" && bar.keywords.length > 0)
                bar.removeKeyword(bar.keywords[bar.keywords.length - 1]);
            }}
            onBlur={() => bar.addKeyword(bar.kwInput)}
            className="pixel-input-sm w-40 text-[12px]"
          />
          {KEYWORD_PRESETS.filter((p) => !bar.keywords.includes(p)).map((p) => (
            <button
              key={p}
              onClick={() => bar.addKeyword(p)}
              className="pixel-btn text-[12px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
            >
              + {p}
            </button>
          ))}
        </div>
      </div>
      {/* Category buckets — bound to the global category filter (also set from
          TRADERS → FILTERS; shared state). */}
      {onCategoryChange && (
        <div className="flex items-center gap-1.5 flex-wrap font-mono">
          <span className="text-[12px] text-pixel-gray tracking-wider shrink-0 mr-1 w-14">MARKET</span>
          {CATEGORIES.map((c) => {
            const active = category === c.slug;
            return (
              <button
                key={c.slug || "all"}
                onClick={() => onCategoryChange(c.slug)}
                className={`pixel-btn text-[12px] px-2 py-0.5 transition-colors ${
                  active
                    ? "border-green-400 text-green-400 bg-green-400/10"
                    : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
                }`}
              >
                {c.label}
              </button>
            );
          })}
          {totalCount > 0 && (
            <button
              onClick={clearAll}
              className="pixel-btn text-[12px] px-2 py-0.5 border-red-400/50 text-red-400 hover:bg-red-400/10 ml-auto"
            >
              ✕ CLEAR
            </button>
          )}
        </div>
      )}
      {!onCategoryChange && totalCount > 0 && (
        <div className="flex font-mono">
          <button
            onClick={clearAll}
            className="pixel-btn text-[12px] px-2 py-0.5 border-red-400/50 text-red-400 hover:bg-red-400/10 ml-auto"
          >
            ✕ CLEAR
          </button>
        </div>
      )}
    </div>
  );
}
