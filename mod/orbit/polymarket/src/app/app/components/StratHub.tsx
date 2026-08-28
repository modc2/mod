"use client";

// THE STRAT HUB — the front door to /strats. Every strat is a card, and the
// card's headline is its N-DAY BACKTEST: the same engine, the same window for
// all of them, replayed live by useHubBacktests. That is what makes a wall of
// strats comparable — the cards used to print `lastPnl`, a leftover from
// whatever window each strat happened to be opened with, so a "+$40" card and
// a "+$4" card were measuring different things.
//
// The RECOMMENDED strats are scored the same way. A template card runs the
// SavedIndex forking it would create, roster and all, so "we suggest this one"
// is a number you can check rather than a claim — and the window pills let you
// ask it over 1, 3, 7, 14 or 30 days, which is the difference between a strat
// that had one good day and one that works.
//
// Search filters both shelves at once, over names, descriptions and the filter
// chips — "sports", "buys only", "top 5" all narrow the grid.
//
// Below the fold: the live money each strat actually holds (engine ledger, not
// the sim) and the filter chips that say what slice of flow it copies. Clicking
// a card opens that strat's workspace; ⑂ / × / + are handed back to the picker
// (the single strat manager) so indexStore + server sync stay in one place.

import { useMemo, useState } from "react";
import { DEFAULT_STRATS, type StratTemplate } from "../lib/defaultStrats";
import { fmtUsd, type StratMoney } from "../lib/stratStats";
import {
  HUB_WINDOWS, templateBacktestKey,
  type ForwardCheck, type ForwardVerdict, type HubBacktest,
} from "../lib/hubBacktest";
import { triggerHubRefresh, triggerHubWorker, type WorkerStatus } from "../lib/hubCache";
import { describeTraderFilter } from "../lib/strats/strat";
import { shortAddress } from "../lib/auth";
import type { PublicStratEntry } from "../lib/stratSync";
import type { SavedIndex } from "../lib/types";

interface Props {
  indexes: SavedIndex[];
  /** Per-strat live money-in/PnL (from useStratStats); keys are strat ids. */
  stats?: Record<string, StratMoney>;
  /** Per-card backtest (from useHubBacktests): saved strats by id, templates
      by `tpl:<slug>` — see `templateBacktestKey`. */
  backtests?: Record<string, HubBacktest>;
  /** Card keys whose replay is still running. */
  backtestPending?: Set<string>;
  /** True while the worker's cache is still being read — every card is
      "loading", not "empty". */
  backtestLoading?: boolean;
  /** The background worker's schedule, for the header status line. */
  worker?: WorkerStatus | null;
  /** The window every card is measured over, and the setter behind the pills. */
  days: number;
  onDaysChange: (days: number) => void;
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCreate: () => void;
  onFork: (t: StratTemplate) => void;
  /** Fork a SAVED strat (⑂ on its card) — the picker copies it and drops
      into rename mode. Distinct from `onFork`, which forks a template. */
  onForkSaved: (id: string) => void;
  /** Re-run every card's backtest now, ignoring the freshness window. */
  onRefresh?: () => void;
  // ── ME / PUBLIC strat classes ──
  /** The community gallery — every strat any account has published. */
  publicStrats?: PublicStratEntry[];
  publicLoading?: boolean;
  /** Signed-in EOA (lowercased), for marking my own gallery cards. */
  myAddress?: string | null;
  /** Fork a gallery strat into my (private) strats. */
  onImportPublic?: (e: PublicStratEntry) => void;
  /** Publish / unpublish one of my strats. */
  onSetVisibility?: (id: string, pub: boolean) => void;
  /** IDENTITY strat: create a strat that copies exactly ONE trader. */
  onCreateIdentity?: (address: string) => void;
}

