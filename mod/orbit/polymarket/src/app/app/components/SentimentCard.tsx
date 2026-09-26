"use client";

// MARKET SENTIMENT — the control, and the receipt.
//
// The strat already has two gates that say WHAT to copy: which markets
// (`marketQuery`) and which trades inside them (`tradeFilters`). This is the
// third, and it is the only one that asks about the market rather than the
// trade: when they took it, which way had the crowd been moving the odds on
// the outcome they bought?
//
//   WITH THE CROWD      the odds were rising — they paid up into strength
//   AGAINST THE CROWD   the odds were falling — a contrarian entry, the dip
//   QUIET               the market had barely moved
//
// One row of four buttons is the whole feature for most people; the dials that
// define those words (how far back, how much movement counts) are behind MORE.
//
// The card is also the receipt, and that half is not decoration. A sentiment
// gate reads price history, history does not exist for every market, and a
// filter that silently dropped every unreadable market would repeat this
// module's worst bug. So the card always prints three numbers over the bench's
// own recent flow: how it splits by mood, how many trades the reading could
// actually be taken on (COVERAGE), and how many the gate would KEEP. A gate
// that would keep 3 of 400 trades says so before it is armed, not after a
// silent week.

import { useState } from "react";
import {
  DEFAULT_SENTIMENT_FLAT_BAND, DEFAULT_SENTIMENT_WINDOW_HOURS,
  sentimentFilterActive, type SentimentFilter, type SentimentLean,
} from "../lib/marketSentiment";
import type { SentimentBookState } from "../lib/useSentimentBook";

/** The four choices, in the order a person considers them. `lean: null` is
    OFF — no gate, no fetch, every mood copied. */
const MOODS: { key: string; lean: SentimentLean[] | null; label: string; sub: string; hint: string }[] = [
  {
    key: "any", lean: null, label: "ANY", sub: "no gate",
    hint: "Copy their trades whatever the market was doing. No price history is fetched.",
  },
  {
    key: "bullish", lean: ["bullish"], label: "WITH THE CROWD", sub: "odds rising",
    hint: "Only copy entries taken while the odds on the outcome they bought were RISING — they paid up into strength. Momentum, confirmation, worse price.",
  },
  {
    key: "bearish", lean: ["bearish"], label: "AGAINST THE CROWD", sub: "odds falling",
    hint: "Only copy their CONTRARIAN entries — the odds had been falling and they bought anyway. Better price, and the market disagrees with them.",
  },
  {
    key: "flat", lean: ["flat"], label: "QUIET", sub: "barely moved",
    hint: "Only copy in markets that have not moved much either way. No crowd to be with or against — the leader's own view is the whole signal.",
  },
];

function moodKey(f: SentimentFilter | undefined | null): string {
  if (!sentimentFilterActive(f)) return "any";
  const l = f?.lean ?? [];
  if (l.length === 1) return l[0];
  return l.length === 0 ? "any" : "custom";
}

function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}

function cents(p: number): string {
  return `${Math.round(p * 100)}¢`;
}

