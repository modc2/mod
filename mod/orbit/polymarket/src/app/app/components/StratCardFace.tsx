"use client";

// THE CARD FACE — the shared chrome every card on the strat wall is drawn
// with, factored out of StratHub so the two card CLASSES can never drift into
// looking like different products.
//
// The wall carries two kinds of card:
//
//   • a SAVED STRAT — a multi-trader index in this browser's store, and
//   • a COPY allocation — one leader with dollars against them, owned by the
//     server's copy book (api/src/copy.rs).
//
// They used to live on two routes with two vocabularies (/strats and the COPY
// DESK at /copy). They are one wall now, which only means anything if a copy
// card's number is computed and drawn the same way a strat card's is: same
// replay (lib/hubReplay.ts), same window, same BacktestBlock, same walk-forward
// verdict, same funnel line. That is what this file guarantees — it is imported
// by both, and there is no second rendering of "roughly the same thing".

import { describeTraderFilter } from "../lib/strats/strat";
import type { ForwardCheck, ForwardVerdict, HubBacktest } from "../lib/hubBacktest";
import type { SavedIndex } from "../lib/types";

export function timeAgo(ts?: number): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** "in 1h 12m" / "any moment" — the worker's next pass. */
export function countdown(ts?: number): string {
  if (!ts) return "soon";
  const ms = ts - Date.now();
  if (ms <= 0) return "any moment";
  const m = Math.round(ms / 60_000);
  if (m < 60) return `in ${m}m`;
  return `in ${Math.floor(m / 60)}h ${m % 60}m`;
}

/// Compact human summary of the per-trade filters, one chip per active
/// dimension. Mirrors the semantics documented on TradeFilters in types.ts.
/// Takes just the two filter fields so default-strat templates (which are
/// Partial<SavedIndex> recipes) can render the same chips as saved strats.
export function filterChips(idx: Pick<SavedIndex, "marketQuery" | "tradeFilters" | "filter">): string[] {
  const chips: string[] = [];
  if (idx.marketQuery?.trim()) chips.push(`"${idx.marketQuery.trim()}"`);
  const f = idx.tradeFilters;
  if (f) {
    if (f.sides === "buy") chips.push("BUYS ONLY");
    if (f.sides === "sell") chips.push("SELLS ONLY");
    if (f.minPrice !== undefined || f.maxPrice !== undefined) {
      const lo = Math.round((f.minPrice ?? 0) * 100);
      const hi = Math.round((f.maxPrice ?? 1) * 100);
      chips.push(`${lo}–${hi}¢`);
    }
    if (f.minNotional !== undefined || f.maxNotional !== undefined) {
      const lo = f.minNotional !== undefined ? `$${f.minNotional}` : "$0";
      const hi = f.maxNotional !== undefined ? `$${f.maxNotional}` : "∞";
      chips.push(`${lo}–${hi}`);
    }
    if (f.categories && f.categories.length > 0) chips.push(f.categories.join("/").toUpperCase());
  }
  // The trader gate reads as a chip too — "top 5 by score" is as much a part
  // of what this strat trades as "BUYS ONLY".
  if (idx.filter) chips.push(describeTraderFilter(idx.filter).toUpperCase());
  return chips;
}

/// THE CARD FACE — the strat's equity path, full-bleed behind everything else.
/// A wall of strats is a wall of shapes first and numbers second: you can see
/// which one ran up, which one bled, and which one did nothing at all without
/// reading a single figure. Baseline = starting capital, so any fill under the
/// dashed line is a drawdown. Purely decorative markup (aria-hidden) — every
/// number it encodes is also printed on the card.
export function CurveFace({ curve, up }: { curve: number[]; up: boolean }) {
  if (curve.length < 2) return null;
  const W = 100, H = 40;
  const min = Math.min(...curve), max = Math.max(...curve);
  const span = max - min || 1;
  const x = (i: number) => (i / (curve.length - 1)) * W;
  const y = (v: number) => H - ((v - min) / span) * H;
  const pts = curve.map((v, i) => `${x(i).toFixed(2)} ${y(v).toFixed(2)}`);
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p}`).join(" ");
  const area = `${line} L${W} ${H} L0 ${H} Z`;
  const base = y(curve[0]);
  const stroke = up ? "rgb(var(--up-rgb))" : "var(--danger)";
  const gid = `curveface-${up ? "up" : "dn"}`;
  return (
    <svg
      aria-hidden
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="absolute inset-x-0 bottom-0 h-[52%] w-full pointer-events-none -z-10"
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.16" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <line
        x1="0" y1={base} x2={W} y2={base}
        stroke="currentColor" strokeWidth="0.4" strokeDasharray="2 2"
        className="text-pixel-gray/20"
      />
      <path d={area} fill={`url(#${gid})`} />
      <path
        d={line} fill="none" stroke={stroke} strokeWidth="1.25"
        strokeLinejoin="round" vectorEffect="non-scaling-stroke" opacity="0.5"
      />
    </svg>
  );
}

