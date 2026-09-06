"use client";

// One wallet on the board, as a card.
//
// A table row prices a trader with a single figure — "+142% over 7d" — and
// that figure cannot tell you whether the wallet ground it out or won it in
// one lucky hour and gave half of it back. Those are not the same trader to
// copy, and copying is what this page is for. The card exists to put the
// evidence next to the claim: the curve is drawn at card width, and the two
// numbers that describe its *shape* rather than its endpoint — how high it
// got, and how far it fell from there — sit directly under it.
//
// The reading order is deliberate and always the same, so twenty cards scan
// as one thing:
//
//   who   → rank, identicon, wallet, when it last traded
//   what  → the window's return, and the dollars behind it
//   how   → the curve, its high, its deepest fall
//   proof → win rate, closes, sharpe, volume, equity — the ranked one lit
//   act   → copy it, or open it
//
// Nothing here fetches. The curve arrives as a prop from `lib/curves`, which
// pages the whole visible grid through one request per screenful, so a card
// can never become a request of its own.

import Link from "next/link";
import type { ReactNode } from "react";
import {
  TopTrader, TraderCurve, fmtPnl, fmtUsd, fmtPct, shortAddr, ago,
  defensibleWin, sharpeMeasured, MIN_CLOSES,
} from "../lib/api";
import { Identicon, Medal, Spark } from "./BoardBits";
import { isCoreCoin } from "./TraderCell";

/** Which stat the board is currently ranked by, so the card can light it. */
export type CardStat = "roi" | "pnl" | "volume" | "account_value" | "win_rate" | "trades" | "sharpe";

/** One proof cell. `lit` marks the stat the board is ranked by — the reason
 *  this card is where it is in the grid. */
function Stat({ label, value, sub, lit, title, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; lit?: boolean; title?: string;
  tone?: "win" | "loss";
}) {
  const toneCls = tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : "text-ink/90";
  return (
    <div title={title} className={`min-w-0 rounded-md px-2 py-1.5 transition-colors
      ${lit ? "bg-accent/[0.07] shadow-[inset_0_0_0_1px_rgb(var(--c-accent)/0.22)]" : ""}`}>
      <div className={`eyebrow !text-[9px] truncate ${lit ? "!text-accent" : ""}`}>{label}</div>
      <div className={`num mt-1 text-[13px] leading-none ${toneCls}`}>{value}</div>
      {sub != null && <div className="num mt-1 text-[9px] leading-none text-dim truncate">{sub}</div>}
    </div>
  );
}