export default function SentimentCard({
  value,
  onChange,
  state,
  sampleLabel = "the bench's recent trades",
}: {
  /** Current filter, or undefined when there is no sentiment gate. */
  value: SentimentFilter | undefined;
  /** `undefined` clears the gate entirely. */
  onChange: (next: SentimentFilter | undefined) => void;
  /** The warm book + tallies over the sample — `useSentimentBook(...)`. */
  state: SentimentBookState;
  sampleLabel?: string;
}) {
  const [more, setMore] = useState(false);
  const on = sentimentFilterActive(value);
  const active = moodKey(value);
  const windowHours = value?.windowHours ?? DEFAULT_SENTIMENT_WINDOW_HOURS;
  const flatBand = value?.flatBand ?? DEFAULT_SENTIMENT_FLAT_BAND;

  const patch = (changes: Partial<SentimentFilter>) => {
    const next = { ...(value ?? {}), ...changes };
    onChange(sentimentFilterActive(next) ? next : undefined);
  };

  const b = state.breakdown;
  const sample = b.bullish + b.bearish + b.flat + b.unknown;
  const readable = b.bullish + b.bearish + b.flat;

  return (
    <div className="w-full space-y-2">
      {/* ── The one decision ── */}
      <div className="flex items-center gap-1 flex-wrap">
        {MOODS.map((m) => {
          const sel = active === m.key;
          return (
            <button
              key={m.key}
              onClick={() => onChange(m.lean === null ? undefined : { ...(value ?? {}), lean: m.lean })}
              title={m.hint}
              className={`px-2 py-1 rounded border font-mono text-left transition-colors ${
                sel
                  ? "border-green-400 text-green-400 bg-green-400/10"
                  : "border-pixel-border text-pixel-gray hover:text-pixel-white"
              }`}
            >
              <span className="block text-[10px] font-bold tracking-[0.06em]">{m.label}</span>
              <span className="block text-[9px] opacity-60">{m.sub}</span>
            </button>
          );
        })}
      </div>

      {/* ── The receipt. Only meaningful once a gate is on. ── */}
      {on && (
        <div className="space-y-1.5 border-t border-pixel-border/40 pt-1.5">
          {state.loading && sample > 0 && (
            <div className="text-[10px] font-mono text-pixel-gray">
              reading the tape over {sampleLabel}…
            </div>
          )}

          {sample > 0 && (
            <>
              {/* Mood split of the bench's own flow — a bar, so "there is
                  almost nothing to copy here" is visible before it is armed. */}
              <div className="flex h-2 w-full overflow-hidden rounded-sm border border-pixel-border/60">
                {([
                  ["bullish", b.bullish, "bg-green-400/70"],
                  ["flat", b.flat, "bg-pixel-gray/50"],
                  ["bearish", b.bearish, "bg-amber-400/70"],
                  ["unknown", b.unknown, "bg-pixel-border/40"],
                ] as const).map(([k, n, cls]) => (
                  <div
                    key={k}
                    className={cls}
                    style={{ width: `${sample > 0 ? (n / sample) * 100 : 0}%` }}
                    title={`${n} of ${sample} — ${k}`}
                  />
                ))}
              </div>

              <div className="flex items-baseline gap-3 flex-wrap font-mono text-[10px]">
                <span className="text-green-400">{b.bullish} rising</span>
                <span className="text-amber-400">{b.bearish} falling</span>
                <span className="text-pixel-gray">{b.flat} quiet</span>
                <span
                  className="text-pixel-gray/70"
                  title="No usable price history in the window. These are NOT rejected — the default lets them through, and 'skip unreadable' below is what changes that."
                >
                  {b.unknown} unreadable
                </span>
              </div>

              <div className="font-mono text-[11px]">
                <span
                  className={state.kept === 0 ? "text-red-400" : "text-pixel-white"}
                  title="How many of the sample's trades this gate would copy. Counted through the same reject the live engine runs."
                >
                  KEEPS {state.kept}
                </span>
                <span className="text-pixel-gray"> / {sample} of {sampleLabel}</span>
                {state.kept === 0 && (
                  <span className="text-red-400"> — this gate copies nothing</span>
                )}
              </div>

              {/* Coverage, always. A gate over 12% of the flow is a gate over
                  12% of the flow, whatever the mood buttons say. */}
              <div
                className={`font-mono text-[10px] ${readable / sample < 0.5 ? "text-amber-400" : "text-pixel-gray"}`}
                title="Trades the mood could actually be read for. The rest had no usable price history in the window — a coverage number, not a filter."
              >
                COVERAGE {pct(sample > 0 ? readable / sample : 0)} · read {readable}/{sample} markets
                {state.overBudget > 0 && ` · ${state.overBudget} markets past the fetch budget`}
                {state.spanCapped && " · history only reaches back 14d"}
              </div>
            </>
          )}

          {sample === 0 && (
            <div className="text-[10px] font-mono text-pixel-gray">
              no trades on the bench yet — the gate is set, and there is nothing to preview it against.
            </div>
          )}
        </div>
      )}

      {/* ── The dials that define the words above ── */}
      <button
        onClick={() => setMore((v) => !v)}
        className="text-[9px] font-mono text-pixel-gray hover:text-pixel-white tracking-[0.08em]"
      >
        {more ? "▾ LESS" : "▸ MORE"}
      </button>

      {more && (
        <div className="space-y-1.5 border-t border-pixel-border/40 pt-1.5">
          <label
            className="flex items-center gap-2 font-mono text-[10px] text-pixel-gray"
            title="How far back the drift is measured. Short windows read a single push; long ones read the week's story."
          >
            <span className="w-20">WINDOW</span>
            {[1, 3, 6, 12, 24, 72].map((h) => (
              <button
                key={h}
                onClick={() => patch({ windowHours: h })}
                className={`px-1.5 py-0.5 rounded border transition-colors ${
                  windowHours === h
                    ? "border-green-400 text-green-400 bg-green-400/10"
                    : "border-pixel-border hover:text-pixel-white"
                }`}
              >
                {h}h
              </button>
            ))}
          </label>

          <label
            className="flex items-center gap-2 font-mono text-[10px] text-pixel-gray"
            title="Movement under this counts as QUIET rather than as a weak direction."
          >
            <span className="w-20">FLAT BAND</span>
            {[0.01, 0.02, 0.05, 0.1].map((v) => (
              <button
                key={v}
                onClick={() => patch({ flatBand: v })}
                className={`px-1.5 py-0.5 rounded border transition-colors ${
                  Math.abs(flatBand - v) < 1e-9
                    ? "border-green-400 text-green-400 bg-green-400/10"
                    : "border-pixel-border hover:text-pixel-white"
                }`}
              >
                {cents(v)}
              </button>
            ))}
          </label>

          <label
            className="flex items-center gap-2 font-mono text-[10px] text-pixel-gray"
            title="Require a minimum amount of movement in the chosen direction, on top of the mood itself."
          >
            <span className="w-20">MIN MOVE</span>
            {[0, 0.03, 0.05, 0.1, 0.2].map((v) => {
              const bear = (value?.lean ?? []).includes("bearish");
              const cur = bear ? -(value?.maxDrift ?? 0) : (value?.minDrift ?? 0);
              return (
                <button
                  key={v}
                  onClick={() =>
                    patch(
                      bear
                        ? { maxDrift: v === 0 ? undefined : -v, minDrift: undefined }
                        : { minDrift: v === 0 ? undefined : v, maxDrift: undefined },
                    )
                  }
                  className={`px-1.5 py-0.5 rounded border transition-colors ${
                    Math.abs(cur - v) < 1e-9
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border hover:text-pixel-white"
                  }`}
                >
                  {v === 0 ? "off" : cents(v)}
                </button>
              );
            })}
          </label>

          <label
            className="flex items-center gap-2 font-mono text-[10px] text-pixel-gray"
            title="A market with no usable price history has no mood. By default those trades are COPIED — a data gap is not a signal. Blocking them is a real choice with a real cost, shown in COVERAGE above."
          >
            <span className="w-20">UNREADABLE</span>
            {(["pass", "block"] as const).map((u) => (
              <button
                key={u}
                onClick={() => patch({ unknown: u })}
                className={`px-1.5 py-0.5 rounded border transition-colors ${
                  (value?.unknown ?? "pass") === u
                    ? "border-green-400 text-green-400 bg-green-400/10"
                    : "border-pixel-border hover:text-pixel-white"
                }`}
              >
                {u === "pass" ? "COPY ANYWAY" : "SKIP"}
              </button>
            ))}
          </label>

          <p className="font-mono text-[9px] leading-relaxed text-pixel-gray/70">
            The mood is price drift on the leader&apos;s own outcome token over the
            window — how much the crowd moved the odds on the thing they bought.
            It is not news and not social sentiment; it is the tape.
          </p>
        </div>
      )}
    </div>
  );
}