/// The card face while the number is still being computed — a drifting
/// shimmer where the curve will be, so "we're working on it" and "this strat
/// traded nothing" never look the same.
export function LoadingFace() {
  return (
    <div
      aria-hidden
      className="absolute inset-x-0 bottom-0 h-[52%] pointer-events-none overflow-hidden -z-10"
    >
      <div className="absolute inset-0 bg-gradient-to-t from-pixel-gray/[0.07] to-transparent" />
      <div className="absolute inset-y-0 -inset-x-1/3 w-1/3 bg-gradient-to-r from-transparent via-pixel-gray/10 to-transparent animate-[hubshimmer_1.6s_ease-in-out_infinite]" />
    </div>
  );
}

/// The card headline — ONE component for saved strats and recommended ones.
/// A recommendation has to be judged on the same footing as something you
/// already own, which means the same markup and the same replay, never a
/// second rendering of "roughly the same thing".
export function BacktestBlock({
  bt,
  running,
  days,
  emptyLabel,
  originates,
}: {
  bt?: HubBacktest;
  running: boolean;
  days: number;
  /** What to show when there's no result yet and nothing is running. */
  emptyLabel: string;
  /** True for a strat that trades a market's own price tape rather than
      anyone's flow — it changes the vocabulary of the funnel line and of
      what the card says while it's replaying. */
  originates?: boolean;
}) {
  const up = (bt?.pnl ?? 0) >= 0;
  const window = days === 1 ? "1D" : `${days}D`;
  return (
    <div className="mb-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[9px] text-pixel-gray font-semibold tracking-[0.14em] shrink-0">
          {window} BACKTEST
        </span>
        {/* Where this number came from and how old it is. A card fed by the
            background worker says so — otherwise "replayed 40m ago" reads as a
            stall rather than as a cache doing its job. */}
        {bt && (
          <span
            className="text-[9.5px] font-mono text-pixel-gray shrink-0"
            title={
              `Replayed ${timeAgo(bt.at)} on $${bt.capital} of paper capital across ${bt.traders} trader(s)` +
              (bt.by === "worker" ? " by the background worker, over its cached trader history" : " in this browser") +
              // An understated number has to explain itself on the card, not
              // only in the header's coverage line.
              (bt.warming ? ` — ${bt.warming} of them had no cached history yet, so this is a FLOOR, not the strat's result` : "")
            }
          >
            {bt.note ? "" : `${bt.trades} TR · `}
            {bt.warming ? <span className="text-amber-400">⧗ </span> : null}
            {bt.by === "worker" ? "⟳ " : ""}{timeAgo(bt.at)}
          </span>
        )}
      </div>
      {running && !bt ? (
        // Say it's working. An empty card and a card mid-replay used to be
        // the same two words in the same grey.
        <div className="mt-1 flex items-center gap-1.5 text-[12px] font-mono text-pixel-gray">
          <span className="inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          replaying {days === 1 ? "24h" : `${days}d`} of {originates ? "price history" : "leader flow"}…
        </div>
      ) : bt && bt.note ? (
        // A structural empty is an answer, not a zero.
        <div className="mt-1 text-[12px] font-mono text-pixel-gray leading-snug">{bt.note}</div>
      ) : bt ? (
        <div className="flex items-baseline gap-2 mt-0.5">
          <span
            className={`text-[26px] leading-none font-mono font-semibold ${up ? "text-green-400" : "text-red-400"}`}
            title={`Net P&L of a $${bt.capital} deployment of this strat over the last ${days === 1 ? "24h" : `${days} days`}${bt.skipped ? ` · ${bt.skipped} candidate trades skipped` : ""}`}
          >
            {up ? "+" : "−"}${Math.abs(bt.pnl).toFixed(2)}
          </span>
          <span className={`text-[12px] font-mono ${up ? "text-green-400/80" : "text-red-400/80"}`}>
            {bt.roi >= 0 ? "+" : ""}{bt.roi.toFixed(2)}%
          </span>
          {/* What the P&L above is already net of. A card that trades a lot in
              expensive markets can hand back its whole edge here, and the
              headline number alone never shows you that. */}
          {bt.fees != null && bt.fees > 0.005 && (
            <span
              className="text-[11px] font-mono text-amber-400/80"
              title={
                `Polymarket's taker fee on this replay: $${bt.fees.toFixed(2)}`
                + (bt.feeBps ? ` — ${bt.feeBps} bps of the notional traded` : "")
                + ". Charged by the matcher on every fill as rate x price x (1 - price) x shares, "
                + "4–7% by category, biggest at 50¢. Already deducted from the P&L on the left."
              }
            >
              −${bt.fees.toFixed(2)} fees
            </span>
          )}
          {running && <span className="text-[10px] font-mono text-amber-400 animate-pulse">↻</span>}
        </div>
      ) : (
        <div className="mt-1 text-[12px] font-mono text-pixel-gray">{emptyLabel}</div>
      )}
      {/* WALK-FORWARD: the previous window, and whether this one confirmed it.
          Rendered for every card that has one — including the ones whose own
          window is empty, because "profitable yesterday, silent today" is
          precisely the state that needs saying out loud. A card without one
          (a snapshot older than this check) shows nothing rather than
          implying it passed. */}
      {bt?.forward && <ForwardLine fwd={bt.forward} pnl={bt.pnl} days={days} />}
      {/* The funnel, one line: how much of the observed flow this strat
          actually traded. "8/225 entries" with the dominant gate named is the
          difference between a strat that's selective and one that's mute. */}
      {bt?.funnel && bt.funnel.observed > 0 && (
        <FunnelLine funnel={bt.funnel} originates={originates} />
      )}
    </div>
  );
}

