"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  stratsBoard, StratRow, ago, shortAddr, fmtUsd, fmtApr,
} from "../lib/api";
import { Field, Freshness, Identicon, Kpi, PageHead } from "../components/BoardBits";
import StratSpark, { type SparkLeg } from "../components/StratSpark";
import { useCurves } from "../lib/curves";

// Two strat types for now — copy a trader, or an HL vault. More come later.
type Kind = "all" | "trader" | "vault";
const KINDS: { key: Kind; label: string }[] = [
  { key: "all", label: "ALL" },
  { key: "trader", label: "COPY TRADERS" },
  { key: "vault", label: "VAULTS" },
];
const kindLabel = (k: StratRow["kind"]) => (k === "trader" ? "copy trader" : "vault");

const tone = (n: number | null | undefined) =>
  n == null ? "text-dim" : n >= 0 ? "text-win" : "text-loss";

/** A raw window return as a ratio (1.02 = +102%), printed as the percent
 *  people read — fmtApr already knows "—" for null and k-notation. */
const fmtRoi = (r: number | null | undefined) => fmtApr(r == null ? null : r * 100);

/** The rec score is a product of ratios, so it lives on a wild scale —
 *  0.0004 and 40 are both real values. Sig-figs, not fixed decimals. */
const fmtScore = (s: number | null | undefined): string => {
  if (s == null) return "—";
  const a = Math.abs(s);
  const sign = s < 0 ? "-" : "";
  if (a >= 100) return sign + a.toFixed(0);
  if (a >= 1) return sign + a.toFixed(2);
  if (a >= 0.0001) return sign + a.toFixed(4);
  return sign + a.toExponential(1);
};

/** Recommendation order: score desc, unscored rows fall back to 7d APR
 *  footing, capital breaks ties. Mirrors the server's own sort so filtering
 *  client-side can't reshuffle the board. */
const byRec = (a: StratRow, b: StratRow) => {
  const as = a.rec_score ?? -Infinity, bs = b.rec_score ?? -Infinity;
  if (as !== bs) return bs - as;
  const a7 = a.apr_7d ?? -Infinity, b7 = b.apr_7d ?? -Infinity;
  if (a7 !== b7) return b7 - a7;
  return b.capital - a.capital;
};

// The chart window. HL prices portfolio history in day / week / month / all,
// so 1 / 7 / 30 are the windows that land exactly; anything else is drawn from
// the nearest period that contains it (curve.rs::period_for_days), which the
// blurb says out loud rather than pretending a 14d line is 14 days of samples.
const DAY_OPTIONS = [1, 7, 30];
const MAX_DAYS = 90;
const DAYS_KEY = "hl.strats.days";
const nearestPeriod = (d: number) => (d <= 1 ? "day" : d <= 7 ? "week" : d <= 30 ? "month" : "all-time");

/** When this row last put a trade on, as precisely as we actually know it.
 *
 *  Three honest states, never a fourth invented one:
 *   - a scanned fill  → the minute, plus when we looked, in the tooltip;
 *   - no scan yet     → the board's own liveness gate ("within 24h") for a
 *                       trader row, and plain "unknown" for a vault;
 *   - a scanned fill older than a day on a row the leaderboard calls active →
 *     still the scanned fill, flagged, because the fills are the evidence and
 *     the leaderboard's day volume is the claim. */
function lastTraded(r: StratRow): { text: string; title: string; stale: boolean } {
  if (r.last_trade_ms) {
    const seen = r.last_trade_scanned_ms ? ` · fills scanned ${ago(r.last_trade_scanned_ms)}` : "";
    const quiet = r.kind === "trader" && Date.now() - r.last_trade_ms > 86_400_000;
    return {
      text: `traded ${ago(r.last_trade_ms)}`,
      title: `last fill we have seen: ${new Date(r.last_trade_ms).toLocaleString()}${seen}`
        + (quiet ? " — Hyperliquid's leaderboard still counts day volume for this wallet, but no fill has landed in our scan since." : ""),
      stale: quiet,
    };
  }
  if (r.kind === "trader") {
    return {
      text: "traded within 24h",
      title: "On the board because Hyperliquid reports day volume for this wallet — its fills have not been scanned for the exact minute yet.",
      stale: false,
    };
  }
  return { text: "last trade unknown", title: "No fills scanned for this account yet.", stale: false };
}

/** Every strat is one wallet now — a trader's own book or the vault account. */
const sparkLegs = (r: StratRow): SparkLeg[] => [{ address: r.id, weight: 1 }];

