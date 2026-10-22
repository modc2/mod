"use client";

// The score market — the slice of the trader board the canonical leaderboard
// score admits, ranked by it. The formula and the admission rule are the
// SERVER's (traders.rs): this page prints what the API states about itself
// and never re-derives the arithmetic.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ago, defensibleWin, fetchScoreMarket, fmtPct, fmtUsd, shortAddr,
  listFollows, liveStatus, liveStart,
  type ScoreMarket,
} from "../lib/api";
import { useSession } from "../lib/auth";
import { formatScore } from "../lib/scoreFormula";
import { DataBar, Freshness, Identicon, Kpi, Medal, PageHead, Spark } from "../components/BoardBits";
import { useCurves } from "../lib/curves";
import { fmtDay } from "../components/TraderCard";

const DAYS = [1, 7, 30];
const EQUITY_FLOORS = [0, 10_000, 100_000, 1_000_000];
const BOARD_CAP_KEY = "hl.board.capital";
const STEPS = [100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];
const fmtCap = (n: number) => `$${n >= 1000 && n % 1000 === 0 ? `${n / 1000}k` : n.toLocaleString("en-US")}`;

type SortKey = "score" | "roi" | "win_lo" | "sharpe" | "pnl" | "equity" | "exp_day";

export default function MarketPage() {
  const router = useRouter();
  const { me } = useSession();
  const [days, setDays] = useState(7);
  const [minEquity, setMinEquity] = useState(0);
  const [capital, setCapital] = useState(1000);
  const [capText, setCapText] = useState("1000");
  const [market, setMarket] = useState<ScoreMarket | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [followedSet, setFollowedSet] = useState<Set<string>>(new Set());
  const [liveEngineSet, setLiveEngineSet] = useState<Set<string>>(new Set());
  const [liveConfig, setLiveConfig] = useState<any>(null);
  const [copyN, setCopyN] = useState(5);

  useEffect(() => {
    try {
      const c = Number(localStorage.getItem(BOARD_CAP_KEY));
      if (c >= 1) { setCapital(c); setCapText(String(c)); }
    } catch {}
  }, []);

  useEffect(() => {
    if (!me) return;
    let alive = true;
    listFollows(me)
      .then((r) => {
        if (!alive) return;
        const s = new Set<string>(
          r.follows.map((f: any) => f.leader.toLowerCase())
        );
        setFollowedSet(s);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [me]);

  useEffect(() => {
    if (!me) return;
    let alive = true;
    liveStatus(me)
      .then((r) => {
        if (!alive) return;
        setLiveConfig(r.config ?? null);
        const traders: any[] = r.config?.traders ?? [];
        setLiveEngineSet(new Set(traders.map((t: any) => t.address.toLowerCase())));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [me]);

  const onAddToLive = async (address: string) => {
    if (!me || !liveConfig) return;
    try {
      const merged = [
        ...(liveConfig.traders ?? []),
        { address, weight: 1, enabled: true },
      ];
      await liveStart({
        eoa: me,
        traders: merged,
        ...(liveConfig.intervalMs != null && { interval_ms: liveConfig.intervalMs }),
        ...(liveConfig.sizePct != null && { size_pct: liveConfig.sizePct }),
        ...(liveConfig.maxPerTradeUsd != null && { max_per_trade_usd: liveConfig.maxPerTradeUsd }),
        ...(liveConfig.minOrderSizeUsd != null && { min_order_size_usd: liveConfig.minOrderSizeUsd }),
        ...(liveConfig.maxSlippageBps != null && { max_slippage_bps: liveConfig.maxSlippageBps }),
        ...(Array.isArray(liveConfig.coinsAllow) && liveConfig.coinsAllow.length && { coins_allow: liveConfig.coinsAllow }),
        ...(Array.isArray(liveConfig.coinsDeny) && liveConfig.coinsDeny.length && { coins_deny: liveConfig.coinsDeny }),
        ...(liveConfig.vaultAddress != null && { vault_address: liveConfig.vaultAddress }),
        ...(liveConfig.capital != null && { capital: liveConfig.capital }),
        ...(liveConfig.strategyId != null && { strategy_id: liveConfig.strategyId }),
      });
      setLiveConfig((prev: any) => prev ? { ...prev, traders: merged } : prev);
      setLiveEngineSet((prev) => new Set([...prev, address.toLowerCase()]));
    } catch {}
  };
  const onCopyTopN = async (n: number) => {
    const candidates = sorted.slice(0, n).map((t) => t.address);
    if (!liveConfig) {
      router.push(`/live?traders=${candidates.join(",")}`);
      return;
    }
    if (!me) return;
    const toAdd = candidates.filter((a) => !liveEngineSet.has(a.toLowerCase()));
    if (toAdd.length === 0) return;
    try {
      const merged = [
        ...(liveConfig.traders ?? []),
        ...toAdd.map((address) => ({ address, weight: 1, enabled: true })),
      ];
      await liveStart({
        eoa: me,
        traders: merged,
        ...(liveConfig.intervalMs != null && { interval_ms: liveConfig.intervalMs }),
        ...(liveConfig.sizePct != null && { size_pct: liveConfig.sizePct }),
        ...(liveConfig.maxPerTradeUsd != null && { max_per_trade_usd: liveConfig.maxPerTradeUsd }),
        ...(liveConfig.minOrderSizeUsd != null && { min_order_size_usd: liveConfig.minOrderSizeUsd }),
        ...(liveConfig.maxSlippageBps != null && { max_slippage_bps: liveConfig.maxSlippageBps }),
        ...(Array.isArray(liveConfig.coinsAllow) && liveConfig.coinsAllow.length && { coins_allow: liveConfig.coinsAllow }),
        ...(Array.isArray(liveConfig.coinsDeny) && liveConfig.coinsDeny.length && { coins_deny: liveConfig.coinsDeny }),
        ...(liveConfig.vaultAddress != null && { vault_address: liveConfig.vaultAddress }),
        ...(liveConfig.capital != null && { capital: liveConfig.capital }),
        ...(liveConfig.strategyId != null && { strategy_id: liveConfig.strategyId }),
      });
      setLiveConfig((prev: any) => prev ? { ...prev, traders: merged } : prev);
      setLiveEngineSet((prev) => new Set([...prev, ...toAdd.map((a) => a.toLowerCase())]));
    } catch {}
  };

  const setMarketCap = (c: number) => {
    const v = Math.min(10_000_000, Math.max(1, Math.round(c)));
    setCapital(v); setCapText(String(v));
    try { localStorage.setItem(BOARD_CAP_KEY, String(v)); } catch {}
  };
  const stepCapDown = () => setMarketCap([...STEPS].reverse().find((s) => s < capital) ?? STEPS[0]);
  const stepCapUp = () => setMarketCap(STEPS.find((s) => s > capital) ?? capital * 2);

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
  const curveMap = useCurves(rows.map(r => r.address), days, rows.length > 0);
  const topScore = rows[0]?.score ?? 0;
  const medianWin = useMemo(() => {
    const ws = rows.map((r) => defensibleWin(r)).filter((w): w is number => w != null).sort((a, b) => a - b);
    return ws.length ? ws[Math.floor(ws.length / 2)] : null;
  }, [rows]);

  const sorted = useMemo(() => {
    const metric = (t: typeof rows[0]): number => {
      if (sortKey === "score") return t.score;
      if (sortKey === "roi") return t.roi;
      if (sortKey === "win_lo") return defensibleWin(t) ?? -Infinity;
      if (sortKey === "sharpe") return t.sharpe;
      if (sortKey === "pnl") return t.pnl;
      if (sortKey === "equity") return t.account_value;
      if (sortKey === "exp_day") return t.expected?.per_1k ?? -Infinity;
      return 0;
    };
    return [...rows].sort((a, b) => {
      const cmp = metric(a) - metric(b);
      return sortDir === "desc" ? -cmp : cmp;
    });
  }, [rows, sortKey, sortDir]);

  const sortHeader = (k: SortKey, label: string) => {
    const on = sortKey === k;
    return (
      <button
        className={`eyebrow !tracking-wider w-full inline-flex items-center justify-end gap-1 transition-colors hover:text-ink ${on ? "!text-accent" : ""}`}
        onClick={() => {
          if (on) setSortDir(sortDir === "desc" ? "asc" : "desc");
          else { setSortKey(k); setSortDir("desc"); }
        }}>
        {label}
        <span className={`text-[8px] transition-opacity ${on ? "opacity-100" : "opacity-0"}`}>
          {on && sortDir === "asc" ? "▲" : "▼"}
        </span>
      </button>
    );
  };

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
        <div>
          <div className="label">copy capital</div>
          <div className="flex items-center gap-1" title="Scale exp/day to your copy amount">
            <button className="btn-ghost !py-0.5 !px-2 text-[12px]" onClick={stepCapDown} aria-label="less capital">−</button>
            <span className="relative">
              <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[12px] text-dim">$</span>
              <input value={capText} inputMode="numeric" aria-label="capital copied"
                onChange={(e) => {
                  const t = e.target.value.replace(/[^0-9]/g, "");
                  setCapText(t);
                  if (Number(t) >= 1) setMarketCap(Number(t));
                }}
                onBlur={() => setCapText(String(capital))}
                className="num w-[84px] pl-5 pr-2 py-1 text-[12px] text-right bg-transparent border border-border rounded-md focus:outline-none focus:border-accent" />
            </span>
            <button className="btn-ghost !py-0.5 !px-2 text-[12px]" onClick={stepCapUp} aria-label="more capital">+</button>
            <span className="text-[10px] text-dim">{fmtCap(capital)}</span>
          </div>
        </div>
        <div>
          <div className="label">copy top N</div>
          <div className="flex items-center gap-1">
            {[3, 5, 10].map((n) => (
              <button key={n} onClick={() => setCopyN(n)}
                className={`btn ${copyN === n ? "border-accent text-accent" : ""}`}>{n}</button>
            ))}
            <button
              className="btn-ghost text-accent2 disabled:opacity-40"
              disabled={sorted.length === 0}
              onClick={() => onCopyTopN(copyN)}>
              copy top {copyN} →
            </button>
          </div>
        </div>
        <p className="ml-auto max-w-[360px] text-[10px] text-muted">
          Same formula as the board&apos;s ƒ SCORE preset — this page is the market it defines,
          with the factors printed beside every product.
        </p>
      </div>

      {/* Board */}
      <div className="panel">
        <div className="grid grid-cols-[2fr_1.4fr_64px_repeat(6,1fr)_0.9fr] gap-2 px-4 py-2 border-b border-border table-head">
          <div className="label !mb-0">trader</div>
          <div className="label !mb-0 text-right">{sortHeader("score", "score")}</div>
          <div className="label !mb-0">curve</div>
          <div className="label !mb-0 text-right">{sortHeader("roi", "roi")}</div>
          <div className="label !mb-0 text-right">{sortHeader("win_lo", "win lo")}</div>
          <div className="label !mb-0 text-right">{sortHeader("sharpe", "sharpe")}</div>
          <div className="label !mb-0 text-right">{sortHeader("pnl", "pnl")}</div>
          <div className="label !mb-0 text-right">{sortHeader("equity", "equity")}</div>
          <div className="label !mb-0 text-right">{sortHeader("exp_day", "exp/day")}</div>
          <div className="label !mb-0 text-right">action</div>
        </div>
        {err && <div className="px-4 py-3 text-xs text-loss">{err}</div>}
        {loading && rows.length === 0 && !err &&
          [...Array(8)].map((_, i) => (
            <div key={i} className="grid grid-cols-[2fr_1.4fr_64px_repeat(6,1fr)_0.9fr] gap-2 px-4 py-3 items-center table-row">
              <div className="skeleton h-4 w-36" />
              {[...Array(9)].map((_, j) => <div key={j} className="skeleton h-4 w-12 justify-self-end" />)}
            </div>
          ))}
        {!err && !loading && rows.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-muted">
            {market?.warming
              ? `no ${days}d board yet — the background refresher is walking the leaderboard; check back in a couple of minutes.`
              : "no trader clears the gate right now — every factor must be positive, with the evidence to back it."}
          </div>
        )}
        {sorted.map((t, i) => {
          const winLo = defensibleWin(t);
          return (
            <div key={t.address}
              className="group grid grid-cols-[2fr_1.4fr_64px_repeat(6,1fr)_0.9fr] gap-2 px-4 py-2.5 items-center table-row hover:bg-accent/[0.04]">
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
              <div className="min-w-0">
                {(() => {
                  const c = curveMap[t.address.toLowerCase()];
                  if (!c || !c.available || c.points.length < 2) return null;
                  return (
                    <span className={c.pnl >= 0 ? "text-win" : "text-loss"}>
                      <Spark points={c.points} height={32} />
                    </span>
                  );
                })()}
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
              <div className={`num text-right ${t.expected?.per_1k != null ? (t.expected.per_1k * (capital / 1000) >= 0 ? "text-win" : "text-loss") : "text-dim"}`}>
                {t.expected?.per_1k != null ? fmtDay(t.expected.per_1k * (capital / 1000)) : "—"}
              </div>
              <div className="flex flex-wrap justify-end items-center gap-1 opacity-80 group-hover:opacity-100 transition-opacity">
                {followedSet.has(t.address.toLowerCase()) && (
                  <span className="text-[10px] text-win border border-win/30 rounded px-1.5 py-0.5">following ✓</span>
                )}
                {liveEngineSet.has(t.address.toLowerCase())
                  ? <Link href="/live" className="btn-ghost text-win">live ✓</Link>
                  : liveConfig != null
                    ? <button className="btn-ghost text-accent2" onClick={() => onAddToLive(t.address)}>add to live</button>
                    : <Link href={`/live?traders=${t.address}`} className="btn-ghost text-accent2">live ↦</Link>
                }
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