/// ── THE WALK-FORWARD BADGE ──
/// A backtest over one window answers "did this make money?", which is the
/// wrong question: over any single window a strat can be found that printed a
/// great number, and that is exactly what a wall of cards sorts to the top.
/// So every card also carries the window BEFORE its own, replayed with no
/// knowledge of what came after (lib/hubReplay.ts `ForwardCheck`), and the
/// badge says whether the edge survived into the window on the card.
///
/// HELD is the only pass. Everything else is a different failure, named:
/// FADED (made money then lost it) is the one that costs real capital.
const FORWARD_FACE: Record<ForwardVerdict, { icon: string; label: string; tone: string; why: string }> = {
  held: {
    icon: "✓", label: "HELD", tone: "text-green-400",
    why: "Profitable in the prior window AND in this one — the headline above survived out of sample. The strongest thing a card can say.",
  },
  faded: {
    icon: "✗", label: "FADED", tone: "text-red-400",
    why: "Made money in the prior window and LOST it in this one. A strat that looks good only on the window you picked is the classic way to lose real money — treat the headline as noise.",
  },
  recovered: {
    icon: "↗", label: "TURNED UP", tone: "text-amber-400",
    why: "Lost in the prior window, profitable in this one. One good window after a bad one is not yet an edge — it needs a second confirmation before it means anything.",
  },
  "no-edge": {
    icon: "✗", label: "NO EDGE", tone: "text-red-400",
    why: "Unprofitable in both windows. Nothing here to deploy.",
  },
  untested: {
    icon: "?", label: "UNTESTED", tone: "text-pixel-gray",
    why: "The prior window executed no trades, so there was no edge to confirm. This card's number rests on one window only.",
  },
  stalled: {
    icon: "⏸", label: "STALLED", tone: "text-amber-400",
    why: "Profitable in the prior window, then stopped trading entirely. It didn't lose — it went quiet, which usually means a gate (or the leaders) shut the flow off. Check the funnel line.",
  },
  idle: {
    icon: "·", label: "NO FLOW", tone: "text-pixel-gray",
    why: "Neither window executed a trade. There is nothing to judge yet.",
  },
};