/** Where a row opens: every strat is a real page somewhere in the app. */
const hrefFor = (r: StratRow) =>
  r.kind === "vault" ? `/vaults/${r.id}` : `/trader/${r.id}`;

/** The three window returns whose product is the score, always in the same
 *  order the formula multiplies them. */
function RoiTrio({ r, compact = false }: { r: StratRow; compact?: boolean }) {
  const cells: [string, number | null][] = [["1d", r.roi_1d], ["7d", r.roi_7d], ["30d", r.roi_30d]];
  return (
    <span className={`inline-flex items-baseline ${compact ? "gap-2" : "gap-3"}`}>
      {cells.map(([w, v]) => (
        <span key={w} className="whitespace-nowrap">
          <span className="text-[10px] uppercase tracking-wider text-dim mr-1">{w}</span>
          <span className={`num ${compact ? "text-[11px]" : "text-xs"} ${tone(v)}`}>{fmtRoi(v)}</span>
        </span>
      ))}
    </span>
  );
}

export default function StratsPage() {
  const [rows, setRows] = useState<StratRow[]>([]);
  const [updatedMs, setUpdatedMs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
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
      const board = await stratsBoard();
      setRows(board.rows);
      setUpdatedMs(board.updated_ms);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (kind !== "all" && r.kind !== kind) return false;
      if (!q) return true;
      return r.name.toLowerCase().includes(q)
        || r.by.toLowerCase().includes(q)
        || r.id.toLowerCase().includes(q);
    });
    out.sort(byRec);
    return out;
  }, [rows, search, kind]);

  // The recommendation shelf: the best fully-scored strats across BOTH types,
  // untouched by the search box so it always answers "what looks good now".
  const recommended = useMemo(
    () => rows.filter((r) => r.rec_score != null).sort(byRec).slice(0, 5),
    [rows],
  );

  // Every wallet the visible cards draw, deduped. The shared curve cache
  // pages these and holds them, so re-filtering a drawn board costs nothing.
  const curveAddrs = useMemo(() => {
    const seen = new Set<string>();
    for (const r of filtered) seen.add(r.id.toLowerCase());
    return [...seen];
  }, [filtered]);
  const curves = useCurves(curveAddrs, days);

  const stats = useMemo(() => {
    const scored = rows.filter((r) => r.rec_score != null);
    const top = scored.slice().sort(byRec)[0] ?? null;
    const green = scored.filter((r) => (r.rec_score as number) > 0).length;
    const counts = { vault: 0, trader: 0 } as Record<string, number>;
    rows.forEach((r) => { counts[r.kind] = (counts[r.kind] || 0) + 1; });
    return { top, green, scored: scored.length, counts };
  }, [rows]);

  return (
    <div className="space-y-5">
      <PageHead
        title="STRATS"
        blurb={<>Two strat types for now — <b>copy a trader</b> or an <b>HL vault</b> — more coming.
          Recommendations rank by the product of the trailing 1d, 7d and 30d returns as ratios
          (+102% is 1.02, −20% is −0.2): a strat has to be green across every horizon to score.
          APR is trailing — what a deposit made 24h or 7d ago would have annualized to — and every
          card draws its own PnL over the last {days} days
          {!DAY_OPTIONS.includes(days) && <> (sampled from Hyperliquid&apos;s {nearestPeriod(days)} history, the
            nearest period that contains a {days}d window)</>}. Open any card to invest.</>}
        right={<Freshness loading={loading} label={updatedMs ? `updated ${ago(updatedMs)}` : `${rows.length} strats`} />}
      />

      {/* Board stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="strats on the board" value={rows.length}
          sub={`${stats.counts.trader} copy traders · ${stats.counts.vault} vaults`} />
        <Kpi label="top rec score"
          value={stats.top ? fmtScore(stats.top.rec_score) : "—"}
          tone={stats.top ? ((stats.top.rec_score as number) >= 0 ? "win" : "loss") : undefined}
          sub={stats.top ? <>{kindLabel(stats.top.kind)} · {stats.top.name || shortAddr(stats.top.id)}</>
            : rows.length ? "scoring…" : "nothing to score yet"} />
        <Kpi label="in the green"
          value={stats.scored ? `${stats.green}/${stats.scored}` : "—"}
          sub="positive 1d × 7d × 30d, scored strats" />
        <Kpi label="scored" value={rows.length ? `${stats.scored}/${rows.length}` : "—"}
          sub="strats with all three windows measured" />
      </div>

      {/* Recommended: the score made legible — the factors sit next to the product */}
      {recommended.length > 0 && (
        <div className="panel p-3">
          <div className="flex items-baseline justify-between gap-2 px-1 pb-2">
            <span className="eyebrow">recommended</span>
            <span className="text-[10px] uppercase tracking-wider text-dim">
              score = 1d × 7d × 30d roi, as ratios
            </span>
          </div>
          <div className="divide-y divide-white/[0.05]">
            {recommended.map((r, i) => (
              <Link key={`${r.kind}:${r.id}`} href={hrefFor(r)}
                className="group flex flex-wrap items-center gap-x-3 gap-y-1 px-1 py-2 transition-colors hover:bg-white/[0.03] rounded">
                <span className={`num w-6 text-center text-xs ${i === 0 ? "text-accent font-semibold" : "text-muted"}`}>
                  #{i + 1}
                </span>
                <Identicon address={r.by} size={16} />
                <span className="text-ink text-sm font-medium truncate max-w-[18ch]">
                  {r.name || shortAddr(r.id)}
                </span>
                <span className={`pill shrink-0 ${r.kind === "vault" ? "border-accent2/40 text-accent2" : "text-muted"}`}>
                  {kindLabel(r.kind)}
                </span>
                <span className="ml-auto"><RoiTrio r={r} compact /></span>
                <span className="w-20 text-right" title="rec score: the three window returns multiplied as ratios">
                  <span className={`num text-sm font-semibold ${tone(r.rec_score)}`}>{fmtScore(r.rec_score)}</span>
                </span>
                <span className="btn-ghost !py-0.5 text-[10px] shrink-0">open →</span>
              </Link>
            ))}
          </div>
        </div>
      )}

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
        <input className="input flex-1 min-w-[20ch]" placeholder="FILTER BY NAME, LEADER, OR ADDRESS…"
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <span className="text-[10px] text-muted uppercase tracking-wider pb-1">{filtered.length} shown</span>
      </div>

      {/* Grid — already in recommendation order */}
      {loading && rows.length === 0 ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {[...Array(6)].map((_, i) => <div key={i} className="panel h-44 skeleton" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="panel p-8 text-center text-xs text-muted">no strats match this filter</div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((r) => {
            const traded = lastTraded(r);
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
                      {r.kind === "vault" ? <>led by {shortAddr(r.by)} · {r.age_days}d old</>
                        : <>trades own book</>}
                    </div>
                  </div>
                  <span className={`pill shrink-0 ${r.kind === "vault" ? "border-accent2/40 text-accent2" : "text-muted"}`}>
                    {kindLabel(r.kind)}
                  </span>
                </div>

                {/* headline: the rec score, with the trailing 7d APR beside it */}
                <div className="mt-4 flex items-end gap-4">
                  <div title="rec score: trailing 1d × 7d × 30d returns multiplied as ratios — null when any window is unmeasured">
                    <span className={`num text-2xl font-semibold ${tone(r.rec_score)}`}>{fmtScore(r.rec_score)}</span>
                    <span className="block text-[10px] uppercase tracking-wider text-muted mt-0.5">rec · 1d×7d×30d roi</span>
                  </div>
                  <div className="mb-0.5">
                    <span className={`num text-base font-medium ${tone(r.apr_7d)}`}>{fmtApr(r.apr_7d)}</span>
                    <span className="block text-[10px] uppercase tracking-wider text-muted mt-0.5">7d apr</span>
                  </div>
                </div>

                {/* the score's three factors, in the order they multiply */}
                <div className="mt-2"><RoiTrio r={r} /></div>

                <div className="text-[11px] text-muted mt-2" title={traded.title}>
                  {fmtUsd(r.capital)}{" "}
                  <span className="text-dim">{r.kind === "vault" ? "tvl" : "equity"} · </span>
                  <span className={traded.stale ? "text-loss/80" : "text-dim"}>{traded.text}</span>
                  {traded.stale && <span className="text-dim" title={traded.title}> ⚠</span>}
                </div>

                {/* the shape behind the number: this row's PnL over the chosen window */}
                <StratSpark legs={sparkLegs(r)} days={days} curves={curves} />

                <div className="mt-auto pt-3 flex items-center gap-2">
                  <span className="btn-ghost !py-1 text-[11px]">
                    {r.kind === "vault" ? "open vault →" : "open trader →"}
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
