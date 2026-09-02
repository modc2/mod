"use client";

// The identity cell of a trader row — and, when you hover that row, the
// wallet's PnL curve drawn in the very same space.
//
// A board row is a claim ("+$9,186 over 7d") with no evidence attached. The
// evidence is the curve, and there is nowhere to put a curve on a row that is
// already eight columns wide. So the address gives up its space: at rest you
// see who the wallet is, on hover you see what it did. The address is the
// least valuable thing in the row — it is a hex string nobody reads — and it
// is the widest, which makes it exactly the right thing to trade away.
//
// Three details make the swap feel free rather than clever:
//
//   * **Nothing moves.** Both layers are absolutely positioned in a fixed-
//     height slot, so the row does not reflow when the curve arrives.
//   * **Nothing is lost.** The curve is itself a link to the same trader page
//     the address pointed at, so hovering never takes a click target away.
//   * **Nothing is fetched until you mean it.** A curve costs one Hyperliquid
//     call, so it waits out a hover delay first, dedupes per wallet, caches,
//     and never runs more than a few at once — dragging the cursor down 200
//     rows must not become 200 requests.
//
// The hover that matters is the *row's*, not this cell's: the user is reading
// the ROI column when they want the curve. Rather than lift hover state into
// the table (a state change per hover, re-rendering every row on the board),
// the cell finds its own row and listens to it. One component re-renders.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { fetchTraderCurve, fmtPnl, fmtUsd, shortAddr, type TraderCurve } from "../lib/api";
import { Identicon, Spark } from "./BoardBits";

/** Marks the row element a cell listens to for hover. The table puts this on
 *  each row; without it the cell simply never lights up. */
export const ROW_ATTR = "data-trader-row";

/** Fill coins arrive raw from HL: HIP-3 builder-dex perps as "dex:TICKER"
 *  (e.g. "xyz:HYUNDAI") and spot markets as "@123" indices. Neither is a
 *  copyable core perp, so neither belongs on a badge. */
export const isCoreCoin = (c: string) => !c.includes(":") && !c.startsWith("@");

// ── Fetch discipline ──────────────────────────────────────────────────────
//
// One shared cache for every cell on the board. Curves are keyed by wallet +
// window because a 1d curve and a 30d curve are different drawings of the
// same account.

type Cached = { at: number; curve: TraderCurve };

const cache = new Map<string, Cached>();
const pending = new Map<string, Promise<TraderCurve>>();

/** How long a successful curve stands. The API caches Hyperliquid's portfolio
 *  payload for 5 minutes upstream, so a shorter TTL here would only re-fetch
 *  the same bytes. */
const TTL_MS = 5 * 60_000;
/** Failures stand for seconds, not minutes — a rate limit clears, and a curve
 *  that says "try again in a moment" must mean it. */
const FAIL_TTL_MS = 20_000;
/** Hover must be intent, not transit. Below ~120ms you are still moving. */
const INTENT_MS = 140;
/** Curves in flight at once. Hyperliquid answers /info per IP; three keeps a
 *  fast scroll from turning into a 429 storm that punishes the whole board. */
const MAX_INFLIGHT = 3;

let inflight = 0;
const waiting: (() => void)[] = [];

function acquire(): Promise<void> {
  return new Promise((resolve) => {
    if (inflight < MAX_INFLIGHT) { inflight++; resolve(); return; }
    waiting.push(() => { inflight++; resolve(); });
  });
}
function release() {
  inflight--;
  waiting.shift()?.();
}

/** A failure wearing the same shape as an answer, so every consumer has one
 *  code path: `available: false` plus a sentence. */
function unavailable(address: string, days: number, note: string): TraderCurve {
  return {
    address, days, period: "", points: [], start_ms: 0, end_ms: 0,
    pnl: 0, high: 0, low: 0, max_drawdown: 0, max_drawdown_pct: 0,
    available: false, note,
  };
}

function fresh(key: string): TraderCurve | null {
  const hit = cache.get(key);
  if (!hit) return null;
  const ttl = hit.curve.available ? TTL_MS : FAIL_TTL_MS;
  if (Date.now() - hit.at > ttl) { cache.delete(key); return null; }
  return hit.curve;
}

function loadCurve(address: string, days: number): Promise<TraderCurve> {
  const key = `${address}:${days}`;
  const hit = fresh(key);
  if (hit) return Promise.resolve(hit);
  const already = pending.get(key);
  if (already) return already;

  const p = acquire()
    .then(() => fetchTraderCurve(address, days))
    .catch((e: any) => unavailable(address, days, e?.message ?? "could not load this curve"))
    .then((curve) => {
      release();
      cache.set(key, { at: Date.now(), curve });
      pending.delete(key);
      return curve;
    });
  pending.set(key, p);
  return p;
}

// ── The cell ──────────────────────────────────────────────────────────────

