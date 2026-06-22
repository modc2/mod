"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { listVaults, Vault, fmtUsd, fmtPct, shortAddr } from "../lib/api";

type SortKey = "apr" | "tvl" | "age_days";
const POOL_OPTIONS = [50, 150, 300, 600];
const TVL_OPTIONS = [10_000, 50_000, 250_000, 1_000_000];

export default function VaultsPage() {
  const [pool, setPool] = useState(150);
  const [minTvl, setMinTvl] = useState(10_000);
  const [vaults, setVaults] = useState<Vault[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("apr");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const load = async () => {
    setLoading(true); setErr(null);
    try {
      const res = await listVaults(pool, minTvl);
      setVaults(res.vaults ?? []);
    } catch (e: any) { setErr(e.message ?? String(e)); setVaults([]); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [pool, minTvl]);

  const sorted = useMemo(() => {
    const arr = [...vaults];
    arr.sort((a, b) => {
      const cmp = (a[sortKey] as number) - (b[sortKey] as number);
      return sortDir === "desc" ? -cmp : cmp;
    });
    return arr;
  }, [vaults, sortKey, sortDir]);

  const sortHeader = (k: SortKey, label: string) => (
    <button
      className="text-[10px] uppercase tracking-wider text-muted hover:text-ink text-right w-full"
      onClick={() => {
        if (sortKey === k) setSortDir(sortDir === "desc" ? "asc" : "desc");
        else { setSortKey(k); setSortDir("desc"); }
      }}>
      {label}{sortKey === k ? (sortDir === "desc" ? " ▼" : " ▲") : ""}
    </button>
  );

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl text-ink">Vaults</h1>
        <p className="text-xs text-muted mt-1">
          Deposit USDC into a Hyperliquid vault and the leader trades it for you — native copy-trading,
          ranked by APR. Open vaults only; closed and dust vaults filtered out.
        </p>
      </div>

      {/* Filters */}
      <div className="panel p-4 flex flex-wrap items-end gap-3">
        <div>
          <div className="label">min TVL</div>
          <div className="flex gap-1">
            {TVL_OPTIONS.map((t) => (
              <button key={t} onClick={() => setMinTvl(t)}
                className={`btn ${minTvl === t ? "border-accent text-accent" : ""}`}>
                {t >= 1e6 ? `${t / 1e6}M` : `${t / 1e3}K`}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="label">show top</div>
          <div className="flex gap-1">
            {POOL_OPTIONS.map((p) => (
              <button key={p} onClick={() => setPool(p)}
                className={`btn ${pool === p ? "border-accent text-accent" : ""}`}>{p}</button>
            ))}
          </div>
        </div>
        <button className="btn-primary ml-auto" onClick={load} disabled={loading}>
          {loading ? "loading…" : "refresh"}
        </button>
      </div>

      {/* Table */}
      <div className="panel">
        <div className="grid grid-cols-[2.4fr_1.6fr_repeat(3,1fr)_1.2fr] gap-2 px-4 py-2 border-b border-border">
          <div className="label !mb-0">vault</div>
          <div className="label !mb-0">leader</div>
          <div>{sortHeader("apr", "apr")}</div>
          <div>{sortHeader("tvl", "tvl")}</div>
          <div>{sortHeader("age_days", "age")}</div>
          <div className="label !mb-0 text-right">action</div>
        </div>
        {err && <div className="px-4 py-3 text-xs text-loss">{err}</div>}
        {loading && sorted.length === 0 &&
          [...Array(6)].map((_, i) => (
            <div key={i} className="grid grid-cols-[2.4fr_1.6fr_repeat(3,1fr)_1.2fr] gap-2 px-4 py-3 items-center table-row">
              <div className="skeleton h-4 w-40" />
              {[...Array(5)].map((_, j) => <div key={j} className="skeleton h-4 w-14 justify-self-end" />)}
            </div>
          ))}
        {!err && !loading && sorted.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-muted">no vaults match — try a lower min TVL.</div>
        )}
        {sorted.map((v, i) => {
          const rank = i + 1;
          const medal = rank === 1 ? "text-warn border-warn/40 shadow-glow"
            : rank <= 3 ? "text-accent border-accent/40" : "text-dim border-white/[0.08]";
          return (
            <div key={v.address}
              className="group grid grid-cols-[2.4fr_1.6fr_repeat(3,1fr)_1.2fr] gap-2 px-4 py-2.5 items-center table-row hover:bg-accent/[0.04]">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`grid place-items-center h-5 w-5 shrink-0 rounded-md border text-[10px] font-mono font-semibold ${medal}`}>
                  {rank}
                </span>
                <Link href={`/vaults/${v.address}`}
                  className="text-accent2 hover:text-accent transition-colors truncate">
                  {v.name || shortAddr(v.address)}
                </Link>
              </div>
              <Link href={`/trader/${v.leader}`}
                className="font-mono text-[11px] text-muted hover:text-ink truncate">
                {shortAddr(v.leader)}
              </Link>
              <div className={`num text-right font-semibold ${v.apr >= 0 ? "text-win" : "text-loss"}`}>
                {`${v.apr >= 0 ? "+" : ""}${fmtPct(v.apr, 0)}`}
              </div>
              <div className="num text-right text-ink/90">{fmtUsd(v.tvl)}</div>
              <div className="num text-right text-muted">{v.age_days}d</div>
              <div className="flex justify-end opacity-80 group-hover:opacity-100 transition-opacity">
                <Link href={`/vaults/${v.address}`} className="btn-primary">invest</Link>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-muted">
        APR is Hyperliquid's published trailing figure and is not a guarantee. Vault deposits carry a
        lockup (typically ~1 day) before you can withdraw.
      </p>
    </section>
  );
}
