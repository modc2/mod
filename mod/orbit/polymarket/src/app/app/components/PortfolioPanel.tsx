"use client";

// Portfolio panel — at-a-glance view of where the user's money lives.
//
// Two charts in one panel, toggled by the user:
//   PIE  — current split between cash (USDC in the V2 deposit wallet)
//          and open positions (live Polymarket position value).
//   LINE — both values plotted over time so you can see how cash drains
//          into positions, positions resolve back into cash, etc.
//
// Snapshots are appended to localStorage every poll (45s), capped at
// PORTFOLIO_HISTORY_CAP points so the entry never grows unbounded.
// History persists across reloads — first time you open the panel after
// 2 weeks of running you'll see the full 2-week curve.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { getOwnerAddress } from "../lib/access";
import { fetchPositions, fetchUserTrades, type GlobalTrade } from "../lib/polymarket";
import EquityChart, { type EquitySnapshot, type EquityMarker } from "./EquityChart";

interface DepositWalletInfo {
  depositWallet: string;
  usdcBalance: string;
}

interface PositionLite {
  market: string;
  outcome: string;
  size: number;
  value: number;
  pnlUsd: number;
  avgPrice: number;
  // Forwarded so the SELL ALL button can build a /order/place body
  // without re-fetching positions just for the asset/conditionId.
  tokenId: string;
  conditionId: string;
  currentPrice: number;
  negRisk: boolean;
  // Market resolved → REDEEM (not SELL) is the only cash-out path.
  redeemable: boolean;
  // Live order book has at least one bid → a SELL can actually fill.
  // Positions that are neither sellable nor redeemable are hidden from the
  // panel entirely (they're unrealizable, so showing their mark-to-market
  // value would overstate what the account is worth).
  sellable: boolean;
  // Best bid from the book, used as the SELL price so FAK orders cross
  // instead of getting killed above the market.
  bestBid: number | null;
}

// Probe the CLOB book for a token and return its best bid.
//   number → there's a bid; a SELL can fill here.
//   null   → the book exists but has no bids, OR the CLOB has no book for
//            this token at all (upstream 404 — a resolved/dead market). Either
//            way a SELL can never fill, so the position is unsellable.
//   undefined → the read was inconclusive (throttled/network/5xx). We DON'T
//            hide on this — a flaky poll shouldn't make a real position vanish.
async function fetchBestBid(tokenId: string): Promise<number | null | undefined> {
  try {
    const r = await fetch(`/api/polymarket/?endpoint=book&token_id=${tokenId}`, { cache: "no-store" });
    if (r.ok) {
      const book = await r.json();
      const bids = Array.isArray(book?.bids) ? book.bids : [];
      let best = 0;
      for (const b of bids) {
        const px = Number(b?.price);
        if (Number.isFinite(px) && px > best) best = px;
      }
      return best > 0 ? best : null;
    }
    // Non-OK: the proxy wraps the upstream status in the error body. A 404
    // means "no order book exists for this token" = definitively unsellable.
    // A 429/5xx is just throttling = inconclusive, keep the position visible.
    const body = await r.json().catch(() => ({}));
    const msg = String((body as { error?: string })?.error ?? "");
    if (msg.includes("404")) return null;
    return undefined;
  } catch {
    return undefined;
  }
}

// Snapshot shape (t/liq/pos) is shared with the backtest's simulated curve
// so LIVE and BACKTEST render through the exact same EquityChart.
type Snapshot = EquitySnapshot;

// History is keyed PER ACCOUNT. One shared key meant signing in with a
// different wallet inherited the previous account's curve, then appended
// this account's (possibly $0) snapshots — rendering as a fake collapse
// to zero. v1 (unkeyed) entries are ignored; the curve restarts per EOA.
const historyKey = (eoa: string) => `poly_portfolio_history_v2:${eoa.toLowerCase()}`;
// ~12h at 15s cadence. Faster cadence = livelier cash + position numbers
// so a fill or withdrawal shows up within seconds instead of half a minute.
const PORTFOLIO_HISTORY_CAP = 3000;
const POLL_MS = 30_000;

// Redeem events stamped on the chart (the fills feed only carries CLOB
// trades — a redeem is an on-chain CTF call, invisible there). Persisted
// per-EOA so the amber dots survive reloads like the curve does.
type RedeemMark = { t: number; usd: number; n: number };
const redeemMarksKey = (eoa: string) => `poly_redeem_marks_v1:${eoa.toLowerCase()}`;