function timeAgo(ts?: number): string {
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
function countdown(ts?: number): string {
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
function filterChips(idx: Pick<SavedIndex, "marketQuery" | "tradeFilters" | "filter">): string[] {
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
function CurveFace({ curve, up }: { curve: number[]; up: boolean }) {
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
function LoadingFace() {
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
function BacktestBlock({
  bt,
  running,
  days,
  emptyLabel,
}: {
  bt?: HubBacktest;
  running: boolean;
  days: number;
  /** What to show when there's no result yet and nothing is running. */
  emptyLabel: string;
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
          replaying {days === 1 ? "24h" : `${days}d`} of leader flow…
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
      {bt?.funnel && bt.funnel.observed > 0 && <FunnelLine funnel={bt.funnel} />}
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

function signedUsd(v: number): string {
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
function FunnelLine({ funnel }: { funnel: NonNullable<HubBacktest["funnel"]> }) {
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
        `${funnel.observed} leader entries seen · ${funnel.executed} copied · ` +
        `${funnel.gated} blocked by strat filters · ${funnel.outranked} outranked · ` +
        `${funnel.skipped} unplaceable\n` +
        reasons.map(([r, n]) => `${n}× ${r}`).join("\n")
      }
    >
      {funnel.executed}/{funnel.observed} entries copied
      {blocked > 0 && topReason ? ` · ${topCount}× ${topReason}` : ""}
    </div>
  );
}

/// Free-text match over everything that identifies a strat on its card: name,
/// description, and the chips it renders. EVERY term must hit, so "sports
/// buys" narrows the grid instead of widening it.
function matchesQuery(query: string, name: string, chips: string[], description?: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [name, description ?? "", ...chips].join(" ").toLowerCase();
  return q.split(/\s+/).filter(Boolean).every((term) => hay.includes(term));
}

export default function StratHub({
  indexes,
  stats,
  backtests,
  backtestPending,
  backtestLoading,
  worker,
  days,
  onDaysChange,
  activeId,
  onSelect,
  onDelete,
  onCreate,
  onFork,
  onForkSaved,
  onRefresh,
  publicStrats = [],
  publicLoading,
  myAddress,
  onImportPublic,
  onSetVisibility,
  onCreateIdentity,
}: Props) {
  const [query, setQuery] = useState("");
  // The two strat classes: ME (my strats — private unless I publish them)
  // and PUBLIC (every strat anyone has published, forkable into ME).
  const [shelf, setShelf] = useState<"me" | "public">("me");
  // "Show me only the ones that actually held up." Filters the grid down to
  // cards whose walk-forward verdict is HELD — profitable over the previous
  // window AND over this one. Off by default: hiding a strat you own is worse
  // than showing it with a red badge.
  const [heldOnly, setHeldOnly] = useState(false);
  // Inline IDENTITY creation — one address, one strat.
  const [identityAddr, setIdentityAddr] = useState("");
  const identityValid = /^0x[0-9a-fA-F]{40}$/.test(identityAddr.trim());
  const submitIdentity = () => {
    if (!identityValid || !onCreateIdentity) return;
    onCreateIdentity(identityAddr.trim());
    setIdentityAddr("");
  };

  // The HELD-ONLY gate, by card key. A card with no walk-forward yet (still
  // replaying, or a snapshot from before the check existed) does NOT pass —
  // "we haven't checked" must never read as "confirmed".
  const held = (key: string) => !heldOnly || backtests?.[key]?.forward?.ok === true;

  // Newest-touched first — the strat you were just editing leads the grid.
  const sorted = useMemo(
    () =>
      [...indexes]
        .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
        .filter((idx) => matchesQuery(query, idx.name, filterChips(idx)) && held(idx.id)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [indexes, query, heldOnly, backtests],
  );
  const recommended = useMemo(
    () => DEFAULT_STRATS.filter((t) =>
      matchesQuery(query, t.name, filterChips(t.params), t.description)
      && held(templateBacktestKey(t.slug)),
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [query, heldOnly, backtests],
  );
  /** How the whole shelf scored, for the header tally. */
  const forwardTally = useMemo(() => {
    let ok = 0, checked = 0;
    for (const idx of indexes) {
      const f = backtests?.[idx.id]?.forward;
      if (!f) continue;
      checked++;
      if (f.ok) ok++;
    }
    return { ok, checked };
  }, [indexes, backtests]);
  // The PUBLIC shelf, through the same search box as everything else.
  const publicGallery = useMemo(
    () => publicStrats.filter((e) => matchesQuery(query, e.strat.name, filterChips(e.strat))),
    [publicStrats, query],
  );
  const oldest = sorted.reduce<number | null>((acc, i) => {
    const at = backtests?.[i.id]?.at;
    if (!at) return acc;
    return acc === null || at < acc ? at : acc;
  }, null);
  const windowLabel = days === 1 ? "24h" : `${days}d`;

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <div className="flex items-baseline gap-3 min-w-0">
          <span
            className="text-[16px] font-bold tracking-[0.2em] text-pixel-white uppercase shrink-0"
            style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
          >
            STRAT HUB
          </span>
          {/* The two strat classes. ME is everything this account owns —
              private by default; PUBLIC is the community gallery of every
              published strat, forkable into ME. */}
          <div className="flex items-center rounded-[var(--radius-sm)] border border-pixel-border overflow-hidden shrink-0">
            {([["me", `ME · ${indexes.length}`], ["public", `PUBLIC · ${publicStrats.length}`]] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setShelf(key)}
                title={key === "me"
                  ? "Your strats. Private by default — publish one to put it in the gallery."
                  : "Every strat any account has published. Fork one to make it yours."}
                className={`px-2.5 py-1 text-[11px] font-mono font-semibold tracking-[0.1em] transition-colors ${
                  shelf === key
                    ? "bg-green-400/[0.12] text-green-400"
                    : "text-pixel-gray hover:text-pixel-white"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="text-[12px] font-mono text-pixel-gray truncate">
            {shelf === "me"
              ? `every card backtested over the last ${windowLabel}, and again over the ${windowLabel} before it`
              : "community gallery · fork a card to copy it into your strats"}
          </span>
          {/* The shelf's walk-forward score. "2/9 HELD" is the number that
              says how much of this wall is a real edge rather than one good
              window — and it's usually small, which is the point. */}
          {shelf === "me" && forwardTally.checked > 0 && (
            <span
              className={`text-[11px] font-mono shrink-0 ${forwardTally.ok > 0 ? "text-green-400" : "text-amber-400"}`}
              title={`${forwardTally.ok} of ${forwardTally.checked} checked strat(s) were profitable over the previous ${windowLabel} AND the ${windowLabel} since. The rest either faded, never had an edge, or stopped trading.`}
            >
              ✓ {forwardTally.ok}/{forwardTally.checked} HELD
            </span>
          )}
          {/* The worker's heartbeat. Cards are cached, not live — saying so
              (and when the next pass lands) is the difference between "these
              numbers are stale" and "these numbers are 40 minutes old, by
              design". */}
          {backtestLoading ? (
            <span className="text-[11px] font-mono text-amber-400 shrink-0 flex items-center gap-1.5">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              LOADING CACHE
            </span>
          ) : worker ? (
            <span
              className="text-[11px] font-mono text-pixel-gray/80 shrink-0"
              title={
                worker.error
                  ? `Background worker last failed: ${worker.error}`
                  : `A worker inside the app replays every strat over the last ${windowLabel}, out of a trader-history cache it keeps on disk. Last pass covered ${worker.strats} strat(s).`
                    + (worker.feeds
                      ? `\n\nFeed cache: ${worker.feeds.cached}/${worker.feeds.traders} traders`
                        + (worker.feeds.missing ? `, ${worker.feeds.missing} not fetched yet` : "")
                        + (worker.feeds.stale ? `, ${worker.feeds.stale} due a top-up` : "")
                        + `. Last topped up ${worker.feeds.at ? timeAgo(worker.feeds.at) : "never"}`
                        + `, next ${countdown(worker.feeds.nextAt)}.`
                        + (worker.feeds.error ? `\nLast fetch error: ${worker.feeds.error}` : "")
                      : "")
              }
            >
              {worker.running
                ? "⟳ worker running…"
                : worker.error
                  ? "⚠ worker error"
                  : `⟳ cached ${timeAgo(worker.at)} · next ${countdown(worker.nextAt)}`}
              {/* Coverage, not just recency: a pass over a half-filled cache
                  is a real pass with understated numbers, and the header is
                  where that has to be visible. */}
              {worker.feeds && worker.feeds.missing > 0 && (
                <span className="ml-1.5 text-amber-400">
                  {worker.feeds.running ? "⇣" : "⧗"} warming {worker.feeds.cached}/{worker.feeds.traders}
                </span>
              )}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Search — one box over both shelves, so "find me a sports strat"
              is answered by the saved ones AND the recommendations at once. */}
          <div className="relative">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="search strats…"
              className="w-[200px] pl-6 pr-6 py-1 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-pixel-border text-[11.5px] font-mono text-pixel-white placeholder:text-pixel-gray/70 focus:outline-none focus:border-green-400/60"
            />
            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[11px] text-pixel-gray pointer-events-none">⌕</span>
            {query && (
              <button
                onClick={() => setQuery("")}
                title="Clear"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[12px] text-pixel-gray hover:text-pixel-white"
              >
                ×
              </button>
            )}
          </div>
          {/* Backtest window — the N in "N-DAY BACKTEST". Every card re-runs
              (or repaints from its stored run) at the new window together, so
              the grid stays comparable. */}
          <div className="flex items-center rounded-[var(--radius-sm)] border border-pixel-border overflow-hidden">
            {HUB_WINDOWS.map((d) => (
              <button
                key={d}
                onClick={() => onDaysChange(d)}
                title={`Backtest every card over the last ${d === 1 ? "24 hours" : `${d} days`}`}
                className={`px-2 py-1 text-[11px] font-mono transition-colors ${
                  d === days
                    ? "bg-amber-400/[0.12] text-amber-400"
                    : "text-pixel-gray hover:text-pixel-white"
                }`}
              >
                {d}D
              </button>
            ))}
          </div>
          {/* The walk-forward filter — the whole point of the check, as a
              control: hide everything that didn't stay profitable. */}
          <button
            onClick={() => setHeldOnly((v) => !v)}
            title={
              heldOnly
                ? "Showing only strats that were profitable over the previous window AND over this one. Click to show all."
                : `Show only strats that made money over the previous ${windowLabel} AND over the ${windowLabel} since — everything else (faded, no edge, stalled, unchecked) is hidden.`
            }
            className={`shrink-0 px-2.5 py-1 rounded-[var(--radius-sm)] border text-[11px] font-mono font-semibold tracking-[0.08em] transition-colors ${
              heldOnly
                ? "border-green-400/60 text-green-400 bg-green-400/[0.10]"
                : "border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400/60"
            }`}
          >
            ✓ HELD ONLY
          </button>
          {onRefresh && (
            <button
              onClick={() => {
                // Kick all three: top up the server's trader-feed cache (the
                // only upstream work), re-replay every strat on the server
                // (including ones this browser isn't showing), and re-run
                // locally so these cards repaint without waiting for either.
                void triggerHubRefresh();
                void triggerHubWorker();
                onRefresh();
              }}
              title={`Re-run every strat's ${windowLabel} backtest now${oldest ? ` — oldest is ${timeAgo(oldest)}` : ""}`}
              className="shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded-[var(--radius-sm)] border border-pixel-border text-[11px] font-mono text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
            >
              ↻ RERUN
              {oldest && <span className="opacity-60">{timeAgo(oldest)}</span>}
            </button>
          )}
        </div>
      </div>

      {shelf === "me" && (<>
      {/* An empty HELD-ONLY shelf is an answer, and a common one. Say it in
          words so it can't be read as a broken filter. */}
      {heldOnly && sorted.length === 0 && indexes.length > 0 && (
        <div className="mb-3 px-3 py-2 rounded-[var(--radius-sm)] border border-amber-400/40 text-[11.5px] font-mono text-amber-400/90 leading-relaxed">
          None of your {indexes.length} strat(s) held up over both windows — each one either faded,
          never had an edge, stopped trading, or hasn&apos;t been checked yet. That is a result, not a
          broken filter: turn HELD ONLY off to see what each card actually did.
        </div>
      )}
      {/* Card grid */}
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
        {sorted.map((idx) => {
          const isActive = idx.id === activeId;
          const enabledTraders = idx.traders.filter((t) => t.enabled !== false).length;
          const chips = filterChips(idx);
          // The card's headline: this strat replayed over the last 24h.
          const bt = backtests?.[idx.id];
          const running = (backtestPending?.has(idx.id) ?? false) || (backtestLoading && !backtests?.[idx.id]);
          // Always render the viewer's live money row — an untraded strat
          // reads $0 rather than hiding it, so cards compare at a glance.
          const money = stats?.[idx.id];
          const totalPnl = money?.totalPnl ?? 0;
          return (
            <div
              key={idx.id}
              onClick={() => onSelect(idx.id)}
              className={`market-card group cursor-pointer flex flex-col relative overflow-hidden isolate ${isActive ? "market-card-selected" : ""}`}
            >
              {/* The card IS the curve — everything below sits on top of it. */}
              {bt && bt.curve.length > 1 ? (
                <CurveFace curve={bt.curve} up={bt.pnl >= 0} />
              ) : running ? (
                <LoadingFace />
              ) : null}
              {/* Top: status + updated */}
              <div className="flex items-center justify-between mb-2.5">
                <span className="flex items-center gap-1.5 text-[10.5px] tracking-[0.16em] uppercase font-semibold">
                  {idx.liveEnabled ? (
                    <>
                      <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                      <span className="text-green-400">LIVE</span>
                    </>
                  ) : (
                    <span className="text-pixel-gray">DRAFT</span>
                  )}
                  {isActive && (
                    <span className="ml-1 px-1.5 py-0.5 border border-green-400/60 text-green-400 rounded-full text-[9px] tracking-[0.1em]">
                      ACTIVE
                    </span>
                  )}
                  {idx.identity && (
                    <span
                      className="ml-1 px-1.5 py-0.5 border border-cyan-400/60 text-cyan-400 rounded-full text-[9px] tracking-[0.1em]"
                      title={`IDENTITY strat — copies exactly one trader: ${idx.identity}`}
                    >
                      IDENTITY
                    </span>
                  )}
                </span>
                <div className="flex items-center gap-2">
                  {/* The strat's class, and the switch between them. Every
                      strat starts PRIVATE; publishing puts it (plaintext) in
                      the community gallery for anyone to fork. */}
                  {onSetVisibility && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onSetVisibility(idx.id, idx.visibility !== "public"); }}
                      title={idx.visibility === "public"
                        ? "This strat is PUBLIC — listed in the gallery for anyone to view and fork. Click to make it private again."
                        : "This strat is PRIVATE (the default) — only you can see it. Click to publish it to the PUBLIC gallery."}
                      className={`px-1.5 py-0.5 rounded-full border text-[9px] font-semibold tracking-[0.1em] transition-colors ${
                        idx.visibility === "public"
                          ? "border-green-400/60 text-green-400 hover:border-pixel-border hover:text-pixel-gray"
                          : "border-pixel-border text-pixel-gray hover:border-green-400/60 hover:text-green-400"
                      }`}
                    >
                      {idx.visibility === "public" ? "PUBLIC" : "PRIVATE"}
                    </button>
                  )}
                  <span className="text-[11px] text-pixel-gray font-mono">{timeAgo(idx.updatedAt)}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); onForkSaved(idx.id); }}
                    title={`Fork "${idx.name}" — an independent copy, stopped and un-funded, ready to rename`}
                    className="text-[12px] leading-none text-pixel-gray hover:text-green-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    ⑂
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDelete(idx.id); }}
                    title="Delete strat"
                    className="text-[13px] leading-none text-pixel-gray hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    ×
                  </button>
                </div>
              </div>

              {/* Name */}
              <div className="text-[16px] font-semibold tracking-[-0.01em] text-pixel-white leading-[1.35] mb-3 line-clamp-2 font-mono">
                {idx.name}
              </div>

              {/* ── The headline: the N-day backtest ──
                  Every card is replayed over the same window through the same
                  engine the BACKTEST tab and the live session run, so these
                  numbers are comparable across the whole hub. */}
              <BacktestBlock
                bt={bt}
                running={running}
                days={days}
                emptyLabel={enabledTraders === 0 ? "no traders to copy" : "queued"}
              />

              {/* Params */}
              <div className="grid grid-cols-3 gap-1.5 mb-2.5">
                {[
                  { label: "TRADERS", value: `${enabledTraders}${enabledTraders !== idx.traders.length ? `/${idx.traders.length}` : ""}` },
                  { label: "CAPITAL", value: `$${idx.capital ?? 1000}` },
                  { label: "TRADE", value: `$${idx.minTrade ?? 5}–${idx.maxTrade ?? 100}` },
                ].map((p) => (
                  <div key={p.label} className="px-2 py-1.5 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-[var(--border)]">
                    <div className="text-[9px] text-pixel-gray font-semibold tracking-[0.14em]">{p.label}</div>
                    <div className="text-[12.5px] text-pixel-white font-mono truncate">{p.value}</div>
                  </div>
                ))}
              </div>

              {/* Filter chips — what slice of the watched flow this strat copies */}
              {chips.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2.5">
                  {chips.map((c) => (
                    <span
                      key={c}
                      className="px-1.5 py-0.5 text-[10px] font-mono text-cyan-400 border border-cyan-400/40 rounded-full truncate max-w-full"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}

              <div className="flex-1" />

              {/* Real money footer — what THIS strat has in play and its
                  cumulative live P&L (engine ledger, not the sim). Deliberately
                  not the wallet's USDC: that number is shared by every strat, so
                  printing it per card told the user each one held the whole
                  balance. */}
              <div className="flex items-center justify-between gap-2 pt-2.5 mt-1 border-t border-[var(--border)] text-[11px] font-mono">
                <span
                  className="text-pixel-gray truncate"
                  title={
                    money?.openPositions
                      ? `${fmtUsd(money.openValue)} across ${money.openPositions} open position(s)`
                      : "This strat holds no positions"
                  }
                >
                  LIVE {money?.openPositions ? `${fmtUsd(money.openValue)} in play` : "flat"}
                </span>
                <span
                  className={`shrink-0 ${totalPnl > 0 ? "text-green-400" : totalPnl < 0 ? "text-red-400" : "text-pixel-gray"}`}
                  title={money ? `$${money.moneyIn.toFixed(2)} deployed · realized ${fmtUsd(money.realized)} · unrealized ${fmtUsd(money.unrealized)}` : "No fills from your wallet in this strat yet"}
                >
                  {money ? `${totalPnl >= 0 ? "+" : ""}${fmtUsd(totalPnl)}` : "—"}
                </span>
              </div>
            </div>
          );
        })}

        {/* New-strat card */}
        <button
          onClick={onCreate}
          className="min-h-[180px] rounded-[var(--radius-lg)] border border-dashed border-pixel-border grid place-items-center text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
        >
          <div className="text-center">
            <div className="text-[22px] leading-none mb-1.5">+</div>
            <div className="text-[11px] font-mono font-semibold tracking-[0.14em]">NEW STRAT</div>
          </div>
        </button>

        {/* New IDENTITY card — a strat that copies exactly ONE trader.
            Paste the address here, or hit ⑂ IDENTITY on any trader profile. */}
        {onCreateIdentity && (
          <div className="min-h-[180px] rounded-[var(--radius-lg)] border border-dashed border-pixel-border flex flex-col items-center justify-center gap-2 px-4 text-pixel-gray">
            <div className="text-[11px] font-mono font-semibold tracking-[0.14em] text-cyan-400">NEW IDENTITY</div>
            <div className="text-[10.5px] font-mono text-center leading-snug">
              copy ONE trader — the strat is their identity
            </div>
            <input
              value={identityAddr}
              onChange={(e) => setIdentityAddr(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submitIdentity(); }}
              placeholder="0x trader address…"
              spellCheck={false}
              className="w-full max-w-[220px] px-2 py-1 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-pixel-border text-[11px] font-mono text-pixel-white placeholder:text-pixel-gray/60 focus:outline-none focus:border-cyan-400/60"
            />
            <button
              onClick={submitIdentity}
              disabled={!identityValid}
              title={identityValid ? "Create an IDENTITY strat copying this trader" : "Paste a full 0x… trader address"}
              className={`px-2.5 py-1 rounded-[var(--radius-sm)] border text-[10.5px] font-mono font-semibold tracking-[0.1em] transition-colors ${
                identityValid
                  ? "border-cyan-400/60 text-cyan-400 hover:bg-cyan-400/10"
                  : "border-pixel-border text-pixel-gray/50 cursor-not-allowed"
              }`}
            >
              ⑂ CREATE IDENTITY
            </button>
          </div>
        )}
      </div>

      {/* Recommended strats — curated recipes forked into user-owned strats,
          each one BACKTESTED over the same window as the saved cards above.
          A recommendation you can't check isn't a recommendation; the number
          on the card is this recipe seeded with today's leaderboard and
          replayed, which is exactly what forking it gives you. */}
      <div className="flex items-baseline gap-3 mt-8 mb-4">
        <span
          className="text-[13px] font-bold tracking-[0.2em] text-pixel-white uppercase"
          style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
        >
          RECOMMENDED
        </span>
        <span className="text-[12px] font-mono text-pixel-gray">
          {recommended.length} recipes · backtested over the last {windowLabel} with today&apos;s leaderboard · click to fork
        </span>
        {/* Say the quiet part. These rosters are picked BY trailing P&L over
            the window they're then scored on, so the number is survivorship-
            biased by construction — an upper bound on what the recipe can do,
            not a forecast. A saved strat's card has no such bias: its traders
            were chosen before the window it's measured over. */}
        <span
          className="text-[11px] font-mono text-amber-400/80"
          title="Each recipe seeds itself with the traders who ALREADY topped the leaderboard for this window, then replays that window. Copying them from the start was not possible — read these as an upper bound."
        >
          ⚠ leaderboard-seeded — upper bound, not a forecast
        </span>
      </div>
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
        {recommended.map((t) => {
          const chips = filterChips(t.params);
          const key = templateBacktestKey(t.slug);
          const bt = backtests?.[key];
          const running = (backtestPending?.has(key) ?? false) || (backtestLoading && !bt);
          return (
            <div
              key={t.slug}
              onClick={() => onFork(t)}
              className="market-card group cursor-pointer flex flex-col relative overflow-hidden isolate"
            >
              {bt && bt.curve.length > 1 ? (
                <CurveFace curve={bt.curve} up={bt.pnl >= 0} />
              ) : running ? (
                <LoadingFace />
              ) : null}
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-[10.5px] tracking-[0.16em] uppercase font-semibold text-cyan-400">
                  TEMPLATE
                </span>
                <span className="text-[11px] font-mono text-pixel-gray opacity-60 group-hover:opacity-100 group-hover:text-green-400 transition-opacity">
                  ⑂ FORK
                </span>
              </div>
              <div className="text-[16px] font-semibold tracking-[-0.01em] text-pixel-white leading-[1.35] mb-2 font-mono">
                {t.name}
              </div>
              {/* Same block, same replay, same window as a saved strat's card. */}
              <BacktestBlock bt={bt} running={running} days={days} emptyLabel="queued" />
              <div className="text-[11.5px] leading-snug text-pixel-gray mb-3">{t.description}</div>
              {chips.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2.5">
                  {chips.map((c) => (
                    <span
                      key={c}
                      className="px-1.5 py-0.5 text-[10px] font-mono text-cyan-400 border border-cyan-400/40 rounded-full truncate max-w-full"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
              <div className="flex-1" />
              <div className="pt-2.5 mt-1 border-t border-[var(--border)] text-[10.5px] font-mono text-pixel-gray">
                seeds top {t.seed.count ?? 10} traders
                {t.seed.category ? ` · ${t.seed.category}` : ""}
                {t.seed.days ? ` · ${t.seed.days}d` : ""}
              </div>
            </div>
          );
        })}
      </div>
      </>)}

      {/* ── PUBLIC shelf — the community gallery. Every published strat from
          every account, mine included (badged MINE). Forking copies the whole
          strategy into MY strats, private, with lineage. ── */}
      {shelf === "public" && (
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
          {publicGallery.map((entry) => {
            const s = entry.strat;
            const chips = filterChips(s);
            const mine = !!myAddress && entry.owner === myAddress.toLowerCase();
            const enabledTraders = (s.traders ?? []).filter((t) => t.enabled !== false).length;
            return (
              <div
                key={entry.id}
                onClick={() => onImportPublic?.(entry)}
                className="market-card group cursor-pointer flex flex-col relative overflow-hidden isolate"
              >
                <div className="flex items-center justify-between mb-2.5">
                  <span className="flex items-center gap-1.5 text-[10.5px] tracking-[0.16em] uppercase font-semibold">
                    <span className="text-green-400">PUBLIC</span>
                    {s.identity && (
                      <span
                        className="px-1.5 py-0.5 border border-cyan-400/60 text-cyan-400 rounded-full text-[9px] tracking-[0.1em]"
                        title={`IDENTITY strat — copies exactly one trader: ${s.identity}`}
                      >
                        IDENTITY
                      </span>
                    )}
                    {mine && (
                      <span className="px-1.5 py-0.5 border border-amber-400/60 text-amber-400 rounded-full text-[9px] tracking-[0.1em]">
                        MINE
                      </span>
                    )}
                  </span>
                  <span className="text-[11px] font-mono text-pixel-gray opacity-60 group-hover:opacity-100 group-hover:text-green-400 transition-opacity">
                    ⑂ FORK
                  </span>
                </div>
                <div className="text-[16px] font-semibold tracking-[-0.01em] text-pixel-white leading-[1.35] mb-2 font-mono line-clamp-2">
                  {s.name}
                </div>
                <div className="text-[11px] font-mono text-pixel-gray mb-2.5">
                  by {entry.owner ? shortAddress(entry.owner) : "anon"} · {timeAgo(entry.updated_at)}
                </div>
                <div className="grid grid-cols-3 gap-1.5 mb-2.5">
                  {[
                    { label: "TRADERS", value: `${enabledTraders}` },
                    { label: "CAPITAL", value: `$${s.capital ?? 1000}` },
                    { label: "TRADE", value: `$${s.minTrade ?? 5}–${s.maxTrade ?? 100}` },
                  ].map((p) => (
                    <div key={p.label} className="px-2 py-1.5 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-[var(--border)]">
                      <div className="text-[9px] text-pixel-gray font-semibold tracking-[0.14em]">{p.label}</div>
                      <div className="text-[12.5px] text-pixel-white font-mono truncate">{p.value}</div>
                    </div>
                  ))}
                </div>
                {chips.length > 0 && (
                  <div className="flex flex-wrap gap-1 mb-2.5">
                    {chips.map((c) => (
                      <span
                        key={c}
                        className="px-1.5 py-0.5 text-[10px] font-mono text-cyan-400 border border-cyan-400/40 rounded-full truncate max-w-full"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex-1" />
                <div className="flex items-center justify-between pt-2.5 mt-1 border-t border-[var(--border)] text-[10.5px] font-mono text-pixel-gray">
                  <span>fork to copy into YOUR strats — forks land private</span>
                  {mine && onSetVisibility && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onSetVisibility(entry.id, false); }}
                      title="Take your strat off the public gallery (it stays in ME)"
                      className="shrink-0 text-pixel-gray hover:text-red-400 transition-colors"
                    >
                      UNPUBLISH
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          {publicGallery.length === 0 && (
            <div className="min-h-[180px] rounded-[var(--radius-lg)] border border-dashed border-pixel-border grid place-items-center text-pixel-gray text-[11.5px] font-mono px-6 text-center leading-relaxed">
              {publicLoading
                ? "loading the gallery…"
                : query
                  ? "no public strats match the search"
                  : "no public strats yet — flip one of yours to PUBLIC on the ME shelf and it will show up here for everyone"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
