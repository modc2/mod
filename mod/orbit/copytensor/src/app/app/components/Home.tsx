"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { CopyConfig, LeaderboardEntry } from "../lib/types";
import { fetchCopies, fetchLeaderboard, fmtCompact, shortSs58 } from "../lib/api";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import Identicon from "./Identicon";
import SimpleCopy from "./SimpleCopy";

const DAYS = 7;
const CARDS = 9;

/**
 * The front door. Three things, in the order a first-time visitor needs
 * them: who's worth copying (cards, not a 9-column table), the copies
 * they already run, and a three-line explanation of what pressing COPY
 * does. Everything else — the full board, subnets, strat baskets, the
 * agent — is one click away in the top bar, not on this page.
 */
export default function Home() {
  const { currency, usdPerTao } = useCurrency();
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [copies, setCopies] = useState<CopyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [pick, setPick] = useState<LeaderboardEntry | null>(null);

  useEffect(() => {
    fetchLeaderboard(DAYS, 2000).then(setEntries).catch(() => {}).finally(() => setLoading(false));
    fetchCopies().then(setCopies).catch(() => {});
  }, []);

  // Worth showing = priced, still holding something, spread over more than
  // one subnet, and a return that isn't an artefact of a wallet being
  // emptied (those read as +150% market on −100% total).
  const top = useMemo(
    () =>
      entries
        .filter(
          (e) =>
            e.baseline !== false &&
            e.total_stake_tao >= 25 &&
            e.num_subnets >= 2 &&
            Number.isFinite(e.market_pct ?? NaN) &&
            (e.market_pct ?? 0) < 500,
        )
        .sort((a, b) => (b.market_pct ?? 0) - (a.market_pct ?? 0))
        .slice(0, CARDS),
    [entries],
  );

  const active = copies.filter((c) => c.status === "active");

  return (
    <div className="space-y-8">
      <section className="home-hero">
        <h1 className="arcade-title">Copy the best <span className="text-green-400">Bittensor</span> traders.</h1>
        <p className="arcade-prose mt-3">
          Pick a trader. Say how much TAO should follow them. We mirror
          what they hold across subnets, and keep it lined up as they move.
        </p>
      </section>

      {active.length > 0 && (
        <section className="space-y-3">
          <SectionHead title="You are copying" href="/portfolio" cta="MANAGE" />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {active.map((c) => (
              <Link key={c.id} href="/portfolio" className="trader-card no-underline">
                <div className="flex items-center gap-2">
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
        <SectionHead title={`Top traders · last ${DAYS} days`} href="/traders" cta="SEE ALL" />
        {loading ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => <div key={i} className="trader-card skeleton-pulse h-[150px]" />)}
          </div>
        ) : top.length === 0 ? (
          <div className="pixel-panel p-5"><p className="arcade-prose-sm">Still pricing the board — give it a minute and refresh.</p></div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {top.map((e, i) => {
              const pct = e.market_pct ?? 0;
              const up = pct >= 0;
              return (
                <div key={e.ss58} className="trader-card">
                  <div className="flex items-center gap-2">
                    <span className="trader-card-rank">{i + 1}</span>
                    <Identicon ss58={e.ss58} size={24} />
                    <Link href={`/traders/${e.ss58}`} className="trader-card-name truncate no-underline">
                      {e.label || shortSs58(e.ss58)}
                    </Link>
                  </div>
                  <p className={`trader-card-big ${up ? "text-green-400" : "text-red-400"}`}>
                    {up ? "+" : ""}{pct.toFixed(1)}%
                  </p>
                  <p className="trader-card-sub">
                    {DAYS}-day return · holds {fmtCompact(e.total_stake_tao)} τ · {e.num_subnets} subnets
                  </p>
                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={() => setPick(e)}
                      className="pixel-btn border-green-400 text-green-400 flex-1 py-2"
                    >
                      COPY
                    </button>
                    <Link href={`/traders/${e.ss58}`} className="pixel-btn no-underline px-3 py-2 text-pixel-gray-light">
                      DETAILS
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
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

function SectionHead({ title, href, cta }: { title: string; href?: string; cta?: string }) {
  return (
    <div className="flex items-center gap-3">
      <h2 className="font-display text-pixel-white flex-1 min-w-0 truncate">{title}</h2>
      {href && cta && (
        <Link href={href} className="pixel-btn px-3 py-1 text-[11px] no-underline text-pixel-gray-light">
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