function loadRedeemMarks(eoa: string): RedeemMark[] {
  try {
    const arr = JSON.parse(localStorage.getItem(redeemMarksKey(eoa)) || "[]");
    return Array.isArray(arr) ? arr.filter((m) => typeof m?.t === "number") : [];
  } catch {
    return [];
  }
}

function loadHistory(eoa: string): Snapshot[] {
  try {
    const raw = localStorage.getItem(historyKey(eoa));
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((s) => typeof s?.t === "number") : [];
  } catch {
    return [];
  }
}

function saveHistory(eoa: string, history: Snapshot[]): void {
  try {
    localStorage.setItem(historyKey(eoa), JSON.stringify(history));
  } catch {}
}

function fmtUsd(v: number): string {
  if (!Number.isFinite(v)) return "$0.00";
  return v >= 1000
    ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
    : `$${v.toFixed(2)}`;
}

function fmtRelTime(now: number, t: number): string {
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 90) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

// ── Pie chart (2 slices) ───────────────────────────────────────────────

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
        {/* Position slice (full circle as background, "filled" with position color) */}
        <circle cx="100" cy="100" r={r} fill="none"
          stroke="#f59e0b" strokeWidth="36" />
        {/* Liquidity slice (overlay arc starting at top, going clockwise) */}
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

// ── Main panel ─────────────────────────────────────────────────────────

type Mode = "pie" | "line";

function tickRound(p: number): number {
  if (!Number.isFinite(p)) return 0.01;
  const clamped = Math.max(0.01, Math.min(0.99, p));
  return Math.round(clamped * 100) / 100;
}

