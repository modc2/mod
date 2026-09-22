"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { CopyConfig, CurvePoint, LeaderboardEntry, MarketStats } from "../lib/types";
import {
  fetchCopies, fetchCurve, fetchLeaderboard, fetchMarket, fmtCompact,
  shortSs58, windowPhrase,
} from "../lib/api";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import { useFilters, type SortKey } from "../context/FiltersContext";
import { useCoverage } from "../lib/useCoverage";
import Identicon from "./Identicon";
import SimpleCopy from "./SimpleCopy";
import Sparkline from "./Sparkline";
import WindowRail from "./WindowRail";
import { RankBy, StakeFloor } from "./BoardFilters";

const PAGE = 9;

/**
 * The front door. Who's worth copying, over a window you choose, out of an
 * index whose depth is printed on the page; the copies you already run; and
 * three lines on what pressing COPY does.
 *
 * The window used to be welded to seven days here and the 25 τ floor was a
 * constant in this file, so the page answered exactly one question and
 * couldn't say why a trader was missing. Both are controls now, shared with
 * the full board through FiltersContext, and the rail underneath says how
 * far back the numbers can honestly reach.
 */
export default function Home() {
  const { currency, usdPerTao } = useCurrency();
  const { days, sortKey, minStake } = useFilters();
  const cov = useCoverage();
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [copies, setCopies] = useState<CopyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [shown, setShown] = useState(PAGE);
  const [pick, setPick] = useState<LeaderboardEntry | null>(null);

  useEffect(() => {
    let stale = false;
    setLoading(true);
    fetchLeaderboard(days, 2000)
      .then((r) => { if (!stale) setEntries(r); })
      .catch(() => {})
      .finally(() => { if (!stale) setLoading(false); });
    return () => { stale = true; };
  }, [days]);

  useEffect(() => { setShown(PAGE); }, [days, sortKey, minStake]);

  useEffect(() => {
    fetchCopies().then(setCopies).catch(() => {});
  }, []);

  // Worth showing = priced, still holding a book you could copy at size,
  // spread over more than one subnet, and a return that isn't an artefact of
  // a wallet being emptied (those read as +150% market on −100% total).
  const ranked = useMemo(() => {
    const rows = entries.filter(
      (e) =>
        e.baseline !== false &&
        e.total_stake_tao >= minStake &&
        e.num_subnets >= 2 &&
        Number.isFinite(e.market_pct ?? NaN) &&
        (e.market_pct ?? 0) < 500,
    );
    return rows.sort(
      (a, b) =>
        ((b as Record<SortKey, number>)[sortKey] ?? 0) -
        ((a as Record<SortKey, number>)[sortKey] ?? 0),
    );
  }, [entries, sortKey, minStake]);

  const top = ranked.slice(0, shown);
  const active = copies.filter((c) => c.status === "active");

  // One small curve per visible plate, from our own snapshot table — cheap
  // (~60ms each), and unlike bt's bulk /traders it can't hang the front
  // door when bt is mid refresh. Keyed by ss58, reset when the window
  // moves so the trace always covers the days the number claims.
  // Refs, not effect deps: nine fetches land near-together, and a
  // cleanup-based stale flag lets the first arrival mark the other eight
  // stale — eight plates stuck on the dashed placeholder. A resolve is
  // dropped only when the window moved out from under it.
  const [curves, setCurves] = useState<Record<string, CurvePoint[]>>({});
  const curvesRef = useRef<Record<string, CurvePoint[]>>({});
  const inflight = useRef(new Set<string>());
  const daysRef = useRef(days);
  useEffect(() => {
    daysRef.current = days;
    curvesRef.current = {};
    inflight.current.clear();
    setCurves({});
  }, [days]);
  useEffect(() => {
    for (const e of top) {
      if (curvesRef.current[e.ss58] !== undefined || inflight.current.has(e.ss58)) continue;
      inflight.current.add(e.ss58);
      const d = days;
      const land = (pts: CurvePoint[]) => {
        if (daysRef.current !== d) return;
        curvesRef.current[e.ss58] = pts;
        setCurves((p) => ({ ...p, [e.ss58]: pts }));
      };
      fetchCurve(e.ss58, d)
        .then((c) => land(c.points ?? []))
        .catch(() => land([]))
        .finally(() => { inflight.current.delete(e.ss58); });
    }
  }, [top, days]);

  return (
    <div className="space-y-7">
      <section className="home-hero">
        <h1 className="arcade-title">Copy the best <span className="text-green-400">Bittensor</span> traders.</h1>
        <p className="arcade-prose mt-3">
          Pick a trader. Say how much TAO should follow them. We mirror
          what they hold across subnets, and keep it lined up as they move.
        </p>
        <HeroStats traders={cov?.priced} />
      </section>

      {active.length > 0 && (
        <section className="space-y-3">
          <SectionHead title="You are copying" href="/portfolio" cta="MANAGE" />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {active.map((c) => (
              <Link key={c.id} href="/portfolio" className="trader-card trader-card-live no-underline">
                <div className="trader-card-head">
                  <Identicon ss58={c.target_ss58} size={24} />
                  <span className="trader-card-name truncate">{c.label || c.target_info?.label || shortSs58(c.target_ss58)}</span>
                </div>
                <p className="trader-card-big text-cyan-400">{fmtValue(c.alloc_tao, currency, usdPerTao)}</p>
                <p className="trader-card-sub">following · {c.target_info ? `${c.target_info.num_subnets} subnets` : "syncing"}</p>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-3">
        <SectionHead
          title={`Top traders · ${windowPhrase(days)}`}
          href="/traders"
          cta="FULL BOARD"
          count={ranked.length ? `${Math.min(shown, ranked.length)} of ${ranked.length}` : undefined}
        />

        {/* Everything that decides which rows you're looking at, on one
            plate: the window, what the ranking means, and the size floor. */}
        <div className="filter-bar">
          <WindowRail caption={false} />
          <div className="filter-bar-row">
            <RankBy />
            <StakeFloor />
          </div>
          <p className="window-rail-note">
            {cov ? (
              <>
                ranked over {windowPhrase(days)} · index reaches back{" "}
                <span className="window-rail-em">{cov.depth_days} days</span>
                {" "}(to {fmtDay(cov.oldest_ts)}) across {cov.priced} traders
              </>
            ) : (
              <>measuring how far back the index goes…</>
            )}
          </p>
        </div>

        {loading && entries.length === 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => <div key={i} className="trader-card skeleton-pulse h-[178px]" />)}
          </div>
        ) : top.length === 0 ? (
          <div className="pixel-panel p-5">
            <p className="arcade-prose-sm">
              {entries.length === 0
                ? "Still pricing the board over this window — give it a minute and refresh."
                : `No trader clears a ${minStake} τ book over ${windowPhrase(days)}. Drop the size floor, or change the window.`}
            </p>
          </div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {top.map((e, i) => (
                <TraderPlate
                  key={e.ss58}
                  e={e}
                  rank={i}
                  days={days}
                  sortKey={sortKey}
                  curve={curves[e.ss58]}
                  onCopy={() => setPick(e)}
                />
              ))}
            </div>
            {shown < ranked.length && (
              <button
                onClick={() => setShown((n) => n + PAGE * 2)}
                className="pixel-btn w-full py-2 text-[11px] text-pixel-gray-light"
              >
                SHOW {Math.min(PAGE * 2, ranked.length - shown)} MORE ↓
              </button>
            )}
          </>
        )}
      </section>

      <section className="space-y-3">
        <SectionHead title="How it works" />
        <div className="grid gap-3 md:grid-cols-3">
          <Step n="1" title="Pick a trader">
            The cards above rank real wallets by what their picks earned —
            deposits don&rsquo;t count, only price moves.
          </Step>
          <Step n="2" title="Choose an amount">
            Press COPY and type how much TAO should follow them. Start
            small; you can resize later.
          </Step>
          <Step n="3" title="We keep you in step">
            Your TAO is spread across their subnets in their proportions.
            When they change, so do you. Pause or stop any time.
          </Step>
        </div>
      </section>

      {pick && <SimpleCopy ss58={pick.ss58} label={pick.label} onClose={() => setPick(null)} />}
    </div>
  );
}

/**
 * One trader, one plate. The headline is whatever the board is ranked by —
 * ask for the biggest books and the card leads with the book, not with a
 * percentage you didn't sort on — and under it the two facts that qualify
 * it: how much history is really behind the number, and how much of the
 * move was trading rather than money walking in the door.
 */
function TraderPlate({
  e, rank, days, sortKey, curve, onCopy,
}: {
  e: LeaderboardEntry;
  rank: number;
  days: number;
  sortKey: SortKey;
  curve?: CurvePoint[];
  onCopy: () => void;
}) {
  const { currency, usdPerTao } = useCurrency();
  const pct = (sortKey === "pnl_pct" ? e.pnl_pct : e.market_pct) ?? 0;
  const up = pct >= 0;
  const size = sortKey === "total_stake_tao";
  const spread = sortKey === "num_subnets";

  // The trace agrees with the headline: price-only PnL under a price
  // return, total PnL under a total return, the book itself when the grid
  // is ranked by size or spread.
  const trace = useMemo(() => {
    if (!curve) return null; // still loading → Sparkline's dashed placeholder
    return curve.map((p) =>
      size || spread ? p.value_tao : sortKey === "pnl_pct" ? p.pnl_tao : p.market_tao,
    );
  }, [curve, sortKey, size, spread]);

  // A window shorter than asked for isn't comparable to the rest of the
  // grid; on the all-history window every row is its own length, so the
  // span is information rather than a warning.
  const short = days > 0 && e.window_days > 0 && e.window_days < days * 0.9;
  // Money that walked in the door rather than being earned.
  const flowLed = !!e.flow_tao && Math.abs(e.flow_tao) > Math.abs(e.market_pnl_tao ?? 0);

  return (
    <div className={`trader-card ${up ? "trader-card-up" : "trader-card-down"}`}>
      <div className="trader-card-head">
        <span className={`trader-card-rank ${rank < 3 ? `medal-${rank + 1}` : ""}`}>{rank + 1}</span>
        <Identicon ss58={e.ss58} size={24} />
        <Link href={`/traders/${e.ss58}`} className="trader-card-name truncate no-underline">
          {e.label || shortSs58(e.ss58)}
        </Link>
      </div>

      <div className="trader-card-hero">
        <p className={`trader-card-big ${size || spread ? "text-cyan-400" : up ? "text-green-400" : "text-red-400"}`}>
          {size
            ? fmtValue(e.total_stake_tao, currency, usdPerTao)
            : spread
              ? `${e.num_subnets} subnets`
              : `${up ? "+" : ""}${pct.toFixed(1)}%`}
        </p>
        <Sparkline
          values={trace}
          width={104}
          height={40}
          className="trader-card-spark"
        />
      </div>

      <p className="trader-card-sub">
        {size || spread
          ? `${up ? "+" : ""}${pct.toFixed(1)}% return · `
          : `${sortKey === "pnl_pct" ? "total" : "price"} return · `}
        holds {fmtCompact(e.total_stake_tao)} τ · {e.num_subnets} subnets
      </p>

      {/* The qualifiers. Quiet, but on the card rather than in a tooltip —
          they're the difference between a real record and a good week. A
          full window says nothing worth a chip, so only a short one (or the
          all-history window, where every row is its own length) prints. */}
      <div className="trader-card-tags">
        {(short || days === 0 || e.window_days <= 0) && (
          <span
            className={`trader-tag ${short || e.window_days <= 0 ? "trader-tag-warn" : ""}`}
            title={
              short
                ? `Only ${e.window_days.toFixed(1)} days of history — not a full ${days}-day window`
                : "How much history this number is measured over"
            }
          >
            {e.window_days > 0
              ? `${e.window_days.toFixed(e.window_days < 10 ? 1 : 0)}d history`
              : "no history yet"}
          </span>
        )}
        {flowLed && (
          <span
            className="trader-tag trader-tag-warn"
            title={`${e.flow_tao! > 0 ? "Deposited" : "Withdrew"} ${Math.abs(e.flow_tao!).toFixed(2)} τ over this window — the headline is mostly flow, not trading`}
          >
            {e.flow_tao! > 0 ? "+" : "−"}{fmtCompact(Math.abs(e.flow_tao!))} τ flow
          </span>
        )}
      </div>

      <div className="trader-card-actions">
        <button onClick={onCopy} className="pixel-btn border-green-400 text-green-400 flex-1 py-2">
          COPY
        </button>
        <Link href={`/traders/${e.ss58}`} className="pixel-btn no-underline px-3 py-2 text-pixel-gray-light">
          DETAILS
        </Link>
      </div>
    </div>
  );
}

function SectionHead({
  title, href, cta, count,
}: {
  title: string; href?: string; cta?: string; count?: string;
}) {
  return (
    <div className="flex items-center gap-3">
      {/* Wraps rather than truncating: on a phone the window is part of the
          title ("Top traders · last 7 days") and an ellipsis ate it. */}
      <h2 className="font-display text-pixel-white flex-1 min-w-0 break-words">
        {title}
        {count && <span className="text-pixel-gray text-xs ml-2 font-mono">({count})</span>}
      </h2>
      {href && cta && (
        <Link href={href} className="pixel-btn px-3 py-1 text-[11px] no-underline text-pixel-gray-light shrink-0">
          {cta} →
        </Link>
      )}
    </div>
  );
}

function Step({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <div className="pixel-panel p-4">
      <div className="flex items-center gap-3 mb-2">
        <span className="mario-block-sm">{n}</span>
        <span className="text-[13px] font-bold uppercase tracking-[0.08em]">{title}</span>
      </div>
      <p className="arcade-prose-sm">{children}</p>
    </div>
  );
}

/**
 * The market is on. Four live numbers under the pitch — TAO's price, what
 * the alpha market is worth, what traded today, and how many wallets the
 * index watches — so the front door opens onto a running exchange rather
 * than a static sales page. One cached /market call, nothing that blocks.
 */
function HeroStats({ traders }: { traders?: number }) {
  const { currency, usdPerTao } = useCurrency();
  const [m, setM] = useState<MarketStats | null>(null);
  useEffect(() => {
    fetchMarket().then(setM).catch(() => {});
  }, []);
  const show = (tao: number) =>
    currency === "USD" && usdPerTao ? `$${fmtCompact(tao * usdPerTao)}` : `${fmtCompact(tao)} τ`;
  return (
    <div className="hero-stats">
      <HeroStat k="TAO" v={m?.tao_usd ? `$${m.tao_usd.toFixed(2)}` : "—"} tone="text-green-400" />
      <HeroStat k="alpha mcap" v={m ? show(m.total_market_cap_tao) : "—"} />
      <HeroStat k="24h volume" v={m ? show(m.volume_24h_tao) : "—"} />
      <HeroStat k="traders indexed" v={traders != null ? String(traders) : "—"} />
    </div>
  );
}

function HeroStat({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <span className="hero-stat">
      <span className="hero-stat-k">{k}</span>
      <b className={tone}>{v}</b>
    </span>
  );
}

function fmtDay(ts: number | null) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
