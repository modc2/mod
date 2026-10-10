"use client";

// SCOUT — the background agent hunting the most profitable trader to copy.
//
// The server (scout.rs) runs every 15 minutes: it ranks the 7d board by
// expected profit per day per $1k copied (expect.rs), backtests the top 12
// with $1,000 at 1 / 7 / 30 days, and scores each by the LOWER of the two
// answers — so a wallet only leads here when its fills and its equity curve
// agree it makes money. This panel is a read of that report; "run now" asks
// for a fresh pass (the server allows one per 5 minutes).

import Link from "next/link";
import { useEffect, useState } from "react";
import { scout, ScoutPick, ScoutState, ago, fmtUsd, shortAddr } from "../lib/api";
import { Identicon } from "./BoardBits";

const OPEN_KEY = "hl.scout.open";

const fmtDay = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : "-"}$${Math.abs(n) >= 100 ? Math.round(Math.abs(n)).toLocaleString("en-US") : Math.abs(n).toFixed(2)}`;
const tone = (n: number | null | undefined) => (n == null ? "text-dim" : n > 0 ? "text-win" : "text-loss");

const VERDICT: Record<ScoutPick["verdict"], { cls: string; hint: string }> = {
  consistent: { cls: "text-win", hint: "every trusted backtest window made money" },
  mixed: { cls: "text-warn", hint: "positive overall, but at least one trusted window lost" },
  losing: { cls: "text-loss", hint: "model or backtest says this copy loses money" },
  unverified: { cls: "text-dim", hint: "no trusted backtest window — the model is unconfirmed" },
};

function Row({ p, i }: { p: ScoutPick; i: number }) {
  const v = VERDICT[p.verdict] ?? VERDICT.unverified;
  return (
    <div className="grid grid-cols-[20px_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.6fr)_auto] items-center gap-3 px-4 py-2 border-t border-white/[0.05] text-[12px]">
      <span className="num text-dim">{i + 1}</span>
      <Link href={`/trader/${p.address}?days=7`} title={p.address}
        className="min-w-0 flex items-center gap-2 text-ink/90 hover:text-accent transition-colors">
        <Identicon address={p.address} size={16} />
        <span className="font-mono truncate">{shortAddr(p.address)}</span>
      </Link>
      <span className={`num ${tone(p.expected_per_day)}`}
        title="Expected profit per day per $1,000 copied — the lower of the model and the backtest">
        {fmtDay(p.expected_per_day)}
      </span>
      <span className="num text-[11px] text-dim"
        title={`model ${fmtDay(p.model.per_1k)}/day from fills (${p.closes} closes, PF ${p.profit_factor < 0 ? "∞" : p.profit_factor.toFixed(2)}) · backtest ${fmtDay(p.backtest_per_day)}/day median of trusted windows`}>
        {fmtDay(p.model.per_1k)} · {fmtDay(p.backtest_per_day)}
      </span>
      <span className="flex gap-2 text-[11px]">
        {p.cells.map((c) => (
          <span key={c.days} className={`num ${c.trusted ? tone(c.per_day) : "text-dim line-through"}`}
            title={`$1,000 copied ${c.days}d ago: ${c.pnl == null ? "nothing to replay" : `${fmtDay(c.pnl)} total, ${fmtDay(c.per_day)}/day`}` +
              (c.max_drawdown_pct != null ? ` · worst fall ${c.max_drawdown_pct}%` : "") +
              (c.flags.length ? `\nchecks: ${c.flags.join(", ")}` : "") +
              (c.trusted ? "" : "\nnot trusted — not counted")}>
            {c.days}d {fmtDay(c.per_day)}
          </span>
        ))}
      </span>
      <span className="flex items-center gap-2">
        <span className={`text-[10px] uppercase tracking-wider ${v.cls}`} title={v.hint}>{p.verdict}</span>
        <Link href={`/follows/new?leader=${p.address}`} className="btn-ghost !py-0.5 !px-2 text-[10px]">copy</Link>
      </span>
    </div>
  );
}

export default function ScoutPanel() {
  const [open, setOpen] = useState(true);
  const [s, setS] = useState<ScoutState | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    try { if (localStorage.getItem(OPEN_KEY) === "0") setOpen(false); } catch {}
  }, []);
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
              <span className={`num ${tone(best.expected_per_day)}`}>{fmtDay(best.expected_per_day)}/day per $1k</span>
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
                      {fmtDay(best.expected_per_day)}
                    </span>
                    <span className="text-[11px] text-dim">expected per day for every $1,000 copied</span>
                  </div>
                  <div className="mt-1 text-[11px] text-dim">
                    ≈ {fmtDay((best.expected_per_day ?? 0) * 30)} a month per $1k · {best.closes} closes ·
                    win {best.win_rate.toFixed(0)}% · equity {fmtUsd(best.equity)} · {best.green_windows}/{best.trusted_windows} backtest windows green
                  </div>
                </>
              ) : (
                <div className="text-[12px] text-muted">
                  {r?.note ?? (r ? "No copy passed both the model and the backtest this run." : "The scout hasn't finished a run yet.")}
                </div>
              )}
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
              <div className="grid grid-cols-[20px_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.6fr)_auto] gap-3 px-4 py-1.5 table-head">
                <span>#</span><span>trader</span><span>exp / day · $1k</span>
                <span>model · backtest</span><span>$1k backtest, per day</span><span>verdict</span>
              </div>
              {r.picks.map((p, i) => <Row key={p.address} p={p} i={i} />)}
            </>
          )}

          <div className="px-4 py-2.5 border-t border-white/[0.05] text-[10px] leading-relaxed text-dim">
            How it works: every 15 min the scout takes the {r?.board_days ?? 7}-day board
            {r ? ` (${r.scanned} wallets, ${r.eligible} passed the copy checks)` : ""}, estimates each wallet&apos;s
            profit per day from its average win, average loss and the win rate its sample can defend, then replays
            $1,000 on the top {r?.picks.length || 12} at 1, 7 and 30 days. A pick&apos;s number is the lower of the two —
            both have to agree. A forecast from history, not a promise.
            {s && s.history.length > 1 && (
              <span className="block mt-1">
                past winners:{" "}
                {s.history.slice(0, 6).map((h) => (
                  <span key={h.ts_ms} className="mr-3" title={new Date(h.ts_ms).toLocaleString()}>
                    {h.best ? <span className="font-mono">{shortAddr(h.best)}</span> : "none"}{" "}
                    <span className={tone(h.best_expected_per_day)}>{fmtDay(h.best_expected_per_day)}</span>
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
