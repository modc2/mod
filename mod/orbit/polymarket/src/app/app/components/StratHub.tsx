"use client";

// THE STRAT HUB — the front door to /strats. Every strat is a card, and the
// card's headline is its N-DAY BACKTEST: the same engine, the same window for
// all of them, replayed live by useHubBacktests. That is what makes a wall of
// strats comparable — the cards used to print `lastPnl`, a leftover from
// whatever window each strat happened to be opened with, so a "+$40" card and
// a "+$4" card were measuring different things.
//
// The RECOMMENDED strats are scored the same way. A template card runs the
// SavedIndex forking it would create, roster and all, so "we suggest this one"
// is a number you can check rather than a claim — and the window pills let you
// ask it over 1, 3, 7, 14 or 30 days, which is the difference between a strat
// that had one good day and one that works.
//
// Search filters both shelves at once, over names, descriptions and the filter
// chips — "sports", "buys only", "top 5" all narrow the grid.
//
// Below the fold: the live money each strat actually holds (engine ledger, not
// the sim) and the filter chips that say what slice of flow it copies. Clicking
// a card opens that strat's workspace; ⑂ / × / + are handed back to the picker
// (the single strat manager) so indexStore + server sync stay in one place.

import { useMemo, useState } from "react";
import { DEFAULT_STRATS, type StratTemplate } from "../lib/defaultStrats";
import { fmtUsd, type StratMoney } from "../lib/stratStats";
import { HUB_WINDOWS, templateBacktestKey, type HubBacktest } from "../lib/hubBacktest";
import { describeTraderFilter } from "../lib/strats/strat";
import type { SavedIndex } from "../lib/types";

interface Props {
  indexes: SavedIndex[];
  /** Per-strat live money-in/PnL (from useStratStats); keys are strat ids. */
  stats?: Record<string, StratMoney>;
  /** Per-card backtest (from useHubBacktests): saved strats by id, templates
      by `tpl:<slug>` — see `templateBacktestKey`. */
  backtests?: Record<string, HubBacktest>;
  /** Card keys whose replay is still running. */
  backtestPending?: Set<string>;
  /** The window every card is measured over, and the setter behind the pills. */
  days: number;
  onDaysChange: (days: number) => void;
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCreate: () => void;
  onFork: (t: StratTemplate) => void;
  /** Fork a SAVED strat (⑂ on its card) — the picker copies it and drops
      into rename mode. Distinct from `onFork`, which forks a template. */
  onForkSaved: (id: string) => void;
  /** Re-run every card's backtest now, ignoring the freshness window. */
  onRefresh?: () => void;
}

function timeAgo(ts?: number): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/// Compact human summary of the per-trade filters, one chip per active
/// dimension. Mirrors the semantics documented on TradeFilters in types.ts.
/// Takes just the two filter fields so default-strat templates (which are
/// Partial<SavedIndex> recipes) can render the same chips as saved strats.
function filterChips(idx: Pick<SavedIndex, "marketQuery" | "tradeFilters" | "filter">): string[] {
  const chips: string[] = [];
  if (idx.marketQuery?.trim()) chips.push(`"${idx.marketQuery.trim()}"`);
  const f = idx.tradeFilters;
  if (f) {
    if (f.sides === "buy") chips.push("BUYS ONLY");
    if (f.sides === "sell") chips.push("SELLS ONLY");
    if (f.minPrice !== undefined || f.maxPrice !== undefined) {
      const lo = Math.round((f.minPrice ?? 0) * 100);
      const hi = Math.round((f.maxPrice ?? 1) * 100);
      chips.push(`${lo}–${hi}¢`);
    }
    if (f.minNotional !== undefined || f.maxNotional !== undefined) {
      const lo = f.minNotional !== undefined ? `$${f.minNotional}` : "$0";
      const hi = f.maxNotional !== undefined ? `$${f.maxNotional}` : "∞";
      chips.push(`${lo}–${hi}`);
    }
    if (f.categories && f.categories.length > 0) chips.push(f.categories.join("/").toUpperCase());
  }
  // The trader gate reads as a chip too — "top 5 by score" is as much a part
  // of what this strat trades as "BUYS ONLY".
  if (idx.filter) chips.push(describeTraderFilter(idx.filter).toUpperCase());
  return chips;
}

