"use client";

// WHERE THE MONEY IS — the copy book as a bar chart, and one trader opened up.
//
// Two questions the desk's rows answer one at a time, answered together here:
//
//   LEFT   how the dollars are spread. One bar per trader, its length the $
//          behind them, with the backtest AT THAT $ (the worker's replay of
//          the row, lib/hubBacktest.ts) printed on the bar — so every name
//          reads "$250 · +$12.40 · HELD" at a glance. Click a bar to open it.
//
//   RIGHT  the opened trader at ANY size. A $ box (defaulting to what the row
//          holds), the same replay re-run at that number, its curve, and the
//          ladder — the standard sizes plus the row's own — because copying
//          is not linear in N: $50 and $500 behind the same leader are
//          different strategies (lib/copyLadder.ts). PUT $N ON THEM commits
//          the size you just simulated through the same /copy/allocations
//          route the row's $ box uses.
//
// Chart rules this follows on purpose: one hue (the bars encode one quantity
// — magnitude — so they share one color; state is a text chip, never a bar
// color), direct labels instead of an axis, the rows below ARE the table
// view, and every bar is a real button with a hover title.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import type { CopyBookRow } from "../lib/copyBook";
import {
  ladderInputs, ladderSizes, replayAtSize, type LadderInputs, type SizedReplay,
} from "../lib/copyLadder";
import type { HubBacktest, TraderFeed } from "../lib/hubReplay";
import { shortAddress } from "../lib/identityStrat";
import { describeMarketQuery } from "../lib/marketTypes";
import EquityChart from "./EquityChart";
import { MODE } from "../lib/tradingMode";

const AMOUNT_PRESETS = [25, 100, 500, 1000];
/** Typing "1000" is one replay, not four. */
const DEBOUNCE_MS = 350;

function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const s = a >= 1000 && digits === 2
    ? Math.round(a).toLocaleString("en-US")
    : a.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${v < 0 ? "-" : ""}$${s}`;
}

function fmtSigned(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${fmtUsd(v)}`;
}

function fmtPct(v: number): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}

function tone(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return "text-pixel-gray-light";
  return v > 0 ? "text-pixel-green" : "text-red-400";
}

function verdictTone(verdict: string | undefined): string {
  if (verdict === "held") return "text-pixel-green";
  if (verdict === "faded" || verdict === "no-edge") return "text-red-400";
  return "text-pixel-gray";
}

/** The row's state, as the one word the whole console uses. */
function stateOf(row: CopyBookRow): { label: string; cls: string; title: string } {
  if (!row.enabled) return { label: "PAUSED", cls: "text-pixel-gray/70", title: "Paused — in the book, kept out of START ALL" };
  if (row.live?.running) {
    return row.live.autoExecute
      ? { label: MODE.LIVE.label, cls: "text-pixel-green", title: MODE.LIVE.active }
      : { label: MODE.TEST.label, cls: "text-amber-400", title: MODE.TEST.active };
  }
  return { label: "OFF", cls: "text-pixel-gray", title: "Not started" };
}

