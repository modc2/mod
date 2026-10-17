"use client";

// SCOUT — the background agent hunting the most profitable trader to copy.
//
// The server (scout.rs) runs every 15 minutes: it ranks the 7d board by
// expected profit per day per $1k copied (expect.rs), backtests the top 12
// with $1,000 at 1 / 7 / 30 days, and scores each by the LOWER of the two
// answers — so a wallet only leads here when its fills and its equity curve
// agree it makes money. Every number is NET of the copy's fees (a copy pays
// the 0.045% taker fee on every fill it mirrors), and each pick carries its
// $1,000 backtest curve — net solid, before-fees dashed. This panel is a read of that report; "run now" asks
// for a fresh pass (the server allows one per 5 minutes).

import Link from "next/link";
import { useEffect, useState } from "react";
import { scout, ScoutPick, ScoutState, ago, fmtUsd, shortAddr } from "../lib/api";
import { Identicon } from "./BoardBits";

const OPEN_KEY = "hl.scout.open";
const CAP_KEY = "hl.scout.capital";
/** Stepper stops for the "$ copied" control; free-typed values are kept too. */
const STEPS = [100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];
/** Every row is its own grid, so columns must be fixed-width or they drift
 *  row to row with their content (that was the misalignment). */
const GRID = "grid grid-cols-[20px_minmax(120px,1.3fr)_88px_64px_150px_120px_minmax(200px,1.6fr)_56px_56px] items-center gap-3";

