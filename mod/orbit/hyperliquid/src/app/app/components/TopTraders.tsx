"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchBoard, fetchScanProgress, BoardMeta, ScanProgress, TopTrader, fmtPnl, fmtUsd, fmtPct, shortAddr, ago } from "../lib/api";
import Link from "next/link";
import { DataBar, Field, Freshness, Identicon, Kpi, Medal, Meter, PageHead, SparkBars, SplitBar, Switch } from "./BoardBits";

type SortKey = "roi" | "pnl" | "volume" | "account_value" | "win_rate" | "trades" | "sharpe";
type Rank = "roi" | "pnl" | "volume";

// HL's native ROI windows are day / week / month — 1, 7, 30 days. Other values
// would just bucket back to these, so we don't pretend to offer them.
const DAY_OPTIONS = [1, 7, 30];
// How many top rows (by rank) get fill stats. Every row past the leaderboard
// scrape costs one throttled /info query, so this is the only knob that
// spends anything — the board itself is always the whole gated universe.
const ENRICH_OPTIONS = [120, 250, 400];
const RANKS: { k: Rank; label: string; hint: string }[] = [
  { k: "roi", label: "roi", hint: "fill stats for the best return-on-equity wallets" },
  { k: "pnl", label: "pnl", hint: "fill stats for the biggest dollar winners (HL's own order)" },
  { k: "volume", label: "volume", hint: "fill stats for the most active books" },
];
const PAGE = 250;

// Score floors — applied on the client over the full list, so they're instant.
type Floors = { roi: string; sharpe: string; win: string; equity: string; volume: string; trades: string; stats: boolean };
const NO_FLOORS: Floors = { roi: "", sharpe: "", win: "", equity: "", volume: "", trades: "", stats: false };
const num = (s: string) => { const v = parseFloat(s.replace(/[,$%\s]/g, "")); return Number.isFinite(v) ? v : null; };
const hasStats = (t: TopTrader) => t.win_rate >= 0;
// Stat columns are only real on measured rows — an unmeasured 0 must never
// outrank a measured negative.
const STAT_KEYS: SortKey[] = ["win_rate", "trades", "sharpe"];

// Fill coins arrive raw from HL: HIP-3 builder-dex perps as "dex:TICKER"
// (e.g. "xyz:HYUNDAI") and spot markets as "@123" indices. Neither is a
// copyable core perp, so they're excluded from badges and the positions filter.
const isCoreCoin = (c: string) => !c.includes(":") && !c.startsWith("@");

// Shared column template: trader | roi(+pnl) | equity | volume | win%(+trades) | sharpe | last | copy.
const GRID = "grid grid-cols-[minmax(0,2.6fr)_1.15fr_1fr_1fr_1fr_0.9fr_0.9fr_auto] gap-2 px-4";

