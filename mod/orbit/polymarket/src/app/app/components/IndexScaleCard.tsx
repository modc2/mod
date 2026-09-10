"use client";

// THE SCALE CARD — "you are 1/2,400th of this bench, so here is what their
// trades look like on your book."
//
// The trader index sizes every mirror off the ratio between YOUR capital and
// each leader's net worth (lib/traderIndex.ts). That ratio is the strategy,
// and until this card existed it was invisible: the engine computed it every
// cycle, the backtest divided by it, and the user never saw the one number
// that decides whether copying a whale means anything at their size.
//
// So the card answers, in this order, the three questions a person actually
// has:
//
//   1. How big are they compared to me?      → 1 : N, per trader
//   2. What does one of their trades become? → a live $ slider, projected
//   3. What do I MISS?                       → the trade size below which
//                                              their flow never reaches me,
//                                              and the capital that fixes it
//
// (3) is the honest part and the reason this is a card rather than a tooltip.
// Bankroll sizing on a small account produces mirrors under Polymarket's $1 /
// 5-share order floor, and the engine refuses those as SUB_SCALE — a strat
// that looks like it's running and places almost nothing. Rather than inflate
// them (which throws proportionality away), the card names the threshold and
// says what capital clears it.
//
// Pure presentation over `lib/traderIndex.ts`. It fetches exactly one thing —
// the bankrolls, from the same `/live/bankroll` endpoint the live engine and
// the backtest divide by — so the number on screen is the number that trades.

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { fetchTraderBankrolls } from "../lib/liveSessions";
import { shortAddress } from "../lib/identityStrat";
import {
  capitalToTrack,
  formatCompactUsd,
  formatScale,
  projectMirror,
  scaleIndex,
  summarizeIndex,
  visibilityThreshold,
  type TraderScale,
} from "../lib/traderIndex";
import type { SavedIndex } from "../lib/types";

/** The trade size the projection opens on — a normal conviction entry, not a
    whale print, so the first thing you read is the typical case. */
const DEFAULT_THEIR_TRADE = 500;

/** Sizes the "one of their trades" chip row offers. */
const TRADE_STEPS = [50, 100, 500, 2_000, 10_000];