export default function TraderCard({
  t, rank, days, curve, picked, onPick, statKey, enrichNote,
}: {
  t: TopTrader;
  rank: number;
  days: number;
  /** `undefined` while the page it belongs to is still in flight. */
  curve?: TraderCurve;
  picked: boolean;
  onPick: () => void;
  /** The board's current sort — its cell is lit on every card. */
  statKey: CardStat;
  /** Why an unmeasured row has no win rate, in the board's own words. */
  enrichNote: string;
}) {
  const roiUp = (t.roi ?? 0) >= 0;
  const measured = t.win_rate >= 0;
  const win = defensibleWin(t);
  const core = t.coins.filter(isCoreCoin);
  const dexOnly = core.length === 0 && t.coins.length > 0;

  return (
    <div className={`group panel panel-hover relative flex flex-col p-4
      ${picked ? "!border-accent/45 shadow-glow" : ""}`}>
      {/* ── who ── */}
      <div className="flex items-start gap-2.5">
        <Medal rank={rank} />
        <Link href={`/trader/${t.address}?days=${days}`} title={t.address}
          className="min-w-0 flex items-center gap-2 text-ink/90 hover:text-accent transition-colors">
          <Identicon address={t.address} size={20} />
          <span className="min-w-0">
            <span className="block font-mono text-[13px] leading-none truncate">{shortAddr(t.address)}</span>
            <span className="block text-[10px] leading-none mt-1 text-dim">
              traded {t.last_active > 0 ? ago(t.last_active) : "≤24h ago"}
            </span>
          </span>
        </Link>
        <label className="ml-auto shrink-0 flex items-center gap-1.5 cursor-pointer text-[9px] uppercase tracking-wider text-dim hover:text-ink transition-colors"
          title="Pick this wallet for a strat basket">
          <input type="checkbox" className="accent-accent" checked={picked} onChange={onPick} />
          pick
        </label>
      </div>

      {/* ── what ── the window's number, and the dollars behind it */}
      <div className="mt-4 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className={`text-[26px] leading-none font-semibold tracking-tight ${roiUp ? "text-win" : "text-loss"}`}
            title={t.account_value > 0 ? `return on ${fmtUsd(t.account_value)} of equity` : undefined}>
            {t.roi == null ? "—" : `${t.roi >= 0 ? "+" : ""}${fmtPct(t.roi, 1)}`}
          </div>
          <div className="eyebrow mt-1.5">roi · {days}d</div>
        </div>
        <div className="text-right shrink-0">
          <div className={`num text-[15px] leading-none ${t.pnl >= 0 ? "text-win/85" : "text-loss/85"}`}>
            {fmtPnl(t.pnl)}
          </div>
          <div className="eyebrow mt-1.5">pnl · net of fees</div>
        </div>
      </div>

      {/* ── how ── the shape behind that number.
          Fixed height in every state so a grid of cards never reflows as
          curves land one page at a time. */}
      <div className="mt-3 h-[58px]">
        {curve == null ? (
          <div className="skeleton h-full w-full opacity-60" />
        ) : curve.available ? (
          <Link href={`/trader/${t.address}?days=${days}`}
            aria-label={`${shortAddr(t.address)} — ${days} day pnl curve`}
            title={
              `${days}d pnl curve · ends ${fmtPnl(curve.pnl)} · high ${fmtPnl(curve.high)}` +
              ` · low ${fmtPnl(curve.low)} · deepest fall ${fmtUsd(curve.max_drawdown)}` +
              (curve.max_drawdown_pct > 0 ? ` (${curve.max_drawdown_pct}% off its peak)` : "") +
              `\nsource: hyperliquid portfolio "${curve.period}" — the whole account` +
              ` (perps and spot), realised and unrealised`
            }
            className={`block h-full ${curve.pnl >= 0 ? "text-win" : "text-loss"}`}>
            <Spark points={curve.points} height={58} />
          </Link>
        ) : (
          <div className="grid h-full place-items-center rounded-md border border-dashed border-white/[0.08] px-3">
            <span className="text-[10px] leading-snug text-dim text-center">{curve.note ?? "no curve for this wallet"}</span>
          </div>
        )}
      </div>

      {/* The two things the endpoint cannot say: how good it got, and how far
          it fell from there. */}
      <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-dim">
        {curve?.available ? (
          <>
            <span title="Best the cumulative curve ever got inside this window">
              high <span className="num text-ink/70">{fmtPnl(curve.high)}</span>
            </span>
            <span title="Deepest peak → trough fall anywhere in the window — what you would have been down had you started at the worst moment">
              max fall{" "}
              <span className="num text-ink/70">
                {curve.max_drawdown > 0 ? fmtUsd(curve.max_drawdown) : "none"}
                {curve.max_drawdown_pct > 0 ? ` · ${curve.max_drawdown_pct}%` : ""}
              </span>
            </span>
          </>
        ) : (
          <span className="opacity-0" aria-hidden>·</span>
        )}
      </div>

      {/* ── proof ── */}
      <div className="mt-3 grid grid-cols-3 gap-1">
        <Stat
          label="win rate" lit={statKey === "win_rate"}
          value={measured ? fmtPct(t.win_rate, 0) : "—"}
          sub={measured ? (t.closes > 0 ? `${t.closes} closes` : `${t.trades} fills`) : "not measured"}
          tone={measured && t.win_rate >= 50 ? "win" : undefined}
          title={
            !measured ? enrichNote
              : `${t.wins} win / ${t.losses} loss over ${t.closes} closes, net of fees` +
                ` (${t.trades} fills total; opens can neither win nor lose).` +
                (win != null ? ` Defensible rate at this sample size: ${fmtPct(win, 0)}.` : "") +
                (t.closes < MIN_CLOSES ? " Too few closes to lean on." : "") +
                " Measured from perp fills."
          }
        />
        <Stat
          label="sharpe" lit={statKey === "sharpe"}
          value={measured && sharpeMeasured(t) ? t.sharpe.toFixed(2) : "—"}
          sub={measured ? `${t.sharpe_days}d of history` : "not measured"}
          title={
            !measured ? enrichNote
              : sharpeMeasured(t) ? `Annualised over ${t.sharpe_days} days of daily perp PnL.`
              : `Only ${t.sharpe_days} days of history — too few for a Sharpe ratio.`
          }
        />
        <Stat
          label="trades" lit={statKey === "trades"}
          value={measured ? t.trades.toLocaleString("en-US") : "—"}
          sub={measured && t.avg_trade_usd > 0 ? `${fmtUsd(t.avg_trade_usd)} avg` : measured ? "" : "not measured"}
          title={measured ? `Every perp fill in the window, opens included.` : enrichNote}
        />
        <Stat
          label="volume" lit={statKey === "volume"}
          value={t.volume > 0 ? fmtUsd(t.volume) : "—"} sub={`${days}d`}
          title="Notional traded in the window, as Hyperliquid's leaderboard reports it."
        />
        <Stat
          label="equity" lit={statKey === "account_value"}
          value={t.account_value > 0 ? fmtUsd(t.account_value) : "—"} sub="account value"
          title="Current account value — what the roi above is a return on."
        />
        <Stat
          label="profit factor"
          value={t.profit_factor < 0 ? (measured ? "∞" : "—") : t.profit_factor.toFixed(2)}
          sub={measured && t.worst_close < 0 ? `worst ${fmtPnl(t.worst_close)}` : measured ? "" : "not measured"}
          tone={t.profit_factor > 1 || (measured && t.profit_factor < 0) ? "win" : undefined}
          title={
            !measured ? enrichNote
              : t.profit_factor < 0
                ? "Σ wins ÷ |Σ losses| — this wallet had no losing close in the window, so the ratio is undefined rather than good."
                : "Σ wins ÷ |Σ losses| across closed trades, net of fees. Above 1 makes money."
          }
        />
      </div>

      {/* ── what it trades ── */}
      <div className="mt-3 flex flex-wrap gap-1" title={core.join(", ") || t.coins.join(", ")}>
        {core.slice(0, 5).map((c) => <span key={c} className="pill">{c}</span>)}
        {core.length > 5 && <span className="pill">+{core.length - 5}</span>}
        {dexOnly && <span className="pill opacity-60">builder dex only</span>}
        {t.coins.length === 0 && (
          <span className="pill opacity-50" title={enrichNote}>coins not measured</span>
        )}
      </div>

      {/* ── act ── */}
      <div className="mt-4 pt-3 border-t border-white/[0.05] flex items-center gap-2">
        <Link href={`/follows/new?leader=${t.address}`} className="btn-ghost flex-1"
          title="Copy this wallet — same destination as the board's copy button">copy</Link>
        <Link href={`/trader/${t.address}?days=${days}`} className="btn flex-1"
          title="Fills, round trips and the full curve">details →</Link>
      </div>
    </div>
  );
}
