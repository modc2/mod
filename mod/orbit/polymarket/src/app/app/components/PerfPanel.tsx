"use client";

// ── PerfPanel — the ONE performance layout LIVE and BACKTEST both render ──
//
// The two tabs answer the same question ("what is this strat worth, and how
// did it get there?") off the same accounting, so they must LOOK the same:
// same metric vocabulary, same typography, same row order, same chart chrome,
// same EquityChart props. Previously each tab hand-rolled its own header and
// they drifted — LIVE led with a 19px EQUITY headline in muted sans labels and
// boxed its chart; BACKTEST led with P&L in mono terminal labels and drew a
// naked chart with a different marker line AND a different default unit. Same
// numbers, two different-looking panels.
//
// Fix is structural, not cosmetic: this component owns the whole skeleton, and
// the call sites pass DATA plus their mode-specific controls. Divergence can't
// creep back in because there's only one copy of the markup — the same reason
// the strat playbook lives in one Strat class.
//
// What each side supplies, with matched meanings:
//   EQUITY / CASH / POSITIONS — mark-to-market wallet state (live: real
//     wallet; backtest: the simulated wallet at the end of the replay)
//   UNREAL  — open-position P&L vs average entry
//   P&L/ROI — equity change across the window the chart plots
//   costs   — executed notional + the friction it paid, on the ONE cost model
//             both sides book (lib/fees.ts): per-market taker fees measured
//             off real fills, and Polygon gas priced per deployment

import { useEffect, useState, type ReactNode } from "react";
import EquityChart, { type EquitySnapshot, type EquityMarker } from "./EquityChart";
import { fmtGasUsd, type CostBreakdown } from "../lib/fees";

// ── Shared data shapes ─────────────────────────────────────────────────

export interface PerfStats {
  /** Cash + mark-to-market positions. */
  equity: number;
  cash: number;
  positions: number;
  /** Open-position P&L vs average entry. */
  unrealized: number;
  /** Unrealized as a % of cost basis; null when there's no basis. */
  unrealizedPct: number | null;
  /** Equity change across the plotted window. */
  pnl: number;
  /** `pnl` as a % of the window's starting equity; null when unknown. */
  roiPct: number | null;
  /** Tooltip for P&L — the two modes measure the same delta over different
      windows ("since capital" vs "since the oldest snapshot"), so each says so. */
  pnlTitle?: string;
}

export interface PerfCosts {
  /** Total executed notional. */
  amount: number;
  fees: number;
  gas: number;
  /** Executed trade count. */
  txs: number;
  /** P&L before fees/gas. */
  gross: number;
  /** The full cost model behind `fees` + `gas` — per-rate split, effective
      bps, rebate tier, and whether each rate was measured or modelled.
      Renders the COSTS drawer; omit it and the row still shows the totals. */
  breakdown?: CostBreakdown;
  /** Trades the wallet could not place (backtest only today). */
  skipped?: number;
  skippedTitle?: string;
  /** How much of the result was settled at a looked-up resolution vs valued
      at the last price anyone printed — backtest only, since LIVE settles
      against the real wallet. A replay whose exits are mostly MARKED is a
      hypothesis, and the panel has to say so rather than quoting the P&L with
      the same confidence as a verified one. */
  settled?: { resolved: number; marked: number; markedUsd: number };
  /** ORIGINATION replays only: how much price tape the replay stood on
      ("96/288 CANDLES"), and the long form for the tooltip. A momentum strat
      replayed over the tail of its window must say so — the alternative is a
      P&L that reads as a full day's evidence and isn't. */
  tape?: { label: string; title: string; partial: boolean };
}

export interface PerfPosition {
  key: string;
  market: string;
  outcome: string;
  size: number;
  avgPrice: number;
  curPrice: number;
  value: number;
  pnlUsd: number;
  /** Small pill after the market name (e.g. REDEEM on a resolved market). */
  badge?: string;
  badgeTitle?: string;
  rowTitle?: string;
  /** The strat that actually opened this position, when it ISN'T the one
      whose panel you're reading.
      One wallet funds several strats and they all trade out of the same
      deposit wallet, so this table — which lists the WALLET's holdings — will
      show rows another strat bought. Unlabelled, under a strat-scoped header,
      that reads as "my BTC strat bought a tennis match". It didn't; the copy
      strat next to it did. */
  owner?: { label: string; title: string };
}