const fmtDay = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : "-"}$${Math.abs(n) >= 100 ? Math.round(Math.abs(n)).toLocaleString("en-US") : Math.abs(n).toFixed(2)}`;
const fmtCap = (n: number) => `$${n >= 1000 && n % 1000 === 0 ? `${n / 1000}k` : n.toLocaleString("en-US")}`;

// Standard normal CDF (Abramowitz–Stegun 7.1.26), enough for a percent.
const phi = (z: number) => {
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z / 2);
  return z >= 0 ? (1 + y) / 2 : (1 - y) / 2;
};

/** Probability of profit, 0–100: the chance a copy is up after `days`.
 *  Two reads, and — like the scout's own number — the LOWER one wins:
 *   · model: each close wins avg_win at the defensible win rate (win_lo) or
 *     loses avg_loss. Closes within a day are correlated, so a day counts as
 *     ONE draw: PoP = Φ(edge/sd · √days). (Counting every close as
 *     independent pins every wallet at 99%.)
 *   · backtest: share of trusted $1k windows that finished green, Laplace-
 *     smoothed — (green+1)/(trusted+2), so 0 windows reads 50%, 2/2 reads 75%.
 *  Capital-independent: a copy is proportional. */
function pop(p: ScoutPick, days = 7): number | null {
  const m = p.model;
  if (!m || !(m.avg_win > 0 || m.avg_loss > 0)) return null;
  const q = Math.min(Math.max(m.win_lo, 0), 1);
  const w = Math.abs(m.avg_win), l = Math.abs(m.avg_loss);
  const mean = q * w - (1 - q) * l;
  const sd = Math.sqrt(Math.max(q * w * w + (1 - q) * l * l - mean * mean, 1e-12));
  const model = 100 * phi((mean / sd) * Math.sqrt(days));
  const backtest = (100 * (p.green_windows + 1)) / (p.trusted_windows + 2);
  return Math.round(Math.min(99, Math.max(1, Math.min(model, backtest))));
}
/** A pick's backtest curve: net of fees (solid, filled) over before-fees
 *  (dashed). Small in the row; `big` adds the axis figures. Values arrive
 *  per $1,000 and are scaled by `k` — the shape doesn't change, the labels do. */
function Curve({ p, k, big = false }: { p: ScoutPick; k: number; big?: boolean }) {
  const net = p.curve ?? [], gross = p.gross_curve ?? [];
  if (net.length < 2) return <span className="text-[10px] text-dim">no curve</span>;
  const W = big ? 600 : 120, H = big ? 140 : 28, pad = big ? 6 : 2;
  const all = [...net, ...gross];
  const t0 = net[0][0], t1 = net[net.length - 1][0];
  let lo = Math.min(...all.map((q) => q[1])), hi = Math.max(...all.map((q) => q[1]));
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const x = (t: number) => ((t - t0) / (t1 - t0 || 1)) * W;
  const y = (v: number) => pad + ((hi - v) / (hi - lo)) * (H - 2 * pad);
  const path = (q: [number, number][]) => q.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const base = y(Math.min(Math.max(1000, lo), hi));
  const end = net[net.length - 1][1], gEnd = gross.length ? gross[gross.length - 1][1] : end;
  const cls = end >= 1000 ? "text-win" : "text-loss";
  const svg = (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={`${big ? "h-[140px]" : "h-[28px]"} w-full ${cls}`}>
      <path d={`${path(net)}L${W},${base}L0,${base}Z`} fill="currentColor" opacity={0.1} />
      <line x1={0} x2={W} y1={base} y2={base} stroke="currentColor" strokeDasharray="2 3" opacity={0.3} vectorEffect="non-scaling-stroke" />
      {gross.length > 1 && <path d={path(gross)} fill="none" stroke="currentColor" strokeDasharray="3 3" strokeWidth={1} opacity={0.45} vectorEffect="non-scaling-stroke" />}
      <path d={path(net)} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
  if (!big) return svg;
  const d = (n: number) => fmtDay((n - 1000) * k);
  return (
    <div>
      <div className="flex flex-wrap gap-x-4 text-[11px] text-dim mb-1">
        <span>{p.curve_days}d backtest of {fmtUsd(1000 * k)}</span>
        <span><span className={`num ${tone(end - 1000)}`}>{d(end)}</span> after fees (solid)</span>
        <span><span className="num">{d(gEnd)}</span> before fees (dashed)</span>
        <span>fees <span className="num text-loss">{fmtDay(-(gEnd - end) * k)}</span></span>
        <span className="ml-auto">{new Date(t0).toLocaleDateString()} → {new Date(t1).toLocaleDateString()}</span>
      </div>
      <div className="relative">
        {svg}
        <span className="absolute left-1 top-0 num text-[10px] text-dim">{d(hi)}</span>
        <span className="absolute left-1 bottom-0 num text-[10px] text-dim">{d(lo)}</span>
      </div>
    </div>
  );
}

const popTone = (x: number | null) => (x == null ? "text-dim" : x >= 65 ? "text-win" : x >= 50 ? "text-warn" : "text-loss");
const tone = (n: number | null | undefined) => (n == null ? "text-dim" : n > 0 ? "text-win" : "text-loss");

const VERDICT: Record<ScoutPick["verdict"], { cls: string; hint: string }> = {
  consistent: { cls: "text-win", hint: "every trusted backtest window made money" },
  mixed: { cls: "text-warn", hint: "positive overall, but at least one trusted window lost" },
  losing: { cls: "text-loss", hint: "model or backtest says this copy loses money" },
  unverified: { cls: "text-dim", hint: "no trusted backtest window — the model is unconfirmed" },
};

function Row({ p, i, k, cap }: { p: ScoutPick; i: number; k: number; cap: string }) {
  const v = VERDICT[p.verdict] ?? VERDICT.unverified;
  const x = (n: number | null | undefined) => (n == null ? n : n * k);
  const pp = pop(p);
  const [big, setBig] = useState(false);
  const fee = p.model.copy_fee_1k;
  return (
    <div className="border-t border-white/[0.05]">
    <div className={`${GRID} px-4 py-2 text-[12px]`}>
      <span className="num text-dim">{i + 1}</span>
      <Link href={`/trader/${p.address}?days=7`} title={p.address}
        className="min-w-0 flex items-center gap-2 text-ink/90 hover:text-accent transition-colors">
        <Identicon address={p.address} size={16} />
        <span className="font-mono truncate">{shortAddr(p.address)}</span>
      </Link>
      <span className={`num ${tone(p.expected_per_day)}`}
        title={`Expected profit per day per ${cap} copied — the lower of the model and the backtest`}>
        {fmtDay(x(p.expected_per_day))}
      </span>
      <span className={`num text-[11px] ${fee ? "text-loss" : "text-dim"}`}
        title={`Extra taker fees a copy pays per day per ${cap}, over what the trader paid (0.045% on every mirrored fill). Already taken off every number in this row.`}>
        {fee == null ? "—" : fmtDay(-fee * k)}
      </span>
      <span className="num text-[11px] text-dim whitespace-nowrap truncate"
        title={`model ${fmtDay(x(p.model.per_1k))}/day from fills (${p.closes} closes, PF ${p.profit_factor < 0 ? "∞" : p.profit_factor.toFixed(2)}) · backtest ${fmtDay(x(p.backtest_per_day))}/day median of trusted windows`}>
        {fmtDay(x(p.model.per_1k))} · {fmtDay(x(p.backtest_per_day))}
      </span>
      <button type="button" onClick={() => setBig(!big)} className="block w-full hover:opacity-80"
        title={`${p.curve_days ?? "—"}d backtest of ${cap}: solid = after the copy's fees, dashed = before. Click to ${big ? "fold" : "enlarge"}.`}>
        <Curve p={p} k={k} />
      </button>
      <span className="flex gap-2 text-[11px] whitespace-nowrap overflow-hidden">
        {p.cells.map((c) => (
          <span key={c.days} className={`num ${c.trusted ? tone(c.per_day) : "text-dim line-through"}`}
            title={`${cap} copied ${c.days}d ago: ${c.pnl == null ? "nothing to replay" : `${fmtDay(x(c.pnl))} total, ${fmtDay(x(c.per_day))}/day`}` +
              (c.fees != null ? ` · ${fmtDay(-x(c.fees)!)} fees (before fees ${fmtDay(x(c.gross_pnl))})` : "") +
              (c.max_drawdown_pct != null ? ` · worst fall ${c.max_drawdown_pct}%` : "") +
              (c.flags.length ? `\nchecks: ${c.flags.join(", ")}` : "") +
              (c.trusted ? "" : "\nnot trusted — not counted")}>
            {c.days}d {fmtDay(x(c.per_day))}
          </span>
        ))}
      </span>
      <span className={`num text-right ${popTone(pp)}`}
        title={`Probability of profit: chance a copy is up after 7 days, from ${p.closes} closes ` +
          `(win rate ${(p.model.win_lo * 100).toFixed(0)}% defensible, avg win ${fmtDay(p.model.avg_win)}, avg loss ${fmtDay(-Math.abs(p.model.avg_loss))}), ` +
          `capped by the backtest.` +
          `\nbacktest: ${p.green_windows}/${p.trusted_windows} trusted windows green — ${v.hint}`}>
        {pp == null ? "—" : `${pp}%`}
      </span>
      <Link href={`/follows/new?leader=${p.address}`} className="btn-ghost !py-0.5 !px-2 text-[10px] justify-self-end">copy</Link>
    </div>
    {big && <div className="px-4 pb-3"><Curve p={p} k={k} big /></div>}
    </div>
  );
}

