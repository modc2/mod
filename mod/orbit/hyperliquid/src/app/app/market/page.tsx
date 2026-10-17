"use client";

// The score market — the slice of the trader board the canonical leaderboard
// score admits, ranked by it. The formula and the admission rule are the
// SERVER's (traders.rs): this page prints what the API states about itself
// and never re-derives the arithmetic.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ago, defensibleWin, fetchScoreMarket, fmtPct, fmtUsd, shortAddr,
  type ScoreMarket,
} from "../lib/api";
import { formatScore } from "../lib/scoreFormula";
import { DataBar, Freshness, Identicon, Kpi, Medal, PageHead } from "../components/BoardBits";

const DAYS = [1, 7, 30];
const EQUITY_FLOORS = [0, 10_000, 100_000, 1_000_000];

export default function MarketPage() {
  const [days, setDays] = useState(7);
  const [minEquity, setMinEquity] = useState(0);
  const [market, setMarket] = useState<ScoreMarket | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    const load = async () => {
      setLoading(true); setErr(null);
      try {
        const m = await fetchScoreMarket({
          days, limit: 100, minEquity: minEquity || undefined,
        });
        if (!dead) setMarket(m);
      } catch (e: any) {
        if (!dead) { setErr(e.message ?? String(e)); setMarket(null); }
      } finally {
        if (!dead) setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 60_000);
    return () => { dead = true; clearInterval(t); };
  }, [days, minEquity]);

  const rows = market?.rows ?? [];
  const topScore = rows[0]?.score ?? 0;
  const medianWin = useMemo(() => {
    const ws = rows.map((r) => defensibleWin(r)).filter((w): w is number => w != null).sort((a, b) => a - b);
    return ws.length ? ws[Math.floor(ws.length / 2)] : null;
  }, [rows]);

  return (
    <section className="space-y-4">
      <PageHead
        title="Score market"
        blurb={
          <>
            Traders admitted by the canonical score —{" "}
            <span className="font-mono text-ink/80">{market?.score ?? "roi × winRateLo/100 × sharpe"}</span>:
            the window return, discounted by the win rate the sample size can defend
            (Wilson 95% lower bound) and by consistency (annualised daily-PnL Sharpe).
            A trader is in the market only when measured with ≥{market?.gate.min_closes ?? 10} realised
            closes and ≥{market?.gate.min_sharpe_days ?? 7} Sharpe days AND every factor is positive —
            anything less is unscored, never zero.
          </>
        }
        right={<Freshness loading={loading}
          label={market?.updated_at ? `updated ${ago(market.updated_at)}` : "…"} />}
      />

      {/* KPI strip: the funnel is the honesty — most of the board CAN'T score. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="in the market" value={loading && !market ? "…" : String(market?.matched ?? 0)}
          sub={`of ${market?.measured ?? 0} measured · ${market?.priced ?? 0} priced`} />
        <Kpi label="best score" value={rows.length ? formatScore(rows[0].score) : "—"}
          sub={rows.length ? shortAddr(rows[0].address) : "no admitted trader"} tone={rows.length ? "win" : undefined} />
        <Kpi label="median win rate" value={medianWin != null ? fmtPct(medianWin, 0) : "—"}
          sub="Wilson lower bound, admitted rows" />
        <Kpi label="window" value={`${days}d`} sub="score factors measured over this window" />
      </div>

      {/* Filters */}
      <div className="panel p-4 flex flex-wrap items-end gap-3">
        <div>
          <div className="label">window</div>
          <div className="flex gap-1">
            {DAYS.map((d) => (
              <button key={d} onClick={() => setDays(d)}
                className={`btn ${days === d ? "border-accent text-accent" : ""}`}>{d}d</button>
            ))}
          </div>
        </div>
        <div>
          <div className="label">min equity</div>
          <div className="flex gap-1">
            {EQUITY_FLOORS.map((e) => (
              <button key={e} onClick={() => setMinEquity(e)}
                className={`btn ${minEquity === e ? "border-accent text-accent" : ""}`}>
                {e === 0 ? "any" : e >= 1e6 ? `${e / 1e6}M` : `${e / 1e3}K`}
              </button>
            ))}
          </div>
        </div>
        <p className="ml-auto max-w-[360px] text-[10px] text-muted">
          Same formula as the board&apos;s ƒ SCORE preset — this page is the market it defines,
          with the factors printed beside every product.
        </p>
      </div>

      {/* Board */}
      <div className="panel">
        <div className="grid grid-cols-[2fr_1.4fr_repeat(5,1fr)_0.9fr] gap-2 px-4 py-2 border-b border-border table-head">
          <div className="label !mb-0">trader</div>
          <div className="label !mb-0 text-right">score</div>
          <div className="label !mb-0 text-right">roi</div>
          <div className="label !mb-0 text-right">win lo</div>
          <div className="label !mb-0 text-right">sharpe</div>
          <div className="label !mb-0 text-right">pnl</div>
          <div className="label !mb-0 text-right">equity</div>
          <div className="label !mb-0 text-right">action</div>
        </div>
        {err && <div className="px-4 py-3 text-xs text-loss">{err}</div>}
        {loading && rows.length === 0 && !err &&
          [...Array(8)].map((_, i) => (
            <div key={i} className="grid grid-cols-[2fr_1.4fr_repeat(5,1fr)_0.9fr] gap-2 px-4 py-3 items-center table-row">
              <div className="skeleton h-4 w-36" />
              {[...Array(7)].map((_, j) => <div key={j} className="skeleton h-4 w-12 justify-self-end" />)}
            </div>
          ))}
        {!err && !loading && rows.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-muted">
            {market?.warming
              ? `no ${days}d board yet — the background refresher is walking the leaderboard; check back in a couple of minutes.`
              : "no trader clears the gate right now — every factor must be positive, with the evidence to back it."}
          </div>
        )}
        {rows.map((t, i) => {
          const winLo = defensibleWin(t);
          return (
            <div key={t.address}
              className="group grid grid-cols-[2fr_1.4fr_repeat(5,1fr)_0.9fr] gap-2 px-4 py-2.5 items-center table-row hover:bg-accent/[0.04]">
              <div className="flex items-center gap-2 min-w-0">
                <Medal rank={i + 1} />
                <Identicon address={t.address} />
                <Link href={`/trader/${t.address}`}
                  className="font-mono text-[11px] text-accent2 hover:text-accent transition-colors truncate">
                  {shortAddr(t.address)}
                </Link>
              </div>
              <div className="text-right">
                <span className="num font-semibold text-win">{formatScore(t.score)}</span>
                <DataBar value={t.score} max={topScore} className="mt-1" />
              </div>
              <div className={`num text-right ${t.roi >= 0 ? "text-win" : "text-loss"}`}>
                {`${t.roi >= 0 ? "+" : ""}${fmtPct(t.roi, 1)}`}
              </div>
              <div className="num text-right text-ink/90">
                {winLo != null ? fmtPct(winLo, 0) : "—"}
                <span className="text-dim text-[10px]"> ·{t.wins}/{t.closes}</span>
              </div>
              <div className="num text-right text-ink/90">
                {t.sharpe.toFixed(2)}
                <span className="text-dim text-[10px]"> ·{t.sharpe_days}d</span>
              </div>
              <div className={`num text-right ${t.pnl >= 0 ? "text-win" : "text-loss"}`}>{fmtUsd(t.pnl)}</div>
              <div className="num text-right text-muted">{fmtUsd(t.account_value)}</div>
              <div className="flex justify-end opacity-80 group-hover:opacity-100 transition-opacity">
                <Link href={`/trader/${t.address}`} className="btn-ghost">open</Link>
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-[10px] text-muted">
        Only fill-measured rows can score, so the market draws from the enriched top of the board —
        &ldquo;{market?.measured ?? 0} measured&rdquo; is the real candidate pool, not the whole
        leaderboard. Every number is trailing, not a forecast: the win-lo cell shows the wins/closes
        behind the bound and the sharpe cell its days of history, so nothing hides behind the product.
      </p>
    </section>
  );
}
