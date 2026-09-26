"use client";

// THE SENTENCE BOX — type what you mean, see what it understood.
//
// One text input over lib/semanticFilter.ts. The input is not the feature; the
// CHIPS under it are. A filter that silently drops 90% of a feed is
// indistinguishable from a broken feed, so every clause the parser took out of
// the sentence is rendered as its own chip with the terms it expanded to in
// the tooltip, and anything it could not place is shown dimmed as a literal.
//
// The chips also carry the one honest distinction this module cares about:
// green = the live engine can enforce this, dim = it filters this screen only.
// `compileGate()` says which is which, and ARM (when the caller offers it)
// pushes exactly the green half onto real copy allocations.

import { useEffect, useMemo, useState } from "react";

import {
  compileGate, describeGate, parseSemanticQuery, SEMANTIC_EXAMPLES,
  type SemanticQuery,
} from "../lib/semanticFilter";

const CHIP_TONE: Record<string, string> = {
  topic: "border-cyan-400/40 text-cyan-300",
  exclude: "border-red-400/40 text-red-300",
  side: "border-green-400/40 text-green-300",
  price: "border-amber-400/40 text-amber-300",
  notional: "border-amber-400/40 text-amber-300",
  time: "border-pixel-border text-pixel-gray",
  outcome: "border-pixel-border text-pixel-gray",
  leader: "border-pixel-border text-pixel-gray",
  status: "border-pixel-border text-pixel-gray",
};

export interface SemanticFilterBarProps {
  value: string;
  onChange: (next: string) => void;
  /** Parsed form, lifted so the caller filters with the same object. */
  onParsed?: (q: SemanticQuery) => void;
  /** "18 of 412" — the caller counts, this only displays. */
  kept?: number;
  total?: number;
  placeholder?: string;
  /** Offered ⇒ an ARM button appears with the enforceable half of the query. */
  onArm?: (gate: ReturnType<typeof compileGate>) => void;
  armLabel?: string;
  compact?: boolean;
}

export default function SemanticFilterBar({
  value, onChange, onParsed, kept, total, placeholder, onArm, armLabel, compact,
}: SemanticFilterBarProps) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  // Typing re-filters, so debounce it — the parse is cheap but re-rendering a
  // 400-row board on every keystroke is not.
  useEffect(() => {
    const t = setTimeout(() => onChange(draft), 180);
    return () => clearTimeout(t);
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps

  const parsed = useMemo(() => parseSemanticQuery(value), [value]);
  useEffect(() => onParsed?.(parsed), [parsed]); // eslint-disable-line react-hooks/exhaustive-deps
  const gate = useMemo(() => compileGate(parsed), [parsed]);

  const example = useMemo(
    () => SEMANTIC_EXAMPLES[Math.floor(Math.random() * SEMANTIC_EXAMPLES.length)],
    [],
  );

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className="text-[9px] font-mono tracking-[0.14em] text-pixel-gray shrink-0">⌕</span>
        <input
          className={`pixel-input-sm input-xs flex-1 min-w-0 font-mono ${compact ? "text-[10.5px]" : "text-[12px]"}`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") { setDraft(""); onChange(""); } }}
          placeholder={placeholder ?? `e.g. ${example}`}
          title="Say what you want in words: a topic (crypto, politics, sports, candles), a side (buys/sells), a price band (under 30¢, longshots, coin flips), a size (over $500, dust), a window (last 3 days), or a status (missed, copied). Commas mean OR."
        />
        {value && (
          <button
            onClick={() => { setDraft(""); onChange(""); }}
            className="text-[12px] leading-none text-pixel-gray hover:text-red-400 shrink-0 px-1"
            title="Clear the filter"
          >
            ×
          </button>
        )}
        {total !== undefined && (
          <span
            className="shrink-0 text-[9.5px] font-mono tabular-nums text-pixel-gray"
            title={`${kept ?? total} of ${total} rows match`}
          >
            {kept ?? total}<span className="text-pixel-gray/50">/{total}</span>
          </span>
        )}
      </div>

      {!parsed.empty && (
        <div className="flex flex-wrap items-center gap-1">
          {parsed.chips.map((c, i) => (
            <span
              key={`${c.kind}-${i}`}
              title={`${c.detail}${c.enforceable ? "" : "\n\nThis one filters the screen only — the live copy gate has no such knob."}`}
              className={`px-1 py-[1px] rounded-[3px] border text-[9px] font-mono tracking-[0.06em] ${
                CHIP_TONE[c.kind] ?? "border-pixel-border text-pixel-gray"
              } ${c.enforceable ? "" : "opacity-60 border-dashed"}`}
            >
              {c.label}
            </span>
          ))}
          {onArm && gate.any && (
            <button
              onClick={() => onArm(gate)}
              className="pixel-btn btn-xs ml-auto border-green-400/50 text-green-400"
              title={`Arm the enforceable half of this sentence as a real copy gate — ${describeGate(gate)}.${
                gate.viewOnly.length ? `\n\nNOT armed (screen-only): ${gate.viewOnly.join(", ")}` : ""
              }`}
            >
              {armLabel ?? "ARM AS GATE"}
            </button>
          )}
        </div>
      )}

      {!parsed.empty && parsed.literals.length > 0 && (
        <div
          className="text-[9px] font-mono text-pixel-gray/70 truncate"
          title="These words aren't in the concept lexicon, so they were matched literally against the market title."
        >
          literal: {parsed.literals.join(", ")}
        </div>
      )}
    </div>
  );
}