/// The 24h equity path as a 2-line sparkline: the shape says whether the P&L
/// came from one lucky fill or from a run. Baseline = starting capital, so
/// anything under the dashed line is a drawdown.
function Sparkline({ curve, up }: { curve: number[]; up: boolean }) {
  if (curve.length < 2) return null;
  const W = 100, H = 28;
  const min = Math.min(...curve), max = Math.max(...curve);
  const span = max - min || 1;
  const x = (i: number) => (i / (curve.length - 1)) * W;
  const y = (v: number) => H - ((v - min) / span) * H;
  const d = curve.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const base = y(curve[0]);
  const stroke = up ? "rgb(var(--up-rgb))" : "var(--danger)";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-7 overflow-visible">
      <line x1="0" y1={base} x2={W} y2={base} stroke="currentColor" strokeWidth="0.5" strokeDasharray="2 2" className="text-pixel-gray/40" />
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/// The card headline — ONE component for saved strats and recommended ones.
/// A recommendation has to be judged on the same footing as something you
/// already own, which means the same markup and the same replay, never a
/// second rendering of "roughly the same thing".
function BacktestBlock({
  bt,
  running,
  days,
  emptyLabel,
}: {
  bt?: HubBacktest;
  running: boolean;
  days: number;
  /** What to show when there's no result yet and nothing is running. */
  emptyLabel: string;
}) {
  const up = (bt?.pnl ?? 0) >= 0;
  const window = days === 1 ? "1D" : `${days}D`;
  return (
    <div className="rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-[var(--border)] px-2.5 py-2 mb-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[9px] text-pixel-gray font-semibold tracking-[0.14em] shrink-0">
          {window} BACKTEST
        </span>
        {bt && !bt.note && (
          <span
            className="text-[9.5px] font-mono text-pixel-gray shrink-0"
            title={`Replayed ${timeAgo(bt.at)} on $${bt.capital} of paper capital across ${bt.traders} trader(s)`}
          >
            {bt.trades} TR
          </span>
        )}
      </div>
      {bt && bt.note ? (
        // A structural empty is an answer, not a zero.
        <div className="mt-1 text-[12px] font-mono text-pixel-gray leading-snug">{bt.note}</div>
      ) : bt ? (
        <>
          <div className="flex items-baseline gap-2 mt-0.5">
            <span
              className={`text-[22px] leading-none font-mono font-semibold ${up ? "text-green-400" : "text-red-400"}`}
              title={`Net P&L of a $${bt.capital} deployment of this strat over the last ${days === 1 ? "24h" : `${days} days`}${bt.skipped ? ` · ${bt.skipped} candidate trades skipped` : ""}`}
            >
              {up ? "+" : "−"}${Math.abs(bt.pnl).toFixed(2)}
            </span>
            <span className={`text-[12px] font-mono ${up ? "text-green-400/80" : "text-red-400/80"}`}>
              {bt.roi >= 0 ? "+" : ""}{bt.roi.toFixed(2)}%
            </span>
            {running && <span className="text-[10px] font-mono text-pixel-gray animate-pulse">↻</span>}
          </div>
          <div className="mt-1 -mb-0.5">
            <Sparkline curve={bt.curve} up={up} />
          </div>
        </>
      ) : (
        <div className="mt-1 text-[12px] font-mono text-pixel-gray">
          {running ? "replaying…" : emptyLabel}
        </div>
      )}
    </div>
  );
}

/// Free-text match over everything that identifies a strat on its card: name,
/// description, and the chips it renders. EVERY term must hit, so "sports
/// buys" narrows the grid instead of widening it.
function matchesQuery(query: string, name: string, chips: string[], description?: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [name, description ?? "", ...chips].join(" ").toLowerCase();
  return q.split(/\s+/).filter(Boolean).every((term) => hay.includes(term));
}

export default function StratHub({
  indexes,
  stats,
  backtests,
  backtestPending,
  days,
  onDaysChange,
  activeId,
  onSelect,
  onDelete,
  onCreate,
  onFork,
  onForkSaved,
  onRefresh,
}: Props) {
  const [query, setQuery] = useState("");

  // Newest-touched first — the strat you were just editing leads the grid.
  const sorted = useMemo(
    () =>
      [...indexes]
        .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
        .filter((idx) => matchesQuery(query, idx.name, filterChips(idx))),
    [indexes, query],
  );
  const recommended = useMemo(
    () => DEFAULT_STRATS.filter((t) => matchesQuery(query, t.name, filterChips(t.params), t.description)),
    [query],
  );
  const oldest = sorted.reduce<number | null>((acc, i) => {
    const at = backtests?.[i.id]?.at;
    if (!at) return acc;
    return acc === null || at < acc ? at : acc;
  }, null);
  const windowLabel = days === 1 ? "24h" : `${days}d`;

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <div className="flex items-baseline gap-3 min-w-0">
          <span
            className="text-[16px] font-bold tracking-[0.2em] text-pixel-white uppercase shrink-0"
            style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
          >
            STRAT HUB
          </span>
          <span className="text-[12px] font-mono text-pixel-gray truncate">
            {indexes.length} saved · every card backtested over the last {windowLabel}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Search — one box over both shelves, so "find me a sports strat"
              is answered by the saved ones AND the recommendations at once. */}
          <div className="relative">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="search strats…"
              className="w-[200px] pl-6 pr-6 py-1 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-pixel-border text-[11.5px] font-mono text-pixel-white placeholder:text-pixel-gray/70 focus:outline-none focus:border-green-400/60"
            />
            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[11px] text-pixel-gray pointer-events-none">⌕</span>
            {query && (
              <button
                onClick={() => setQuery("")}
                title="Clear"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[12px] text-pixel-gray hover:text-pixel-white"
              >
                ×
              </button>
            )}
          </div>
          {/* Backtest window — the N in "N-DAY BACKTEST". Every card re-runs
              (or repaints from its stored run) at the new window together, so
              the grid stays comparable. */}
          <div className="flex items-center rounded-[var(--radius-sm)] border border-pixel-border overflow-hidden">
            {HUB_WINDOWS.map((d) => (
              <button
                key={d}
                onClick={() => onDaysChange(d)}
                title={`Backtest every card over the last ${d === 1 ? "24 hours" : `${d} days`}`}
                className={`px-2 py-1 text-[11px] font-mono transition-colors ${
                  d === days
                    ? "bg-amber-400/[0.12] text-amber-400"
                    : "text-pixel-gray hover:text-pixel-white"
                }`}
              >
                {d}D
              </button>
            ))}
          </div>
          {onRefresh && (
            <button
              onClick={onRefresh}
              title={`Re-run every strat's ${windowLabel} backtest now${oldest ? ` — oldest is ${timeAgo(oldest)}` : ""}`}
              className="shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded-[var(--radius-sm)] border border-pixel-border text-[11px] font-mono text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
            >
              ↻ RERUN
              {oldest && <span className="opacity-60">{timeAgo(oldest)}</span>}
            </button>
          )}
        </div>
      </div>

      {/* Card grid */}
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
        {sorted.map((idx) => {
          const isActive = idx.id === activeId;
          const enabledTraders = idx.traders.filter((t) => t.enabled !== false).length;
          const chips = filterChips(idx);
          // The card's headline: this strat replayed over the last 24h.
          const bt = backtests?.[idx.id];
          const running = backtestPending?.has(idx.id) ?? false;
          // Always render the viewer's live money row — an untraded strat
          // reads $0 rather than hiding it, so cards compare at a glance.
          const money = stats?.[idx.id];
          const totalPnl = money?.totalPnl ?? 0;
          return (
            <div
              key={idx.id}
              onClick={() => onSelect(idx.id)}
              className={`market-card group cursor-pointer flex flex-col ${isActive ? "market-card-selected" : ""}`}
            >
              {/* Top: status + updated */}
              <div className="flex items-center justify-between mb-2.5">
                <span className="flex items-center gap-1.5 text-[10.5px] tracking-[0.16em] uppercase font-semibold">
                  {idx.liveEnabled ? (
                    <>
                      <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                      <span className="text-green-400">LIVE</span>
                    </>
                  ) : (
                    <span className="text-pixel-gray">DRAFT</span>
                  )}
                  {isActive && (
                    <span className="ml-1 px-1.5 py-0.5 border border-green-400/60 text-green-400 rounded-full text-[9px] tracking-[0.1em]">
                      ACTIVE
                    </span>
                  )}
                </span>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-pixel-gray font-mono">{timeAgo(idx.updatedAt)}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); onForkSaved(idx.id); }}
                    title={`Fork "${idx.name}" — an independent copy, stopped and un-funded, ready to rename`}
                    className="text-[12px] leading-none text-pixel-gray hover:text-green-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    ⑂
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDelete(idx.id); }}
                    title="Delete strat"
                    className="text-[13px] leading-none text-pixel-gray hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    ×
                  </button>
                </div>
              </div>

              {/* Name */}
              <div className="text-[16px] font-semibold tracking-[-0.01em] text-pixel-white leading-[1.35] mb-3 line-clamp-2 font-mono">
                {idx.name}
              </div>

              {/* ── The headline: the N-day backtest ──
                  Every card is replayed over the same window through the same
                  engine the BACKTEST tab and the live session run, so these
                  numbers are comparable across the whole hub. */}
              <BacktestBlock
                bt={bt}
                running={running}
                days={days}
                emptyLabel={enabledTraders === 0 ? "no traders to copy" : "queued"}
              />

              {/* Params */}
              <div className="grid grid-cols-3 gap-1.5 mb-2.5">
                {[
                  { label: "TRADERS", value: `${enabledTraders}${enabledTraders !== idx.traders.length ? `/${idx.traders.length}` : ""}` },
                  { label: "CAPITAL", value: `$${idx.capital ?? 1000}` },
                  { label: "TRADE", value: `$${idx.minTrade ?? 5}–${idx.maxTrade ?? 100}` },
                ].map((p) => (
                  <div key={p.label} className="px-2 py-1.5 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-[var(--border)]">
                    <div className="text-[9px] text-pixel-gray font-semibold tracking-[0.14em]">{p.label}</div>
                    <div className="text-[12.5px] text-pixel-white font-mono truncate">{p.value}</div>
                  </div>
                ))}
              </div>

              {/* Filter chips — what slice of the watched flow this strat copies */}
              {chips.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2.5">
                  {chips.map((c) => (
                    <span
                      key={c}
                      className="px-1.5 py-0.5 text-[10px] font-mono text-cyan-400 border border-cyan-400/40 rounded-full truncate max-w-full"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}

              <div className="flex-1" />

              {/* Real money footer — what THIS strat has in play and its
                  cumulative live P&L (engine ledger, not the sim). Deliberately
                  not the wallet's USDC: that number is shared by every strat, so
                  printing it per card told the user each one held the whole
                  balance. */}
              <div className="flex items-center justify-between gap-2 pt-2.5 mt-1 border-t border-[var(--border)] text-[11px] font-mono">
                <span
                  className="text-pixel-gray truncate"
                  title={
                    money?.openPositions
                      ? `${fmtUsd(money.openValue)} across ${money.openPositions} open position(s)`
                      : "This strat holds no positions"
                  }
                >
                  LIVE {money?.openPositions ? `${fmtUsd(money.openValue)} in play` : "flat"}
                </span>
                <span
                  className={`shrink-0 ${totalPnl > 0 ? "text-green-400" : totalPnl < 0 ? "text-red-400" : "text-pixel-gray"}`}
                  title={money ? `$${money.moneyIn.toFixed(2)} deployed · realized ${fmtUsd(money.realized)} · unrealized ${fmtUsd(money.unrealized)}` : "No fills from your wallet in this strat yet"}
                >
                  {money ? `${totalPnl >= 0 ? "+" : ""}${fmtUsd(totalPnl)}` : "—"}
                </span>
              </div>
            </div>
          );
        })}

        {/* New-strat card */}
        <button
          onClick={onCreate}
          className="min-h-[180px] rounded-[var(--radius-lg)] border border-dashed border-pixel-border grid place-items-center text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
        >
          <div className="text-center">
            <div className="text-[22px] leading-none mb-1.5">+</div>
            <div className="text-[11px] font-mono font-semibold tracking-[0.14em]">NEW STRAT</div>
          </div>
        </button>
      </div>

      {/* Recommended strats — curated recipes forked into user-owned strats,
          each one BACKTESTED over the same window as the saved cards above.
          A recommendation you can't check isn't a recommendation; the number
          on the card is this recipe seeded with today's leaderboard and
          replayed, which is exactly what forking it gives you. */}
      <div className="flex items-baseline gap-3 mt-8 mb-4">
        <span
          className="text-[13px] font-bold tracking-[0.2em] text-pixel-white uppercase"
          style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
        >
          RECOMMENDED
        </span>
        <span className="text-[12px] font-mono text-pixel-gray">
          {recommended.length} recipes · backtested over the last {windowLabel} with today&apos;s leaderboard · click to fork
        </span>
      </div>
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
        {recommended.map((t) => {
          const chips = filterChips(t.params);
          const key = templateBacktestKey(t.slug);
          const bt = backtests?.[key];
          const running = backtestPending?.has(key) ?? false;
          return (
            <div
              key={t.slug}
              onClick={() => onFork(t)}
              className="market-card group cursor-pointer flex flex-col"
            >
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-[10.5px] tracking-[0.16em] uppercase font-semibold text-cyan-400">
                  TEMPLATE
                </span>
                <span className="text-[11px] font-mono text-pixel-gray opacity-60 group-hover:opacity-100 group-hover:text-green-400 transition-opacity">
                  ⑂ FORK
                </span>
              </div>
              <div className="text-[16px] font-semibold tracking-[-0.01em] text-pixel-white leading-[1.35] mb-2 font-mono">
                {t.name}
              </div>
              {/* Same block, same replay, same window as a saved strat's card. */}
              <BacktestBlock bt={bt} running={running} days={days} emptyLabel="queued" />
              <div className="text-[11.5px] leading-snug text-pixel-gray mb-3">{t.description}</div>
              {chips.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2.5">
                  {chips.map((c) => (
                    <span
                      key={c}
                      className="px-1.5 py-0.5 text-[10px] font-mono text-cyan-400 border border-cyan-400/40 rounded-full truncate max-w-full"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
              <div className="flex-1" />
              <div className="pt-2.5 mt-1 border-t border-[var(--border)] text-[10.5px] font-mono text-pixel-gray">
                seeds top {t.seed.count ?? 10} traders
                {t.seed.category ? ` · ${t.seed.category}` : ""}
                {t.seed.days ? ` · ${t.seed.days}d` : ""}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