export default function TraderCell({ address, coins, days, href }: {
  address: string;
  coins: string[];
  days: number;
  /** Where both layers point — the trader's page. */
  href: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hot, setHot] = useState(false);
  const [curve, setCurve] = useState<TraderCurve | null>(() => fresh(`${address}:${days}`));

  // Follow the row's hover, not this cell's: the cursor is usually parked over
  // the ROI or volume column when the question "what did this look like?"
  // occurs. Listeners are attached imperatively so hovering a row costs no
  // React state anywhere except inside this one cell.
  useEffect(() => {
    const row = ref.current?.closest(`[${ROW_ATTR}]`) as HTMLElement | null;
    if (!row) return;
    const on = () => setHot(true);
    const off = () => setHot(false);
    // Tabbing between the checkbox and the link inside a row must not flicker
    // the curve off and on — only a focus that actually leaves the row counts.
    const out = (e: FocusEvent) => {
      if (!row.contains(e.relatedTarget as Node | null)) setHot(false);
    };
    row.addEventListener("pointerenter", on);
    row.addEventListener("pointerleave", off);
    row.addEventListener("focusin", on);
    row.addEventListener("focusout", out);
    return () => {
      row.removeEventListener("pointerenter", on);
      row.removeEventListener("pointerleave", off);
      row.removeEventListener("focusin", on);
      row.removeEventListener("focusout", out);
    };
  }, []);

  // A curve already in cache paints on the first frame of the hover; a cold
  // one waits out the intent delay so crossing the board costs nothing.
  useEffect(() => {
    const cached = fresh(`${address}:${days}`);
    setCurve(cached);
    if (!hot || cached) return;
    let alive = true;
    const t = setTimeout(() => {
      loadCurve(address, days).then((c) => { if (alive) setCurve(c); });
    }, INTENT_MS);
    return () => { alive = false; clearTimeout(t); };
  }, [hot, address, days]);

  const core = coins.filter(isCoreCoin);
  const extra = core.length - 2;
  const up = (curve?.pnl ?? 0) >= 0;

  const tip = curve == null
    ? `${address} — reading its ${days}d pnl curve…`
    : curve.available
      ? `${days}d pnl curve · ends ${fmtPnl(curve.pnl)} · high ${fmtPnl(curve.high)}` +
        ` · low ${fmtPnl(curve.low)} · deepest fall ${fmtUsd(curve.max_drawdown)}` +
        (curve.max_drawdown_pct > 0 ? ` (${curve.max_drawdown_pct}% off its peak)` : "") +
        `\nsource: hyperliquid portfolio "${curve.period}" — realised and unrealised` +
        `\n${address}`
      : `${curve.note ?? "no curve for this wallet"}\n${address}`;

  return (
    <div ref={ref} className="relative min-w-0 flex-1 h-[34px]">
      {/* At rest: who this is. */}
      <div className={`absolute inset-0 flex items-center gap-2 transition-opacity duration-150
        ${hot ? "opacity-0 pointer-events-none" : "opacity-100"}`}>
        <Link href={href} title="view trader"
          className="flex items-center gap-2 font-mono text-[13px] text-ink/90 hover:text-accent transition-colors shrink-0">
          <Identicon address={address} />
          {shortAddr(address)}
        </Link>
        {/* Core perps only. One row, never wraps into the ROI column;
            overflow collapses into a "+n" count. */}
        <div className="flex flex-wrap gap-1 min-w-0 max-h-[20px] overflow-hidden"
          title={core.join(", ") || coins.join(", ")}>
          {core.slice(0, 2).map((c) => (
            <span key={c} className="pill whitespace-nowrap">{c}</span>
          ))}
          {extra > 0 && <span className="pill whitespace-nowrap">+{extra}</span>}
          {core.length === 0 && coins.length > 0 && (
            <span className="pill whitespace-nowrap opacity-60">dex</span>
          )}
        </div>
      </div>

      {/* On hover: what it did. Same destination, so no click is lost. */}
      <Link href={href} title={tip} aria-label={`${shortAddr(address)} — ${days} day pnl curve`}
        className={`absolute inset-0 flex items-center gap-2.5 pr-1 transition-opacity duration-150
          ${hot ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
        <Identicon address={address} size={14} />
        <div className={`relative flex-1 min-w-0 ${up ? "text-win" : "text-loss"}`}>
          {curve == null ? (
            <div className="skeleton h-[3px] w-full opacity-70" />
          ) : curve.available ? (
            <Spark points={curve.points} height={30} />
          ) : (
            <div className="text-[10px] leading-tight text-dim truncate">{curve.note}</div>
          )}
        </div>
        {curve?.available && (
          // The one number the board does not already show, and the one that
          // replaced the sharpe column: how far this wallet fell from its own
          // high inside the window.
          <div className="shrink-0 text-right leading-none">
            <div className="eyebrow !text-[9px] !tracking-wider">max dd</div>
            <div className="num mt-1 text-[11px] text-ink/80">
              {curve.max_drawdown > 0 ? fmtUsd(curve.max_drawdown) : "none"}
            </div>
          </div>
        )}
      </Link>
    </div>
  );
}