type ChartMode = "line" | "pie";

// Chart-mode choice is a display preference, not per-tab state — remembering
// it globally keeps LIVE and BACKTEST showing the same view as you flip.
const CHART_MODE_KEY = "polymarket.perf.chartMode";

// ── Formatters ─────────────────────────────────────────────────────────

function fmtUsd(v: number): string {
  if (!Number.isFinite(v)) return "$0.00";
  return v >= 1000
    ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
    : `$${v.toFixed(2)}`;
}

function fmtSigned(v: number): string {
  return `${v >= 0 ? "+" : "-"}${fmtUsd(Math.abs(v))}`;
}

// ── Row primitives — every label/value pair in the panel goes through these,
//    so the two tabs can't drift on type size, tracking, or colour. ──

function Metric({
  label,
  value,
  tone = "white",
  title,
}: {
  label: string;
  value: ReactNode;
  tone?: "white" | "gray" | "amber" | "signed";
  title?: string;
}) {
  const cls =
    tone === "amber" ? "text-amber-400"
    : tone === "gray" ? "text-pixel-gray"
    : "text-pixel-white";
  return (
    <div className="flex items-baseline gap-1.5" title={title}>
      <span className="text-[12px] text-pixel-gray tracking-[0.15em]">{label}</span>
      <span className={cls}>{value}</span>
    </div>
  );
}

function SignedMetric({
  label,
  value,
  text,
  dim = false,
  title,
}: {
  label: string;
  value: number;
  text: ReactNode;
  dim?: boolean;
  title?: string;
}) {
  const tone = value >= 0
    ? (dim ? "text-green-400/70" : "text-green-400")
    : (dim ? "text-red-400/70" : "text-red-400");
  return (
    <div className="flex items-baseline gap-1.5" title={title}>
      <span className="text-[12px] text-pixel-gray tracking-[0.15em]">{label}</span>
      <span className={tone}>{text}</span>
    </div>
  );
}

const Dot = () => <span className="text-pixel-border/60">·</span>;

// ── COSTS drawer — where the friction actually went ───────────────────
//
// The FEES cell is one number and one number cannot answer the question people
// actually have, which is "why is it that much, and do you even know?". So the
// drawer says three things:
//
//   1. WHAT WAS CHARGED, split by the rate that charged it. A 7% crypto market
//      and a fee-free geopolitics market in the same replay are two different
//      businesses and the average of them means nothing.
//   2. HOW BIG IT IS relative to something — bps of notional traded, and the
//      share of gross P&L it ate.
//   3. WHERE THE RATE CAME FROM. A rate measured off a real fill's USDC is a
//      fact; a rate inferred from the market's title is a model, and the row
//      says how many of each so nobody mistakes one for the other.

