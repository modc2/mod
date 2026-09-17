"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  listIndexes, stratsBoard, deleteIndex, indexPerf,
  Index, StratRow, ago, shortAddr, fmtPnl, fmtUsd, fmtApr,
} from "../lib/api";
import { useWallet } from "../lib/wallet";
import { Field, Freshness, Identicon, Kpi, PageHead, Switch } from "../components/BoardBits";
import StratSpark, { type SparkLeg } from "../components/StratSpark";
import { useCurves } from "../lib/curves";

// Per-basket weighted PnL, loaded lazily after the list renders.
type Perf = { weighted_pnl: number; days: number } | "loading" | "err";

type Kind = "all" | "basket" | "vault" | "trader";
const KINDS: { key: Kind; label: string }[] = [
  { key: "all", label: "ALL" },
  { key: "basket", label: "BASKETS" },
  { key: "vault", label: "VAULTS" },
  { key: "trader", label: "TRADERS" },
];

const aprTone = (n: number | null | undefined) =>
  n == null ? "text-dim" : n >= 0 ? "text-win" : "text-loss";

// The chart window. HL prices portfolio history in day / week / month / all,
// so 1 / 7 / 30 are the windows that land exactly; anything else is drawn from
// the nearest period that contains it (curve.rs::period_for_days), which the
// blurb says out loud rather than pretending a 14d line is 14 days of samples.
const DAY_OPTIONS = [1, 7, 30];
const MAX_DAYS = 90;
const DAYS_KEY = "hl.strats.days";
const nearestPeriod = (d: number) => (d <= 1 ? "day" : d <= 7 ? "week" : d <= 30 ? "month" : "all-time");

/** When this row last put a trade on, as precisely as we actually know it.
 *  No timestamp is not "never": the board only lists wallets that traded
 *  inside 24h, so that gate is what we say instead of inventing a minute. */
function lastTraded(r: StratRow): { text: string; title: string } {
  if (r.last_trade_ms) {
    return {
      text: `traded ${ago(r.last_trade_ms)}`,
      title: `last fill seen ${new Date(r.last_trade_ms).toLocaleString()}`,
    };
  }
  if (r.kind === "trader") {
    return {
      text: "traded within 24h",
      title: "This wallet is on the board because it traded in the last 24h — we have not scanned its fills for the exact minute yet.",
    };
  }
  return { text: "last trade unknown", title: "No fills scanned for this account yet." };
}

/** Which wallets a card's line is made of: one for a trader or a vault, the
 *  weighted legs for a basket. */
function sparkLegs(r: StratRow, idx: Index | undefined): SparkLeg[] {
  if (r.kind === "basket") return idx ? idx.legs.map((l) => ({ address: l.address, weight: l.weight })) : [];
  return [{ address: r.id, weight: 1 }];
}

/** Where a row opens: every strat is a real page somewhere in the app. */
const hrefFor = (r: StratRow) =>
  r.kind === "basket" ? `/strats/${r.id}`
  : r.kind === "vault" ? `/vaults/${r.id}`
  : `/trader/${r.id}`;

