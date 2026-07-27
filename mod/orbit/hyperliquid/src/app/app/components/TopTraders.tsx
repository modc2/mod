"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchTopTraders, fetchScanProgress, ScanProgress, TopTrader, fmtPnl, fmtUsd, fmtPct, shortAddr, ago } from "../lib/api";
import Link from "next/link";

type SortKey = "roi" | "pnl" | "volume" | "win_rate" | "trades" | "sharpe";

// HL's native ROI windows are day / week / month — 1, 7, 30 days. Other values
// would just bucket back to these, so we don't pretend to offer them.
const DAY_OPTIONS = [1, 7, 30];
const POOL_OPTIONS = [50, 150, 300, 600];

// Fill coins arrive raw from HL: HIP-3 builder-dex perps as "dex:TICKER"
// (e.g. "xyz:HYUNDAI") and spot markets as "@123" indices. Neither is a
// copyable core perp, so they're excluded from badges and the positions filter.
const isCoreCoin = (c: string) => !c.includes(":") && !c.startsWith("@");

// Shared column template: trader | roi(+pnl) | volume | win%(+trades) | sharpe | last | copy.
const GRID = "grid grid-cols-[minmax(0,2.6fr)_1.1fr_1fr_1fr_0.9fr_0.9fr_auto] gap-2 px-4";

