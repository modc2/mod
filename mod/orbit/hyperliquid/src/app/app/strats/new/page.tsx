"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  autoIndex, createIndex, getIndex, fetchTopTraders, IndexLeg, TopTrader, fmtPnl, shortAddr, fmtPct,
} from "../../lib/api";
import { useWallet } from "../../lib/wallet";

export default function NewStratPage() {
  return <Suspense fallback={<div className="text-xs text-muted">loading…</div>}><Inner /></Suspense>;
}

function Inner() {
  const router = useRouter();
  const sp = useSearchParams();
  const { address } = useWallet();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [days, setDays] = useState(Number(sp.get("days") || 7));
  const [notionalPct, setNotionalPct] = useState(50);
  const [legs, setLegs] = useState<IndexLeg[]>(() => {
    const seed = sp.get("seed") || "";
    if (!seed) return [];
    const addrs = seed.split(",").map((s) => s.trim()).filter(Boolean);
    const w = 1 / addrs.length;
    return addrs.map((a) => ({ address: a, weight: w }));
  });
  const [pool, setPool] = useState<TopTrader[]>([]);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Fork: ?fork=<id> pre-loads another strat's legs/name/window.
  useEffect(() => {
    const fork = sp.get("fork");
    if (!fork) return;
    getIndex(fork).then((i) => {
      setName(`${i.name} (fork)`);
      setDescription(i.description);
      setDays(i.days_window || 7);
      setNotionalPct(i.notional_pct || 50);
      setLegs(i.legs);
    }).catch(() => {});
  }, [sp]);

  useEffect(() => {
    fetchTopTraders(days, 1, 150)
      .then((r) => setPool(r.traders))
      .catch(() => setPool([]));
  }, [days]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return pool.filter((t) =>
      !legs.find((l) => l.address === t.address) &&
      (!q || t.address.includes(q) || t.coins.some((c) => c.toLowerCase().includes(q)))
    ).slice(0, 25);
  }, [pool, search, legs]);

  const totalWeight = legs.reduce((s, l) => s + l.weight, 0);

  const addLeg = (a: string) => setLegs((p) => [...p, { address: a, weight: 0.1 }]);
  const removeLeg = (a: string) => setLegs((p) => p.filter((l) => l.address !== a));
  const setLegWeight = (a: string, w: number) =>
    setLegs((p) => p.map((l) => (l.address === a ? { ...l, weight: w } : l)));
  const equalize = () => {
    if (legs.length === 0) return;
    const w = 1 / legs.length;
    setLegs((p) => p.map((l) => ({ ...l, weight: w })));
  };
  const proportional = async () => {
    const top = pool.filter((t) => legs.find((l) => l.address === t.address));
    const totalPnl = top.filter((t) => t.pnl > 0).reduce((s, t) => s + t.pnl, 0);
    if (totalPnl <= 0) { equalize(); return; }
    setLegs(top.map((t) => ({ address: t.address, weight: t.pnl > 0 ? t.pnl / totalPnl : 0 })));
  };
  const auto = async () => {
    try {
      const r = await autoIndex({ days, top: 10, pool: 200 });
      setLegs(r.legs);
    } catch (e: any) { setErr(e.message ?? String(e)); }
  };

  const save = async () => {
    if (!address) { setErr("connect wallet first"); return; }
    if (!name.trim()) { setErr("name required"); return; }
    if (legs.length === 0) { setErr("add at least one trader"); return; }
    setBusy(true); setErr(null);
    try {
      const idx = await createIndex({
        name: name.trim(),
        description: description.trim(),
        owner: address,
        legs,
        days_window: days,
        notional_pct: notionalPct,
      });
      router.push(`/strats/${idx.id}`);
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-display font-bold text-2xl tracking-tight text-gradient">NEW STRAT</h1>
          <p className="text-xs text-muted mt-1">
            Pick traders, weight them into a basket, and publish it to the community. Optionally bind a private vault.
          </p>
        </div>
        <button className="btn" onClick={auto}>auto-build (top 10)</button>
      </div>

      <div className="grid md:grid-cols-[1.4fr_1fr] gap-4">
        {/* Form */}
        <div className="panel p-5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="name">
              <input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="window">
              <select className="input w-full" value={days}
                onChange={(e) => setDays(Number(e.target.value))}>
                {[1, 7, 30].map((d) => <option key={d} value={d}>{d}d</option>)}
              </select>
            </Field>
          </div>
          <Field label="description">
            <input className="input w-full" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <Field label={`vault notional % per signal (${notionalPct}%)`}>
            <input type="range" min={0} max={100} step={5}
              value={notionalPct} onChange={(e) => setNotionalPct(Number(e.target.value))}
              className="w-full" />
          </Field>

          <div className="border-t border-border pt-3">
            <div className="flex items-center justify-between mb-2">
              <div className="label !mb-0">traders ({legs.length})</div>
              <div className="flex gap-1">
                <button className="btn" onClick={equalize} type="button">equal weights</button>
                <button className="btn" onClick={proportional} type="button">∝ pnl</button>
              </div>
            </div>
            {legs.length === 0 ? (
              <div className="text-xs text-muted">no traders yet — add from the right panel</div>
            ) : (
              <div className="space-y-1">
                {legs.map((l) => (
                  <div key={l.address}
                    className="flex items-center gap-2 py-1.5 border-b border-border/60">
                    <span className="text-accent2 text-xs num">{shortAddr(l.address)}</span>
                    <input className="input w-24 num" type="number" min={0} max={1} step={0.01}
                      value={l.weight}
                      onChange={(e) => setLegWeight(l.address, Number(e.target.value))} />
                    <span className="text-[11px] text-muted">{(l.weight * 100).toFixed(1)}%</span>
                    <button className="ml-auto text-loss text-xs hover:underline"
                      onClick={() => removeLeg(l.address)}>remove</button>
                  </div>
                ))}
                <div className="flex justify-end text-[11px] text-muted pt-1">
                  total: <span className={`num ml-2 ${Math.abs(totalWeight - 1) < 0.001 ? "text-win" : "text-warn"}`}>
                    {(totalWeight * 100).toFixed(1)}%
                  </span> <span className="ml-1">(saved as normalised)</span>
                </div>
              </div>
            )}
          </div>

          {err && <div className="text-loss text-xs">{err}</div>}
          <button className="btn-primary w-full" onClick={save} disabled={busy}>
            {busy ? "saving…" : "publish strat"}
          </button>
        </div>

        {/* Pool / picker */}
        <div className="panel p-4">
          <div className="flex items-center gap-2 mb-3">
            <input className="input flex-1" placeholder="search address or coin"
              value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="space-y-1 max-h-[60vh] overflow-y-auto">
            {filtered.map((t) => (
              <button key={t.address} onClick={() => addLeg(t.address)}
                className="w-full text-left px-2 py-2 hover:bg-panel2 rounded flex items-center gap-3">
                <span className="text-accent2 text-xs num">{shortAddr(t.address)}</span>
                <span className={`text-xs num ${t.pnl >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(t.pnl)}</span>
                <span className="text-[11px] text-muted ml-auto">
                  {t.win_rate < 0 ? "—" : fmtPct(t.win_rate, 0)} · {t.trades}t
                </span>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="text-xs text-muted px-2">no matches</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="label">{label}</div>{children}</div>;
}
