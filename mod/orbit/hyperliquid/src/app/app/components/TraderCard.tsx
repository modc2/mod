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
// The card fetches nothing at the board's window. The curve arrives as a
// prop from `lib/curves`, which pages the whole visible grid through one
// request per screenful. The one exception is the card's own window toggle:
// re-windowing a single card borrows the hover path (`loadCurve` — deduped,
// cached, three in flight), so a click costs at most one request and a
// re-click costs none.

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import {
  TopTrader, TraderCurve, fmtPnl, fmtUsd, fmtPct, shortAddr, ago,
  defensibleWin, sharpeMeasured, MIN_CLOSES,
} from "../lib/api";
import { Identicon, Medal, Spark } from "./BoardBits";
import { isCoreCoin } from "./TraderCell";
import { fresh, loadCurve } from "../lib/curves";

// The windows a single card can re-view itself at — HL's official ones.
// A custom board window (say 14d) joins the row so the board's own view
// is always one of the choices.
const CARD_WINDOWS = [1, 7, 30];

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

const fmtDay = (n: number) =>
  `${n >= 0 ? "+" : "-"}$${Math.abs(n) >= 100 ? Math.round(Math.abs(n)).toLocaleString("en-US") : Math.abs(n).toFixed(2)}`;

/** Expected $/day: per $1k copied first, the trader's own $/day beside it. */
function ExpectedBand({ t, days, enrichNote }: { t: TopTrader; days: number; enrichNote: string }) {
  const e = t.expected;
  const why = e
    ? `Expected profit per day, over the ${days}d window:\n` +
      `  edge per close = ${(e.win_lo * 100).toFixed(0)}% × ${fmtUsd(e.avg_win)} avg win` +
      ` − ${((1 - e.win_lo) * 100).toFixed(0)}% × ${fmtUsd(e.avg_loss)} avg loss = ${fmtDay(e.edge_per_close)}\n` +
      `  × ${e.closes_per_day} closes/day = ${fmtDay(e.per_day)}/day at the trader's size\n` +
      (e.per_1k != null
        ? `  × $1,000 / ${fmtUsd(e.basis)} window-start equity = ${fmtDay(e.per_1k)}/day per $1k copied\n`
        : `  per $1k withheld: the window opened on ${fmtUsd(e.basis)} — under $1k, ratios are noise\n`) +
      `The win rate is the Wilson lower bound (what ${t.closes} closes can defend), not the printed ` +
      `${fmtPct(t.win_rate, 0)} — that's the haircut on the raw pace of ${fmtDay(e.pace_per_day)}/day.` +
      (e.source === "derived" ? "\nAvg win/loss rebuilt from pnl and profit factor (row scored by an older build)." : "") +
      (e.thin ? `\nUnder ${MIN_CLOSES} closes — anecdote, not evidence.` : "") +
      "\nA forecast from history, not a promise."
    : enrichNote;
  const head = e?.per_1k ?? null;
  const tone = head == null ? "text-dim" : head > 0 ? "text-win" : "text-loss";
  return (
    <div title={why}
      className="mt-3 flex items-baseline justify-between gap-2 rounded-md px-2 py-1.5 bg-white/[0.03] border border-white/[0.06]">
      <div className="min-w-0">
        <div className="eyebrow !text-[9px]">expected / day</div>
        <div className={`num mt-1 text-[15px] leading-none ${tone} ${e?.thin ? "opacity-60" : ""}`}>
          {head != null ? fmtDay(head) : "—"}
          <span className="ml-1 text-[10px] text-dim">{head != null ? "per $1k copied" : e ? "dust basis" : "not measured"}</span>
        </div>
      </div>
      {e && (
        <div className="text-right shrink-0 text-[10px] leading-tight text-dim">
          <div>trader <span className={`num ${e.per_day >= 0 ? "text-win/80" : "text-loss/80"}`}>{fmtDay(e.per_day)}</span>/day</div>
          <div className="mt-0.5">
            {e.haircut != null ? `${Math.round(e.haircut * 100)}% of pace` : `pace ${fmtDay(e.pace_per_day)}`}
            {e.thin ? " · thin" : ""}
          </div>
        </div>
      )}
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
  const measured = t.win_rate >= 0;
  const win = defensibleWin(t);
  const core = t.coins.filter(isCoreCoin);
  const dexOnly = core.length === 0 && t.coins.length > 0;

  // ── the card's own window ──
  // `null` = follow the board. Picking another window re-draws THIS card's
  // headline + curve at that window without touching the board's order or
  // any other card. The board's window changing folds every card back onto
  // it — an override is a glance, not a setting.
  const [ownDays, setOwnDays] = useState<number | null>(null);
  useEffect(() => { setOwnDays(null); }, [days]);
  const wDays = ownDays ?? days;
  const overridden = wDays !== days;

  const [ownCurve, setOwnCurve] = useState<TraderCurve | null>(null);
  useEffect(() => {
    if (!overridden) { setOwnCurve(null); return; }
    const hit = fresh(t.address, wDays);
    setOwnCurve(hit);
    if (hit) return;
    let alive = true;
    loadCurve(t.address, wDays).then((c) => { if (alive) setOwnCurve(c); });
    return () => { alive = false; };
  }, [overridden, wDays, t.address]);

  // What the card draws and headlines: the board's curve normally, its own
  // window's curve when overridden (undefined = skeleton while in flight).
  const c = overridden ? (ownCurve ?? undefined) : curve;
  // The leaderboard only prices ROI at the board's window, so an overridden
  // headline is derived from the curve instead: window pnl over the equity
  // the window opened on (current equity minus what the window made). Same
  // basis the backtest uses; deposits and withdrawals can bend it, hence ≈.
  const ownBasis = overridden && c?.available ? t.account_value - c.pnl : 0;
  const roiShown = overridden
    ? (c?.available && ownBasis > 0 ? (c.pnl / ownBasis) * 100 : null)
    : t.roi;
  const pnlShown = overridden ? (c?.available ? c.pnl : null) : t.pnl;
  const roiUp = (roiShown ?? 0) >= 0;
  const windows = CARD_WINDOWS.includes(days)
    ? CARD_WINDOWS : [...CARD_WINDOWS, days].sort((a, b) => a - b);

  return (
    <div className={`group panel panel-hover relative flex flex-col p-4
      ${picked ? "!border-accent/45 shadow-glow" : ""}`}>
      {/* ── who ── */}
      <div className="flex items-start gap-2.5">
        <Medal rank={rank} />
        <Link href={`/trader/${t.address}?days=${wDays}`} title={t.address}
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

      {/* ── what ── the window's number, and the dollars behind it. The
          window itself is the toggle: each `d` re-views this one card. */}
      <div className="mt-4 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className={`text-[26px] leading-none font-semibold tracking-tight ${roiUp ? "text-win" : "text-loss"}`}
            title={
              overridden
                ? `≈ derived from the ${wDays}d curve: window pnl ÷ the equity the window opened on` +
                  ` (${fmtUsd(t.account_value)} now − ${fmtPnl(c?.available ? c.pnl : 0)} made).` +
                  ` Deposits/withdrawals can bend it. The proof stats below still score the board's ${days}d window.`
                : t.account_value > 0 ? `return on ${fmtUsd(t.account_value)} of equity` : undefined
            }>
            {roiShown == null ? "—" : `${overridden ? "≈" : ""}${roiShown >= 0 ? "+" : ""}${fmtPct(roiShown, 1)}`}
          </div>
          <div className="mt-1.5 flex items-center gap-1">
            <span className="eyebrow">roi ·</span>
            {windows.map((d) => (
              <button key={d}
                onClick={() => setOwnDays(d === days ? null : d)}
                title={d === days
                  ? `the board's window`
                  : `re-view just this card over ${d} days — headline and curve; the board's order doesn't move`}
                className={`eyebrow !text-[9px] px-1 py-0.5 -my-0.5 rounded transition-colors
                  ${wDays === d ? "!text-accent bg-accent/10" : "!text-dim hover:!text-ink"}`}>
                {d}d
              </button>
            ))}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={`num text-[15px] leading-none ${(pnlShown ?? 0) >= 0 ? "text-win/85" : "text-loss/85"}`}>
            {pnlShown == null ? "—" : fmtPnl(pnlShown)}
          </div>
          <div className="eyebrow mt-1.5">pnl{overridden ? ` · ${wDays}d` : " · net of fees"}</div>
        </div>
      </div>

      {/* ── how ── the shape behind that number.
          Fixed height in every state so a grid of cards never reflows as
          curves land one page at a time. */}
      <div className="mt-3 h-[58px]">
        {c == null ? (
          <div className="skeleton h-full w-full opacity-60" />
        ) : c.available ? (
          <Link href={`/trader/${t.address}?days=${wDays}`}
            aria-label={`${shortAddr(t.address)} — ${wDays} day pnl curve`}
            title={
              `${wDays}d pnl curve · ends ${fmtPnl(c.pnl)} · high ${fmtPnl(c.high)}` +
              ` · low ${fmtPnl(c.low)} · deepest fall ${fmtUsd(c.max_drawdown)}` +
              (c.max_drawdown_pct > 0 ? ` (${c.max_drawdown_pct}% off its peak)` : "") +
              `\nsource: hyperliquid portfolio "${c.period}" — the whole account` +
              ` (perps and spot), realised and unrealised`
            }
            className={`block h-full ${c.pnl >= 0 ? "text-win" : "text-loss"}`}>
            <Spark points={c.points} height={58} />
          </Link>
        ) : (
          <div className="grid h-full place-items-center rounded-md border border-dashed border-white/[0.08] px-3">
            <span className="text-[10px] leading-snug text-dim text-center">{c.note ?? "no curve for this wallet"}</span>
          </div>
        )}
      </div>

      {/* The two things the endpoint cannot say: how good it got, and how far
          it fell from there. */}
      <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-dim">
        {c?.available ? (
          <>
            <span title="Best the cumulative curve ever got inside this window">
              high <span className="num text-ink/70">{fmtPnl(c.high)}</span>
            </span>
            <span title="Deepest peak → trough fall anywhere in the window — what you would have been down had you started at the worst moment">
              max fall{" "}
              <span className="num text-ink/70">
                {c.max_drawdown > 0 ? fmtUsd(c.max_drawdown) : "none"}
                {c.max_drawdown_pct > 0 ? ` · ${c.max_drawdown_pct}%` : ""}
              </span>
            </span>
          </>
        ) : (
          <span className="opacity-0" aria-hidden>·</span>
        )}
      </div>

      {/* ── expected profit per day ── the forward-looking number. The
          window's pace (pnl ÷ days) re-priced at the win rate the sample can
          defend, so a lucky streak is haircut and a long record isn't. Per
          $1k copied is the headline — it's what a copy of this book earns. */}
      <ExpectedBand t={t} days={days} enrichNote={enrichNote} />

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
        <Link href={`/trader/${t.address}?days=${wDays}`} className="btn flex-1"
          title="Fills, round trips and the full curve">details →</Link>
      </div>
    </div>
  );
}