export default function DeskAllocationChart({
  rows, results, pending, days, selected, onSelect, onAllocate, busy,
}: {
  rows: CopyBookRow[];
  /** The desk's per-row replays, keyed by strategyId — each at the row's $. */
  results: Record<string, HubBacktest>;
  pending: Set<string>;
  days: number;
  selected: string | null;
  onSelect: (address: string) => void;
  /** Commit a new $ for a row — the same write the row's $ box makes. */
  onAllocate: (address: string, usd: number) => void;
  busy: string | null;
}) {
  const total = rows.reduce((s, r) => s + r.allocationUsd, 0);
  const max = Math.max(1, ...rows.map((r) => r.allocationUsd));
  const sel = rows.find((r) => r.address === selected) ?? null;
  const replayed = rows.map((r) => results[r.strategyId]).filter(Boolean);
  const sumPnl = replayed.reduce((s, b) => s + b.pnl, 0);
  const stateCounts = rows.reduce<Record<string, number>>((acc, r) => {
    const l = stateOf(r).label;
    acc[l] = (acc[l] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="pixel-panel">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2.5 border-b-2 border-pixel-border">
        <span className="font-mono text-[13px] tracking-[0.14em] text-pixel-white">WHERE THE MONEY IS</span>
        <span className="font-mono text-[11px] text-pixel-gray">
          {fmtUsd(total, 0)} across {rows.length} trader{rows.length === 1 ? "" : "s"} — each bar is the $ behind a name, with the {days}D backtest at that $. Click one to try other sizes.
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        {/* ── The chart ── */}
        <div className="p-3 flex flex-col lg:border-r-2 border-pixel-border">
          <div className="space-y-1.5">
          {rows.map((row) => {
            const bt = results[row.strategyId];
            const isSel = row.address === selected;
            const share = total > 0 ? (row.allocationUsd / total) * 100 : 0;
            const width = Math.max(row.allocationUsd > 0 ? 2 : 0, (row.allocationUsd / max) * 100);
            const st = stateOf(row);
            const gate = row.params?.marketQuery?.trim() ?? "";
            const stale = bt && bt.capital !== row.allocationUsd;
            const title = [
              `${row.name} · ${fmtUsd(row.allocationUsd, 0)} (${share.toFixed(0)}% of the desk)`,
              gate ? `gated to ${describeMarketQuery(gate)}` : "every market they trade",
              bt
                ? `${bt.days}D backtest at ${fmtUsd(bt.capital, 0)}: ${fmtSigned(bt.pnl)} · ${bt.trades} trades · ${bt.forward?.verdict?.toUpperCase() ?? "UNCHECKED"}`
                : "backtest not ready yet",
              "click to replay at other sizes",
            ].join("\n");
            return (
              <button
                key={row.address}
                onClick={() => onSelect(row.address)}
                title={title}
                aria-pressed={isSel}
                className={`w-full text-left rounded-[3px] px-1.5 py-1 transition-colors ${
                  isSel ? "bg-pixel-green/10 ring-1 ring-pixel-green/60" : "hover:bg-pixel-white/5"
                } ${row.enabled ? "" : "opacity-60"}`}
              >
                <div className="flex items-baseline gap-2 font-mono text-[10px]">
                  <span className={`truncate ${isSel ? "text-pixel-green" : "text-pixel-gray-light"}`} style={{ maxWidth: 160 }}>
                    {row.name}
                  </span>
                  <span className={`text-[9px] tracking-[0.1em] ${st.cls}`} title={st.title}>{st.label}</span>
                  {gate && (
                    <span className="text-[9px] text-pixel-gray truncate" style={{ maxWidth: 110 }}>
                      IN {describeMarketQuery(gate)}
                    </span>
                  )}
                  <span className="flex-1" />
                  <span className="text-pixel-white shrink-0">{fmtUsd(row.allocationUsd, 0)}</span>
                  <span className="text-pixel-gray shrink-0 w-8 text-right">{share.toFixed(0)}%</span>
                </div>
                <div className="mt-0.5 h-[10px] bg-pixel-black/40 rounded-[2px] overflow-hidden">
                  <div
                    className={`h-full rounded-[2px] ${isSel ? "bg-pixel-green" : "bg-pixel-green/70"}`}
                    style={{ width: `${width}%` }}
                  />
                </div>
                <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 font-mono text-[9.5px]">
                  <span className="text-[8.5px] tracking-[0.12em] text-pixel-gray shrink-0">{days}D AT {fmtUsd(row.allocationUsd, 0)}</span>
                  {!bt ? (
                    <span className="text-pixel-gray">{pending.has(row.strategyId) ? "replaying…" : "queued"}</span>
                  ) : (
                    <>
                      <span className={tone(bt.pnl)}>{fmtSigned(bt.pnl)}</span>
                      <span className="text-pixel-gray-light">{fmtPct(bt.roi)}</span>
                      <span className="text-pixel-gray-light">{bt.trades} trades</span>
                      <span className={verdictTone(bt.forward?.verdict)}>
                        {bt.forward?.verdict?.toUpperCase() ?? "UNCHECKED"}
                      </span>
                      {stale && (
                        <span className="text-amber-400" title={`This replay ran at ${fmtUsd(bt.capital, 0)} — the $ changed since; the next worker pass catches up`}>
                          at {fmtUsd(bt.capital, 0)}
                        </span>
                      )}
                    </>
                  )}
                </div>
                {bt && bt.trades === 0 && bt.note && (
                  <div className="font-mono text-[9.5px] text-amber-400 truncate" title={bt.note}>{bt.note}</div>
                )}
              </button>
            );
          })}
          </div>

          {/* The tall panel on the right leaves this column with air when the
              book is short — say what the air is, instead of leaving a void. */}
          <div className="hidden lg:flex flex-1 min-h-[24px] items-center justify-center py-4">
            {rows.length < 4 && (
              <span className="font-mono text-[10px] leading-relaxed text-pixel-gray/60 text-center px-6">
                the desk has room —<br />+ ADD A TRADER above finds the best in a market
              </span>
            )}
          </div>

          {/* The sum row every bar chart owes its reader: the whole desk as
              one segmented bar (share per name), with the replays' total. */}
          <div className="mt-2 lg:mt-auto pt-2 border-t border-pixel-gray/20">
            <div className="flex items-baseline gap-2 font-mono text-[10px] px-1.5">
              <span className="tracking-[0.12em] text-pixel-gray">THE WHOLE DESK</span>
              <span className="flex-1" />
              <span className="text-pixel-white">{fmtUsd(total, 0)}</span>
            </div>
            <div className="mt-1 mx-1.5 h-[10px] bg-pixel-black/40 rounded-[2px] overflow-hidden flex">
              {rows.map((r) => (
                <div
                  key={r.address}
                  className={`h-full ${r.address === selected ? "bg-pixel-green" : "bg-pixel-green/45"}`}
                  style={{
                    width: `${total > 0 ? (r.allocationUsd / total) * 100 : 0}%`,
                    boxShadow: "inset -1px 0 0 rgba(0,0,0,0.6)",
                  }}
                  title={`${r.name} · ${fmtUsd(r.allocationUsd, 0)}`}
                />
              ))}
            </div>
            <div className="mt-1 px-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 font-mono text-[9.5px]">
              {replayed.length > 0 && (
                <>
                  <span className="text-[8.5px] tracking-[0.12em] text-pixel-gray">{days}D AT THESE SIZES</span>
                  <span className={tone(sumPnl)} title={replayed.length < rows.length ? `${replayed.length} of ${rows.length} replays ready` : `The ${replayed.length} bars' backtests, added up`}>
                    {fmtSigned(sumPnl)}{replayed.length < rows.length ? ` (${replayed.length}/${rows.length})` : ""}
                  </span>
                  <span className="text-pixel-gray/60">·</span>
                </>
              )}
              {(["LIVE", "TEST", "OFF", "PAUSED"] as const).map((l) =>
                stateCounts[l] ? (
                  <span key={l} className="text-pixel-gray">
                    {stateCounts[l]} {l}
                  </span>
                ) : null,
              )}
            </div>
          </div>
        </div>

        {/* ── The opened trader ── */}
        <div className="min-w-0">
          {sel ? (
            <SelectedTrader
              key={sel.address}
              row={sel}
              days={days}
              card={results[sel.strategyId]}
              busy={busy?.endsWith(sel.address) ?? false}
              onAllocate={(usd) => onAllocate(sel.address, usd)}
            />
          ) : (
            <div className="p-6 text-center font-mono text-[11px] text-pixel-gray">
              click a bar to replay that trader at any size
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── One trader, at N dollars ──

/** Feeds memoized for the session — a 30-day walk per leader is the cost,
    and re-opening a name you already opened shouldn't pay it again. */
const feedCache = new Map<string, Promise<TraderFeed>>();

function SelectedTrader({
  row, days, card, busy, onAllocate,
}: {
  row: CopyBookRow;
  days: number;
  /** The worker's replay at the row's own $, for the anchor line. */
  card?: HubBacktest;
  busy: boolean;
  onAllocate: (usd: number) => void;
}) {
  const [amountStr, setAmountStr] = useState(String(row.allocationUsd || 100));
  const [amount, setAmount] = useState(row.allocationUsd || 100);
  useEffect(() => {
    const n = Number(amountStr);
    const t = setTimeout(() => setAmount(Number.isFinite(n) && n > 0 ? n : 0), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [amountStr]);

  // Inputs once per (leader, window); rungs per size, computed lazily and
  // kept — a size you already saw doesn't re-run.
  const [inputs, setInputs] = useState<LadderInputs | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setInputs(null);
    setLoadError(null);
    void ladderInputs(row.address, days, feedCache)
      .then((i) => { if (live) setInputs(i); })
      .catch((e) => { if (live) setLoadError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [row.address, days]);

  // Rungs are keyed by everything that changes them: size, the row's params
  // (gate + knobs), and the inputs' timestamp.
  const paramsKey = JSON.stringify(row.params ?? {});
  const [rungs, setRungs] = useState<Record<string, SizedReplay>>({});
  const rungKey = (c: number) => `${c}|${paramsKey}|${inputs?.at ?? 0}`;
  const sizes = useMemo(
    () => ladderSizes(row.allocationUsd, amount > 0 ? amount : null),
    [row.allocationUsd, amount],
  );
  const [running, setRunning] = useState(false);
  const runToken = useRef(0);

  useEffect(() => {
    if (!inputs) return;
    // The typed size first — it is what the headline waits on — then the
    // rest of the ladder, one per tick so the screen stays responsive.
    const order = [...new Set([amount, row.allocationUsd, ...sizes])].filter((c) => c > 0);
    const todo = order.filter((c) => !(rungKey(c) in rungs));
    if (todo.length === 0) return;
    const token = ++runToken.current;
    setRunning(true);
    void (async () => {
      for (const c of todo) {
        if (runToken.current !== token) return;
        try {
          const r = replayAtSize(row, c, inputs);
          setRungs((prev) => ({ ...prev, [rungKey(c)]: r }));
        } catch {
          // One rung failing leaves it "…" — the others still land.
        }
        await new Promise((res) => setTimeout(res, 0));
      }
      if (runToken.current === token) setRunning(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputs, sizes.join(","), amount, paramsKey]);

  const at = amount > 0 ? rungs[rungKey(amount)] : undefined;
  const onDesk = rungs[rungKey(row.allocationUsd)];
  const gate = row.params?.marketQuery?.trim() ?? "";
  const isDeskAmount = amount === row.allocationUsd;

  return (
    <div>
      {/* Who, and with how much. */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-pixel-gray/20">
        <Link
          href={`/copy/${row.address}`}
          className="font-mono text-[12px] text-pixel-green hover:text-pixel-white"
          title={`Open ${row.name} — backtest, live session and wallet`}
        >
          {row.name}
        </Link>
        <Link
          href={`/traders/${row.address}${gate ? `?mq=${encodeURIComponent(gate)}` : ""}`}
          className="font-mono text-[10px] text-pixel-gray hover:text-pixel-green normal-case"
          title="Their own trading record"
        >
          {/* Auto-named rows are "COPY 0xAB…CD" — printing the address again
              beside them reads as a stutter. */}
          {row.name.includes(shortAddress(row.address)) ? "their record" : shortAddress(row.address)} ↗
        </Link>
        {gate && <span className="font-mono text-[10px] text-pixel-gray">IN {describeMarketQuery(gate)}</span>}
        <span className="flex-1" />
        <span className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">BACKTEST $</span>
        <input
          className="pixel-input-sm input-xs w-20 font-mono text-[12px]"
          value={amountStr}
          inputMode="decimal"
          onChange={(e) => setAmountStr(e.target.value)}
          title="Replay this trader with this many dollars behind them — the same sizing the engine would use"
        />
        <button
          className={`pixel-btn btn-xs ${isDeskAmount ? "border-pixel-green text-pixel-green" : ""}`}
          onClick={() => setAmountStr(String(row.allocationUsd))}
          title="Back to what the row actually holds"
        >
          ON DESK {fmtUsd(row.allocationUsd, 0)}
        </button>
        {AMOUNT_PRESETS.map((p) => (
          <button
            key={p}
            className={`pixel-btn btn-xs ${amount === p ? "border-pixel-green text-pixel-green" : ""}`}
            onClick={() => setAmountStr(String(p))}
          >
            ${p}
          </button>
        ))}
      </div>

      {loadError ? (
        <div className="p-4 font-mono text-[11px] text-red-400">{loadError}</div>
      ) : !inputs ? (
        <div className="p-6 text-center font-mono text-[11px] text-pixel-gray">
          PULLING {row.name}&apos;S LAST {days}D…
        </div>
      ) : !(amount > 0) ? (
        <div className="p-6 text-center font-mono text-[11px] text-pixel-gray">
          ENTER AN AMOUNT ABOVE $0
        </div>
      ) : !at ? (
        <div className="p-6 text-center font-mono text-[11px] text-pixel-gray">
          REPLAYING AT {fmtUsd(amount, 0)}…
        </div>
      ) : (
        <>
          {/* Headline: the number at N, and what it rests on. */}
          <div className="grid grid-cols-3 md:grid-cols-6 gap-px bg-pixel-border">
            {([
              { label: `${days}D NET`, value: fmtSigned(at.pnl), t: at.pnl, hint: `Final simulated equity minus the ${fmtUsd(amount, 0)} put in` },
              { label: "RETURN", value: fmtPct(at.roi), t: at.pnl, hint: `On ${fmtUsd(amount, 0)}` },
              { label: "ENDS WITH", value: fmtUsd(at.endEquity), t: at.endEquity - amount, hint: "Cash + the value of whatever the copy still holds" },
              { label: "COPIED", value: `${at.executed} of ${at.funnel.observed}`, t: 0, hint: `${at.executed} entries filled out of ${at.funnel.observed} BUYs the leader placed` },
              { label: "WALK-FWD", value: at.forward.verdict.toUpperCase(), t: at.forward.ok ? 1 : at.forward.verdict === "faded" || at.forward.verdict === "no-edge" ? -1 : 0, hint: `The window before this one, replayed at the same ${fmtUsd(amount, 0)}: ${fmtSigned(at.forward.pnl)} on ${at.forward.trades} trades. HELD is the only pass.` },
              { label: "SETTLED", value: at.executed > 0 ? `${Math.round(at.confidence * 100)}%` : "—", t: at.executed > 0 ? (at.confidence >= 0.8 ? 1 : at.confidence >= 0.4 ? 0 : -1) : 0, hint: "Share of the exit value that came from a looked-up resolution rather than the last price a leader printed. Marked legs flatter the result." },
            ] as const).map((s) => (
              <div key={s.label} className="bg-pixel-black px-2 py-1.5 text-center" title={s.hint}>
                <div className="font-mono text-[8.5px] tracking-[0.12em] text-pixel-gray mb-0.5">{s.label}</div>
                <div className={`font-mono text-[13px] ${tone(s.t)}`}>{s.value}</div>
              </div>
            ))}
          </div>

          {/* The curve at N — or the reason there isn't one. */}
          {at.executed === 0 ? (
            <div className="px-3 py-2.5 border-t border-pixel-gray/20 font-mono text-[11px] text-amber-400">
              NOTHING WAS COPIED AT {fmtUsd(amount, 0)} — {at.note ?? "every candidate was filtered out"}. The ladder below says whether another size changes that.
            </div>
          ) : at.history.length > 1 && (
            <div className="px-2 py-1.5 border-t border-pixel-gray/20">
              <EquityChart
                history={at.history}
                markers={at.markers}
                emptyHint="no simulated fills in this window"
                timeControls={false}
              />
            </div>
          )}

          {/* The ladder — the real answer to "with N dollars". */}
          <div className="border-t border-pixel-gray/20">
            <div className="flex flex-wrap items-baseline gap-x-2 px-3 pt-2 pb-1 font-mono">
              <span className="text-[10px] tracking-[0.14em] text-pixel-white">WITH HOW MUCH?</span>
              <span className="text-[10px] text-pixel-gray">
                the same {days}D at every size — copying is not linear in N{running ? " · replaying…" : ""}
              </span>
              {card && onDesk && Math.abs(card.pnl - onDesk.pnl) > 0.005 && card.capital === row.allocationUsd && (
                <span className="text-[9.5px] text-pixel-gray" title="The bar's number came from the background worker's earlier pass over its own feed store; this one just ran in your browser on a fresh pull. They drift as the leader trades.">
                  · the bar said {fmtSigned(card.pnl)} ({card.by === "worker" ? "worker" : "earlier"} pass)
                </span>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="pixel-table w-full" style={{ minWidth: 520 }}>
                <thead>
                  <tr>
                    <th style={{ width: 96 }}>CAPITAL</th>
                    <th className="text-right" style={{ width: 70 }}>COPIED</th>
                    <th className="text-right" style={{ width: 80 }}>NET</th>
                    <th className="text-right" style={{ width: 70 }}>RETURN</th>
                    <th style={{ width: 90 }}>WALK-FWD</th>
                    <th style={{ width: 90 }} />
                  </tr>
                </thead>
                <tbody>
                  {sizes.map((c) => {
                    const r = rungs[rungKey(c)];
                    const here = c === amount;
                    const desk = c === row.allocationUsd;
                    return (
                      <tr key={c} className={here ? "bg-green-400/5" : ""}>
                        <td className={`font-mono text-[11px] ${here ? "text-pixel-green" : "text-pixel-white"}`}>
                          {fmtUsd(c, 0)}
                          {desk && <span className="ml-1 text-[8px] tracking-[0.1em] text-pixel-gray" title="What the row holds right now">ON DESK</span>}
                        </td>
                        {!r ? (
                          <td colSpan={4} className="font-mono text-[10px] text-pixel-gray">…</td>
                        ) : (
                          <>
                            <td className="num text-right font-mono text-[11px] text-pixel-gray-light" title={r.skipped > 0 ? `${r.skipped} reached the wallet but couldn't be placed — under the order floor, or out of cash` : undefined}>
                              {r.executed}{r.skipped > 0 && <span className="text-amber-400"> ·{r.skipped}</span>}
                            </td>
                            <td className={`num text-right font-mono text-[11px] ${tone(r.pnl)}`}>{fmtSigned(r.pnl)}</td>
                            <td className={`num text-right font-mono text-[11px] ${tone(r.pnl)}`}>{fmtPct(r.roi)}</td>
                            <td className={`font-mono text-[10px] ${verdictTone(r.forward.verdict)}`} title={`Prior window at ${fmtUsd(c, 0)}: ${fmtSigned(r.forward.pnl)} on ${r.forward.trades} trades`}>
                              {r.forward.verdict.toUpperCase()}
                            </td>
                          </>
                        )}
                        <td className="text-right" style={{ overflow: "visible" }}>
                          {!here && (
                            <button className="pixel-btn btn-xs" onClick={() => setAmountStr(String(c))} title="Replay at this size">
                              TRY
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Commit the size you just looked at. */}
          <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-t border-pixel-gray/20">
            <button
              className="pixel-btn btn-xs border-pixel-green text-pixel-green disabled:opacity-40"
              disabled={busy || isDeskAmount || !(amount > 0)}
              onClick={() => onAllocate(amount)}
              title={isDeskAmount ? "That's what the row already holds" : `Change ${row.name}'s allocation from ${fmtUsd(row.allocationUsd, 0)} to ${fmtUsd(amount, 0)} — a running session is resized in place`}
            >
              {isDeskAmount ? `ON THE DESK AT ${fmtUsd(amount, 0)}` : `PUT ${fmtUsd(amount, 0)} ON THEM`}
            </button>
            <span className="font-mono text-[10px] text-pixel-gray">
              writes the amount to the copy book — nothing is placed until the row is started. Fills are at the leader&apos;s price; real slippage is a cost no number here carries.
            </span>
          </div>
        </>
      )}
    </div>
  );
}