function CostsDrawer({ bd, gross }: { bd: CostBreakdown; gross: number }) {
  const total = bd.fees + bd.gas;
  const cov = bd.coverage;
  const measuredPct = cov.markets > 0
    ? ((cov.measured + cov.feeFree) / (cov.measured + cov.feeFree + cov.modelled)) * 100
    : 0;
  return (
    <div className="border-t border-pixel-border/40 pt-2 space-y-2">
      <div className="flex items-center gap-4 flex-wrap text-[12px] font-mono">
        <span className="text-pixel-gray tracking-[0.15em]">COSTS</span>
        <Dot />
        <span className="text-pixel-gray/80">
          fee = <span className="text-pixel-white">rate x p x (1-p) x shares</span>, taker side only
        </span>
        <Dot />
        <span className="text-pixel-gray/80">
          <span className="text-amber-400">{bd.effectiveBps.toFixed(0)} bps</span> of ${bd.volume.toFixed(0)} traded
        </span>
        {bd.dragPct != null && (
          <>
            <Dot />
            <span className={bd.dragPct > 50 ? "text-red-400" : "text-pixel-gray/80"}>
              ate <span className={bd.dragPct > 50 ? "text-red-400" : "text-amber-400"}>{bd.dragPct.toFixed(0)}%</span> of gross P&amp;L
            </span>
          </>
        )}
      </div>

      {bd.buckets.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] font-mono">
            <thead>
              <tr className="text-pixel-gray/70 tracking-[0.1em] text-left">
                <th className="font-normal py-0.5 pr-3">CATEGORY</th>
                <th className="font-normal py-0.5 pr-3">RATE</th>
                <th className="font-normal py-0.5 pr-3 text-right">FILLS</th>
                <th className="font-normal py-0.5 pr-3 text-right">NOTIONAL</th>
                <th className="font-normal py-0.5 pr-3 text-right">FEES</th>
                <th className="font-normal py-0.5 text-right">BPS</th>
              </tr>
            </thead>
            <tbody>
              {bd.buckets.map((b) => (
                <tr key={`${b.category}:${b.rate}`} className="border-t border-pixel-border/20">
                  <td className="py-0.5 pr-3 text-pixel-white">{b.category.toUpperCase()}</td>
                  <td className="py-0.5 pr-3 text-pixel-gray">
                    {b.rate > 0 ? `${(b.rate * 100).toFixed(0)}%` : "FREE"}
                  </td>
                  <td className="py-0.5 pr-3 text-right text-pixel-gray">{b.txs}</td>
                  <td className="py-0.5 pr-3 text-right text-pixel-gray">${b.volume.toFixed(2)}</td>
                  <td className="py-0.5 pr-3 text-right text-amber-400">${b.fees.toFixed(2)}</td>
                  <td className="py-0.5 text-right text-pixel-gray">
                    {b.volume > 0 ? ((b.fees / b.volume) * 10_000).toFixed(0) : "0"}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-pixel-border/40">
                <td
                  className="py-0.5 pr-3 text-pixel-gray"
                  title="Deploying the trading wallet, approving it and funding it. Fills, redeems and withdrawals are paid for by Polymarket's operator and relayer."
                >
                  GAS (POLYGON)
                </td>
                <td className="py-0.5 pr-3 text-pixel-gray/70" colSpan={3}>
                  {bd.gasQuote.gasPriceGwei.toFixed(1)} gwei · ${bd.gasQuote.polUsd.toFixed(3)}/POL
                  {bd.gasQuote.source === "fallback" && " (est)"}
                </td>
                <td className="py-0.5 pr-3 text-right text-amber-400">{fmtGasUsd(bd.gas)}</td>
                <td className="py-0.5 text-right text-pixel-gray/50">—</td>
              </tr>
              <tr className="border-t border-pixel-border/40">
                <td className="py-0.5 pr-3 text-pixel-white tracking-[0.1em]">TOTAL</td>
                <td className="py-0.5 pr-3" colSpan={3} />
                <td className="py-0.5 pr-3 text-right text-amber-400">${total.toFixed(2)}</td>
                <td className="py-0.5 text-right text-pixel-gray/50">—</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center gap-4 flex-wrap text-[11px] font-mono text-pixel-gray/70">
        <span
          title={
            "MEASURED means the rate was read straight off a real fill: the data-api reports the USDC that actually "
            + "moved, and the gap between that and price x size IS the fee. MODELLED means no fill was available for "
            + "that market, so its category's published rate was used instead."
          }
        >
          RATES <span className={measuredPct >= 60 ? "text-green-400" : "text-amber-400"}>{measuredPct.toFixed(0)}% MEASURED</span>
          {" "}({cov.measured} charged · {cov.feeFree} fee-free · {cov.modelled} modelled)
        </span>
        {bd.tier.rebate > 0 && (
          <span
            title={
              "Polymarket's Taker Rebate Program pays a share of your fees back daily, by 30-day weighted volume "
              + "(size x (1 - price) x category weight). NOT netted into the P&L above — a rebate only applies from the "
              + "moment you reach the tier, so counting it in a replay would be inventing money."
            }
          >
            <Dot /> REBATE TIER <span className="text-green-400">{bd.tier.name}</span>
            {" "}— {(bd.tier.rebate * 100).toFixed(0)}% back at ${Math.round(bd.weightedVolume).toLocaleString()} wV
          </span>
        )}
        <span title="Neither this replay nor the live engine charges itself for the bid/ask spread or for a limit order that never fills. Real friction is at least this much, never less.">
          <Dot /> SPREAD + UNFILLED LIMITS NOT MODELLED
        </span>
      </div>
      {gross > 0 && total > gross * 0.5 && (
        <div className="text-[11px] font-mono text-red-400">
          FEES ATE MOST OF THE EDGE — this strat trades in expensive markets or trades too often for what it wins.
        </div>
      )}
    </div>
  );
}

// ── Pie — cash vs positions, the other view of the same two series the
//    equity curve plots. Lives here so both tabs get it. ──

function PieChart({ liq, pos }: { liq: number; pos: number }) {
  const total = liq + pos;
  if (total <= 0) {
    return (
      <div className="flex items-center justify-center h-40 text-pixel-muted text-xs">
        No funds yet
      </div>
    );
  }
  const liqPct = (liq / total) * 100;
  const posPct = 100 - liqPct;

  // For a 2-slice pie we just need a single arc. SVG circle + dasharray
  // is way simpler than computing path arc d-strings for one slice.
  const r = 70;
  const c = 2 * Math.PI * r;
  const liqArc = (liq / total) * c;
  const posArc = c - liqArc;

  return (
    <div className="flex items-center gap-6">
      <svg viewBox="0 0 200 200" className="w-44 h-44 -rotate-90">
        <circle cx="100" cy="100" r={r} fill="none" stroke="#f59e0b" strokeWidth="36" />
        <circle cx="100" cy="100" r={r} fill="none"
          stroke="#10b981" strokeWidth="36"
          strokeDasharray={`${liqArc} ${posArc}`}
          strokeDashoffset="0" />
      </svg>
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: "#10b981" }} />
          <span className="text-pixel-muted">Cash</span>
          <span className="font-mono">{fmtUsd(liq)}</span>
          <span className="text-pixel-muted text-xs">({liqPct.toFixed(0)}%)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: "#f59e0b" }} />
          <span className="text-pixel-muted">Positions</span>
          <span className="font-mono">{fmtUsd(pos)}</span>
          <span className="text-pixel-muted text-xs">({posPct.toFixed(0)}%)</span>
        </div>
        <div className="border-t border-pixel-border pt-1 text-xs">
          <span className="text-pixel-muted mr-1">Total</span>
          <span className="font-mono">{fmtUsd(total)}</span>
        </div>
      </div>
    </div>
  );
}

