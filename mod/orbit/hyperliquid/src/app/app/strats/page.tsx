"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { listIndexes, indexPerf, deleteIndex, Index, ago, shortAddr, fmtPnl } from "../lib/api";
import { useWallet } from "../lib/wallet";

// Per-strat performance, loaded lazily after the list renders.
type Perf = { weighted_pnl: number; days: number } | "loading" | "err";

export default function StratsPage() {
  const { address } = useWallet();
  const [items, setItems] = useState<Index[]>([]);
  const [perf, setPerf] = useState<Record<string, Perf>>({});
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [mine, setMine] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const list = (await listIndexes()).indexes;
      setItems(list);
      // Fan out perf fetches — cheap (server caches fills) and non-blocking.
      setPerf(Object.fromEntries(list.map((i) => [i.id, "loading" as Perf])));
      list.forEach((i) => {
        indexPerf(i.id, i.days_window || 7)
          .then((p) => setPerf((m) => ({ ...m, [i.id]: { weighted_pnl: p.weighted_pnl ?? 0, days: p.days ?? i.days_window } })))
          .catch(() => setPerf((m) => ({ ...m, [i.id]: "err" })));
      });
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const onDelete = async (e: React.MouseEvent, id: string) => {
    e.preventDefault(); e.stopPropagation();
    if (!confirm("delete strat?")) return;
    await deleteIndex(id); load();
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((i) => {
      if (mine && (!address || i.owner.toLowerCase() !== address.toLowerCase())) return false;
      if (!q) return true;
      return i.name.toLowerCase().includes(q)
        || i.owner.toLowerCase().includes(q)
        || i.description.toLowerCase().includes(q)
        || i.legs.some((l) => l.address.toLowerCase().includes(q));
    });
  }, [items, search, mine, address]);

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display font-bold text-2xl tracking-tight text-gradient">STRATS</h1>
          <p className="text-xs text-muted mt-1 max-w-xl">
            Community trading strategies — weighted baskets of top traders, each one forkable.
            Browse what others run, copy a basket, or compose your own.
          </p>
        </div>
        <Link href="/strats/new" className="btn-primary">+ new strat</Link>
      </div>

      {/* Filter bar */}
      <div className="panel p-3 flex flex-wrap items-center gap-2">
        <input className="input flex-1 min-w-[20ch]" placeholder="FILTER STRATS BY NAME, OWNER, OR TRADER…"
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <button onClick={() => setMine((v) => !v)}
          className={`btn ${mine ? "border-accent text-accent" : ""}`}>mine</button>
        <span className="text-[10px] text-muted uppercase tracking-wider">{filtered.length} strats</span>
      </div>

      {/* Grid */}
      {loading && items.length === 0 ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {[...Array(6)].map((_, i) => <div key={i} className="panel h-44 skeleton" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="panel p-8 text-center text-xs text-muted">
          no strats {mine ? "of yours " : ""}yet — <Link href="/strats/new" className="text-accent2">compose one →</Link>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((idx) => {
            const p = perf[idx.id];
            const pnl = p && p !== "loading" && p !== "err" ? p.weighted_pnl : null;
            const isOwner = address && idx.owner.toLowerCase() === address.toLowerCase();
            return (
              <Link key={idx.id} href={`/strats/${idx.id}`}
                className="group relative flex flex-col rounded-lg border border-white/[0.07] bg-gradient-to-b from-white/[0.025] to-transparent p-4 transition-all hover:border-accent/40 hover:shadow-glow">
                {/* top accent bar */}
                <span className="absolute inset-x-0 top-0 h-px bg-accent-grad opacity-0 group-hover:opacity-80 transition-opacity" />

                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-ink font-medium truncate">{idx.name}</div>
                    <div className="text-[10px] uppercase tracking-wider text-muted mt-0.5">
                      by {shortAddr(idx.owner)} · {ago(idx.created_ms)}
                    </div>
                  </div>
                  {idx.vault_address
                    ? <span className="pill border-accent/40 text-accent shrink-0">vault</span>
                    : <span className="pill text-muted shrink-0">{idx.legs.length} legs</span>}
                </div>

                {/* headline perf — the polymarket-style number */}
                <div className="mt-4 flex items-end gap-2">
                  {p === "loading" ? (
                    <div className="skeleton h-7 w-24" />
                  ) : pnl === null ? (
                    <span className="num text-2xl text-dim">—</span>
                  ) : (
                    <span className={`num text-2xl font-semibold ${pnl >= 0 ? "text-win" : "text-loss"}`}>
                      {fmtPnl(pnl)}
                    </span>
                  )}
                  <span className="text-[10px] uppercase tracking-wider text-muted mb-1">
                    weighted · {idx.days_window}d
                  </span>
                </div>

                {idx.description && (
                  <div className="text-xs text-muted mt-2 line-clamp-2">{idx.description}</div>
                )}

                {/* leg chips */}
                <div className="flex flex-wrap gap-1 mt-3">
                  {idx.legs.slice(0, 5).map((l) => (
                    <span key={l.address} className="pill text-[10px]">
                      {shortAddr(l.address)}<span className="text-accent ml-1">{(l.weight * 100).toFixed(0)}%</span>
                    </span>
                  ))}
                  {idx.legs.length > 5 && <span className="pill text-[10px] text-muted">+{idx.legs.length - 5}</span>}
                </div>

                <div className="mt-auto pt-3 flex items-center gap-2">
                  <span className="btn-primary !py-1 text-[11px]">view & fork →</span>
                  {isOwner && (
                    <button onClick={(e) => onDelete(e, idx.id)}
                      className="ml-auto text-[11px] text-loss hover:underline">delete</button>
                  )}
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