export default function StratsPage() {
  const { address } = useWallet();
  const [rows, setRows] = useState<StratRow[]>([]);
  const [indexes, setIndexes] = useState<Record<string, Index>>({});
  const [perf, setPerf] = useState<Record<string, Perf>>({});
  const [updatedMs, setUpdatedMs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [mine, setMine] = useState(false);
  const [kind, setKind] = useState<Kind>("all");
  // The chart window, chosen at the top of the board and remembered.
  const [days, setDays] = useState(7);
  const [dayDraft, setDayDraft] = useState("");
  useEffect(() => {
    const saved = parseInt(localStorage.getItem(DAYS_KEY) || "", 10);
    if (Number.isFinite(saved) && saved >= 1 && saved <= MAX_DAYS) {
      setDays(saved);
      if (!DAY_OPTIONS.includes(saved)) setDayDraft(String(saved));
    }
  }, []);
  const applyDays = (n: number) => {
    const d = Math.min(MAX_DAYS, Math.max(1, n));
    setDays(d);
    try { localStorage.setItem(DAYS_KEY, String(d)); } catch {}
  };

  const load = async () => {
    setLoading(true);
    try {
      // The board is one call; the basket objects ride along for legs,
      // descriptions and ownership (delete), and the per-basket weighted-PnL
      // fan-out stays as before — cheap, server-cached, non-blocking.
      const [board, list] = await Promise.all([stratsBoard(), listIndexes()]);
      setRows(board.rows);
      setUpdatedMs(board.updated_ms);
      const byId = Object.fromEntries(list.indexes.map((i) => [i.id, i]));
      setIndexes(byId);
      setPerf(Object.fromEntries(list.indexes.map((i) => [i.id, "loading" as Perf])));
      list.indexes.forEach((i) => {
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
    const out = rows.filter((r) => {
      if (kind !== "all" && r.kind !== kind) return false;
      if (mine) {
        // "mine" means baskets I composed — vaults and traders are nobody's.
        if (r.kind !== "basket" || !address || r.by.toLowerCase() !== address.toLowerCase()) return false;
      }
      if (!q) return true;
      const idx = r.kind === "basket" ? indexes[r.id] : undefined;
      return r.name.toLowerCase().includes(q)
        || r.by.toLowerCase().includes(q)
        || r.id.toLowerCase().includes(q)
        || (idx ? idx.description.toLowerCase().includes(q)
             || idx.legs.some((l) => l.address.toLowerCase().includes(q)) : false);
    });
    // The board's default order IS the metric: trailing 7d APR, unmeasured
    // rows last. Within equal footing, more capital first.
    out.sort((a, b) => {
      const av = a.apr_7d ?? -Infinity, bv = b.apr_7d ?? -Infinity;
      if (av !== bv) return bv - av;
      return b.capital - a.capital;
    });
    return out;
  }, [rows, indexes, search, mine, kind, address]);

  // Every wallet the visible cards draw, deduped: a trader or a vault row is
  // one wallet, a basket brings its legs. The shared curve cache pages these
  // and holds them, so re-filtering or re-sorting a drawn board costs nothing.
  const curveAddrs = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    for (const r of filtered) {
      for (const l of sparkLegs(r, r.kind === "basket" ? indexes[r.id] : undefined)) {
        const a = l.address.toLowerCase();
        if (!seen.has(a)) { seen.add(a); out.push(a); }
      }
    }
    return out;
  }, [filtered, indexes]);
  const curves = useCurves(curveAddrs, days);

  const stats = useMemo(() => {
    const measured = rows.filter((r) => r.apr_7d != null);
    const best = measured.reduce<StratRow | null>(
      (b, r) => (b === null || (r.apr_7d as number) > (b.apr_7d as number) ? r : b), null);
    const green = measured.filter((r) => (r.apr_7d as number) >= 0).length;
    const counts = { basket: 0, vault: 0, trader: 0 } as Record<string, number>;
    rows.forEach((r) => { counts[r.kind] = (counts[r.kind] || 0) + 1; });
    const traders = new Set(Object.values(indexes).flatMap((i) => i.legs.map((l) => l.address.toLowerCase())));
    return { best, green, measured: measured.length, counts, traders: traders.size };
  }, [rows, indexes]);

  return (
    <div className="space-y-5">
      <PageHead
        title="STRATS"
        blurb={<>Everything you can put money behind — composed baskets, Hyperliquid vaults, and
          copyable traders — on one board. APR is trailing: what a deposit made 24h or 7d ago
          would have annualized to, and every card draws its own PnL over the last {days} days
          {!DAY_OPTIONS.includes(days) && <> (sampled from Hyperliquid&apos;s {nearestPeriod(days)} history, the
            nearest period that contains a {days}d window)</>}. Open any card to invest or fork.</>}
        right={<>
          <Freshness loading={loading} label={updatedMs ? `updated ${ago(updatedMs)}` : `${rows.length} strats`} />
          <Link href="/strats/new" className="btn-primary ml-2">+ new strat</Link>
        </>}
      />

      {/* Board stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="strats on the board" value={rows.length}
          sub={`${stats.counts.basket} baskets · ${stats.counts.vault} vaults · ${stats.counts.trader} traders`} />
        <Kpi label="best 7d apr"
          value={stats.best ? fmtApr(stats.best.apr_7d) : "—"}
          tone={stats.best ? ((stats.best.apr_7d as number) >= 0 ? "win" : "loss") : undefined}
          sub={stats.best ? <>{stats.best.kind} · {stats.best.name || shortAddr(stats.best.id)}</>
            : rows.length ? "scoring…" : "nothing to score yet"} />
        <Kpi label="in the green"
          value={stats.measured ? `${stats.green}/${stats.measured}` : "—"}
          sub="positive trailing 7d apr, measured rows" />
        <Kpi label="traders copied" value={stats.traders}
          sub="unique wallets across all baskets" />
      </div>

      {/* Filter bar */}
      <div className="panel p-3 flex flex-wrap items-end gap-3">
        <Field label="chart window" title="How many days of PnL every card draws — 1 / 7 / 30 land on Hyperliquid's own periods, or type any window up to 90 days">
          <div className="seg">
            {DAY_OPTIONS.map((d) => (
              <button key={d} onClick={() => { applyDays(d); setDayDraft(""); }}
                className={`seg-btn ${days === d ? "seg-btn-active" : ""}`}>{d}d</button>
            ))}
            <input
              className={`seg-btn w-12 bg-transparent outline-none text-center font-mono
                ${!DAY_OPTIONS.includes(days) ? "seg-btn-active" : ""}`}
              placeholder="n d" inputMode="numeric"
              title={`Any window, 1–${MAX_DAYS} days — enter to apply.`}
              value={dayDraft}
              onChange={(e) => setDayDraft(e.target.value.replace(/[^0-9]/g, ""))}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                const n = parseInt(dayDraft, 10);
                if (Number.isFinite(n)) applyDays(n);
              }}
              onBlur={() => {
                const n = parseInt(dayDraft, 10);
                if (Number.isFinite(n)) applyDays(n);
              }} />
          </div>
        </Field>
        <div className="flex items-center gap-1 pb-1">
          {KINDS.map((k) => (
            <button key={k.key} onClick={() => setKind(k.key)}
              className={`pill transition-colors ${kind === k.key
                ? "border-accent/60 text-accent" : "text-muted hover:text-ink"}`}>
              {k.label}
            </button>
          ))}
        </div>
        <input className="input flex-1 min-w-[20ch]" placeholder="FILTER BY NAME, OWNER, OR ADDRESS…"
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="flex items-center gap-3 pb-1">
          <Switch on={mine} onChange={setMine} label="mine only" />
          <span className="text-[10px] text-muted uppercase tracking-wider">{filtered.length} shown</span>
        </div>
      </div>

      {/* Grid */}
      {loading && rows.length === 0 ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {[...Array(6)].map((_, i) => <div key={i} className="panel h-44 skeleton" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="panel p-8 text-center text-xs text-muted">
          no strats {mine ? "of yours " : ""}here — <Link href="/strats/new" className="text-accent2">compose one →</Link>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((r) => {
            const idx = r.kind === "basket" ? indexes[r.id] : undefined;
            const p = idx ? perf[r.id] : undefined;
            const pnl = p && p !== "loading" && p !== "err" ? p.weighted_pnl : null;
            const isOwner = idx && address && idx.owner.toLowerCase() === address.toLowerCase();
            const partial = r.kind === "basket" && r.legs_priced < r.legs;
            const traded = lastTraded(r);
            const legsForSpark = sparkLegs(r, idx);
            return (
              <Link key={`${r.kind}:${r.id}`} href={hrefFor(r)}
                className="group relative flex flex-col rounded-lg border border-white/[0.07] bg-gradient-to-b from-white/[0.025] to-transparent p-4 transition-all hover:border-accent/40 hover:shadow-glow">
                {/* top accent bar */}
                <span className="absolute inset-x-0 top-0 h-px bg-accent-grad opacity-0 group-hover:opacity-80 transition-opacity" />

                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-ink font-medium truncate">{r.name || shortAddr(r.id)}</div>
                    <div className="text-[10px] uppercase tracking-wider text-muted mt-0.5 flex items-center gap-1.5">
                      <Identicon address={r.by} size={13} />
                      {r.kind === "basket" ? <>by {shortAddr(r.by)} · {idx ? ago(idx.created_ms) : `${r.age_days}d`}</>
                        : r.kind === "vault" ? <>led by {shortAddr(r.by)} · {r.age_days}d old</>
                        : <>trades own book</>}
                    </div>
                  </div>
                  {r.kind === "basket"
                    ? (r.vault_address
                        ? <span className="pill border-accent/40 text-accent shrink-0">vault</span>
                        : <span className="pill text-muted shrink-0">{r.legs} legs</span>)
                    : <span className={`pill shrink-0 ${r.kind === "vault" ? "border-accent2/40 text-accent2" : "text-muted"}`}>{r.kind}</span>}
                </div>

                {/* headline: trailing APR pair — the one metric every kind shares */}
                <div className="mt-4 flex items-end gap-4">
                  <div>
                    <span className={`num text-2xl font-semibold ${aprTone(r.apr_7d)}`}>{fmtApr(r.apr_7d)}</span>
                    <span className="block text-[10px] uppercase tracking-wider text-muted mt-0.5">apr · if invested 7d ago</span>
                  </div>
                  <div className="mb-0.5">
                    <span className={`num text-base font-medium ${aprTone(r.apr_24h)}`}>{fmtApr(r.apr_24h)}</span>
                    <span className="block text-[10px] uppercase tracking-wider text-muted mt-0.5">24h</span>
                  </div>
                </div>

                {/* kind-specific body */}
                {idx ? (
                  <>
                    <div className="text-[11px] text-muted mt-2 flex items-center gap-2">
                      {p === "loading" ? <span className="skeleton inline-block h-3 w-16" /> : pnl !== null && (
                        <span className={pnl >= 0 ? "text-win" : "text-loss"}>
                          {fmtPnl(pnl)} <span className="text-muted">weighted · {idx.days_window}d</span>
                        </span>
                      )}
                      {partial && <span className="text-dim">apr on {r.legs_priced}/{r.legs} legs</span>}
                    </div>
                    {idx.description && (
                      <div className="text-xs text-muted mt-2 line-clamp-2">{idx.description}</div>
                    )}
                    <div className="mt-3 space-y-1.5">
                      <div className="flex items-center">
                        {idx.legs.slice(0, 8).map((l, i) => (
                          <span key={l.address} title={`${shortAddr(l.address)} · ${(l.weight * 100).toFixed(0)}%`}
                            className="rounded-full ring-2 ring-bg" style={{ marginLeft: i ? -5 : 0 }}>
                            <Identicon address={l.address} size={18} />
                          </span>
                        ))}
                        {idx.legs.length > 8 && (
                          <span className="ml-1.5 text-[10px] text-muted">+{idx.legs.length - 8}</span>
                        )}
                      </div>
                      <div className="flex h-1 w-full overflow-hidden rounded-full bg-white/[0.06]" aria-hidden>
                        {idx.legs.slice(0, 8).map((l, i) => (
                          <span key={l.address} className="h-full bg-accent"
                            style={{ width: `${Math.max(2, l.weight * 100)}%`, opacity: 1 - i * 0.1, marginLeft: i ? 1 : 0 }} />
                        ))}
                      </div>
                    </div>
                    <div className="text-[11px] text-muted mt-2" title={traded.title}>
                      <span className="text-dim">last leg trade · </span>{traded.text.replace("traded ", "")}
                    </div>
                  </>
                ) : (
                  <div className="text-[11px] text-muted mt-2" title={traded.title}>
                    {fmtUsd(r.capital)}{" "}
                    <span className="text-dim">{r.kind === "vault" ? "tvl" : "equity"} · {traded.text}</span>
                  </div>
                )}

                {/* the shape behind the number: this row's PnL over the chosen window */}
                <StratSpark legs={legsForSpark} days={days} curves={curves} />

                <div className="mt-auto pt-3 flex items-center gap-2">
                  <span className="btn-ghost !py-1 text-[11px]">
                    {r.kind === "basket" ? "view & fork →" : r.kind === "vault" ? "open vault →" : "open trader →"}
                  </span>
                  {isOwner && (
                    <button onClick={(e) => onDelete(e, r.id)}
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
