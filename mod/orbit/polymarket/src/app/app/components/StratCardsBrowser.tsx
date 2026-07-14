"use client";

// Full-viewport strat browser: every saved strat as a card — name, LIVE
// state, trader count, key params, filter chips and the cached backtest
// snapshot — so strats can be compared at a glance instead of scanned as
// dropdown rows. Opened from the ▦ button beside the header strat picker;
// purely presentational — select / delete / create are handed back to the
// picker (the single strat manager) so indexStore + server sync stay in
// one place.

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { DEFAULT_STRATS, type StratTemplate } from "../lib/defaultStrats";
import type { SavedIndex } from "../lib/types";

interface Props {
  open: boolean;
  indexes: SavedIndex[];
  activeId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCreate: () => void;
  onFork: (t: StratTemplate) => void;
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
function filterChips(idx: Pick<SavedIndex, "marketQuery" | "tradeFilters">): string[] {
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
  return chips;
}

export default function StratCardsBrowser({
  open,
  indexes,
  activeId,
  onClose,
  onSelect,
  onDelete,
  onCreate,
  onFork,
}: Props) {
  // Escape closes, and the page behind doesn't scroll while browsing.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  // Newest-touched first — the strat you were just editing leads the grid.
  const sorted = [...indexes].sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));

  // Portal to <body>: the picker lives inside the TopBar, whose
  // backdrop-blur makes the header the containing block for fixed
  // descendants — rendered in place, inset-0 would collapse to the
  // 48px header strip instead of the viewport.
  return createPortal(
    <div
      className="fixed inset-0 z-50 overflow-y-auto backdrop-blur-md"
      style={{ background: "rgb(var(--pixel-black-rgb)/0.88)" }}
      onClick={onClose}
    >
      <div className="max-w-[1400px] mx-auto px-4 py-6" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-baseline gap-3">
            <span
              className="text-[16px] font-bold tracking-[0.2em] text-pixel-white uppercase"
              style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
            >
              STRATS
            </span>
            <span className="text-[12px] font-mono text-pixel-gray">
              {indexes.length} saved · click a card to switch
            </span>
          </div>
          <button
            onClick={onClose}
            title="Close (Esc)"
            className="grid place-items-center w-8 h-8 rounded-[var(--radius-sm)] border border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors text-[15px]"
          >
            ×
          </button>
        </div>

        {/* Card grid */}
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
          {sorted.map((idx) => {
            const isActive = idx.id === activeId;
            const enabledTraders = idx.traders.filter((t) => t.enabled !== false).length;
            const pnl = idx.lastPnlAfterCosts ?? idx.lastPnl;
            const chips = filterChips(idx);
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

                {/* Backtest snapshot footer */}
                <div className="flex items-center justify-between pt-2.5 mt-1 border-t border-[var(--border)]">
                  {pnl !== undefined ? (
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-[9.5px] text-pixel-gray font-semibold tracking-[0.14em]">PNL</span>
                        <span className={`text-[13px] font-mono font-semibold ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {pnl < 0 ? "-" : "+"}${Math.abs(pnl).toFixed(0)}
                        </span>
                      </div>
                      {idx.lastRoi1k !== undefined && (
                        <div className="flex items-baseline gap-1.5">
                          <span className="text-[9.5px] text-pixel-gray font-semibold tracking-[0.14em]">ROI</span>
                          <span className={`text-[13px] font-mono ${idx.lastRoi1k >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {idx.lastRoi1k >= 0 ? "+" : ""}{idx.lastRoi1k.toFixed(1)}%
                          </span>
                        </div>
                      )}
                      {idx.lastTradeCount !== undefined && (
                        <span className="text-[11px] text-pixel-gray font-mono">{idx.lastTradeCount}tr</span>
                      )}
                    </div>
                  ) : (
                    <span className="text-[11px] text-pixel-gray font-mono">no backtest yet</span>
                  )}
                  {idx.lastBacktestAt && (
                    <span className="text-[10px] text-pixel-gray font-mono shrink-0" title="Last backtest">
                      {timeAgo(idx.lastBacktestAt)}
                    </span>
                  )}
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

        {/* Default strats — curated recipes forked into user-owned strats;
            traders are seeded from the LIVE leaderboard at fork time. */}
        <div className="flex items-baseline gap-3 mt-8 mb-4">
          <span
            className="text-[13px] font-bold tracking-[0.2em] text-pixel-white uppercase"
            style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
          >
            DEFAULT STRATS
          </span>
          <span className="text-[12px] font-mono text-pixel-gray">
            fork one to customize — traders seeded from today&apos;s leaderboard
          </span>
        </div>
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
          {DEFAULT_STRATS.map((t) => {
            const chips = filterChips(t.params);
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
    </div>,
    document.body,
  );
}