export function signedUsd(v: number): string {
  return `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(2)}`;
}

/// "✓ HELD · prior day +$4.10 → +$2.30" — the previous window's result, the
/// current one, and the verdict of putting them side by side.
function ForwardLine({ fwd, pnl, days }: { fwd: ForwardCheck; pnl: number; days: number }) {
  const face = FORWARD_FACE[fwd.verdict] ?? FORWARD_FACE.idle;
  const unit = days === 1 ? "day" : `${days}d`;
  const stamp = (t: number) => new Date(t).toISOString().slice(5, 16).replace("T", " ");
  return (
    <div
      className="mt-1.5 flex items-center gap-1.5 text-[10px] font-mono min-w-0"
      title={
        `WALK-FORWARD — is this strat profitable NOW given what it did the previous ${unit}?\n\n` +
        `${face.label}: ${face.why}\n\n` +
        `Prior ${unit} (${stamp(fwd.from)} → ${stamp(fwd.to)} UTC): ${signedUsd(fwd.pnl)} ` +
        `(${fwd.roi >= 0 ? "+" : ""}${fwd.roi.toFixed(2)}%) over ${fwd.trades} trade(s)\n` +
        `This ${unit}: ${signedUsd(pnl)}\n\n` +
        "The prior window is replayed with the clock wound back: no trade, no trader stat and no " +
        "market it couldn't have seen at the time reaches it. Only the settlement prices are " +
        "today's — that's what values the window, not what decides it."
      }
    >
      <span className={`shrink-0 font-semibold tracking-[0.12em] ${face.tone}`}>
        {face.icon} {face.label}
      </span>
      <span className="text-pixel-gray truncate">
        prior {unit} {fwd.trades > 0 ? signedUsd(fwd.pnl) : "no trades"} → {signedUsd(pnl)}
      </span>
    </div>
  );
}

/// "12/225 ENTRIES · 213 blocked by time-to-close" — the one line that makes
/// over-filtering visible instead of leaving a suspiciously quiet card.
function FunnelLine({ funnel, originates }: {
  funnel: NonNullable<HubBacktest["funnel"]>;
  /** Origination strats have no leader flow — their funnel counts the signals
      the strat raised itself, so "copied" would be the wrong word for them. */
  originates?: boolean;
}) {
  const reasons = Object.entries(funnel.reasons).sort((a, b) => b[1] - a[1]);
  const [topReason, topCount] = reasons[0] ?? ["", 0];
  const blocked = funnel.observed - funnel.executed;
  const share = funnel.observed > 0 ? funnel.executed / funnel.observed : 0;
  // Under a tenth of the flow copied is worth flagging — that's the "why is
  // nothing happening?" state, and it should look different from selectivity.
  const thin = share < 0.1;
  return (
    <div
      className={`mt-1 text-[10px] font-mono truncate ${thin ? "text-amber-400/90" : "text-pixel-gray"}`}
      title={
        `${funnel.observed} ${originates ? "signals raised" : "leader entries seen"} · ${funnel.executed} taken · ` +
        `${funnel.gated} blocked by strat filters · ${funnel.outranked} outranked · ` +
        `${funnel.skipped} unplaceable\n` +
        reasons.map(([r, n]) => `${n}× ${r}`).join("\n")
      }
    >
      {funnel.executed}/{funnel.observed} {originates ? "signals taken" : "entries copied"}
      {blocked > 0 && topReason ? ` · ${topCount}× ${topReason}` : ""}
    </div>
  );
}

/// Free-text match over everything that identifies a strat on its card: name,
/// description, and the chips it renders. EVERY term must hit, so "sports
/// buys" narrows the grid instead of widening it.
export function matchesQuery(query: string, name: string, chips: string[], description?: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [name, description ?? "", ...chips].join(" ").toLowerCase();
  return q.split(/\s+/).filter(Boolean).every((term) => hay.includes(term));
}