const usd = (v: number) =>
  `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const VERDICT_TONE: Record<string, string> = {
  placed: "text-green-400",
  upscaled: "text-amber-400",
  capped: "text-cyan-300",
  "sub-scale": "text-red-400",
  unknown: "text-pixel-gray",
};

export interface IndexScaleCardProps {
  strat: SavedIndex;
  /** Capital behind the index right now. The workspace passes the live
      backtest/session capital so the card and the sim can't disagree. */
  capital: number;
  /** Rendered inside the sidebar / a narrow column — drops the table to a
      stacked list and hides the projection chips. */
  compact?: boolean;
}

export default function IndexScaleCard({ strat, capital, compact = false }: IndexScaleCardProps) {
  const [bankrolls, setBankrolls] = useState<Map<string, number>>(new Map());
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [theirTrade, setTheirTrade] = useState(DEFAULT_THEIR_TRADE);
  const [expanded, setExpanded] = useState(false);

  const addresses = useMemo(
    () => strat.traders.filter((t) => t.enabled !== false).map((t) => t.address),
    [strat.traders],
  );
  const addrKey = addresses.join(",");

  const load = useCallback(async () => {
    if (!addrKey) {
      setBankrolls(new Map());
      return;
    }
    setLoading(true);
    setFailed(false);
    try {
      setBankrolls(await fetchTraderBankrolls(addrKey.split(",")));
    } catch {
      // A missing denominator is not an error state the user can act on —
      // every row just reads "size unknown". Only the retry affordance cares.
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [addrKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const scales = useMemo(
    () => scaleIndex(strat.traders, capital, bankrolls),
    [strat.traders, capital, bankrolls],
  );

  const projOpts = useMemo(
    () => ({ minTrade: strat.minTrade, maxTrade: strat.maxTrade, maxUpscale: strat.maxUpscale }),
    [strat.minTrade, strat.maxTrade, strat.maxUpscale],
  );

  const summary = useMemo(() => summarizeIndex(scales, projOpts), [scales, projOpts]);

  // Rows worth leading with: biggest traders first, because they are the ones
  // whose flow gets cut down the hardest.
  const rows = useMemo(
    () => [...scales].sort((a, b) => (b.bankroll ?? -1) - (a.bankroll ?? -1)),
    [scales],
  );
  const shown = compact || expanded ? rows : rows.slice(0, 6);

  if (addresses.length === 0) {
    return (
      <div className="pixel-panel p-3 text-[12px] text-pixel-gray-light leading-relaxed">
        <Header />
        <p className="mt-2">
          No traders on this index yet.{" "}
          <Link href="/traders" className="text-pixel-green underline">
            Go find some →
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="pixel-panel p-3 space-y-3">
      <Header
        right={
          <button
            onClick={() => void load()}
            className="text-pixel-gray hover:text-green-400 text-[13px] leading-none"
            title="Re-read every trader's book"
          >
            {loading ? <span className="animate-pulse">···</span> : "↻"}
          </button>
        }
      />

      {/* ── The headline: one sentence with the whole model in it ── */}
      <p className="text-[12px] leading-relaxed text-pixel-gray-light">
        {summary.ratio === null ? (
          failed ? (
            <>Couldn&apos;t read the bench&apos;s books just now — sizes fill in on the next pass.</>
          ) : (
            <>Reading each trader&apos;s book to work out your scale…</>
          )
        ) : (
          <>
            Your <span className="text-pixel-white font-mono">{usd(capital)}</span> stands against{" "}
            <span className="text-pixel-white font-mono">{formatCompactUsd(summary.benchBankroll)}</span>{" "}
            of trader capital across {summary.known} {summary.known === 1 ? "name" : "names"} — you copy at{" "}
            <span className="text-green-400 font-mono">
              {(summary.ratio * 100).toFixed(summary.ratio < 0.001 ? 3 : 2)}%
            </span>{" "}
            of their size. Whatever fraction of their book they stake, you stake the same fraction of yours.
            {summary.unknown > 0 && (
              <span className="text-pixel-gray">
                {" "}
                ({summary.unknown} {summary.unknown === 1 ? "book" : "books"} unreadable — those fall back to
                conviction sizing.)
              </span>
            )}
          </>
        )}
      </p>

      {/* ── The projection: one of their trades, at a size you choose ── */}
      {!compact && (
        <div className="rounded-[var(--radius-sm)] border border-pixel-border/60 bg-pixel-black/30 px-3 py-2.5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[10px] font-bold text-green-400/80 tracking-[0.24em]">IF THEY BET</span>
            {TRADE_STEPS.map((v) => (
              <button
                key={v}
                onClick={() => setTheirTrade(v)}
                className={`pixel-btn text-[11px] px-2 py-0.5 font-mono ${
                  theirTrade === v ? "border-green-400/80 text-green-400" : "text-pixel-gray"
                }`}
              >
                ${v >= 1000 ? `${v / 1000}k` : v}
              </button>
            ))}
          </div>
          <div className="space-y-1">
            {shown.map((s) => (
              <ProjectionRow key={s.address} scale={s} theirTrade={theirTrade} opts={projOpts} />
            ))}
          </div>
        </div>
      )}

      {/* ── The bench, as a scale table ── */}
      <div className="space-y-1">
        {shown.map((s) => (
          <ScaleRow key={s.address} scale={s} opts={projOpts} compact={compact} />
        ))}
        {!compact && rows.length > 6 && (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[11px] font-mono tracking-[0.12em] text-pixel-gray hover:text-pixel-white"
          >
            {expanded ? "⌃ FEWER" : `⌄ ALL ${rows.length} TRADERS`}
          </button>
        )}
      </div>

      {/* ── The honest footnote: what this size can't see ── */}
      {summary.worstThreshold !== null && summary.worstThreshold > 1 && (
        <BlindSpot scales={rows} worst={summary.worstThreshold} capital={capital} opts={projOpts} />
      )}
    </div>
  );
}

function Header({ right }: { right?: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2">
      <h3 className="text-[12px] font-semibold tracking-[0.18em] text-pixel-white">YOUR SCALE</h3>
      <span className="text-[10px] text-pixel-gray truncate">their size vs yours, per trade</span>
      {right && <span className="ml-auto shrink-0">{right}</span>}
    </div>
  );
}

/** One trader's line: who, how big, and at what ratio you track them. */
function ScaleRow({
  scale,
  opts,
  compact,
}: {
  scale: TraderScale;
  opts: { minTrade?: number; maxTrade?: number; maxUpscale?: number | null };
  compact: boolean;
}) {
  const threshold = visibilityThreshold(scale, opts);
  return (
    <div className="flex items-center gap-2 text-[11px] font-mono min-w-0">
      <Link
        href={`/traders/${scale.address}`}
        className="text-pixel-gray-light hover:text-green-400 shrink-0"
        title={scale.address}
      >
        {shortAddress(scale.address)}
      </Link>
      <span className="text-pixel-gray shrink-0" title="This trader's share of the index">
        {(scale.weight * 100).toFixed(0)}%
      </span>
      <span
        className="text-pixel-white shrink-0"
        title="Their net worth on Polymarket: positions at mark + free collateral"
      >
        {formatCompactUsd(scale.bankroll)}
      </span>
      <span className="text-pixel-gray truncate" title="Your slice against their book">
        {formatScale(scale)}
      </span>
      {!compact && threshold !== null && threshold > 1 && (
        <span
          className="ml-auto text-pixel-gray/70 shrink-0"
          title="Trades of theirs smaller than this land under Polymarket's order floor and are skipped"
        >
          sees ≥ {formatCompactUsd(threshold)}
        </span>
      )}
    </div>
  );
}

/** One trader's line under the projection: their $N becomes your $M. */
function ProjectionRow({
  scale,
  theirTrade,
  opts,
}: {
  scale: TraderScale;
  theirTrade: number;
  opts: { minTrade?: number; maxTrade?: number; maxUpscale?: number | null };
}) {
  const p = projectMirror(theirTrade, scale, opts);
  return (
    <div className="flex items-center gap-2 text-[11px] font-mono min-w-0" title={p.note}>
      <span className="text-pixel-gray-light shrink-0 w-[86px]">{shortAddress(scale.address)}</span>
      <span className="text-pixel-gray shrink-0">→</span>
      <span className={`shrink-0 ${VERDICT_TONE[p.verdict]}`}>
        {p.notional > 0 ? usd(p.notional) : "skipped"}
      </span>
      <span className="text-pixel-gray/70 truncate">
        {p.verdict === "placed"
          ? ""
          : p.verdict === "upscaled"
            ? `rounded up from ${usd(p.proportional)} to clear the ${usd(p.floor)} floor`
            : p.verdict === "capped"
              ? `proportional ${usd(p.proportional)}, held to your per-order cap`
              : p.verdict === "sub-scale"
                ? `proportional ${usd(p.proportional)} is under the ${usd(p.floor)} floor`
                : "size unknown"}
      </span>
    </div>
  );
}

/** What this capital cannot see, and the capital that would see it. The one
    number that turns "why isn't it trading?" into a decision. */
function BlindSpot({
  scales,
  worst,
  capital,
  opts,
}: {
  scales: TraderScale[];
  worst: number;
  capital: number;
  opts: { minTrade?: number; maxTrade?: number; maxUpscale?: number | null };
}) {
  // The trader who sets the threshold — the biggest book on the bench.
  const blindest = scales.find((s) => visibilityThreshold(s, opts) === worst) ?? scales[0];
  // "Track them down to a $100 trade" is the concrete ask; solve for capital.
  const needed = blindest ? capitalToTrack(blindest, 100, opts) : null;

  return (
    <div className="rounded-[var(--radius-sm)] border border-amber-400/30 bg-amber-400/[0.05] px-3 py-2 text-[11px] leading-relaxed text-pixel-gray-light">
      <span className="text-amber-400 font-semibold tracking-[0.14em]">WHAT YOU MISS </span>
      At {usd(capital)}, the smallest trade that still reaches your book is about{" "}
      <span className="font-mono text-pixel-white">{formatCompactUsd(worst)}</span> of theirs — anything under
      that is proportionally worth cents, and Polymarket won&apos;t take an order that small, so it&apos;s
      skipped rather than inflated.
      {needed !== null && needed > capital * 1.05 && (
        <>
          {" "}
          Copying {blindest ? shortAddress(blindest.address) : "the biggest name"} all the way down to a $100
          trade of theirs needs about{" "}
          <span className="font-mono text-green-400">{formatCompactUsd(needed)}</span> behind the index.
        </>
      )}{" "}
      <span className="text-pixel-gray">
        Or switch SIZING to CONVICTION in the strat params, which spreads your capital over the flow they
        actually deployed instead of their whole balance sheet — smaller ratio, more coverage, no longer a
        risk mirror.
      </span>
    </div>
  );
}
