"use client";

// "$N on every strat, at every horizon" — the board-wide backtest table.
//
// The server replays each board strat (copy-traders and vaults) at 1, 3, 7,
// 14 and 30 days in one report (/strats/backtest): one portfolio + one fills
// call per strat however many windows, cached 10 min. Each cell is the trader
// page's backtest compacted, so its data checks travel with it — a cell whose
// checks FAILED is printed struck through and never counts toward the window
// summary or the "best" line. Click a row for the full per-window checks.
//
// Folded by default and only fetched while open: a cold build walks ~50
// wallets against Hyperliquid's rate limits and takes a minute or two.

import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  stratsBacktest, StratsBacktestReport, StratBacktestRow, BacktestCell,
  BACKTEST_WINDOWS, ago, fmtUsd, shortAddr,
} from "../lib/api";

const OPEN_KEY = "hl.strats.backtestOpen";
const CAPITAL_KEY = "hl.strats.backtestCapital";

const tone = (n: number | null | undefined) =>
  n == null ? "text-dim" : n >= 0 ? "text-win" : "text-loss";
const pct = (n: number | null | undefined) => {
  if (n == null) return "—";
  const a = Math.abs(n);
  const body = a >= 10_000 ? `${(a / 1000).toFixed(0)}k` : a >= 100 ? a.toFixed(0) : a.toFixed(1);
  return `${n >= 0 ? "+" : "-"}${body}%`;
};
const hrefFor = (r: StratBacktestRow) => (r.kind === "vault" ? `/vaults/${r.id}` : `/trader/${r.id}`);

function CellView({ c, capital }: { c: BacktestCell | undefined; capital: number }) {
  if (!c || !c.available) {
    return <span className="text-dim" title={c?.note ?? "nothing to replay"}>—</span>;
  }
  const warned = c.flags.some((f) => f.endsWith(":warn"));
  const title = [
    `$${capital} → ${c.final_value != null ? fmtUsd(c.final_value) : "—"} over ${c.days}d`,
    `worst fall ${c.max_drawdown_pct ?? 0}% · realised fills ${pct(c.realized_roi_pct)}`,
    c.flags.length ? `checks: ${c.flags.join(", ")}` : "all data checks passed",
  ].join("\n");
  return (
    <span title={title} className={`num ${c.ok ? tone(c.roi_pct) : "text-dim line-through"}`}>
      {pct(c.roi_pct)}
      {c.ok && warned && <sup className="text-warn ml-0.5">!</sup>}
    </span>
  );
}