export default function ScoutPanel() {
  const [open, setOpen] = useState(true);
  const [s, setS] = useState<ScoutState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [capital, setCapital] = useState(1000);
  const [capText, setCapText] = useState("1000");

  useEffect(() => {
    try {
      if (localStorage.getItem(OPEN_KEY) === "0") setOpen(false);
      const c = Number(localStorage.getItem(CAP_KEY));
      if (c >= 1) { setCapital(c); setCapText(String(c)); }
    } catch {}
  }, []);
  const setCap = (c: number) => {
    const v = Math.min(10_000_000, Math.max(1, Math.round(c)));
    setCapital(v); setCapText(String(v));
    try { localStorage.setItem(CAP_KEY, String(v)); } catch {}
  };
  const stepDown = () => setCap([...STEPS].reverse().find((s) => s < capital) ?? STEPS[0]);
  const stepUp = () => setCap(STEPS.find((s) => s > capital) ?? capital * 2);
  // Every figure from the server is per $1,000 copied and scales linearly.
  const k = capital / 1000;
  const cap = fmtCap(capital);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try { localStorage.setItem(OPEN_KEY, next ? "1" : "0"); } catch {}
  };

  const load = (run = false) =>
    scout(run).then((d) => { setS(d); setErr(null); }).catch((e) => setErr(e.message ?? String(e)));
  useEffect(() => {
    if (!open) return;
    load();
    // Poll faster while a pass is running so the result lands promptly.
    const id = setInterval(() => load(), s?.running ? 10_000 : 60_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, s?.running]);

  const r = s?.report;
  const best = s?.best && (s.best.expected_per_day ?? 0) > 0 ? s.best : null;
  const status = s?.running ? "scouting…"
    : r ? `last run ${ago(r.updated_ms)}` : s ? "first run in a few minutes" : "";

  return (
    <div className="panel">
      <button onClick={toggle} className="w-full px-4 py-2 flex items-center justify-between gap-2 text-left">
        <span className="eyebrow">
          scout agent · most profitable copy
          {best && !open && (
            <span className="ml-2 normal-case tracking-normal">
              <span className="font-mono text-ink/80">{shortAddr(best.address)}</span>{" "}
              <span className={`num ${tone(best.expected_per_day)}`}>{fmtDay((best.expected_per_day ?? 0) * k)}/day per {cap}</span>
            </span>
          )}
        </span>
        <span className="text-[10px] uppercase tracking-wider text-dim">
          {status}<span className="ml-2">{open ? "▾" : "▸"}</span>
        </span>
      </button>

      {open && (
        <div className="border-t border-border">
          <div className="px-4 py-3 flex flex-wrap items-start gap-4">
            <div className="min-w-0 flex-1">
              {best ? (
                <>
                  <div className="text-[11px] text-muted">best copy right now</div>
                  <div className="mt-1 flex items-baseline gap-3 flex-wrap">
                    <Link href={`/trader/${best.address}?days=7`}
                      className="flex items-center gap-2 font-mono text-[15px] text-ink hover:text-accent">
                      <Identicon address={best.address} size={18} />{shortAddr(best.address)}
                    </Link>
                    <span className={`num text-[22px] font-semibold ${tone(best.expected_per_day)}`}>
                      {fmtDay((best.expected_per_day ?? 0) * k)}
                    </span>
                    <span className="text-[11px] text-dim">expected per day for every {cap} copied, after fees</span>
                  </div>
                  <div className="mt-1 text-[11px] text-dim">
                    ≈ {fmtDay((best.expected_per_day ?? 0) * 30 * k)} a month per {cap} · {best.closes} closes ·
                    win {best.win_rate.toFixed(0)}% · POP {pop(best) ?? "—"}{pop(best) != null ? "%" : ""} · equity {fmtUsd(best.equity)} · {best.green_windows}/{best.trusted_windows} backtest windows green
                  </div>
                </>
              ) : (
                <div className="text-[12px] text-muted">
                  {r?.note ?? (r ? "No copy passed both the model and the backtest this run." : "The scout hasn't finished a run yet.")}
                </div>
              )}
            </div>
            <div className="shrink-0 flex items-center gap-1" title="How much you'd copy — every $ figure below scales to it">
              <span className="text-[10px] uppercase tracking-wider text-dim mr-1">copy</span>
              <button className="btn-ghost !py-0.5 !px-2 text-[12px]" onClick={stepDown} aria-label="less">−</button>
              <span className="relative">
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[12px] text-dim">$</span>
                <input value={capText} inputMode="numeric" aria-label="capital copied"
                  onChange={(e) => {
                    const t = e.target.value.replace(/[^0-9]/g, "");
                    setCapText(t);
                    if (Number(t) >= 1) setCap(Number(t));
                  }}
                  onBlur={() => setCapText(String(capital))}
                  className="num w-[84px] pl-5 pr-2 py-1 text-[12px] text-right bg-transparent border border-border rounded-md focus:outline-none focus:border-accent" />
              </span>
              <button className="btn-ghost !py-0.5 !px-2 text-[12px]" onClick={stepUp} aria-label="more">+</button>
            </div>
            <button className="btn-ghost !py-1 text-[11px] shrink-0" disabled={s?.running}
              onClick={() => load(true)}
              title="Start a fresh pass now (the server allows one every 5 minutes)">
              {s?.running ? "scouting…" : "run now"}
            </button>
          </div>

          {err && <div className="px-4 pb-3 text-xs text-loss">{err}</div>}

          {r && r.picks.length > 0 && (
            <>
              <div className="overflow-x-auto">
                <div className="min-w-[940px]">
                  <div className={`${GRID} px-4 py-1.5 table-head whitespace-nowrap`}>
                    <span>#</span><span>trader</span><span>exp / day</span>
                    <span title="Extra taker fees a copy pays per day — already subtracted">fees / day</span>
                    <span>model · backtest</span>
                    <span title="Backtest curve: solid after fees, dashed before. Click one to enlarge.">curve</span><span>{cap} backtest, per day</span>
                    <span className="text-right" title="Probability of profit — chance a copy is up after 7 days">pop</span><span />
                  </div>
                  {r.picks.map((p, i) => <Row key={p.address} p={p} i={i} k={k} cap={cap} />)}
                </div>
              </div>
            </>
          )}

          <div className="px-4 py-2.5 border-t border-white/[0.05] text-[10px] leading-relaxed text-dim">
            How it works: every 15 min the scout takes the {r?.board_days ?? 7}-day board
            {r ? ` (${r.scanned} wallets, ${r.eligible} passed the copy checks)` : ""}, estimates each wallet&apos;s
            profit per day from its average win, average loss and the win rate its sample can defend, then replays
            $1,000 (scaled to your {cap}) on the top {r?.picks.length || 12} at 1, 7 and 30 days. A pick&apos;s number is the lower of the two —
            both have to agree. Both are after fees: a copy pays hyperliquid&apos;s 0.045% taker fee on every fill it
            mirrors, which for a VIP or rebate-earning trader is far more than they paid — that gap comes off. POP is the chance a copy is up after 7 days: the lower of the win-rate model (defensible win
            rate, average win, average loss) and the share of backtest windows that finished green. A forecast from history, not a promise.
            {s && s.history.length > 1 && (
              <span className="block mt-1">
                past winners:{" "}
                {s.history.slice(0, 6).map((h) => (
                  <span key={h.ts_ms} className="mr-3" title={new Date(h.ts_ms).toLocaleString()}>
                    {h.best ? <span className="font-mono">{shortAddr(h.best)}</span> : "none"}{" "}
                    <span className={tone(h.best_expected_per_day)}>{fmtDay(h.best_expected_per_day == null ? null : h.best_expected_per_day * k)}</span>
                  </span>
                ))}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