// ── Open positions — one table shape for the real book and the sim book ──

const POS_COLS = "grid grid-cols-[16px_minmax(0,1fr)_44px_84px_64px_108px] items-center gap-x-3 px-2 py-1";

function PositionsTable({
  rows,
  totalValue,
  totalPnl,
  note,
}: {
  rows: PerfPosition[];
  totalValue: number;
  totalPnl: number;
  note?: ReactNode;
}) {
  return (
    <div className="bg-pixel-bg border border-pixel-border rounded-[var(--radius)] overflow-hidden">
      <div className="px-2 py-1.5 border-b border-pixel-border flex items-center justify-between">
        <span className="text-[12px] text-pixel-gray tracking-[0.15em]">
          OPEN POSITIONS · {rows.length}
        </span>
        {note && <span className="text-[9.5px] text-pixel-muted">{note}</span>}
      </div>
      <div className={`${POS_COLS} text-[9.5px] font-semibold uppercase tracking-[0.12em] text-pixel-muted border-b border-pixel-border/40`}>
        <span />
        <span>Market</span>
        <span className="text-right">Size</span>
        <span className="text-right" title="Average entry price → current price">Avg→Cur</span>
        <span className="text-right">Value</span>
        <span className="text-right" title="Unrealized P&L (current price vs avg entry)">P&L</span>
      </div>
      <div className="max-h-[220px] overflow-y-auto">
        {rows.map((p, i) => {
          const basis = p.avgPrice * p.size;
          const pct = basis > 0 ? (p.pnlUsd / basis) * 100 : 0;
          return (
            <div
              key={p.key}
              className={`${POS_COLS} text-[11px] font-mono border-b border-pixel-border/30 last:border-b-0 hover:bg-pixel-white/[0.03]`}
            >
              <span
                className={`text-center ${i === 0 ? "text-red-400 font-bold" : "text-pixel-muted"}`}
                title={i === 0 ? "Next to be sold when cash is freed" : `#${i + 1} in rotation order`}
              >
                {i === 0 ? "▸" : i + 1}
              </span>
              <span className="truncate min-w-0 text-pixel-white" title={p.rowTitle ?? `${p.market} · ${p.outcome}`}>
                {p.market}
                <span className={p.outcome.toLowerCase() === "no" ? "text-red-400/80" : "text-green-400/80"}> · {p.outcome}</span>
                {p.badge && (
                  <span
                    className="ml-1.5 text-[9px] px-1 py-px rounded-full border border-amber-400/50 text-amber-400 font-sans font-semibold tracking-wide"
                    title={p.badgeTitle}
                  >
                    {p.badge}
                  </span>
                )}
                {p.owner && (
                  <span
                    className="ml-1.5 text-[9px] px-1 py-px rounded-full border border-pixel-border text-pixel-muted font-sans font-semibold tracking-wide"
                    title={p.owner.title}
                  >
                    {p.owner.label}
                  </span>
                )}
              </span>
              <span className="text-right text-pixel-muted">{p.size.toFixed(1)}</span>
              <span className="text-right text-pixel-muted" title="avg entry → current">
                {Math.round(p.avgPrice * 100)}¢→{Math.round(p.curPrice * 100)}¢
              </span>
              <span className="text-right text-pixel-white">{fmtUsd(p.value)}</span>
              <span className={`text-right ${p.pnlUsd >= 0 ? "text-green-400" : "text-red-400"}`}>
                {p.pnlUsd >= 0 ? "+" : "-"}${Math.abs(p.pnlUsd).toFixed(2)}
                <span className="opacity-70"> {pct >= 0 ? "+" : ""}{pct.toFixed(0)}%</span>
              </span>
            </div>
          );
        })}
      </div>
      <div className={`${POS_COLS} py-1.5 text-[11px] font-mono border-t border-pixel-border bg-pixel-white/[0.02]`}>
        <span />
        <span className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-pixel-muted font-sans">Total</span>
        <span />
        <span />
        <span className="text-right text-pixel-white font-semibold">{fmtUsd(totalValue)}</span>
        <span className={`text-right font-semibold ${totalPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
          {totalPnl >= 0 ? "+" : "-"}${Math.abs(totalPnl).toFixed(2)}
        </span>
      </div>
    </div>
  );
}

// ── The panel ──────────────────────────────────────────────────────────

export default function PerfPanel({
  label,
  controls,
  notices,
  stats,
  costs,
  caption,
  history,
  markers,
  highlightT,
  onHoverMarker,
  emptyHint,
  loading = false,
  chartRef,
  positions = [],
  positionsNote,
  footer,
}: {
  /** Mode name in the header — "TEST" / "LIVE". */
  label: string;
  /** Mode-specific header controls (RUN/DAYS/FUNDS vs engine controls). */
  controls?: ReactNode;
  /** Warnings rendered above the header (both modes use the same styling). */
  notices?: ReactNode;
  stats: PerfStats;
  costs: PerfCosts;
  /** Chart caption prefix — e.g. "7D SIMULATED EQUITY" / "LIVE EQUITY". */
  caption: string;
  history: EquitySnapshot[];
  markers?: EquityMarker[];
  highlightT?: number | null;
  onHoverMarker?: (t: number | null) => void;
  emptyHint?: string;
  loading?: boolean;
  /** Scroll target for "VIEW TEST →" style jumps. */
  chartRef?: React.Ref<HTMLDivElement>;
  positions?: PerfPosition[];
  positionsNote?: ReactNode;
  /** Mode-specific tail content (LIVE's sell/redeem actions). */
  footer?: ReactNode;
}) {
  const [chartMode, setChartMode] = useState<ChartMode>("line");
  // Read after mount, not in the initializer — the server render has no
  // localStorage and a mismatch trips hydration.
  useEffect(() => {
    try {
      if (localStorage.getItem(CHART_MODE_KEY) === "pie") setChartMode("pie");
    } catch { /* storage unavailable — keep the line chart */ }
  }, []);
  const pickChartMode = (m: ChartMode) => {
    setChartMode(m);
    try { localStorage.setItem(CHART_MODE_KEY, m); } catch { /* quota full — non-fatal */ }
  };

  const costTotal = costs.fees + costs.gas;
  const [showCosts, setShowCosts] = useState(false);
  const bd = costs.breakdown;

  return (
    <div className="pixel-panel px-3 py-2.5 space-y-3">
      {notices}

      {/* ── Header — mode label + mode controls | P&L + ROI ── */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <span className="text-[14px] text-pixel-white tracking-[0.2em]">{label}</span>
          {controls}
        </div>
        <div className="flex items-center gap-4 text-[13px] font-mono">
          <SignedMetric
            label="P&L"
            value={stats.pnl}
            text={fmtSigned(stats.pnl)}
            title={stats.pnlTitle}
          />
          <SignedMetric
            label="ROI"
            value={stats.roiPct ?? 0}
            text={stats.roiPct == null ? "—" : `${stats.roiPct >= 0 ? "+" : ""}${stats.roiPct.toFixed(1)}%`}
          />
        </div>
      </div>

      {/* ── Wallet state — the same three series the curve plots, plus the
             open-position P&L riding on top of them. ── */}
      <div className="flex items-center justify-between flex-wrap gap-3 text-[13px] font-mono border-t border-pixel-border/40 pt-2">
        <div className="flex items-center gap-4">
          <Metric
            label="EQUITY"
            value={fmtUsd(stats.equity)}
            title="Free cash + mark-to-market value of open positions — what the account is worth right now."
          />
          <Dot />
          <Metric label="CASH" value={fmtUsd(stats.cash)} title="Uninvested USDC available to spend." />
          <Dot />
          <Metric label="POSITIONS" value={fmtUsd(stats.positions)} tone="amber" title="Mark-to-market value of all open positions." />
          <Dot />
          <SignedMetric
            label="UNREAL"
            value={stats.unrealized}
            text={
              <>
                {fmtSigned(stats.unrealized)}
                {stats.unrealizedPct != null && (
                  <span className="opacity-75"> ({stats.unrealizedPct >= 0 ? "+" : ""}{stats.unrealizedPct.toFixed(1)}%)</span>
                )}
              </>
            }
            title="Unrealized P&L across all open positions (current price vs avg entry)."
          />
        </div>
        <div className="flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          {(["line", "pie"] as ChartMode[]).map((m) => (
            <button
              key={m}
              onClick={() => pickChartMode(m)}
              className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
                chartMode === m ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
              }`}
            >
              {m === "line" ? "OVER TIME" : "PIE"}
            </button>
          ))}
        </div>
      </div>

      {/* ── Cost row — executed notional and the friction it paid. Both modes
             book the ONE cost model (lib/fees.ts), so the numbers agree. The
             totals live here; click COSTS for where each dollar went. ── */}
      <div className="flex items-center justify-between flex-wrap gap-3 text-[13px] font-mono border-t border-pixel-border/40 pt-2">
        <div className="flex items-center gap-4">
          <Metric label="AMOUNT" value={`$${costs.amount.toFixed(2)}`} title="Total notional traded in this window (sum of BUY/SELL amounts)" />
          <Dot />
          <Metric
            label="FEES"
            value={`$${costs.fees.toFixed(2)}`}
            tone="amber"
            title={
              "Polymarket's taker fee, charged by the matcher on every fill: rate x price x (1 - price) x shares. "
              + "The rate is per category (crypto 7%, sports/economics 5%, politics/finance 4%, geopolitics free) and the "
              + "dollar fee PEAKS at 50\u00a2 — a fill at 30\u00a2 and one at 70\u00a2 pay the same. Makers are never charged; "
              + "this console takes liquidity, so it always pays."
              + (bd && bd.volume > 0 ? ` Here: ${bd.effectiveBps.toFixed(0)} bps of the $${bd.volume.toFixed(0)} traded.` : "")
            }
          />
          <Dot />
          <Metric
            label="GAS"
            value={fmtGasUsd(costs.gas)}
            tone="amber"
            title={
              "Polygon gas — and almost none of it is trading. CLOB fills are matched on-chain by Polymarket's operator, "
              + "and redeems and withdrawals go through its relayer, so the trader pays for none of those. What you do pay "
              + "for is deploying the trading wallet once, approving it, and funding it. That makes gas a fixed few cents "
              + "per deployment, not a per-trade cost."
              + (bd ? ` Priced at ${bd.gasQuote.gasPriceGwei.toFixed(1)} gwei and $${bd.gasQuote.polUsd.toFixed(3)}/POL (${bd.gasQuote.source}).` : "")
            }
          />
          <Dot />
          <div className="flex items-baseline gap-1.5">
            <span className="text-[12px] text-pixel-gray tracking-[0.15em]">TOTAL</span>
            <span className="text-amber-400">${costTotal.toFixed(2)}</span>
            <span className="text-[12px] text-pixel-gray/70">({costs.txs} TXS)</span>
          </div>
          {bd && (
            <button
              onClick={() => setShowCosts((v) => !v)}
              className="text-[11px] tracking-[0.15em] px-2 py-0.5 border border-pixel-border/60 text-pixel-gray hover:text-pixel-white hover:border-pixel-border transition-colors"
              title="Where every cent of friction went — per category, per rate, and how much of it was measured rather than modelled"
            >
              {showCosts ? "HIDE COSTS" : "COSTS"} {showCosts ? "\u25be" : "\u25b8"}
            </button>
          )}
          {costs.skipped != null && costs.skipped > 0 && (
            <>
              <Dot />
              <span className="text-[12px] text-pixel-gray/70 tracking-wider" title={costs.skippedTitle}>
                {costs.skipped} SKIPPED
              </span>
            </>
          )}
          {costs.settled != null && costs.settled.resolved + costs.settled.marked > 0 && (
            <>
              <Dot />
              <span
                className={`text-[12px] tracking-wider ${costs.settled.marked > 0 ? "text-amber-400/80" : "text-pixel-gray/70"}`}
                title={
                  costs.settled.marked > 0
                    ? `${costs.settled.resolved} position(s) closed at the price the market actually resolved at; `
                      + `${costs.settled.marked} ($${costs.settled.markedUsd.toFixed(2)}) had no resolution on file and were valued at the last price a copied trader printed. `
                      + `Leaders trade their winners and let their losers expire, so unresolved marks read HIGH — treat this P&L as provisional until the server's resolution store catches up.`
                    : `Every closed position was settled at the price its market actually resolved at.`
                }
              >
                {costs.settled.resolved}/{costs.settled.resolved + costs.settled.marked} SETTLED
              </span>
            </>
          )}
          {costs.tape && (
            <>
              <Dot />
              <span
                className={`text-[12px] tracking-wider ${costs.tape.partial ? "text-amber-400/80" : "text-pixel-gray/70"}`}
                title={costs.tape.title}
              >
                {costs.tape.label}
              </span>
            </>
          )}
        </div>
        <SignedMetric label="GROSS" value={costs.gross} text={fmtSigned(costs.gross)} dim />
      </div>

      {bd && showCosts && <CostsDrawer bd={bd} gross={costs.gross} />}

      {/* ── Chart — caption + the SAME EquityChart, same props, both modes.
             markerLine="pos" because a fill moves the Positions line (BUY:
             pos ↑ cash ↓); the "%" unit is the only one that means the same
             thing on both sides, since it strips deposits/withdrawals — a
             backtest has none, so % is pure return there too. ── */}
      <div ref={chartRef}>
        {loading ? (
          <div className="p-6 text-center">
            <span className="text-[13px] text-pixel-gray animate-pulse">LOADING...</span>
          </div>
        ) : (
          <div className="space-y-1">
            <div className="flex items-center justify-between px-1">
              <span className="text-[12px] text-pixel-gray tracking-[0.15em]">
                {caption} · CASH + POSITIONS (MTM) — SAME CURVE, SAME ACCOUNTING
              </span>
              <span className={`text-[13px] font-mono ${stats.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                {fmtSigned(stats.pnl)}
              </span>
            </div>
            <div className="bg-pixel-bg border border-pixel-border rounded p-3">
              {chartMode === "pie" ? (
                <PieChart liq={stats.cash} pos={stats.positions} />
              ) : (
                <EquityChart
                  history={history}
                  markers={markers}
                  markerLine="pos"
                  defaultUnit="%"
                  highlightT={highlightT}
                  onHoverMarker={onHoverMarker}
                  emptyHint={emptyHint}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {positions.length > 0 && (
        <PositionsTable
          rows={positions}
          totalValue={stats.positions}
          totalPnl={stats.unrealized}
          note={positionsNote}
        />
      )}

      {footer}
    </div>
  );
}