export default function StratsBacktest({ kind }: { kind: "all" | "trader" | "vault" }) {
  const [open, setOpen] = useState(false);
  const [capital, setCapital] = useState(1000);
  const [draft, setDraft] = useState("1000");
  const [data, setData] = useState<StratsBacktestReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    try {
      setOpen(localStorage.getItem(OPEN_KEY) === "1");
      const c = parseFloat(localStorage.getItem(CAPITAL_KEY) || "");
      if (Number.isFinite(c) && c > 0) { setCapital(c); setDraft(String(c)); }
    } catch {}
  }, []);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try { localStorage.setItem(OPEN_KEY, next ? "1" : "0"); } catch {}
  };

  const run = (refresh = false) => {
    let alive = true;
    setLoading(true);
    setErr(null);
    stratsBacktest(capital, BACKTEST_WINDOWS, refresh)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setErr(e.message ?? String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  };
  useEffect(() => (open ? run() : undefined), [open, capital]);

  const applyCapital = () => {
    const n = parseFloat(draft);
    if (!Number.isFinite(n) || n <= 0 || n === capital) return;
    setCapital(n);
    try { localStorage.setItem(CAPITAL_KEY, String(n)); } catch {}
  };

  const rows = useMemo(
    () => (data?.rows ?? []).filter((r) => kind === "all" || r.kind === kind),
    [data, kind],
  );
  const windows = data?.windows ?? BACKTEST_WINDOWS;

  return (
    <div className="panel">
      <button onClick={toggle}
        className="w-full px-4 py-2 flex items-center justify-between gap-2 text-left">
        <span className="eyebrow">backtest · ${capital.toLocaleString()} on every strat · {windows.map((d) => `${d}d`).join(" / ")}</span>
        <span className="text-[10px] uppercase tracking-wider text-dim">
          {open ? (data ? (data.cached ? `cached · built ${ago(data.updated_ms)}` : `built in ${(data.build_ms / 1000).toFixed(0)}s`) : "") : "show"}
          <span className="ml-2">{open ? "▾" : "▸"}</span>
        </span>
      </button>

      {open && (
        <div className="border-t border-border">
          <div className="px-4 py-3 flex flex-wrap items-center gap-3 text-[11px] text-muted">
            <label className="uppercase tracking-wider" htmlFor="sbt-capital">$ on each strat</label>
            <input id="sbt-capital" className="input w-28 num" inputMode="decimal" value={draft}
              onChange={(e) => setDraft(e.target.value.replace(/[^0-9.]/g, ""))}
              onKeyDown={(e) => { if (e.key === "Enter") applyCapital(); }}
              onBlur={applyCapital} />
            <button className="btn-ghost !py-1 text-[11px]" disabled={loading} onClick={() => run(true)}>
              {loading ? "replaying…" : "re-run"}
            </button>
            <span className="text-dim">
              Equity model: your money riding each book (realised + unrealised). Struck-through = a data
              check failed, not counted. <sup className="text-warn">!</sup> = a caveat. Hover a cell for detail,
              click a row for its checks. A replay, not a forecast.
            </span>
          </div>

          {err && <div className="px-4 pb-3 text-xs text-loss">{err}</div>}
          {loading && !data && (
            <div className="px-4 pb-4 text-xs text-muted">
              replaying every strat at {windows.length} horizons — a cold run takes a minute or two…
            </div>
          )}

          {data && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wider text-muted border-b border-border">
                    <th className="text-left font-normal px-4 py-2">strat</th>
                    {windows.map((d) => <th key={d} className="text-right font-normal px-2 py-2">{d}d</th>)}
                    <th className="text-right font-normal px-4 py-2" title="trusted windows that ended in the green">green</th>
                  </tr>
                  {/* the per-window roll-up over trusted cells */}
                  <tr className="border-b border-border bg-white/[0.02]">
                    <td className="px-4 py-2 text-[10px] uppercase tracking-wider text-muted">median · in green</td>
                    {data.summary.map((s) => (
                      <td key={s.days} className="text-right px-2 py-2 whitespace-nowrap"
                        title={s.best_name ? `best: ${s.best_name} ${pct(s.best_roi_pct)} · mean ${pct(s.mean_roi_pct)}` : "no trusted results"}>
                        <span className={`num ${tone(s.median_roi_pct)}`}>{pct(s.median_roi_pct)}</span>
                        <span className="block text-[10px] text-dim">{s.in_green}/{s.trusted}</span>
                      </td>
                    ))}
                    <td />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const key = `${r.kind}:${r.id}`;
                    return (
                      <Fragment key={key}>
                        <tr onClick={() => setExpanded(expanded === key ? null : key)}
                          className="border-b border-white/[0.04] hover:bg-white/[0.03] cursor-pointer">
                          <td className="px-4 py-1.5">
                            <Link href={hrefFor(r)} onClick={(e) => e.stopPropagation()}
                              className="text-ink hover:text-accent truncate inline-block max-w-[22ch] align-bottom">
                              {r.name || shortAddr(r.id)}
                            </Link>
                            <span className={`ml-2 text-[10px] uppercase tracking-wider ${r.kind === "vault" ? "text-accent2" : "text-dim"}`}>
                              {r.kind === "vault" ? "vault" : "trader"}
                            </span>
                          </td>
                          {windows.map((d, i) => (
                            <td key={d} className="text-right px-2 py-1.5"><CellView c={r.cells[i]} capital={data.capital} /></td>
                          ))}
                          <td className="text-right px-4 py-1.5 num text-muted">{r.green_windows}/{windows.length}</td>
                        </tr>
                        {expanded === key && (
                          <tr className="border-b border-white/[0.04] bg-white/[0.02]">
                            <td colSpan={windows.length + 2} className="px-4 py-2 text-[11px] text-muted">
                              {r.cells.map((c) => (
                                <div key={c.days}>
                                  <span className="num text-ink w-10 inline-block">{c.days}d</span>
                                  {c.available
                                    ? <>{fmtUsd(data.capital)} → {c.final_value != null ? fmtUsd(c.final_value) : "—"}
                                        {" · "}worst fall {c.max_drawdown_pct ?? 0}%
                                        {" · "}realised fills {pct(c.realized_roi_pct)}
                                        {c.wiped && <span className="text-loss"> · wiped out</span>}</>
                                    : <span className="text-dim">{c.note ?? "nothing to replay"}</span>}
                                  {c.flags.length > 0 && <span className={c.ok ? "text-warn" : "text-loss"}> · {c.flags.join(", ")}</span>}
                                </div>
                              ))}
                              <div className="mt-1 text-dim">full check sentences on the <Link className="underline" href={hrefFor(r)}>{r.kind} page</Link>.</div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              {rows.length === 0 && <div className="px-4 py-4 text-xs text-muted">no strats of this type</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