export default function TopTraders() {
  const [days, setDays] = useState(7);
  const [pool, setPool] = useState(50);
  const [seed, setSeed] = useState("");
  const [traders, setTraders] = useState<TopTrader[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("roi");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [prog, setProg] = useState<ScanProgress | null>(null);
  const [coinFilter, setCoinFilter] = useState<Set<string>>(new Set());
  const [seedOpen, setSeedOpen] = useState(false);
  const [coinsExpanded, setCoinsExpanded] = useState(false);

  // While a scan request is in flight, poll the API's live scan progress so
  // the user sees "X of N wallets" instead of an opaque spinner. Cached
  // fast-path responses resolve before the first poll — no flash.
  useEffect(() => {
    if (!loading) { setProg(null); return; }
    let alive = true;
    const poll = () => fetchScanProgress()
      .then((p) => { if (alive) setProg(p.running ? p : null); })
      .catch(() => {});
    poll();
    const id = setInterval(poll, 1000);
    return () => { alive = false; clearInterval(id); };
  }, [loading]);

  const pct = prog && prog.total > 0
    ? Math.min(100, Math.round((prog.scanned / prog.total) * 100)) : null;

  const load = async () => {
    setLoading(true); setErr(null);
    try {
      const seedArr = seed.split(",").map((s) => s.trim()).filter(Boolean);
      // Liveness filtering is server-side (traded in last 24h); no trades/day knob.
      const res = await fetchTopTraders(days, 0, pool, seedArr);
      setTraders(res.traders ?? []);
      // Server reports when the board was actually computed — show that, not
      // when we happened to fetch it.
      setUpdatedAt(res.updated_at ?? Date.now());
    } catch (e: any) { setErr(e.message ?? String(e)); setTraders([]); }
    finally { setLoading(false); }
  };

  // Refetch when filters change.
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days, pool]);

  // Auto-refresh every 60s (matches the API-side cache TTL ceiling).
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => { if (!loading) load(); }, 60_000);
    return () => clearInterval(id);
    // eslint-disable-next-line
  }, [autoRefresh, days, pool, seed, loading]);

  // Coins seen across the current board (core perps only), most-traded first —
  // these become the positions-filter pills.
  const coinOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of traders)
      for (const c of t.coins)
        if (isCoreCoin(c)) counts.set(c, (counts.get(c) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c).slice(0, 14);
  }, [traders]);

  const toggleCoin = (c: string) => {
    setCoinFilter((prev) => {
      const n = new Set(prev);
      n.has(c) ? n.delete(c) : n.add(c);
      return n;
    });
  };

  const sorted = useMemo(() => {
    // Positions filter: keep traders that traded at least one selected coin.
    const arr = coinFilter.size === 0 ? [...traders]
      : traders.filter((t) => t.coins.some((c) => coinFilter.has(c)));
    arr.sort((a, b) => {
      const cmp = (a[sortKey] as number) - (b[sortKey] as number);
      return sortDir === "desc" ? -cmp : cmp;
    });
    return arr;
  }, [traders, sortKey, sortDir, coinFilter]);

  const togglePick = (a: string) => {
    setPicked((p) => {
      const n = new Set(p);
      n.has(a) ? n.delete(a) : n.add(a);
      return n;
    });
  };

  const sortHeader = (k: SortKey, label: string, align: "left" | "right" = "right") => (
    <button
      className={`text-[10px] uppercase tracking-wider text-muted hover:text-ink
        ${align === "right" ? "text-right" : "text-left"} w-full`}
      onClick={() => {
        if (sortKey === k) setSortDir(sortDir === "desc" ? "asc" : "desc");
        else { setSortKey(k); setSortDir("desc"); }
      }}>
      {label}{sortKey === k ? (sortDir === "desc" ? " ▼" : " ▲") : ""}
    </button>
  );

  // Board-level stats for the tile strip — computed from what's on screen so
  // the tiles always agree with the table below them.
  const stats = useMemo(() => {
    if (sorted.length === 0) return null;
    const vol = sorted.reduce((s, t) => s + (t.volume > 0 ? t.volume : 0), 0);
    const best = sorted.reduce((m, t) => (t.roi ?? -Infinity) > (m.roi ?? -Infinity) ? t : m, sorted[0]);
    const wins = sorted.map((t) => t.win_rate).filter((w) => w >= 0).sort((a, b) => a - b);
    const medianWin = wins.length ? wins[Math.floor(wins.length / 2)] : null;
    return { count: sorted.length, vol, best, medianWin };
  }, [sorted]);

  const indexBuildHref = useMemo(() => {
    if (picked.size === 0) return "/strats/new";
    const seedQ = Array.from(picked).join(",");
    return `/strats/new?seed=${encodeURIComponent(seedQ)}&days=${days}`;
  }, [picked, days]);

  return (
    <section className="space-y-4">
      {/* Board stats — one glance before the table */}
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="panel px-4 py-3">
            <div className="stat">Traders on board</div>
            <div className="text-xl font-semibold mt-0.5">{stats.count}</div>
            <div className="text-[10px] text-dim mt-0.5">active ≤24h · pool {pool}</div>
          </div>
          <div className="panel px-4 py-3">
            <div className="stat">Combined volume</div>
            <div className="text-xl font-semibold mt-0.5">{fmtUsd(stats.vol)}</div>
            <div className="text-[10px] text-dim mt-0.5">{days}d window</div>
          </div>
          <div className="panel px-4 py-3">
            <div className="stat">Best ROI</div>
            <div className={`text-xl font-semibold mt-0.5 ${(stats.best.roi ?? 0) >= 0 ? "text-win" : "text-loss"}`}>
              {stats.best.roi == null ? "—" : `${stats.best.roi >= 0 ? "+" : ""}${fmtPct(stats.best.roi, 1)}`}
            </div>
            <div className="text-[10px] text-dim mt-0.5 font-mono">{shortAddr(stats.best.address)}</div>
          </div>
          <div className="panel px-4 py-3">
            <div className="stat">Median win rate</div>
            <div className="text-xl font-semibold mt-0.5">
              {stats.medianWin == null ? "—" : fmtPct(stats.medianWin, 0)}
            </div>
            <div className="text-[10px] text-dim mt-0.5">across traders with fills</div>
          </div>
        </div>
      )}

      {/* Filters — one compact row; seed input tucked behind a toggle */}
      <div className="panel p-3 space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="seg" title="ROI window">
            {DAY_OPTIONS.map((d) => (
              <button key={d} onClick={() => setDays(d)}
                className={`seg-btn ${days === d ? "seg-btn-active" : ""}`}>{d}d</button>
            ))}
          </div>
          <div className="seg" title="candidate pool size — traders scanned from the leaderboard">
            <span className="px-1.5 text-[9px] uppercase tracking-wider text-dim">pool</span>
            {POOL_OPTIONS.map((p) => (
              <button key={p} onClick={() => setPool(p)}
                className={`seg-btn ${pool === p ? "seg-btn-active" : ""}`}>{p}</button>
            ))}
          </div>
          <button className="btn-primary" onClick={load} disabled={loading}>
            {loading ? (pct != null ? `syncing ${pct}%` : "syncing…") : "scan"}
          </button>
          <button
            className={`btn ${seedOpen || seed.trim() ? "border-accent/40 text-accent2" : ""}`}
            title="Seed the scan with specific wallets"
            onClick={() => setSeedOpen((v) => !v)}>
            seeds{seed.trim() ? " ●" : ""}
          </button>
          <span className="ml-auto flex items-center gap-2 text-[10px] text-muted whitespace-nowrap">
            <label className="flex items-center gap-1 select-none cursor-pointer">
              <input type="checkbox" className="accent-accent" checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)} />
              auto
            </label>
            <span title="Only wallets that traded in the last 24h are scanned">
              active ≤24h{updatedAt ? ` · updated ${ago(updatedAt)}` : ""}
            </span>
          </span>
        </div>
        {seedOpen && (
          <input className="input w-full font-mono" autoFocus
            placeholder="seed wallets — 0xabc…, 0xdef… (comma-separated)"
            value={seed} onChange={(e) => setSeed(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()} />
        )}
        {coinOptions.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[9px] uppercase tracking-[0.14em] text-dim mr-1"
              title="Only show traders who traded these coins">coins</span>
            {(coinsExpanded ? coinOptions : coinOptions.slice(0, 8)).map((c) => (
              <button key={c} onClick={() => toggleCoin(c)}
                className={`pill transition-colors ${coinFilter.has(c)
                  ? "!border-accent/60 !text-accent !bg-accent/10" : "hover:text-ink"}`}>
                {c}
              </button>
            ))}
            {!coinsExpanded && coinOptions.length > 8 && (
              <button className="pill hover:text-ink" onClick={() => setCoinsExpanded(true)}>
                +{coinOptions.length - 8} more
              </button>
            )}
            {coinFilter.size > 0 && (
              <button className="pill hover:text-ink" onClick={() => setCoinFilter(new Set())}>
                ✕ clear
              </button>
            )}
          </div>
        )}
      </div>

      {/* Sync progress — shown while the API is actively scanning Hyperliquid */}
      {loading && (
        <div className="panel p-3 space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-accent2">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent animate-pulse mr-1.5 align-middle" />
              syncing with Hyperliquid
              {prog && prog.total > 0
                ? ` — ${prog.scanned} / ${prog.total} wallets`
                : " — fetching leaderboard…"}
            </span>
            <span className="text-muted">
              {prog && prog.total > 0
                ? `${prog.hours_scanned}h of ${prog.hours_total}h history · ${pct}%`
                : ""}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
            {pct != null ? (
              <div className="h-full rounded-full bg-accent-grad shadow-glow transition-[width] duration-500"
                style={{ width: `${Math.max(pct, 2)}%` }} />
            ) : (
              <div className="h-full w-1/3 rounded-full bg-accent-grad animate-pulse" />
            )}
          </div>
        </div>
      )}

      {/* Selection bar */}
      {picked.size > 0 && (
        <div className="panel p-3 flex items-center gap-3">
          <span className="text-xs text-accent2">{picked.size} selected</span>
          <Link href={indexBuildHref} className="btn-primary">build strat from selection</Link>
          <button className="btn" onClick={() => setPicked(new Set())}>clear</button>
        </div>
      )}

      {/* Table — horizontal scroll on narrow screens instead of crushed columns */}
      <div className="panel overflow-x-auto">
       <div className="min-w-[640px]">
        <div className={`${GRID} py-2 border-b border-border`}>
          <div className="label !mb-0">trader</div>
          <div>{sortHeader("roi", "roi")}</div>
          <div>{sortHeader("volume", "volume")}</div>
          <div>{sortHeader("win_rate", "win%")}</div>
          <div>{sortHeader("sharpe", "sharpe")}</div>
          <div className="label !mb-0 text-right">last</div>
          <div />
        </div>
        {err && <div className="px-4 py-3 text-xs text-loss">{err}</div>}
        {loading && sorted.length === 0 &&
          [...Array(6)].map((_, i) => (
            <div key={i} className={`${GRID} py-3 items-center table-row`}>
              <div className="skeleton h-4 w-44" />
              {[...Array(6)].map((_, j) => <div key={j} className="skeleton h-4 w-12 justify-self-end" />)}
            </div>
          ))}
        {!err && !loading && sorted.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-muted">
            no traders match the filters yet — try a wider pool or longer window{coinFilter.size > 0 ? ", or clear the positions filter" : ""}.
          </div>
        )}
        {sorted.map((t, i) => {
          const rank = i + 1;
          const medal = rank === 1 ? "text-warn border-warn/40 shadow-glow"
            : rank <= 3 ? "text-accent border-accent/40" : "text-dim border-white/[0.08]";
          return (
          <div key={t.address}
            className={`group ${GRID} py-2 items-center table-row hover:bg-accent/[0.04]`}>
            <div className="flex items-center gap-2 min-w-0">
              <span className={`grid place-items-center h-5 w-5 shrink-0 rounded-md border text-[10px] font-mono font-semibold ${medal}`}>
                {rank}
              </span>
              <input type="checkbox" className="accent-accent" checked={picked.has(t.address)}
                onChange={() => togglePick(t.address)} />
              <Link href={`/trader/${t.address}?days=${days}`} title="view trader"
                className="font-mono text-accent2 hover:text-accent transition-colors shrink-0">
                {shortAddr(t.address)}
              </Link>
              {(() => {
                // Badges show core perp coins only — builder-dex ("xyz:…") and
                // spot ("@…") fills stay out. One row, never wraps into the ROI
                // column; overflow collapses into a "+n" count.
                const core = t.coins.filter(isCoreCoin);
                const extra = core.length - 2;
                return (
                  <div className="flex flex-wrap gap-1 min-w-0 max-h-[20px] overflow-hidden"
                    title={core.join(", ") || t.coins.join(", ")}>
                    {core.slice(0, 2).map((c) => (
                      <span key={c} className="pill whitespace-nowrap">{c}</span>
                    ))}
                    {extra > 0 && <span className="pill whitespace-nowrap">+{extra}</span>}
                    {core.length === 0 && t.coins.length > 0 && (
                      <span className="pill whitespace-nowrap opacity-60">dex</span>
                    )}
                  </div>
                );
              })()}
            </div>
            <div className="text-right"
              title={t.account_value > 0 ? `on ${fmtUsd(t.account_value)} equity` : undefined}>
              <div className={`num font-semibold ${(t.roi ?? 0) >= 0 ? "text-win" : "text-loss"}`}>
                {t.roi == null ? "—" : `${t.roi >= 0 ? "+" : ""}${fmtPct(t.roi, 1)}`}
              </div>
              <div className={`num text-[10px] leading-tight ${t.pnl >= 0 ? "text-win/60" : "text-loss/60"}`}>
                {fmtPnl(t.pnl)}
              </div>
            </div>
            <div className="num text-right text-ink/90">{t.volume > 0 ? fmtUsd(t.volume) : "—"}</div>
            <div className="text-right">
              <div className="num text-ink/90">{t.win_rate < 0 ? "—" : fmtPct(t.win_rate, 0)}</div>
              <div className="num text-[10px] leading-tight text-dim">
                {t.win_rate < 0 ? "" : `${t.trades} tx`}
              </div>
            </div>
            <div className="num text-right text-ink/90">{t.win_rate < 0 ? "—" : t.sharpe.toFixed(2)}</div>
            <div className="text-right text-[11px] text-muted">{t.last_active > 0 ? ago(t.last_active) : "≤24h"}</div>
            <div className="flex justify-end opacity-80 group-hover:opacity-100 transition-opacity">
              <Link href={`/follows/new?leader=${t.address}`} className="btn-primary">copy</Link>
            </div>
          </div>
          );
        })}
       </div>
      </div>
    </section>
  );
}
