"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchTopTraders, TopTrader, fmtPnl, fmtUsd, fmtPct, shortAddr, ago } from "../lib/api";
import Link from "next/link";

type SortKey = "roi" | "pnl" | "volume" | "win_rate" | "trades" | "sharpe";

// HL's native ROI windows are day / week / month — 1, 7, 30 days. Other values
// would just bucket back to these, so we don't pretend to offer them.
const DAY_OPTIONS = [1, 7, 30];
const POOL_OPTIONS = [50, 150, 300, 600];

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

  const load = async () => {
    setLoading(true); setErr(null);
    try {
      const seedArr = seed.split(",").map((s) => s.trim()).filter(Boolean);
      // Liveness filtering is server-side (traded in last 24h); no trades/day knob.
      const res = await fetchTopTraders(days, 0, pool, seedArr);
      setTraders(res.traders ?? []);
      setUpdatedAt(Date.now());
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

  const sorted = useMemo(() => {
    const arr = [...traders];
    arr.sort((a, b) => {
      const cmp = (a[sortKey] as number) - (b[sortKey] as number);
      return sortDir === "desc" ? -cmp : cmp;
    });
    return arr;
  }, [traders, sortKey, sortDir]);

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

  const indexBuildHref = useMemo(() => {
    if (picked.size === 0) return "/indexes/new";
    const seedQ = Array.from(picked).join(",");
    return `/indexes/new?seed=${encodeURIComponent(seedQ)}&days=${days}`;
  }, [picked, days]);

  return (
    <section className="space-y-4">
      {/* Filters */}
      <div className="panel p-4 flex flex-wrap items-end gap-3">
        <div>
          <div className="label">window</div>
          <div className="flex gap-1">
            {DAY_OPTIONS.map((d) => (
              <button key={d} onClick={() => setDays(d)}
                className={`btn ${days === d ? "border-accent text-accent" : ""}`}>{d}d</button>
            ))}
          </div>
        </div>
        <div>
          <div className="label">activity</div>
          <div className="btn border-accent/30 text-accent2 cursor-default">traded ≤24h</div>
        </div>
        <div>
          <div className="label">candidate pool</div>
          <div className="flex gap-1">
            {POOL_OPTIONS.map((p) => (
              <button key={p} onClick={() => setPool(p)}
                className={`btn ${pool === p ? "border-accent text-accent" : ""}`}>{p}</button>
            ))}
          </div>
        </div>
        <div className="flex-1 min-w-[20ch]">
          <div className="label">seed wallets (comma-separated)</div>
          <input className="input w-full" placeholder="0xabc…, 0xdef…"
            value={seed} onChange={(e) => setSeed(e.target.value)} />
        </div>
        <button className="btn-primary" onClick={load} disabled={loading}>
          {loading ? "scanning…" : "scan"}
        </button>
        <label className="flex items-center gap-1 text-[11px] text-muted select-none">
          <input type="checkbox" checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)} />
          auto
        </label>
        <span className="text-[10px] text-muted">
          {updatedAt ? `updated ${ago(updatedAt)}` : ""}
        </span>
      </div>

      {/* Selection bar */}
      {picked.size > 0 && (
        <div className="panel p-3 flex items-center gap-3">
          <span className="text-xs text-accent2">{picked.size} selected</span>
          <Link href={indexBuildHref} className="btn-primary">build index from selection</Link>
          <button className="btn" onClick={() => setPicked(new Set())}>clear</button>
        </div>
      )}

      {/* Table */}
      <div className="panel">
        <div className="grid grid-cols-[2.2fr_repeat(6,1fr)_2fr_1.6fr] gap-2 px-4 py-2 border-b border-border">
          <div className="label !mb-0">trader</div>
          <div>{sortHeader("roi", "roi")}</div>
          <div>{sortHeader("pnl", "pnl")}</div>
          <div>{sortHeader("volume", "volume")}</div>
          <div>{sortHeader("win_rate", "win%")}</div>
          <div>{sortHeader("trades", "trades")}</div>
          <div>{sortHeader("sharpe", "sharpe")}</div>
          <div className="label !mb-0 text-right">last</div>
          <div className="label !mb-0 text-right">action</div>
        </div>
        {err && <div className="px-4 py-3 text-xs text-loss">{err}</div>}
        {loading && sorted.length === 0 &&
          [...Array(6)].map((_, i) => (
            <div key={i} className="grid grid-cols-[2.2fr_repeat(6,1fr)_2fr_1.6fr] gap-2 px-4 py-3 items-center table-row">
              <div className="skeleton h-4 w-44" />
              {[...Array(8)].map((_, j) => <div key={j} className="skeleton h-4 w-12 justify-self-end" />)}
            </div>
          ))}
        {!err && !loading && sorted.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-muted">no traders match the filters yet — try a wider pool or longer window.</div>
        )}
        {sorted.map((t, i) => {
          const rank = i + 1;
          const medal = rank === 1 ? "text-warn border-warn/40 shadow-glow"
            : rank <= 3 ? "text-accent border-accent/40" : "text-dim border-white/[0.08]";
          return (
          <div key={t.address}
            className="group grid grid-cols-[2.2fr_repeat(6,1fr)_2fr_1.6fr] gap-2 px-4 py-2.5 items-center table-row hover:bg-accent/[0.04]">
            <div className="flex items-center gap-2 min-w-0">
              <span className={`grid place-items-center h-5 w-5 shrink-0 rounded-md border text-[10px] font-mono font-semibold ${medal}`}>
                {rank}
              </span>
              <input type="checkbox" className="accent-accent" checked={picked.has(t.address)}
                onChange={() => togglePick(t.address)} />
              <Link href={`/trader/${t.address}?days=${days}`}
                className="font-mono text-accent2 hover:text-accent transition-colors truncate">
                {shortAddr(t.address)}
              </Link>
              <div className="flex flex-wrap gap-1 max-w-[14ch]">
                {t.coins.slice(0, 3).map((c) => (
                  <span key={c} className="pill">{c}</span>
                ))}
              </div>
            </div>
            <div className={`num text-right font-semibold ${(t.roi ?? 0) >= 0 ? "text-win" : "text-loss"}`}
              title={t.account_value > 0 ? `on ${fmtUsd(t.account_value)} equity` : undefined}>
              {t.roi == null ? "—" : `${t.roi >= 0 ? "+" : ""}${fmtPct(t.roi, 1)}`}
            </div>
            <div className={`num text-right ${t.pnl >= 0 ? "text-win/90" : "text-loss/90"}`}>{fmtPnl(t.pnl)}</div>
            <div className="num text-right text-ink/90">{t.volume > 0 ? fmtUsd(t.volume) : "—"}</div>
            <div className="num text-right text-ink/90">{t.win_rate < 0 ? "—" : fmtPct(t.win_rate, 0)}</div>
            <div className="num text-right text-ink/90">{t.win_rate < 0 ? "—" : t.trades}</div>
            <div className="num text-right text-ink/90">{t.win_rate < 0 ? "—" : t.sharpe.toFixed(2)}</div>
            <div className="text-right text-[11px] text-muted">{t.last_active > 0 ? ago(t.last_active) : "≤24h"}</div>
            <div className="flex justify-end gap-1 opacity-80 group-hover:opacity-100 transition-opacity">
              <Link href={`/trader/${t.address}?days=${days}`} className="btn">view</Link>
              <Link href={`/follows/new?leader=${t.address}`} className="btn-primary">copy</Link>
            </div>
          </div>
          );
        })}
      </div>
    </section>
  );
}