export default function PortfolioPanel({ strategyId }: { strategyId?: string }) {
  const { auth } = useAuth();
  // Default to the over-time PnL curve (like the backtest's PnL curve) so the
  // chart leads MY TRADES; PIE is a click away for the cash/positions split.
  const [mode, setMode] = useState<Mode>("line");
  const [liq, setLiq] = useState(0);
  const [posValue, setPosValue] = useState(0);
  const [positions, setPositions] = useState<PositionLite[]>([]);
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [lastError, setLastError] = useState<string | null>(null);
  const [selling, setSelling] = useState(false);
  const [sellStatus, setSellStatus] = useState<string | null>(null);
  const [redeeming, setRedeeming] = useState(false);
  const [redeemStatus, setRedeemStatus] = useState<string | null>(null);
  // Positions hidden because they're unrealizable (no bid, not redeemable).
  const [hiddenInfo, setHiddenInfo] = useState<{ count: number; value: number }>({ count: 0, value: 0 });
  // Trading (deposit) wallet — the address whose fills draw the chart markers.
  const [tradingWallet, setTradingWallet] = useState<string | null>(null);
  // My executed fills (deposit-wallet trades) → BUY/SELL dots on the curve.
  const [fills, setFills] = useState<GlobalTrade[]>([]);
  const [redeemMarks, setRedeemMarks] = useState<RedeemMark[]>([]);
  // In-flight guard so the auto-redeem effect can't stack requests.
  const redeemBusy = useRef(false);

  // Owner-only console: the funded wallet is the signed-in owner (from the
  // access token), which is authoritative. Fall back to the connected wallet
  // only if there's no token yet. Using auth.address alone made the panel read
  // a stale/previous wallet (e.g. the old owner) and show $0.
  const eoa = getOwnerAddress() ?? auth.address;

  // (Re)load the persisted curve whenever the signed-in account changes so
  // one account's history never bleeds into another's chart.
  useEffect(() => {
    setHistory(eoa ? loadHistory(eoa) : []);
    setRedeemMarks(eoa ? loadRedeemMarks(eoa) : []);
  }, [eoa]);

  const refresh = useCallback(async () => {
    if (!eoa) return;
    let nextLiq = 0;
    let liqOk = false; // wallet-info call actually succeeded
    let nextPosVal = 0;
    let nextPositions: PositionLite[] = [];

    // 1) Liquidity from the deposit wallet
    let wallet: string | null = null;
    try {
      const r = await fetch(
        `/api/polymarket/deposit-wallet/info?eoa=${eoa}`,
        { cache: "no-store" },
      );
      if (r.ok) {
        const j = (await r.json()) as DepositWalletInfo;
        wallet = j.depositWallet;
        setTradingWallet(j.depositWallet || null);
        nextLiq = Number(j.usdcBalance) / 1_000_000;
        liqOk = true;
      }
    } catch (e) {
      setLastError(`liquidity: ${e instanceof Error ? e.message : String(e)}`);
    }

    // 2) Positions for the deposit wallet — that's where trades land in V2.
    // The data-api intermittently returns an EMPTY positions list under load
    // (and the proxy can serve that empty as a cached HIT) — so a "successful"
    // /positions call is NOT proof you hold nothing. We therefore treat the
    // light /value endpoint as the authoritative TOTAL and only let the
    // detailed list drive the total when the list actually has rows.
    let valueTotal: number | null = null; // authoritative total from /value
    let listOk = false;                    // /positions call succeeded (maybe empty)
    if (wallet) {
      // 2a) Authoritative TOTAL positions value — one light call that tends to
      // survive rate-limiting even when the heavier /positions list is empty.
      try {
        const vr = await fetch(`/api/polymarket/?endpoint=value&user=${wallet}`, { cache: "no-store" });
        if (vr.ok) {
          const vj = await vr.json();
          const v = Array.isArray(vj) ? Number(vj[0]?.value) : Number(vj?.value);
          if (Number.isFinite(v)) valueTotal = v;
        }
      } catch { /* keep going; list below may still populate */ }

      // 2b) Per-position breakdown for the list + pie (heavier; best-effort).
      try {
        const pos = await fetchPositions(wallet, { bypassCache: true });
        nextPositions = pos.map((p) => ({
          market: p.market,
          outcome: p.outcome,
          size: p.size,
          value: p.value,
          pnlUsd: p.pnlUsd,
          avgPrice: p.avgPrice,
          tokenId: p.tokenId,
          conditionId: p.conditionId,
          currentPrice: p.currentPrice,
          negRisk: p.negRisk,
          redeemable: p.redeemable,
          sellable: false,
          bestBid: null,
        }));
        listOk = true;

        // Sellability probe — a position only counts if we can actually get
        // out of it: either the market resolved (REDEEM) or the live book
        // has a bid (SELL). Anything else is unrealizable and is hidden so
        // the tiles reflect what the account can really cash out.
        const bids = await Promise.all(
          nextPositions.map((p) =>
            p.redeemable || !p.tokenId ? Promise.resolve(undefined) : fetchBestBid(p.tokenId),
          ),
        );
        nextPositions = nextPositions.map((p, i) => {
          const bid = bids[i];
          return {
            ...p,
            // undefined = probe failed → keep showing rather than hide on a
            // flaky read; null = book confirmed empty → not sellable.
            sellable: !p.redeemable && bid !== null,
            bestBid: typeof bid === "number" ? bid : null,
          };
        });
      } catch (e) {
        setLastError(`positions: ${e instanceof Error ? e.message : String(e)}`);
      }
    }

    // Split realizable vs not. Hidden = no bid on the book AND not
    // redeemable — a SELL there can never fill, so we neither list it nor
    // count its mark-to-market value.
    const hidden = nextPositions.filter((p) => !p.redeemable && !p.sellable);
    nextPositions = nextPositions.filter((p) => p.redeemable || p.sellable);

    // Resolve the headline total. Prefer the detailed sum ONLY when the raw
    // list had rows; otherwise an empty/throttled list must NOT zero a real
    // /value total (the phantom-$0 bug). Genuine zero = list returned empty
    // AND /value also ~0. rawCount (pre-hiding) is what proves the list call
    // returned real data — visible rows alone would mis-read an all-hidden
    // portfolio as a throttled response.
    const rawCount = nextPositions.length + hidden.length;
    const listSum = nextPositions.reduce((s, p) => s + p.value, 0);
    let posValueOk = false;
    if (listOk && rawCount > 0) {
      nextPosVal = listSum; // realizable value only — hidden dust excluded
      posValueOk = true;
    } else if (valueTotal != null) {
      nextPosVal = valueTotal;
      posValueOk = true;
    }
    // NOTE: empty list + no /value reading is AMBIGUOUS (both endpoints get
    // throttled together) — treating it as "truly flat" used to punch a false
    // $0 into the curve. Now we simply keep the previous reading.

    // Cash — only trust it when the wallet-info call actually succeeded;
    // a failed fetch is not a $0 balance.
    if (liqOk) setLiq(nextLiq);
    if (posValueOk) setPosValue(nextPosVal);
    // Only replace the breakdown list when the raw list had rows, or when
    // /value confirms a genuine zero — so a stale-empty list never wipes a
    // populated breakdown.
    if (listOk && (rawCount > 0 || (valueTotal != null && valueTotal < 0.01))) {
      setPositions(nextPositions);
      setHiddenInfo({ count: hidden.length, value: hidden.reduce((s, p) => s + p.value, 0) });
    }

    // 3) Snapshot for the time-series view — only when BOTH readings are real,
    // so a rate-limited tick can't punch a false $0 dip into the P&L curve.
    // De-dupe back-to-back identical snapshots (a paused engine shouldn't
    // pollute the curve with thousands of flatlined points).
    if (liqOk && posValueOk) {
      const snap: Snapshot = { t: Date.now(), liq: nextLiq, pos: nextPosVal };
      setHistory((prev) => {
        const last = prev[prev.length - 1];
        if (last && Math.abs(last.liq - snap.liq) < 0.001 && Math.abs(last.pos - snap.pos) < 0.001) {
          return prev;
        }
        const next = [...prev, snap].slice(-PORTFOLIO_HISTORY_CAP);
        saveHistory(eoa, next);
        return next;
      });
    }
  }, [eoa]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  // My executed fills — drawn as BUY/SELL dots on the curve so the chart
  // answers "which trade caused this move". 60s cadence matches the
  // user-trades proxy TTL; more often would just re-serve the cache.
  useEffect(() => {
    if (!tradingWallet) return;
    let dead = false;
    const load = async () => {
      try {
        const t = await fetchUserTrades(tradingWallet, 300);
        if (!dead) setFills(t);
      } catch { /* markers are decoration — never surface a fetch error */ }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => { dead = true; clearInterval(id); };
  }, [tradingWallet]);

  // Fills + redeem events inside the visible history window → chart markers.
  const markers = useMemo<EquityMarker[]>(() => {
    if (history.length < 2) return [];
    const t0 = history[0].t;
    const out: EquityMarker[] = fills
      .filter((f) => f.timestamp >= t0)
      .map((f) => ({
        t: f.timestamp,
        side: f.side,
        usd: f.size * f.price,
        label: `${f.market} · ${f.outcome} @ ${Math.round(f.price * 100)}¢`,
      }));
    for (const r of redeemMarks) {
      if (r.t >= t0) {
        out.push({
          t: r.t,
          side: "REDEEM",
          usd: r.usd,
          label: `${r.n} settled market${r.n === 1 ? "" : "s"} redeemed`,
        });
      }
    }
    out.sort((a, b) => a.t - b.t);
    return out;
  }, [fills, redeemMarks, history]);

  // ── Redeem (auto + manual) ──
  // Settled markets have no order book, so their winnings sit stranded as
  // CTF tokens until redeemPositions runs. The backend engine auto-redeems
  // for running sessions; this covers everyone else (idempotent — a redeem
  // of an already-redeemed condition just pays 0).
  const doRedeem = useCallback(async (opts: { auto: boolean }) => {
    if (!eoa || redeemBusy.current) return;
    const red = positions.filter((p) => p.redeemable);
    if (red.length === 0) return;
    const claimable = red.reduce((s, p) => s + p.value, 0);
    if (
      !opts.auto &&
      !confirm(
        `Redeem ${red.length} settled position(s) (~${fmtUsd(claimable)} → cash)?\n\nConverts winning tokens to USDC on-chain (gasless) and wraps it into your trading balance. SELL can't cash these out — the markets have already resolved.`,
      )
    ) {
      return;
    }
    redeemBusy.current = true;
    setRedeeming(true);
    setRedeemStatus(`${opts.auto ? "auto-" : ""}redeeming ${red.length} settled position(s)…`);
    try {
      const r = await fetch("/api/polymarket/redeem", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ eoa }),
      });
      const j = (await r.json().catch(() => ({}))) as {
        conditions?: number;
        valueRedeemed?: number;
        skipped?: number;
        error?: string;
      };
      if (r.ok) {
        const v = Number(j.valueRedeemed ?? 0);
        const n = Number(j.conditions ?? 0);
        const skipped = Number(j.skipped ?? 0);
        setRedeemStatus(
          `${opts.auto ? "auto-" : ""}redeemed ${n} market(s) · ~${fmtUsd(v)} → cash${skipped ? ` · ${skipped} skipped` : ""}`,
        );
        // Stamp the event on the chart. The curve is NOT reset — a redeem
        // just moves value from Positions to Cash, Total stays continuous,
        // and wiping history would erase the markers' context.
        if (n > 0) {
          const mark: RedeemMark = { t: Date.now(), usd: v, n };
          setRedeemMarks((prev) => {
            const next = [...prev, mark].slice(-200);
            try { localStorage.setItem(redeemMarksKey(eoa), JSON.stringify(next)); } catch {}
            return next;
          });
        }
      } else {
        setRedeemStatus(null);
        setLastError(`redeem: ${j.error ?? `HTTP ${r.status}`}`);
      }
    } catch (e) {
      setRedeemStatus(null);
      setLastError(`redeem: ${e instanceof Error ? e.message : String(e)}`);
    }
    setRedeeming(false);
    redeemBusy.current = false;
    setTimeout(refresh, 6_000);
  }, [eoa, positions, refresh]);

  // AUTO-REDEEM — settled positions cash themselves out; capital shouldn't
  // sit stranded behind a button. 5-min cooldown (persisted, quota-safe)
  // because the data-api keeps listing just-redeemed positions as redeemable
  // for a minute or two — without it every poll would re-fire the tx.
  useEffect(() => {
    if (!eoa || redeeming || !positions.some((p) => p.redeemable)) return;
    const key = `poly_auto_redeem_at:${eoa.toLowerCase()}`;
    let last = 0;
    try { last = Number(localStorage.getItem(key) || 0); } catch {}
    if (Date.now() - last < 5 * 60_000) return;
    try { localStorage.setItem(key, String(Date.now())); } catch {}
    doRedeem({ auto: true });
  }, [eoa, positions, redeeming, doRedeem]);

  const total = liq + posValue;

  // Unrealized P&L across every open position (mark-to-market vs avg entry),
  // with the cost basis so we can show it as a % too.
  const unrealized = useMemo(
    () => positions.reduce((s, p) => s + p.pnlUsd, 0),
    [positions],
  );
  const costBasis = useMemo(
    () => positions.reduce((s, p) => s + p.avgPrice * p.size, 0),
    [positions],
  );
  const unrealizedPct = costBasis > 0 ? (unrealized / costBasis) * 100 : 0;

  // Session performance: equity now vs the oldest snapshot we hold.
  const sessionDelta = useMemo(() => {
    if (history.length < 2) return null;
    const first = history[0];
    const start = first.liq + first.pos;
    if (!Number.isFinite(start)) return null;
    return { delta: total - start, since: first.t, start };
  }, [history, total]);

  // ── Rotation queue ──
  // Mirror the live engine's forward-EP ranking so the user can see which
  // positions get sold first when cash is freed to fund a new buy. Forward
  // EP = max(0, entryEP − realized P&L) — the engine sorts ascending and
  // rotates the cheapest out first. Positions the engine didn't open (no
  // stored entry) have forward EP 0 → "free" to rotate. Read straight from
  // the engine's persisted ledger so this never drifts from the real logic.
  const rotationQueue = useMemo(() => {
    let ledger: Record<string, { entryEP: number; mirrorNotional: number }> = {};
    if (strategyId) {
      try {
        const raw = localStorage.getItem(`poly_copy_positionep_${strategyId}`);
        if (raw) ledger = JSON.parse(raw) || {};
      } catch {}
    }
    return positions
      .filter((p) => p.size > 0)
      .map((p) => {
        const key = `${p.conditionId.toLowerCase()}:${(p.outcome || "Yes").toLowerCase()}`;
        const stored = ledger[key];
        const forwardEP = stored ? Math.max(0, stored.entryEP - p.pnlUsd) : 0;
        return { ...p, forwardEP, tracked: !!stored };
      })
      .sort((a, b) => a.forwardEP - b.forwardEP || b.pnlUsd - a.pnlUsd);
  }, [positions, strategyId]);

  if (!auth.connected) return null;

  return (
    <div className="pixel-panel p-3 space-y-3">
      {/* Header: equity headline + cash/positions/unrealized-P&L breakdown +
          session performance + chart toggle. EQUITY (cash + mark-to-market
          positions) leads — free capital alone is not the account's worth. */}
      <div className="flex items-center gap-x-4 gap-y-1.5 flex-wrap">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-pixel-muted">
            Equity
          </span>
          <span className="text-[19px] font-mono font-semibold text-pixel-white">{fmtUsd(total)}</span>
        </div>
        <div className="flex items-baseline gap-1.5" title="Free USDC in the trading wallet">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Cash</span>
          <span className="text-[13px] font-mono">{fmtUsd(liq)}</span>
        </div>
        <div className="flex items-baseline gap-1.5" title="Mark-to-market value of all open positions">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">Positions</span>
          <span className="text-[13px] font-mono text-amber-400">{fmtUsd(posValue)}</span>
        </div>
        <div
          className="flex items-baseline gap-1.5"
          title="Unrealized P&L across all open positions (current price vs avg entry)"
        >
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">P&L</span>
          <span className={`text-[13px] font-mono font-semibold ${unrealized >= 0 ? "text-green-400" : "text-red-400"}`}>
            {unrealized >= 0 ? "+" : "-"}{fmtUsd(Math.abs(unrealized))}
            {costBasis > 0 && (
              <span className="opacity-75"> ({unrealizedPct >= 0 ? "+" : ""}{unrealizedPct.toFixed(1)}%)</span>
            )}
          </span>
        </div>
        {sessionDelta && (
          <div
            className="flex items-baseline gap-1.5"
            title={`Equity change since the oldest snapshot (${fmtUsd(sessionDelta.start)} ${fmtRelTime(Date.now(), sessionDelta.since)})`}
          >
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-pixel-muted">
              Δ {fmtRelTime(Date.now(), sessionDelta.since).replace(" ago", "")}
            </span>
            <span className={`text-[13px] font-mono ${sessionDelta.delta >= 0 ? "text-green-400" : "text-red-400"}`}>
              {sessionDelta.delta >= 0 ? "+" : "-"}{fmtUsd(Math.abs(sessionDelta.delta))}
            </span>
          </div>
        )}
        <div className="ml-auto flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          <button
            onClick={() => setMode("line")}
            className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
              mode === "line" ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
            }`}
          >
            OVER TIME
          </button>
          <button
            onClick={() => setMode("pie")}
            className={`px-2.5 py-1 text-[10px] font-semibold tracking-[0.1em] transition-colors ${
              mode === "pie" ? "bg-pixel-border-light text-pixel-white" : "text-pixel-muted hover:bg-pixel-border-light/50"
            }`}
          >
            PIE
          </button>
        </div>
      </div>

      {/* Chart area */}
      <div className="bg-pixel-bg border border-pixel-border rounded p-3">
        {mode === "pie" ? (
          <PieChart liq={liq} pos={posValue} />
        ) : (
          // Markers ride the Positions line — that's the line a fill actually
          // moves (BUY: pos ↑ cash ↓; SELL/REDEEM: pos ↓ cash ↑).
          <EquityChart history={history} markers={markers} markerLine="pos" />
        )}
      </div>

      {/* OPEN POSITIONS — every holding with its live mark-to-market P&L,
          listed in the engine's rotation order (lowest forward-EP first =
          sold first to fund new buys). Totals pinned in the footer. */}
      {rotationQueue.length > 0 && (
        <div className="bg-pixel-bg border border-pixel-border rounded-[var(--radius)] overflow-hidden">
          <div className="px-2 py-1.5 border-b border-pixel-border flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-pixel-muted">
              Open positions · {rotationQueue.length}
            </span>
            <span className="text-[9.5px] text-pixel-muted" title="Rows are in rotation order — the engine sells the lowest forward expected profit first to fund new buys.">
              rotation order · ▸ = sold first
            </span>
          </div>
          <div className="grid grid-cols-[16px_minmax(0,1fr)_44px_84px_64px_108px] items-center gap-x-3 px-2 py-1 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-pixel-muted border-b border-pixel-border/40">
            <span />
            <span>Market</span>
            <span className="text-right">Size</span>
            <span className="text-right" title="Average entry price → current price">Avg→Cur</span>
            <span className="text-right">Value</span>
            <span className="text-right" title="Unrealized P&L (current price vs avg entry)">P&L</span>
          </div>
          <div className="max-h-[220px] overflow-y-auto">
            {rotationQueue.map((p, i) => {
              const basis = p.avgPrice * p.size;
              const pct = basis > 0 ? (p.pnlUsd / basis) * 100 : 0;
              const curPrice = p.size > 0 ? p.value / p.size : p.currentPrice;
              return (
                <div
                  key={`${p.conditionId}-${p.outcome}-${i}`}
                  className="grid grid-cols-[16px_minmax(0,1fr)_44px_84px_64px_108px] items-center gap-x-3 px-2 py-1 text-[11px] font-mono border-b border-pixel-border/30 last:border-b-0 hover:bg-pixel-white/[0.03]"
                >
                  <span
                    className={`text-center ${i === 0 ? "text-red-400 font-bold" : "text-pixel-muted"}`}
                    title={i === 0 ? "Next to be sold when cash is freed" : `#${i + 1} in rotation order`}
                  >
                    {i === 0 ? "▸" : i + 1}
                  </span>
                  <span className="truncate min-w-0 text-pixel-white" title={`${p.market} · ${p.outcome}${p.tracked ? ` · fwd EP $${p.forwardEP.toFixed(2)}` : " · not opened by the engine (rotates first)"}`}>
                    {p.market}
                    <span className={p.outcome.toLowerCase() === "no" ? "text-red-400/80" : "text-green-400/80"}> · {p.outcome}</span>
                    {p.redeemable && (
                      <span className="ml-1.5 text-[9px] px-1 py-px rounded-full border border-amber-400/50 text-amber-400 font-sans font-semibold tracking-wide" title="Market resolved — cash out via REDEEM, not SELL">
                        REDEEM
                      </span>
                    )}
                  </span>
                  <span className="text-right text-pixel-muted">{p.size.toFixed(1)}</span>
                  <span className="text-right text-pixel-muted" title="avg entry → current">
                    {Math.round(p.avgPrice * 100)}¢→{Math.round(curPrice * 100)}¢
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
          {/* Totals */}
          <div className="grid grid-cols-[16px_minmax(0,1fr)_44px_84px_64px_108px] items-center gap-x-3 px-2 py-1.5 text-[11px] font-mono border-t border-pixel-border bg-pixel-white/[0.02]">
            <span />
            <span className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-pixel-muted font-sans">Total</span>
            <span />
            <span />
            <span className="text-right text-pixel-white font-semibold">{fmtUsd(posValue)}</span>
            <span className={`text-right font-semibold ${unrealized >= 0 ? "text-green-400" : "text-red-400"}`}>
              {unrealized >= 0 ? "+" : "-"}${Math.abs(unrealized).toFixed(2)}
            </span>
          </div>
        </div>
      )}

      {/* Unrealizable positions are hidden, not silently dropped — say so. */}
      {hiddenInfo.count > 0 && (
        <div
          className="text-[10px] text-pixel-muted"
          title="These tokens have no bid on the order book and the market hasn't resolved, so they can't be sold or redeemed right now. They're excluded from the Positions value and P&L so the numbers reflect what you can actually cash out."
        >
          {hiddenInfo.count} unsellable position{hiddenInfo.count === 1 ? "" : "s"} hidden
          (no market bid · ~{fmtUsd(hiddenInfo.value)} mark-to-market, not counted)
        </div>
      )}

      {/* Settled positions — redeemed AUTOMATICALLY (see the auto-redeem
          effect above); this row just narrates it, with a manual kick as a
          fallback for when the auto pass is inside its cooldown. */}
      {positions.some((p) => p.redeemable) && (() => {
        const red = positions.filter((p) => p.redeemable);
        const claimable = red.reduce((s, p) => s + p.value, 0);
        return (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] text-pixel-muted whitespace-nowrap">
              {red.length} settled · ~{fmtUsd(claimable)} · auto-redeems to cash
            </span>
            <button
              onClick={() => doRedeem({ auto: false })}
              disabled={redeeming}
              className="text-[10px] px-2 py-0.5 bg-emerald-700/80 hover:bg-emerald-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold rounded"
              title="Settled winnings redeem themselves automatically (every few minutes). This forces a pass right now."
            >
              {redeeming ? "REDEEMING…" : "REDEEM NOW"}
            </button>
          </div>
        );
      })()}

      {redeemStatus && (
        <div className={`text-xs font-mono ${redeeming ? "text-amber-400" : "text-emerald-400"}`}>
          {redeemStatus}
        </div>
      )}

      {/* SELL ALL → CASH — the per-position "Top Positions" list was removed
          for a sleeker panel; the bulk-liquidate action stays. */}
      {posValue > 0 && (
        <div className="flex justify-end">
            <button
              onClick={async () => {
                if (!eoa) return;
                // Resolved (redeemable) markets have no order book — a SELL
                // there always bounces ("invalid token id"). Exclude them;
                // they cash out through REDEEM → CASH instead. Also skip
                // anything whose book probe found no bid — a SELL can't fill.
                const all = positions.filter((p) => p.tokenId && p.size > 0 && !p.redeemable && p.sellable);
                if (all.length === 0) return;
                if (
                  !confirm(
                    `Sell ALL ${all.length} positions (~${fmtUsd(posValue)} → cash)?\n\nUses market-aggressive limit orders (current price). Some may partially fill if liquidity is thin.`,
                  )
                ) {
                  return;
                }
                setSelling(true);
                setSellStatus(`selling ${all.length} positions…`);
                let ok = 0;
                let fail = 0;
                for (let i = 0; i < all.length; i++) {
                  const p = all[i];
                  setSellStatus(`selling ${i + 1}/${all.length} · ${p.market.slice(0, 28)}…`);
                  try {
                    // Sell AT the live best bid when the book probe captured
                    // one — a FAK priced above the bid just gets killed
                    // without filling. Fall back to current price - 1¢.
                    const sellPrice = tickRound(
                      Math.max(0.01, p.bestBid != null ? p.bestBid : p.currentPrice - 0.01),
                    );
                    const body = {
                      eoa,
                      creds: { apiKey: "u", secret: "u", passphrase: "u" },
                      args: {
                        tokenId: p.tokenId,
                        side: "SELL",
                        price: sellPrice,
                        size: Math.round(p.size * 100) / 100,
                        feeRateBps: 0,
                        expiration: 0,
                        signatureType: 3,
                        orderType: "FAK",
                        negRisk: p.negRisk,
                        // Backend ignores this and derives the V2 deposit
                        // wallet from `eoa` itself, but the field is
                        // required by PlaceOrderArgs.
                        maker: "0x0000000000000000000000000000000000000000",
                      },
                    };
                    const r = await fetch("/api/polymarket/order/place", {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify(body),
                    });
                    if (r.ok) {
                      const j = (await r.json()) as { success?: boolean; errorMsg?: string };
                      if (j.success === false) {
                        fail++;
                        if (j.errorMsg) setLastError(`${p.market.slice(0, 28)}: ${j.errorMsg}`);
                      } else ok++;
                    } else {
                      fail++;
                      const detail = await r.text().catch(() => "");
                      setLastError(`${p.market.slice(0, 28)}: HTTP ${r.status} ${detail.slice(0, 120)}`);
                    }
                  } catch (e) {
                    fail++;
                    setLastError(`${p.market.slice(0, 28)}: ${e instanceof Error ? e.message : String(e)}`);
                  }
                }
                setSellStatus(`sold ${ok} ✓ · ${fail} failed`);
                setSelling(false);
                // History is kept — the drop in the Positions line IS the
                // story, and the SELL markers on the curve explain it.
                setTimeout(refresh, 4_000);
              }}
              disabled={selling || posValue <= 0}
              className="text-[10px] px-2 py-0.5 bg-red-700/80 hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold rounded"
              title="Aggressive market-priced FAK sells against every position. Anything that doesn't fill at the bid gets killed (no resting limits)."
            >
              {selling ? "SELLING…" : "SELL ALL → CASH"}
            </button>
        </div>
      )}

      {sellStatus && (
        <div className={`text-xs font-mono ${selling ? "text-amber-400" : "text-green-400"}`}>
          {sellStatus}
        </div>
      )}
      {lastError && (
        <div className="text-[10px] text-red-400/70 font-mono break-all">{lastError}</div>
      )}
    </div>
  );
}