export default function TopTraders() {
  const [days, setDays] = useState(7);
  const [rank, setRank] = useState<Rank>("roi");
  const [enrich, setEnrich] = useState(120);
  const [seed, setSeed] = useState("");
  const [traders, setTraders] = useState<TopTrader[]>([]);
  const [meta, setMeta] = useState<BoardMeta | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("roi");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [prog, setProg] = useState<ScanProgress | null>(null);
  // The API walks a cold board in the background rather than holding the
  // request open past a gateway timeout — `scanning` means the rows on screen
  // are the last good board and a fresh one is still being built.
  const [scanning, setScanning] = useState(false);
  const [coinFilter, setCoinFilter] = useState<Set<string>>(new Set());
  const [seedOpen, setSeedOpen] = useState(false);
  const [coinsExpanded, setCoinsExpanded] = useState(false);
  const [coinDraft, setCoinDraft] = useState("");
  const [floors, setFloors] = useState<Floors>(NO_FLOORS);
  const [visible, setVisible] = useState(PAGE);
  const coinKey = useMemo(() => Array.from(coinFilter).sort().join(","), [coinFilter]);

  // While a scan request is in flight, poll the API's live scan progress so
  // the user sees "X of N wallets" instead of an opaque spinner. Cached
  // fast-path responses resolve before the first poll — no flash.
  useEffect(() => {
    if (!loading && !scanning) { setProg(null); return; }
    let alive = true;
    const poll = () => fetchScanProgress()
      .then((p) => { if (alive) setProg(p.running ? p : null); })
      .catch(() => {});
    poll();
    const id = setInterval(poll, 1000);
    return () => { alive = false; clearInterval(id); };
  }, [loading, scanning]);

  const pct = prog && prog.total > 0
    ? Math.min(100, Math.round((prog.scanned / prog.total) * 100)) : null;

  const load = async () => {
    setLoading(true); setErr(null);
    try {
      const seedArr = seed.split(",").map((s) => s.trim()).filter(Boolean);
      const coins = Array.from(coinFilter);
      // Plain board = EVERY wallet that clears the gates (equity ≥ $1k, traded
      // ≤24h), priced from the leaderboard; only the top `enrich` by `rank`
      // are measured from fills. A coin requirement can only be checked from
      // fills, so that mode walks the ranked list for `enrich` qualifying
      // wallets instead.
      const res = await fetchBoard({
        days, pool: coins.length ? enrich : "all", rank, enrich, seed: seedArr, coins,
      });
      const { traders: rows, ...m } = res;
      // A scanning answer with no rows yet must not blank a board we already
      // have on screen — the walk it kicked off will fill it in.
      if (!m.scanning || (rows?.length ?? 0) > 0) { setTraders(rows ?? []); setMeta(m); }
      setScanning(!!m.scanning);
    } catch (e: any) { setErr(e.message ?? String(e)); setTraders([]); setMeta(null); setScanning(false); }
    finally { setLoading(false); }
  };

  // While the API is walking a board in the background, come back for it —
  // the answer lands in its cache the moment the walk finishes.
  useEffect(() => {
    if (!scanning || loading) return;
    const id = setTimeout(() => load(), 5_000);
    return () => clearTimeout(id);
    // eslint-disable-next-line
  }, [scanning, loading, days, rank, enrich, coinKey]);

  // Refetch when requirements change.
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days, rank, enrich, coinKey]);
  // Any change to what's shown restarts paging from the top.
  useEffect(() => { setVisible(PAGE); }, [days, rank, enrich, coinKey, floors, sortKey, sortDir]);

  // Auto-refresh every 60s (matches the API-side cache TTL ceiling).
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => { if (!loading) load(); }, 60_000);
    return () => clearInterval(id);
    // eslint-disable-next-line
  }, [autoRefresh, days, rank, enrich, seed, coinKey, loading]);

  // Coin pills: whatever is required right now, pinned first, then the coins
  // seen across the measured rows (core perps only), most-traded first.
  const coinOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of traders)
      for (const c of t.coins)
        if (isCoreCoin(c)) counts.set(c, (counts.get(c) ?? 0) + 1);
    const seen = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
    const pinned = Array.from(coinFilter);
    return [...pinned, ...seen.filter((c) => !coinFilter.has(c))].slice(0, Math.max(14, pinned.length));
  }, [traders, coinFilter]);

  const addCoin = () => {
    const c = coinDraft.trim().toUpperCase();
    if (!c) return;
    setCoinFilter((prev) => new Set(prev).add(c));
    setCoinDraft("");
  };

  const toggleCoin = (c: string) => {
    setCoinFilter((prev) => {
      const n = new Set(prev);
      n.has(c) ? n.delete(c) : n.add(c);
      return n;
    });
  };

  const floorsActive = useMemo(() => floors.stats || (Object.keys(floors) as (keyof Floors)[])
    .some((k) => k !== "stats" && num(floors[k] as string) != null), [floors]);

  const filtered = useMemo(() => {
    const f = {
      roi: num(floors.roi), sharpe: num(floors.sharpe), win: num(floors.win),
      equity: num(floors.equity), volume: num(floors.volume), trades: num(floors.trades),
    };
    // A sharpe / win% / trade-count floor can only be met by a measured row.
    const needStats = floors.stats || f.sharpe != null || f.win != null || f.trades != null;
    // The API already applied the coin requirement; re-applying it here only
    // keeps a stale board honest while the next scan is in flight.
    return traders.filter((t) => {
      if (coinFilter.size > 0 && !t.coins.some((c) => coinFilter.has(c.toUpperCase()))) return false;
      if (needStats && !hasStats(t)) return false;
      if (f.roi != null && t.roi < f.roi) return false;
      if (f.equity != null && t.account_value < f.equity) return false;
      if (f.volume != null && t.volume < f.volume) return false;
      if (f.sharpe != null && t.sharpe < f.sharpe) return false;
      if (f.win != null && t.win_rate < f.win) return false;
      if (f.trades != null && t.trades < f.trades) return false;
      return true;
    });
  }, [traders, coinFilter, floors]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    const statSort = STAT_KEYS.includes(sortKey);
    const metric = (t: TopTrader) =>
      statSort && !hasStats(t) ? -Infinity : (t[sortKey] as number);
    arr.sort((a, b) => {
      const ma = metric(a), mb = metric(b);
      // Unmeasured rows sink to the bottom in either direction.
      if (ma === -Infinity || mb === -Infinity) return ma === mb ? 0 : ma === -Infinity ? 1 : -1;
      const cmp = ma - mb;
      return sortDir === "desc" ? -cmp : cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  const togglePick = (a: string) => {
    setPicked((p) => {
      const n = new Set(p);
      n.has(a) ? n.delete(a) : n.add(a);
      return n;
    });
  };

  const sortHeader = (k: SortKey, label: string, align: "left" | "right" = "right", title?: string) => {
    const on = sortKey === k;
    return (
      <button
        title={title}
        className={`eyebrow !tracking-wider w-full inline-flex items-center gap-1 transition-colors hover:text-ink
          ${align === "right" ? "justify-end" : "justify-start"} ${on ? "!text-accent" : ""}`}
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

  // Board-level stats for the tile strip — computed from what's on screen so
  // the tiles always agree with the table below them.
  const stats = useMemo(() => {
    if (sorted.length === 0) return null;
    const vol = sorted.reduce((s, t) => s + (t.volume > 0 ? t.volume : 0), 0);
    const best = sorted.reduce((m, t) => (t.roi ?? -Infinity) > (m.roi ?? -Infinity) ? t : m, sorted[0]);
    const wins = sorted.map((t) => t.win_rate).filter((w) => w >= 0).sort((a, b) => a - b);
    const medianWin = wins.length ? wins[Math.floor(wins.length / 2)] : null;
    const measured = sorted.filter(hasStats).length;
    // Polarity of the board: who is up vs down over the window.
    const up = sorted.filter((t) => (t.roi ?? 0) > 0).length;
    const down = sorted.filter((t) => (t.roi ?? 0) < 0).length;
    // Volume concentration: the biggest books first, so the tile shows how
    // top-heavy the flow is.
    const byVol = [...sorted].filter((t) => t.volume > 0).sort((a, b) => b.volume - a.volume).slice(0, 40);
    const top3Share = vol > 0 ? byVol.slice(0, 3).reduce((s, t) => s + t.volume, 0) / vol : 0;
    // ROI spread, best first — the best is the lit bar.
    const byRoi = [...sorted].filter((t) => t.roi != null).sort((a, b) => (b.roi ?? 0) - (a.roi ?? 0)).slice(0, 40);
    const maxAbsRoi = Math.max(...sorted.map((t) => Math.abs(t.roi ?? 0)), 1e-9);
    return { count: sorted.length, vol, best, medianWin, measured, up, down, byVol, top3Share, byRoi, maxAbsRoi };
  }, [sorted]);

  const measuredTotal = useMemo(() => traders.filter(hasStats).length, [traders]);
  const universe = meta?.candidates ?? traders.length;

  const indexBuildHref = useMemo(() => {
    if (picked.size === 0) return "/strats/new";
    const seedQ = Array.from(picked).join(",");
    return `/strats/new?seed=${encodeURIComponent(seedQ)}&days=${days}`;
  }, [picked, days]);

  const floorInput = (k: keyof Floors, label: string, ph: string, title: string, w = "w-16") => (
    <label className="flex items-center gap-1.5 text-[10px] text-muted" title={title}>
      <span className="uppercase tracking-wider">{label} ≥</span>
      <input className={`input !py-0 !px-2 h-[22px] ${w} text-[10px] font-mono`}
        placeholder={ph} value={floors[k] as string}
        onChange={(e) => setFloors((f) => ({ ...f, [k]: e.target.value }))} />
    </label>
  );

  const shown = sorted.slice(0, visible);
  const fmtN = (n: number) => n.toLocaleString("en-US");
  const coinList = Array.from(coinFilter);

  return (
    <section className="space-y-5">
      {/* Page heading — what this board is, and how fresh it is */}
      <PageHead
        title="Top traders"
        blurb={
          <>
            Every wallet on the Hyperliquid leaderboard with at least $1k of equity that traded in the
            last 24h, ranked by {days}-day return on equity
            {coinList.length > 0 ? ` and trading ${coinList.join(" / ")}` : ""}. Fill stats are
            measured for the top {enrich} by {rank}. Copy one outright, or pick a few and build a strat.
          </>
        }
        right={
          <Freshness loading={loading || scanning} label={
            loading || scanning
              ? (pct != null ? `syncing ${pct}%` : "syncing…")
              : meta?.updated_at ? `updated ${ago(meta.updated_at)}` : "waiting for the first scan"
          } />
        }
      />

      {/* Board stats — one glance before the table */}
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Kpi label="Traders on board"
            value={
              <>
                {fmtN(stats.count)}
                {stats.count !== traders.length && (
                  <span className="text-sm font-normal text-dim"> / {fmtN(traders.length)}</span>
                )}
              </>
            }
            sub={
              coinFilter.size > 0
                ? <span title={`top ${enrich} active wallets that traded ${coinList.join(", ")} — ${meta?.depth ?? "?"} leaderboard rows walked to find them`}>
                    trade {coinList.join(" / ")} · walked {meta?.depth ?? "—"}
                  </span>
                : <span title={`every wallet on the leaderboard with ≥ $1k equity that traded in the last 24h (${fmtN(universe)}); fill stats fetched for the top ${enrich} by ${rank} only`}>
                    <span className="text-win">{fmtN(stats.up)} up</span> · <span className="text-loss">{fmtN(stats.down)} down</span>
                    {" · "}{floorsActive ? `of ${fmtN(universe)} active` : `stats on top ${fmtN(measuredTotal)}`}
                  </span>
            }>
            <SplitBar up={stats.up} down={stats.down} />
          </Kpi>

          <Kpi label="Combined volume" value={fmtUsd(stats.vol)}
            sub={<>{days}d window · top 3 books are {fmtPct(stats.top3Share * 100, 0)} of it</>}>
            <SparkBars values={stats.byVol.map((t) => t.volume)}
              titles={stats.byVol.map((t) => `${shortAddr(t.address)} · ${fmtUsd(t.volume)}`)} />
          </Kpi>

          <Kpi label="Best ROI"
            tone={(stats.best.roi ?? 0) >= 0 ? "win" : "loss"}
            value={stats.best.roi == null ? "—" : `${stats.best.roi >= 0 ? "+" : ""}${fmtPct(stats.best.roi, 1)}`}
            sub={
              <Link href={`/trader/${stats.best.address}?days=${days}`}
                className="inline-flex items-center gap-1.5 font-mono text-ink/80 hover:text-accent transition-colors">
                <Identicon address={stats.best.address} size={12} />
                {shortAddr(stats.best.address)}
              </Link>
            }>
            <SparkBars values={stats.byRoi.map((t) => t.roi ?? 0)} hot={0}
              titles={stats.byRoi.map((t) => `${shortAddr(t.address)} · ${(t.roi ?? 0) >= 0 ? "+" : ""}${fmtPct(t.roi ?? 0, 1)}`)} />
          </Kpi>

          <Kpi label="Median win rate"
            value={stats.medianWin == null ? "—" : fmtPct(stats.medianWin, 0)}
            sub={<>across {fmtN(stats.measured)} measured trader{stats.measured === 1 ? "" : "s"}</>}>
            <Meter pct={stats.medianWin ?? 0} />
          </Kpi>
        </div>
      )}

      {/* Filters — window / measure depth, then score floors, then coins */}
      <div className="panel p-3 space-y-3">
        <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
          <Field label="window" title="ROI window">
            <div className="seg">
              {DAY_OPTIONS.map((d) => (
                <button key={d} onClick={() => setDays(d)}
                  className={`seg-btn ${days === d ? "seg-btn-active" : ""}`}>{d}d</button>
              ))}
            </div>
          </Field>
          <Field label="measure" title="which top wallets get fill stats — win%, sharpe, trades, coins">
            <div className="seg">
              <span className="px-1.5 text-[9px] uppercase tracking-wider text-dim">top</span>
              {ENRICH_OPTIONS.map((n) => (
                <button key={n} onClick={() => setEnrich(n)}
                  className={`seg-btn ${enrich === n ? "seg-btn-active" : ""}`}>{n}</button>
              ))}
              <span className="px-1.5 text-[9px] uppercase tracking-wider text-dim">by</span>
              {RANKS.map((r) => (
                <button key={r.k} onClick={() => setRank(r.k)} title={r.hint}
                  className={`seg-btn ${rank === r.k ? "seg-btn-active" : ""}`}>{r.label}</button>
              ))}
            </div>
          </Field>
          <div className="ml-auto flex items-center gap-2 pb-0.5">
            <Switch on={autoRefresh} onChange={setAutoRefresh} label="auto" />
            <button
              className={`btn ${seedOpen || seed.trim() ? "!border-accent/40 !text-accent" : ""}`}
              title="Seed the scan with specific wallets"
              onClick={() => setSeedOpen((v) => !v)}>
              seeds{seed.trim() ? " ●" : ""}
            </button>
            <button className="btn-primary min-w-[6.5rem]" onClick={load} disabled={loading || scanning}>
              {loading || scanning ? (pct != null ? `syncing ${pct}%` : "syncing…") : "scan"}
            </button>
          </div>
        </div>
        {seedOpen && (
          <input className="input w-full font-mono" autoFocus
            placeholder="seed wallets — 0xabc…, 0xdef… (comma-separated)"
            value={seed} onChange={(e) => setSeed(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()} />
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-white/[0.05] pt-3">
          <span className="eyebrow mr-1"
            title="Score floors — applied instantly to every wallet on the board">score</span>
          {floorInput("roi", "roi", "%", "window return on equity, percent — priced for every wallet")}
          {floorInput("equity", "equity", "$", "account value — priced for every wallet", "w-20")}
          {floorInput("volume", "volume", "$", "window volume — priced for every wallet", "w-20")}
          {floorInput("sharpe", "sharpe", "1.0", "daily-PnL sharpe — only measured rows qualify")}
          {floorInput("win", "win", "%", "win rate, percent — only measured rows qualify", "w-14")}
          {floorInput("trades", "trades", "n", "fills in the window — only measured rows qualify", "w-14")}
          <span title="Hide rows that haven't been measured from fills (no win% / sharpe yet)">
            <Switch on={floors.stats} onChange={(v) => setFloors((f) => ({ ...f, stats: v }))} label="measured only" />
          </span>
          {floorsActive && (
            <button className="pill hover:text-ink" onClick={() => setFloors(NO_FLOORS)}>✕ clear</button>
          )}
        </div>
        {(coinOptions.length > 0 || !loading) && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-white/[0.05] pt-3">
            <span className="eyebrow mr-1"
              title="Requirement: the scan keeps walking the leaderboard until it has enough wallets that traded one of these">coins</span>
            {(coinsExpanded ? coinOptions : coinOptions.slice(0, 8)).map((c) => (
              <button key={c} onClick={() => toggleCoin(c)}
                className={`pill transition-colors ${coinFilter.has(c)
                  ? "!border-accent/60 !text-accent !bg-accent/10" : "hover:text-ink hover:border-white/20"}`}>
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
            <input className="input !py-0 !px-2 h-[22px] w-24 text-[10px] font-mono uppercase !rounded-full"
              placeholder="+ coin" value={coinDraft} title="Require any coin — press enter"
              onChange={(e) => setCoinDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addCoin()} onBlur={addCoin} />
          </div>
        )}
      </div>

      {/* Sync progress — shown while the API is actively scanning Hyperliquid */}
      {(loading || scanning) && (
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
        <div className="panel p-3 flex items-center gap-3 !border-accent/30 shadow-glow">
          <span className="text-xs text-accent2">{picked.size} selected</span>
          <Link href={indexBuildHref} className="btn-primary">build strat from selection</Link>
          <button className="btn" onClick={() => setPicked(new Set())}>clear</button>
        </div>
      )}

      {/* Table — horizontal scroll on narrow screens instead of crushed columns */}
      <div className="panel overflow-x-auto">
       <div className="min-w-[760px]">
        <div className={`${GRID} py-2.5 table-head`}>
          <div className="eyebrow !tracking-wider">trader</div>
          <div>{sortHeader("roi", "roi")}</div>
          <div>{sortHeader("account_value", "equity")}</div>
          <div>{sortHeader("volume", "volume")}</div>
          <div>{sortHeader("win_rate", "win%", "right", "measured rows first")}</div>
          <div>{sortHeader("sharpe", "sharpe", "right", "measured rows first")}</div>
          <div className="eyebrow !tracking-wider text-right">last</div>
          <div />
        </div>
        {err && <div className="px-4 py-3 text-xs text-loss">{err}</div>}
        {(loading || scanning) && sorted.length === 0 &&
          [...Array(6)].map((_, i) => (
            <div key={i} className={`${GRID} py-3 items-center table-row`}>
              <div className="skeleton h-4 w-44" />
              {[...Array(7)].map((_, j) => <div key={j} className="skeleton h-4 w-12 justify-self-end" />)}
            </div>
          ))}
        {!err && !loading && !scanning && sorted.length === 0 && (
          <div className="px-4 py-10 text-center text-xs text-muted">
            {coinFilter.size > 0
              ? `no active wallet in the top ${meta?.depth ?? "—"} of the leaderboard traded ${coinList.join(" / ")} in the last ${days}d — try another coin or a longer window.`
              : floorsActive
                ? `none of the ${fmtN(traders.length)} active wallets clear these floors — loosen a score, or raise "measure · top" so more rows are measured.`
                : "no traders match the filters yet — try a longer window."}
          </div>
        )}
        {shown.map((t, i) => {
          const rank = i + 1;
          const pos = (t.roi ?? 0) >= 0;
          const isPicked = picked.has(t.address);
          return (
          <div key={t.address}
            className={`group ${GRID} py-2.5 items-center table-row hover:bg-accent/[0.04] ${isPicked ? "bg-accent/[0.05]" : ""}`}>
            <div className="flex items-center gap-2.5 min-w-0">
              <Medal rank={rank} />
              <input type="checkbox" className="accent-accent" checked={isPicked}
                onChange={() => togglePick(t.address)} />
              <Link href={`/trader/${t.address}?days=${days}`} title="view trader"
                className="flex items-center gap-2 font-mono text-[13px] text-ink/90 hover:text-accent transition-colors shrink-0">
                <Identicon address={t.address} />
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
              <div className={`num font-semibold ${pos ? "text-win" : "text-loss"}`}>
                {t.roi == null ? "—" : `${t.roi >= 0 ? "+" : ""}${fmtPct(t.roi, 1)}`}
              </div>
              <div className={`num text-[10px] leading-tight ${t.pnl >= 0 ? "text-win/60" : "text-loss/60"}`}>
                {fmtPnl(t.pnl)}
              </div>
              {/* ROI as a bar against the board's largest move — the column reads at a glance. */}
              {stats && <DataBar value={t.roi ?? 0} max={stats.maxAbsRoi} />}
            </div>
            <div className="num text-right text-ink/80">{t.account_value > 0 ? fmtUsd(t.account_value) : "—"}</div>
            <div className="num text-right text-ink/90">{t.volume > 0 ? fmtUsd(t.volume) : "—"}</div>
            <div className="text-right" title={hasStats(t) ? undefined : `not measured — only the top ${enrich} by ${rank} get fill stats`}>
              <div className="num text-ink/90">{t.win_rate < 0 ? "—" : fmtPct(t.win_rate, 0)}</div>
              <div className="num text-[10px] leading-tight text-dim">
                {t.win_rate < 0 ? "" : `${t.trades} tx`}
              </div>
            </div>
            <div className="num text-right text-ink/90">{t.win_rate < 0 ? "—" : t.sharpe.toFixed(2)}</div>
            <div className="text-right text-[11px] text-muted">{t.last_active > 0 ? ago(t.last_active) : "≤24h"}</div>
            <div className="flex justify-end">
              <Link href={`/follows/new?leader=${t.address}`} className="btn-ghost">copy</Link>
            </div>
          </div>
          );
        })}
        {sorted.length > 0 && (
          <div className="flex items-center gap-3 px-4 py-3 text-[11px] text-muted">
            <span>showing {fmtN(shown.length)} of {fmtN(sorted.length)}</span>
            {shown.length < sorted.length && (
              <>
                <button className="btn" onClick={() => setVisible((v) => v + PAGE)}>
                  +{fmtN(Math.min(PAGE, sorted.length - shown.length))} more
                </button>
                <button className="btn" onClick={() => setVisible(sorted.length)}>show all</button>
              </>
            )}
          </div>
        )}
       </div>
      </div>
    </section>
  );
}
