"use client";

import { useState, useCallback, useMemo, useEffect, useRef, type ReactNode } from "react";
import { Contract, JsonRpcProvider, formatUnits } from "ethers";
import { useAuth } from "../context/AuthContext";
import { useCopyEngine } from "../context/CopyEngineContext";
import { loadIndexes, getActiveIndexId, updateIndex } from "../lib/indexStore";
import { getProxyAddress } from "../lib/polymarketProxy";
import { USDC_E } from "../lib/polymarketContracts";
import { networkById, withRpcFallback } from "../lib/networks";
import type { SavedIndex, PolymarketTrade } from "../lib/types";
import type { ExecutionLogEntry, ObservedTrade } from "../lib/copyEngine";
import { fetchWalletTradesUntil } from "../lib/polymarket";
import WalletFundingPanel from "./WalletFundingPanel";
import EnableTradingPanel from "./EnableTradingPanel";
import PolymarketAccountPanel from "./PolymarketAccountPanel";
import BackendSignerPanel from "./BackendSignerPanel";
import WalletPanel from "./WalletPanel";
import PortfolioPanel from "./PortfolioPanel";
import UserStratsPanel from "./UserStratsPanel";
import StratParamsPanel from "./StratParamsPanel";
import ThemeToggle from "./ThemeToggle";

const ERC20_BAL_ABI = [
  "function balanceOf(address) view returns (uint256)",
];

// Live monitoring poll cadence — configurable per-strat via `livePollMinutes`.
// Defaults to 1 minute. The BACKTEST tab has its own `rebalanceMinutes` field
// for historical-simulation aggregation; the two are decoupled so a slow
// backtest cadence doesn't silently throttle real-time copy.
const LIVE_POLL_OPTIONS: { minutes: number; label: string }[] = [
  { minutes: 1, label: "1MIN" },
  { minutes: 2, label: "2MIN" },
  { minutes: 5, label: "5MIN" },
  { minutes: 10, label: "10MIN" },
  { minutes: 15, label: "15MIN" },
  { minutes: 30, label: "30MIN" },
  { minutes: 60, label: "1H" },
];
// Poll cadence floor, in minute units. A near-real-time 5s cadence × N
// watched traders was firing several req/s at Polymarket's data-api 24/7
// and getting throttled with HTTP 429 (Cloudflare 1015) on nearly every
// fetch → no trades observed → no copies. 1 minute keeps the engine
// responsive while staying well under the rate limit. Anything the strat
// configures below this floor gets clamped up.
const DEFAULT_LIVE_POLL_MIN = 1;
const MIN_LIVE_POLL_MIN = 1;

function formatTime(ts: number): string {
  const d = new Date(ts);
  return `${d.getUTCHours().toString().padStart(2, "0")}:${d.getUTCMinutes().toString().padStart(2, "0")}:${d.getUTCSeconds().toString().padStart(2, "0")}`;
}

// Compact label for the LIVE header's read-only POLL display, mirroring
// the STRAT panel's POLL EVERY options (5s … 24h). Same fractional-minute
// space — values below 1 render as seconds, otherwise minute/hour units.
function formatLivePoll(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return "—";
  if (minutes < 1) return `${Math.round(minutes * 60)}s`;
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "NOW";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ${sec % 60}s`;
  return `${Math.floor(min / 60)}h ${min % 60}m`;
}

// Past-tense friend of formatCountdown — "5s ago" / "3m ago" / "1h 2m ago".
function formatAgo(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const sec = Math.floor(ms / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m ago`;
}

// Compact rounded stat card — used in the LIVE stats grid. The `tone` knob
// recolors the value (white default, green for "good", amber for "watching",
// red for "warning"). Label stays muted so the number reads first.
function StatCard({
  label,
  value,
  tone = "white",
  title,
  fullWidth,
}: {
  label: string;
  value: ReactNode;
  tone?: "white" | "green" | "amber" | "red";
  title?: string;
  fullWidth?: boolean;
}) {
  const valueCls =
    tone === "green" ? "text-green-400" :
    tone === "amber" ? "text-amber-400" :
    tone === "red" ? "text-red-400" :
    "text-pixel-white";
  return (
    <div
      title={title}
      className={`rounded-md border border-pixel-border/60 bg-pixel-black/40 px-2.5 py-1.5 flex items-baseline justify-between gap-2 ${fullWidth ? "col-span-2 md:col-span-3" : ""}`}
    >
      <span className="text-[11px] text-pixel-gray tracking-[0.18em] uppercase">{label}</span>
      <span className={`text-[15px] font-mono ${valueCls}`}>{value}</span>
    </div>
  );
}

function LogIcon({ type }: { type: ExecutionLogEntry["type"] }) {
  switch (type) {
    case "COPY_BUY": return <span className="text-red-400">BUY</span>;
    case "COPY_SELL": return <span className="text-green-400">SELL</span>;
    case "SKIP": return <span className="text-pixel-gray">SKIP</span>;
    case "ERROR": return <span className="text-red-400">ERR</span>;
    case "BALANCE": return <span className="text-amber-400">BAL</span>;
    case "CYCLE_START": return <span className="text-pixel-gray">---</span>;
    case "CYCLE_END": return <span className="text-green-400">END</span>;
    default: return <span className="text-pixel-gray">???</span>;
  }
}

export default function LivePanel() {
  const { auth, authenticate, loading: authLoading } = useAuth();
  const { engineState, isLive, startLive, stopLive, pauseLive, resumeLive, backendRunning, catchUp } = useCopyEngine();
  // confirm-start flow removed — user wants direct start/stop.
  const [liveCapital, setLiveCapital] = useState(100);
  // Proxy USDC.e balance — this is the on-chain "BALANCE" the engine should
  // size mirrors against. Polled every 15s while the LIVE tab is mounted.
  const [proxyBalance, setProxyBalance] = useState<number | null>(null);
  // Catch-up status — "running" disables the button, "result" surfaces
  // the placed/failed count after the one-shot scan completes. Declared
  // after proxyBalance so the useCallback dep array doesn't hit a TDZ
  // ReferenceError on first render (const isn't hoisted).
  const [catchUpStatus, setCatchUpStatus] = useState<string | null>(null);
  const [catchingUp, setCatchingUp] = useState(false);
  // Lookback window for CATCH UP, in hours. Persisted to localStorage so
  // it survives page reloads — most users settle on one value (e.g. 6h)
  // and re-typing it every time would be annoying. Clamped [1, 24] in
  // the input handler; the engine accepts fractional but the UI sticks
  // to whole hours for simplicity.
  const [catchUpHours, setCatchUpHours] = useState<number>(() => {
    if (typeof window === "undefined") return 1;
    const saved = parseFloat(window.localStorage.getItem("polyCatchUpHours") || "");
    return Number.isFinite(saved) && saved >= 1 && saved <= 24 ? saved : 1;
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    // QuotaExceededError can happen when other big keys (engine log,
    // copiedIds dedup) have filled the bucket — wrap every preference
    // write so a single tiny "1h" save doesn't crash the panel.
    try { window.localStorage.setItem("polyCatchUpHours", String(catchUpHours)); } catch {}
  }, [catchUpHours]);
  // TOP N cap — how many of the highest-notional candidates across all
  // traders to actually fire. Without a cap the engine sprayed every
  // trade that cleared the floor and burned through capital on dust.
  // Defaults to 5; persisted to localStorage.
  const [catchUpTopN, setCatchUpTopN] = useState<number>(() => {
    if (typeof window === "undefined") return 5;
    const n = parseInt(window.localStorage.getItem("polyCatchUpTopN") || "", 10);
    return Number.isFinite(n) && n >= 1 && n <= 100 ? n : 5;
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    try { window.localStorage.setItem("polyCatchUpTopN", String(catchUpTopN)); } catch {}
  }, [catchUpTopN]);
  // Sell-winners toggle — before catch-up buys, close any open position
  // with positive P&L to recoup USDC. Defaults on so the user gets the
  // "free liquidity" behavior they asked for without an extra click.
  const [catchUpSellWinners, setCatchUpSellWinners] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem("polyCatchUpSellWinners") !== "0";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    try { window.localStorage.setItem("polyCatchUpSellWinners", catchUpSellWinners ? "1" : "0"); } catch {}
  }, [catchUpSellWinners]);
  // Confidence floor for CATCH UP — only mirror trader trades whose
  // notional clears this threshold. `null` means "auto" (use proxy
  // balance, mirrors the original "whatever I have in the proxy"
  // intent). Persisted same as catchUpHours.
  const [catchUpMinNotional, setCatchUpMinNotional] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem("polyCatchUpMinNotional");
    if (raw === "auto" || raw === null) return null;
    const n = parseFloat(raw);
    return Number.isFinite(n) && n >= 0 ? n : null;
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(
        "polyCatchUpMinNotional",
        catchUpMinNotional === null ? "auto" : String(catchUpMinNotional),
      );
    } catch {}
  }, [catchUpMinNotional]);
  // Resolve the actual threshold passed to the engine — auto uses proxy
  // balance; explicit value uses what the user typed.
  const effectiveMinNotional = catchUpMinNotional ?? Math.max(1, proxyBalance ?? 1);
  const handleCatchUp = useCallback(async () => {
    if (catchingUp || !isLive) return;
    setCatchingUp(true);
    setCatchUpStatus(`starting · last ${catchUpHours}h…`);
    try {
      const result = await catchUp({
        lookbackHours: catchUpHours,
        minNotional: effectiveMinNotional,
        topN: catchUpTopN,
        sellWinners: catchUpSellWinners,
        onProgress: (msg) => setCatchUpStatus(msg),
      });
      setCatchUpStatus(`done · sold ${result.sold} · placed ${result.placed} · failed ${result.failed} · skipped ${result.skipped}`);
    } catch (e) {
      setCatchUpStatus(`error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCatchingUp(false);
    }
  }, [catchingUp, isLive, catchUp, effectiveMinNotional, catchUpHours, catchUpTopN, catchUpSellWinners]);
  // Track whether the user has manually overridden capital via the
  // CAPITAL CAP picker. If they have, we stop auto-syncing to proxy balance
  // and respect their explicit cap. Clearing the cap (or hitting MAX) re-
  // enables auto-tracking.
  const userOverrodeCapitalRef = useRef(false);
  const [now, setNow] = useState(Date.now());
  // Re-tick at the strat panel cadence so activeStrat reflects edits the
  // user makes in the STRAT panel (POLL EVERY changes etc) WITHOUT this
  // component being unmounted/remounted. Previously useMemo([]) captured
  // the strat once and the live engine never saw new params.
  const [stratTick, setStratTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setStratTick((n) => n + 1), 2000);
    return () => clearInterval(t);
  }, []);
  // Paginated trade view (under the EXECUTION LOG). Toggle filter shows
  // only actual trade events (COPY_BUY / COPY_SELL / SKIP) instead of the
  // CYCLE_START/END heartbeat noise that dominates a healthy log.
  const [tradesPage, setTradesPage] = useState(0);
  // `upstream` = raw trades observed from watched traders (real-time stream
  // of what they're doing, regardless of our mirror decisions). Defaults to
  // upstream so the panel is useful immediately even when no orders fire yet.
  const [tradesFilter, setTradesFilter] = useState<"upstream" | "trades" | "all" | "fills">("fills");
  const TRADES_PAGE_SIZE = 25;

  // ── Actual on-chain fills (FILLS tab) ──────────────────────────
  // The engine log (MY TRADES) is in-memory and resets when the session
  // restarts; it also hides DRY_RUN decisions. The FILLS tab instead pulls
  // the *ground-truth* fill history straight from Polymarket's data-api for
  // the deposit wallet the engine trades through — so the user always sees
  // every real buy/sell regardless of restarts or execution mode.
  const [fills, setFills] = useState<PolymarketTrade[]>([]);
  const [fillsLoading, setFillsLoading] = useState(false);
  const [fillsError, setFillsError] = useState<string | null>(null);
  const [fillsWallet, setFillsWallet] = useState<string | null>(null);

  const loadFills = useCallback(async () => {
    if (!auth.address) return;
    setFillsLoading(true);
    setFillsError(null);
    try {
      // Resolve the deposit wallet (where V2 trades actually land), then
      // paginate its full activity history (cutoff 0 = all of it).
      const info = await fetch(
        `/api/polymarket/deposit-wallet/info?eoa=${auth.address}`,
        { cache: "no-store" },
      ).then((r) => (r.ok ? r.json() : null));
      const wallet: string | undefined = info?.depositWallet;
      if (!wallet) throw new Error("could not resolve deposit wallet");
      setFillsWallet(wallet);
      const trades = await fetchWalletTradesUntil(wallet, 0);
      setFills(trades);
    } catch (e) {
      setFillsError(e instanceof Error ? e.message : String(e));
    } finally {
      setFillsLoading(false);
    }
  }, [auth.address]);

  // ── Live-page tab nav ──
  // The LIVE view previously stacked 6+ panels vertically and overflowed
  // the screen on anything < 1080p. Tabs group related panels so only the
  // section the user is looking at is visible.
  // Split the LIVE view into focused tabs — "OVERVIEW" used to mean
  // PortfolioPanel + the 7-stat metric grid + the skip-floor banner all
  // stacked, which is the over-stuffed view the user flagged. Now:
  //   PNL    — pie + over-time curve + top positions (PortfolioPanel)
  //   TRADES — execution log + filters
  //   STATS  — balance/orders/volume/cycles/sync grid + last fetch + banners
  //   WALLET — trading wallet deposit/withdraw + V1 proxy
  //   PARAMS — auto-trading config + signer + funding + checklist (ex-SETUP)
  type LiveTab = "pnl" | "trades" | "stats" | "wallet" | "params";
  const [liveTab, setLiveTab] = useState<LiveTab>("pnl");

  // Fetch on-chain fills on first open of the FILLS tab; refresh every 60s
  // while it stays open. Declared after `liveTab` to avoid a TDZ reference.
  useEffect(() => {
    if (liveTab !== "trades" || tradesFilter !== "fills") return;
    void loadFills();
    const t = setInterval(() => void loadFills(), 60_000);
    return () => clearInterval(t);
  }, [liveTab, tradesFilter, loadFills]);

  // Tick for countdown
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // Poll the proxy's on-chain USDC.e balance every 15s. This is the number
  // used to auto-sync CAPITAL — keeps the engine's mirror sizing aligned
  // with the actual funds available on the proxy rather than an arbitrary
  // CAPITAL CAP picker default.
  useEffect(() => {
    if (!auth.address) {
      setProxyBalance(null);
      return;
    }
    let cancelled = false;
    const fetchBal = async () => {
      try {
        const proxy = await getProxyAddress(auth.address!);
        const polygon = networkById("polygon")!;
        const raw: bigint = await withRpcFallback(polygon, async (url) => {
          const provider = new JsonRpcProvider(url);
          const c = new Contract(USDC_E, ERC20_BAL_ABI, provider);
          return c.balanceOf(proxy);
        });
        if (!cancelled) setProxyBalance(Number(formatUnits(raw, 6)));
      } catch { /* keep last known on RPC hiccup */ }
    };
    void fetchBal();
    const t = setInterval(fetchBal, 15_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [auth.address]);

  // Auto-sync CAPITAL → proxy balance unless the user has explicitly capped.
  // Without this, CAPITAL stays at the $100 default while BALANCE shows the
  // real on-chain amount ($302+), and the engine mirrors against the wrong
  // budget. User can override via the CAPITAL CAP picker (sets the ref);
  // they can re-enable auto-tracking by hitting the MAX preset in that picker.
  useEffect(() => {
    if (proxyBalance === null) return;
    if (userOverrodeCapitalRef.current) return;
    const rounded = Math.floor(proxyBalance);
    if (rounded > 0 && rounded !== liveCapital) setLiveCapital(rounded);
  }, [proxyBalance, liveCapital]);

  // Wrap setLiveCapital so the CAPITAL CAP picker (in WalletFundingPanel)
  // flips the override ref — any manual pick disables proxy auto-sync.
  const handleManualCapital = useCallback((n: number) => {
    userOverrodeCapitalRef.current = true;
    setLiveCapital(n);
  }, []);

  const activeStrat = useMemo((): SavedIndex | null => {
    const id = getActiveIndexId();
    if (!id) return null;
    return loadIndexes().find((s) => s.id === id) || null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stratTick]);

  // Single source of truth for poll cadence: the STRAT panel's POLL EVERY
  // field (`rebalanceMinutes`). Falls back to livePollMinutes (legacy) and
  // then the 5s default. Strats that still have the old 1-minute legacy
  // value (`=== 1`) get auto-upgraded to 5s so the user isn't stuck behind
  // a stale default — the previous 1m cadence was what made the "CATCH UP"
  // banner pop up constantly. Anything else (explicit 30s, 5m, 30m, …) is
  // honored as-is.
  const rawLivePollMin =
    activeStrat?.rebalanceMinutes ??
    activeStrat?.livePollMinutes ??
    DEFAULT_LIVE_POLL_MIN;
  // Clamp any sub-minute cadence (incl. the old 5s default) up to the floor so
  // a stale strat config can't keep hammering the data-api into rate limits.
  const livePollMin = Math.max(MIN_LIVE_POLL_MIN, rawLivePollMin);

  // Preconditions
  const hasWallet = auth.connected && !!auth.address;
  const hasCreds = auth.authenticated && !!auth.clobCreds;
  const hasTraders = (activeStrat?.traders.filter((t) => t.enabled !== false).length ?? 0) > 0;
  // Always true now — LIVE is hard-pinned to 1-minute polling. The strat's
  // rebalanceMinutes only affects BACKTEST cadence and isn't a live precondition.
  const hasRebalance = true;
  const hasCapital = liveCapital > 0;
  const canStart = hasWallet && hasCreds && hasTraders && hasRebalance && hasCapital;

  // Direct toggle — no confirmation step. The user explicitly asked to
  // always be able to start/stop without a confirm dialog blocking them.
  // STOP just halts; GO LIVE starts immediately with current params.
  const handleToggle = useCallback(() => {
    if (isLive) {
      stopLive();
      if (activeStrat) updateIndex(activeStrat.id, { liveEnabled: false, updatedAt: Date.now() });
      return;
    }

    if (!auth.clobCreds || !auth.address || !activeStrat) return;

    startLive({
      strategyId: activeStrat.id,
      traders: activeStrat.traders.filter((t) => t.enabled !== false),
      capital: liveCapital,
      intervalMs: livePollMin * 60_000,
      creds: auth.clobCreds,
      address: auth.address,
      // Honor the strat's TRADE SIZE floor — was hardcoded to $1 before,
      // causing every dust mirror to skip with BELOW_MIN_SIZE even when the
      // user had set MIN TRADE to 0.1 in BACKTEST. Falls back to $1.
      minOrderSize: activeStrat.minTrade ?? 1,
      // Ceiling for the proportional sizing. Without this, a single whale
      // trade from a high-volume trader could blow the proportional mirror
      // past the user's TRADE SIZE max and chew through capital in one shot.
      maxOrderSize: activeStrat.maxTrade,
      // Same lookback the BACKTEST tab uses to compute the per-trader
      // volume denominator — keeps live copyRatio == backtest scale, so
      // the preview predicts execution.
      backtestDays: activeStrat.backtestDays ?? 3,
      maxSlippageBps: 300,
    });

    updateIndex(activeStrat.id, {
      liveEnabled: true,
      capital: liveCapital,
      // Persist both for backwards compat — `rebalanceMinutes` is the
      // canonical field the STRAT panel writes; we mirror it into
      // `livePollMinutes` so older code paths keep working.
      rebalanceMinutes: livePollMin,
      livePollMinutes: livePollMin,
      updatedAt: Date.now(),
    });
  }, [isLive, auth, activeStrat, liveCapital, livePollMin, startLive, stopLive]);

  const status = engineState?.status || "stopped";
  const nextIn = engineState?.nextCycleAt ? engineState.nextCycleAt - now : 0;

  // Auto-restart the engine when the STRAT POLL EVERY changes mid-run.
  // Without this the engine keeps polling at whatever interval it was
  // started with — the user sees POLL EVERY 15s but NEXT IN still
  // counts down from 60s. Tracks the last-applied interval in a ref so
  // we don't re-trigger on every render. Skips while paused (the engine
  // is intentionally idle then, no point bouncing).
  const lastAppliedIntervalRef = useRef<number | null>(null);
  useEffect(() => {
    if (!isLive || status !== "running") return;
    if (!auth.clobCreds || !auth.address || !activeStrat) return;
    const intervalMs = Math.max(1000, Math.round(livePollMin * 60_000));
    if (lastAppliedIntervalRef.current === null) {
      lastAppliedIntervalRef.current = intervalMs;
      return;
    }
    if (lastAppliedIntervalRef.current === intervalMs) return;
    lastAppliedIntervalRef.current = intervalMs;
    // Hot-restart: stop the existing engine, start a fresh one with the
    // new interval. The CopyEngineContext preserves cursors via local
    // storage so we don't re-copy old trades on restart.
    stopLive();
    startLive({
      strategyId: activeStrat.id,
      traders: activeStrat.traders.filter((t) => t.enabled !== false),
      capital: liveCapital,
      intervalMs,
      creds: auth.clobCreds,
      address: auth.address,
      minOrderSize: activeStrat.minTrade ?? 1,
      maxOrderSize: activeStrat.maxTrade,
      backtestDays: activeStrat.backtestDays ?? 3,
      maxSlippageBps: 300,
    });
  }, [livePollMin, isLive, status, activeStrat, auth.clobCreds, auth.address, liveCapital, startLive, stopLive]);

  return (
    <div className="space-y-1">
      {/* ── Trading wallet at top of LIVE ──
          Compact deposit/withdraw panel mounted above the engine status
          bar so balance + funding is always one click away regardless
          of which tab is active. Engine spamming "wallet empty" errors
          every cycle was confusing — surface the fix inline. */}
      {auth.connected && <WalletPanel />}

      {/* ── Header ── */}
      <div className="pixel-panel border-2 border-pixel-border">
        <div className="px-3 py-1.5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 shrink-0 ${
              isLive && status === "running" ? "bg-green-400 animate-pulse" :
              isLive && status === "paused" ? "bg-amber-400" :
              isLive && status === "error" ? "bg-red-400 animate-pulse" :
              "bg-pixel-gray"
            }`} />
            <span className="text-[16px] text-pixel-white tracking-wider">LIVE COPY</span>
            {isLive && (
              <span className={`text-[13px] font-mono px-1 py-0.5 border ${
                status === "running" ? "border-green-400/40 text-green-400" :
                status === "paused" ? "border-amber-400/40 text-amber-400" :
                status === "error" ? "border-red-400/40 text-red-400" :
                "border-pixel-border text-pixel-gray"
              }`}>
                {status.toUpperCase()}
              </span>
            )}
            {backendRunning && (
              <span className="text-[11px] font-mono px-1.5 py-0.5 border border-green-400/30 text-green-400/80 bg-green-400/5" title="Backend engine running — survives tab close">
                BG
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            {/* SCAN dropdown removed — the STRAT panel's POLL EVERY field
                is the single source of truth for poll cadence now. The
                engine hot-restarts when that field changes (see the
                lastAppliedIntervalRef effect above). Display-only echo
                so the user can see at-a-glance what the engine is using. */}
            <span className="text-[11px] text-pixel-gray tracking-wider" title="Poll cadence — change in STRAT panel POLL EVERY">
              POLL <span className="font-mono text-pixel-white">{formatLivePoll(livePollMin)}</span>
            </span>
            {/* LOADED badge removed — duplicated the same proxy balance
                already shown in the PROXY row below ("$35.22 / $300" up
                here vs "BAL $300.00" right under it). Proxy panel is the
                single source of truth for funded amount. */}
            {/* CATCH UP removed: engine polls every 5s (POLL above), so a
                manual backfill is redundant for steady-state operation.
                The LAST/MIN/TOP/SELL_WINS settings the button controlled
                were also noise in the top bar. If a true backfill is
                ever needed (e.g. after a long pause), STOP + GO LIVE
                rebuilds the cursor — no separate UI needed. */}
            {isLive && status === "running" && (
              <button
                onClick={pauseLive}
                className="pixel-btn text-[13px] px-1.5 py-0.5 border-amber-400/60 text-amber-400 hover:bg-amber-400/10"
              >
                PAUSE
              </button>
            )}
            {isLive && status === "paused" && (
              <button
                onClick={resumeLive}
                className="pixel-btn text-[13px] px-1.5 py-0.5 border-green-400/60 text-green-400 hover:bg-green-400/10"
              >
                RESUME
              </button>
            )}
            <button
              onClick={handleToggle}
              disabled={!isLive && !canStart}
              title={
                isLive
                  ? "Stop the live copy engine"
                  : canStart ? "Start the live copy engine — places real orders" : "Complete the checklist above to enable"
              }
              className={`pixel-btn text-[14px] px-2.5 py-1 transition-colors ${
                isLive
                  ? "border-red-400 text-red-400 hover:bg-red-400/10"
                  : "border-green-400 text-green-400 hover:bg-green-400/10 disabled:opacity-30 disabled:cursor-not-allowed"
              }`}
            >
              {isLive ? "STOP" : "GO LIVE"}
            </button>
          </div>
        </div>
      </div>

      {/* ── Sidebar layout ──
          Vertical tab nav on the left, content panels stacked on the
          right. Splits the LIVE page so the user never has to scroll
          past one section to find another. Always-visible top control
          bar (status + START/STOP/PAUSE) lives above so the engine can
          be paused from any tab. */}
      <div className="flex gap-3">
      <nav className="w-44 shrink-0 flex flex-col gap-1.5 border-r border-pixel-border pr-2 self-start sticky top-2">
        {/* Per-tab "task running" badges — small status chips that let the
            user see at a glance what activity is happening on each tab
            without flipping through them. Engine running = green dot on
            every tab; counts surface trades/orders/balance inline. */}
        {(() => {
          const isRunning = isLive && status === "running";
          const isPaused = isLive && status === "paused";
          const isErrored = isLive && status === "error";
          const dotCls = isRunning
            ? "bg-green-400 animate-pulse"
            : isPaused
              ? "bg-amber-400"
              : isErrored
                ? "bg-red-400 animate-pulse"
                : "";
          const tradesCount = engineState?.observedTrades.length ?? 0;
          const orderCount = engineState?.totalOrdersPlaced ?? 0;
          const cycleCount = engineState?.cycleCount ?? 0;
          const bal = engineState?.balance;
          const items: { id: LiveTab; label: string; badge: ReactNode }[] = [
            { id: "pnl",    label: "PNL",    badge: isLive ? <span className={`w-2 h-2 rounded-full shrink-0 ${dotCls}`} /> : null },
            { id: "trades", label: "TRADES", badge: isLive ? (
              <span className="flex items-center gap-1.5 shrink-0">
                <span className={`w-2 h-2 rounded-full ${dotCls}`} />
                {tradesCount > 0 && <span className="text-[11px] font-mono text-pixel-gray">{tradesCount}</span>}
              </span>
            ) : null },
            { id: "stats",  label: "STATS",  badge: isLive ? (
              <span className="flex items-center gap-1.5 shrink-0">
                <span className={`w-2 h-2 rounded-full ${dotCls}`} />
                {orderCount > 0 && <span className="text-[11px] font-mono text-pixel-gray">{orderCount}</span>}
              </span>
            ) : null },
            { id: "wallet", label: "WALLET", badge: bal !== null && bal !== undefined ? (
              <span className="text-[11px] font-mono text-pixel-gray shrink-0">${bal < 10 ? bal.toFixed(2) : Math.round(bal)}</span>
            ) : null },
            { id: "params", label: "PARAMS", badge: activeStrat ? (
              <span className="text-[11px] font-mono text-pixel-gray shrink-0 truncate max-w-[60px]" title={activeStrat.name}>
                {activeStrat.name}
              </span>
            ) : null },
          ];
          return (
            <>
              {items.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setLiveTab(t.id)}
                  className={`text-left px-3 py-2.5 text-[14px] font-mono tracking-[0.18em] uppercase border-l-2 transition-colors flex items-center justify-between gap-2 ${
                    liveTab === t.id
                      ? "border-green-400 text-green-400 bg-green-400/5"
                      : "border-transparent text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/5"
                  }`}
                >
                  <span>{t.label}</span>
                  {t.badge}
                </button>
              ))}
              {/* Running summary — surfaces the active task at the bottom
                  of the rail so it's visible no matter which tab is
                  selected. */}
              {isLive && (
                <div className="mt-2 pt-2 border-t border-pixel-border/40 px-1">
                  <div className="text-[10px] text-pixel-gray tracking-[0.18em] mb-1">RUNNING</div>
                  <div className="flex items-center gap-1.5 text-[11px] font-mono">
                    <span className={`w-2 h-2 rounded-full ${dotCls}`} />
                    <span className={
                      isRunning ? "text-green-400" :
                      isPaused ? "text-amber-400" :
                      isErrored ? "text-red-400" : "text-pixel-gray"
                    }>
                      {status.toUpperCase()}
                    </span>
                  </div>
                  {activeStrat && (
                    <div className="text-[10px] text-pixel-white font-mono mt-1 truncate" title={activeStrat.name}>
                      {activeStrat.name}
                    </div>
                  )}
                  {liveCapital > 0 && (
                    <div className="text-[10px] text-pixel-gray font-mono mt-0.5">
                      capital ${liveCapital < 10 ? liveCapital.toFixed(2) : Math.round(liveCapital).toLocaleString()}
                    </div>
                  )}
                  {engineState && (
                    <div className="text-[10px] text-pixel-gray font-mono mt-0.5">
                      {cycleCount} cycle{cycleCount === 1 ? "" : "s"}
                    </div>
                  )}
                  {engineState?.nextCycleAt && isRunning && (
                    <div className="text-[10px] text-pixel-gray font-mono">
                      next {formatCountdown(nextIn)}
                    </div>
                  )}
                </div>
              )}
            </>
          );
        })()}
      </nav>
      <div className="flex-1 min-w-0 space-y-3">

      {/* ── Preconditions ──
          Moved to the TOP so the user knows what's blocking GO LIVE before
          scrolling through funding panels. Compact pill row: each step is
          green-filled when satisfied, amber-outline when actionable, muted
          when stale. The summary count tells you "5/6 ready" at a glance. */}
      {liveTab === "params" && !isLive && (() => {
        const items = [
          { ok: hasWallet, label: "WALLET", action: null as null | { label: string; disabled: boolean; onClick: () => void } },
          {
            ok: hasCreds,
            label: "CLOB",
            // When CLOB isn't authed (and a wallet IS connected) expose a
            // refresh action so the user can sign again without leaving
            // the live panel.
            action: !hasCreds && hasWallet ? {
              label: authLoading ? "signing…" : "sign",
              disabled: authLoading,
              onClick: () => { void authenticate(); },
            } : null,
          },
          { ok: !!activeStrat, label: "STRAT", action: null },
          { ok: hasTraders, label: "TRADERS", action: null },
          { ok: hasRebalance, label: "REBALANCE", action: null },
          { ok: hasCapital, label: "CAPITAL", action: null },
        ];
        const okCount = items.filter((i) => i.ok).length;
        return (
          <div className="pixel-panel border-2 border-pixel-border px-3 py-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-pixel-gray tracking-[0.18em] shrink-0">CHECKLIST</span>
              <span className={`text-[12px] font-mono tracking-wider shrink-0 ${
                okCount === items.length ? "text-green-400" : "text-amber-400"
              }`}>
                {okCount}/{items.length} {okCount === items.length ? "· ready to go live" : "· not ready"}
              </span>
              <div className="flex items-center gap-1.5 flex-wrap ml-auto">
                {items.map((item) => (
                  <span
                    key={item.label}
                    className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-mono tracking-wider border ${
                      item.ok
                        ? "border-green-400/60 text-green-400 bg-green-400/10"
                        : "border-pixel-border/60 text-pixel-gray bg-pixel-black/40"
                    }`}
                  >
                    <span className="text-[10px]">{item.ok ? "✓" : "○"}</span>
                    <span>{item.label}</span>
                    {item.action && (
                      <button
                        onClick={item.action.onClick}
                        disabled={item.action.disabled}
                        className="ml-0.5 text-[10px] font-mono text-amber-400 hover:text-amber-300 disabled:opacity-50 disabled:cursor-not-allowed underline-offset-2 hover:underline"
                        title="Sign a MetaMask message to derive your Polymarket CLOB API key"
                      >
                        {item.action.label}
                      </button>
                    )}
                  </span>
                ))}
              </div>
            </div>
          </div>
        );
      })()}

      {/* WalletFundingPanel moved to top of LivePanel — see directly above
          the engine control bar. */}

      {/* ── CLOB credentials required banner ──
          Orders are signed with a Polymarket CLOB API key derived from a
          one-time MetaMask signature. Without it EVERY order placement fails
          upstream ("clob auth"), but the auto-derive only attempts once on
          connect and silently gives up if rejected — leaving the engine
          running blind. Force it: a loud, always-visible block (even while
          live) with a GENERATE button until creds exist. */}
      {hasWallet && !hasCreds && (
        <div className="pixel-panel border-2 border-red-500/70 bg-red-500/10 p-3 flex items-center gap-3 flex-wrap">
          <span className="text-red-400 text-xl">⚠</span>
          <div className="flex-1">
            <div className="text-sm font-bold text-red-400">
              CLOB credentials required — generate to place orders
            </div>
            <div className="text-xs text-pixel-muted mt-0.5">
              {isLive
                ? "Engine is running but every order fails with “clob auth” until you sign. One MetaMask signature, no gas."
                : "Sign one MetaMask message to derive your Polymarket trading key. No gas, no transaction."}
            </div>
          </div>
          <button
            onClick={() => { void authenticate(); }}
            disabled={authLoading}
            className="px-3 py-1.5 bg-red-600 hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm rounded"
          >
            {authLoading ? "SIGNING…" : "GENERATE CLOB →"}
          </button>
        </div>
      )}

      {/* ── Funding required banner ──
          Engine is running but the V2 deposit wallet is empty — every
          cycle would log a "Trading wallet has $0.00" error and the
          user wouldn't necessarily look at the log. Surface it as a
          loud banner on every tab with a one-click jump to WALLET. */}
      {isLive && engineState && engineState.balance !== null && engineState.balance <= 0 && (
        <div className="pixel-panel border-2 border-amber-400/70 bg-amber-400/10 p-3 flex items-center gap-3 flex-wrap">
          <span className="text-amber-400 text-xl">⚠</span>
          <div className="flex-1">
            <div className="text-sm font-bold text-amber-400">
              Trading wallet is empty — deposit USDC to start trading
            </div>
            <div className="text-xs text-pixel-muted mt-0.5">
              Engine is running but every cycle skips because there&apos;s no cash to trade with.
            </div>
          </div>
          <button
            onClick={() => setLiveTab("wallet")}
            className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white font-bold text-sm rounded"
          >
            FUND NOW →
          </button>
        </div>
      )}

      {/* ── WALLET tab help banner (collapsible) ──
          Two wallet panels was confusing users — explain at the top
          which is the right one to use and what the other is for. The
          collapsed state persists across reloads so users who already
          know the layout don't get the wall of text every time. */}
      {liveTab === "wallet" && auth.connected && (() => {
        const KEY = "poly_wallet_help_open";
        const [open, setOpen] = [
          (() => {
            try { return localStorage.getItem(KEY) !== "0"; }
            catch { return true; }
          })(),
          (next: boolean) => {
            try { localStorage.setItem(KEY, next ? "1" : "0"); }
            catch {}
          },
        ];
        return (
          <details
            className="pixel-panel border-2 border-pixel-border px-3 py-2 text-xs"
            open={open}
            onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
          >
            <summary className="cursor-pointer list-none flex items-center gap-2">
              <span className="inline-block w-4 h-4 rounded-full bg-blue-500/20 border border-blue-400/60 text-blue-400 text-center leading-[14px] text-[10px]">
                ?
              </span>
              <span className="text-pixel-white font-bold tracking-wide">
                Which wallet do I use?
              </span>
              <span className="ml-auto text-[10px] text-pixel-muted">
                click to {open ? "collapse" : "expand"}
              </span>
            </summary>
            <div className="mt-2 space-y-2">
              <div className="text-pixel-muted leading-relaxed">
                <span className="text-green-400 font-bold">TRADING WALLET</span>
                {" "}is where you put money to copy-trade. <b>This is the only
                one you need.</b> Click DEPOSIT, send USDC.e from MetaMask,
                engine starts trading.
              </div>
              <div className="text-pixel-muted leading-relaxed">
                <span className="text-purple-400 font-bold">LEGACY PROXY</span>
                {" "}is a wallet from Polymarket's old (V1) system.
                <b> Ignore it unless you have leftover USDC sitting there.</b>
                {" "}If you do, WITHDRAW it back to your MetaMask, then DEPOSIT
                into TRADING WALLET above. The panel auto-hides once empty.
              </div>
              <div className="text-pixel-muted leading-relaxed">
                <span className="text-amber-400 font-bold">WITHDRAW</span> from
                TRADING WALLET sends USDC.e straight to your MetaMask address
                — gasless, no popup. Use it any time to pull funds out.
              </div>
            </div>
          </details>
        );
      })()}

      {/* ── Legacy V1 Safe (proxy) ── (PARAMS + WALLET tabs)
          Auto-hides itself when balance is 0 (see component). Lets
          users with leftover V1 funds withdraw them out and re-deposit
          into the V2 TRADING WALLET. */}
      {(liveTab === "params" || liveTab === "wallet") && auth.connected && (
        <PolymarketAccountPanel />
      )}

      {/* ── V2 deposit wallet (trading address) ── (WALLET tab) */}
      {liveTab === "wallet" && auth.connected && <WalletPanel />}

      {/* ── Portfolio: cash vs positions, pie + over-time ── (PNL tab) */}
      {liveTab === "pnl" && auth.connected && <PortfolioPanel />}

      {/* BackendSignerPanel removed — the V1 Safe-co-owner flow that
          panel managed isn't needed for V2 trading. The V2 deposit
          wallet is CREATE2-derived from the per-user backend signer's
          own EOA, so the backend is *inherently* the wallet's
          authorized signer — no separate "TURN ON AUTO-TRADING" step
          to flip. Auto-trading is on by construction. */}

      {/* ── Active strat params + watchlist ── (PARAMS tab)
          Read-only mirror of what's set in the STRATS tab — capital,
          min/max trade, poll cadence, the trader list with weights.
          Lets the user sanity-check what's about to fire without
          leaving the LIVE view. `stratTick` re-evaluates on each strat
          edit elsewhere in the app. */}
      {liveTab === "params" && auth.connected && <StratParamsPanel tick={stratTick} />}

      {/* ── Custom strats: upload mod.py / mod.rs ── (PARAMS tab)
          File-based strats stored on the persistent data volume. Engine
          runtime loader is the next layer — this surfaces the upload /
          list / delete plumbing so the framework is ready. */}
      {liveTab === "params" && auth.connected && <UserStratsPanel />}

      {/* ── Stats (when live) ── (STATS tab)
          Card grid; LAST SYNC tracks the most recent successful Polymarket
          data-api pull, LAST FETCH surfaces the CYCLE_END summary. */}
      {liveTab === "stats" && isLive && engineState && (
        <div className="pixel-panel border-2 border-pixel-border p-2">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5">
            <StatCard
              label="BALANCE"
              value={engineState.balance !== null ? `$${engineState.balance.toFixed(2)}` : "—"}
              tone="white"
            />
            {/* Only show CAPITAL when it's actually different from BALANCE —
                i.e. the user has manually capped below the proxy balance.
                When auto-tracking, CAPITAL == BALANCE makes a separate card
                pure noise (the user's complaint). */}
            {userOverrodeCapitalRef.current && proxyBalance !== null && liveCapital < proxyBalance && (
              <StatCard
                label="CAP"
                value={`$${liveCapital.toLocaleString()}`}
                tone="amber"
                title={`You capped at $${liveCapital} below the full proxy balance ($${proxyBalance.toFixed(2)}). Hit MAX in the CAPITAL CAP picker to clear and use the full balance.`}
              />
            )}
            <StatCard
              label="ORDERS"
              value={
                <>
                  <span className="text-green-400">{engineState.totalOrdersPlaced}</span>
                  {engineState.totalOrdersFailed > 0 && (
                    <span className="text-red-400"> / {engineState.totalOrdersFailed}F</span>
                  )}
                </>
              }
            />
            <StatCard
              label="VOLUME"
              value={`$${engineState.totalVolumeMirrored.toFixed(0)}`}
              tone="white"
            />
            <StatCard
              label="CYCLES"
              value={String(engineState.cycleCount)}
              tone="white"
            />
            <StatCard
              label="NEXT IN"
              value={formatCountdown(nextIn)}
              tone="green"
            />
            <StatCard
              label="LAST SYNC"
              value={engineState.lastCycleAt
                ? formatAgo(now - engineState.lastCycleAt)
                : "never"}
              tone={
                engineState.lastCycleAt && (now - engineState.lastCycleAt) < 90_000
                  ? "green"
                  : engineState.lastCycleAt && (now - engineState.lastCycleAt) < 300_000
                    ? "amber"
                    : "red"
              }
              title={
                engineState.lastCycleAt
                  ? `Most recent successful Polymarket data-api fetch at ${new Date(engineState.lastCycleAt).toLocaleTimeString()}`
                  : "No sync yet — first cycle pending"
              }
            />
            {/* LAST FETCH — surfaces the most recent CYCLE_END summary so
                the user can see whether the engine is actually pulling
                trader activity ("polled 7 · 2 active · 5 new trades") vs.
                silently hitting fetch errors ("polled 7 · 7 fetch
                errors"). This was the missing signal in the "0 trades
                forever" failure mode the user kept hitting. */}
            {(() => {
              const lastCycleEnd = engineState.log.find((e) => e.type === "CYCLE_END");
              const lastError = engineState.log.find((e) => e.type === "ERROR" && e.reason);
              const summary = lastCycleEnd?.reason ?? "—";
              const hasFetchErrors = summary.includes("fetch error");
              const tone: "white" | "amber" | "red" | "green" = hasFetchErrors
                ? "red"
                : summary.includes("new trades observed")
                  ? "green"
                  : "white";
              return (
                <StatCard
                  label="LAST FETCH"
                  value={
                    <span className="text-[12px] tracking-wider">
                      {summary}
                    </span>
                  }
                  tone={tone}
                  title={
                    lastError?.reason
                      ? `Most recent error in log: ${lastError.reason.slice(0, 200)}`
                      : "Most recent CYCLE_END summary — what the engine pulled on its last poll"
                  }
                  fullWidth
                />
              );
            })()}
          </div>

          {/* ── Recommended-capital hint ──
              No longer a "drop the floor" prompt — the engine now clamps
              dust mirrors UP to Polymarket's $1 floor instead of skipping
              them, so trades flow regardless of capital size. This banner
              just *suggests* a capital level at which proportional sizing
              would produce $1+ mirrors naturally, instead of relying on
              the clamp (which makes per-trade exposure equal across small
              and large leader trades, blunting the strategy's signal).
              Hidden until there's enough data to make a real recommendation. */}
          {(() => {
            // Pull recent SKIP/CLAMP entries from the log to estimate the
            // average raw (unclamped) mirror size. If the average is way
            // below $1, recommend scaling capital to bring it up.
            const rawSizes = engineState.log
              .filter((e) => e.type === "SKIP" && typeof e.mirrorNotional === "number")
              .map((e) => Math.abs(e.mirrorNotional as number))
              .filter((v) => v > 0);
            if (rawSizes.length < 5) return null;
            const avgRaw = rawSizes.reduce((s, v) => s + v, 0) / rawSizes.length;
            // Target: average raw mirror = $1 (no clamping needed).
            // Scale linearly: recommendedCapital = currentCapital * ($1 / avgRaw).
            if (avgRaw >= 1) return null;
            const recommended = Math.ceil((liveCapital * 1.0 / avgRaw) / 10) * 10;
            return (
              <div className="mt-2 px-3 py-2 border border-pixel-border/60 bg-pixel-black/30 rounded">
                <div className="text-[12px] text-pixel-white font-mono mb-1">
                  Trades are firing, but most are auto-clamped to Polymarket's $1 min
                </div>
                <div className="text-[11px] text-pixel-gray font-mono">
                  Your $${liveCapital.toFixed(0)} capital × leader weight ≈ ${avgRaw.toFixed(2)}/trade.
                  For natural proportional sizing (no clamp),
                  <span className="text-green-400"> ~${recommended.toLocaleString()} recommended</span>.
                  Until then everything still works — copy mirrors just take more relative size than the leader did.
                </div>
              </div>
            );
          })()}

          {engineState.error && (
            <div className="mt-2 px-2 py-1 border border-red-400/40 bg-red-400/5 text-[14px] text-red-400 font-mono rounded">
              {engineState.error}
            </div>
          )}

          {catchUpStatus && (
            <div
              className={`mt-2 px-2 py-1 border text-[13px] font-mono rounded ${
                catchingUp
                  ? "border-amber-400/40 bg-amber-400/5 text-amber-400 animate-pulse"
                  : "border-green-400/40 bg-green-400/5 text-green-400"
              }`}
              title="Result of the most recent CATCH UP scan"
            >
              CATCH UP · {catchUpStatus}
            </div>
          )}
        </div>
      )}

      {/* ── Trades / Execution Log (filterable + paginated) ──
          The full engine log is dominated by CYCLE_START/END heartbeats — the
          "TRADES" view filters to just the entries you actually copy or skip,
          which is what the user usually wants when monitoring. Pagination
          keeps the panel a fixed height instead of growing across the page. */}
      {liveTab === "trades" && isLive && engineState && (() => {
        const isUpstream = tradesFilter === "upstream";
        const isFills = tradesFilter === "fills";
        // UPSTREAM tab pulls from the engine's observed-trades ring buffer
        // (real-time mirror of what watched traders are doing); the other
        // tabs filter the engine log (copy decisions / cycle heartbeats).
        // MIRROR view shows every per-trade decision: successful copies,
        // failures, skips, AND clamp-applied entries from the new sizing
        // path. Previously this filter only let through COPY_*/SKIP so the
        // user saw "No copy events yet" while ORDERS=20F because failures
        // are tagged ERROR (and clamps are tagged BALANCE) — both hidden.
        // Cycle heartbeats stay hidden here; ALL surfaces those.
        const items = isFills
          ? fills
          : isUpstream
          ? engineState.observedTrades
          : engineState.log.filter((e) =>
              tradesFilter === "trades"
                ? e.type === "COPY_BUY" ||
                  e.type === "COPY_SELL" ||
                  e.type === "SKIP" ||
                  e.type === "ERROR" ||
                  (e.type === "BALANCE" && !!e.upstreamTradeId)
                : true,
            );

        // Index every trade-tagged log entry by its upstreamTradeId so each
        // UPSTREAM row can render its mirror outcome inline (✓ copied, ⊘
        // skipped, ✗ failed) with the exact reason. Previously the user
        // could see a trader's trade in the upstream feed but had to dig
        // through the MIRROR / ALL tabs to find out why it didn't fire —
        // and even then the join was implicit (trader + market + timestamp).
        const outcomeById = new Map<string, ExecutionLogEntry>();
        for (const entry of engineState.log) {
          if (entry.upstreamTradeId) {
            // Keep the LATEST entry per upstream id (engine may emit both
            // a SKIP and a later retry; we want the final state).
            const prev = outcomeById.get(entry.upstreamTradeId);
            if (!prev || entry.timestamp > prev.timestamp) {
              outcomeById.set(entry.upstreamTradeId, entry);
            }
          }
        }
        const sorted = [...items].sort((a, b) => b.timestamp - a.timestamp);
        const totalPages = Math.max(1, Math.ceil(sorted.length / TRADES_PAGE_SIZE));
        const safePage = Math.min(tradesPage, totalPages - 1);
        const start = safePage * TRADES_PAGE_SIZE;
        const pageEntries = sorted.slice(start, start + TRADES_PAGE_SIZE);
        const headerLabel =
          tradesFilter === "upstream" ? "TRADER TRADES" :
          tradesFilter === "trades" ? "MY TRADES" :
          tradesFilter === "fills" ? "MY FILLS" : "ALL TRADES";
        const countLabel =
          tradesFilter === "upstream" ? "from watched traders" :
          tradesFilter === "trades" ? "decisions on my account" :
          tradesFilter === "fills" ? "on-chain fills" : "log entries";
        return (
          <div className="pixel-panel border-2 border-pixel-border">
            <div className="px-3 py-1.5 border-b border-pixel-border flex items-center gap-2 flex-wrap">
              <span className="text-[14px] text-pixel-gray tracking-wider shrink-0">{headerLabel}</span>
              <span className="text-[12px] text-pixel-gray font-mono shrink-0">
                {sorted.length} {countLabel}
              </span>
              <div className="ml-auto flex items-center gap-1.5 shrink-0">
                <button
                  onClick={() => { setTradesFilter("upstream"); setTradesPage(0); }}
                  className={`pixel-btn text-[11px] px-2 py-0.5 ${
                    tradesFilter === "upstream"
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                  title="Raw real-time trades from the traders you watch"
                >
                  TRADER TRADES
                </button>
                <button
                  onClick={() => { setTradesFilter("trades"); setTradesPage(0); }}
                  className={`pixel-btn text-[11px] px-2 py-0.5 ${
                    tradesFilter === "trades"
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                  title="Orders the engine placed (or tried to) on your account — copies, skips, errors"
                >
                  MY TRADES
                </button>
                <button
                  onClick={() => { setTradesFilter("fills"); setTradesPage(0); }}
                  className={`pixel-btn text-[11px] px-2 py-0.5 ${
                    tradesFilter === "fills"
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                  title="Your ACTUAL on-chain fills from Polymarket (ground truth — survives restarts)"
                >
                  MY FILLS
                </button>
                <button
                  onClick={() => { setTradesFilter("all"); setTradesPage(0); }}
                  className={`pixel-btn text-[11px] px-2 py-0.5 ${
                    tradesFilter === "all"
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                  title="Everything: trades, decisions, cycle heartbeats, errors"
                >
                  ALL TRADES
                </button>
              </div>
            </div>
            <div className="max-h-[300px] overflow-y-auto">
              {/* UPSTREAM rows look like the BACKTEST trade feed — trader,
                  side, market, notional. MIRROR/ALL rows use the existing
                  engine-log shape (with icons + reason text). */}
              {isFills
                ? (pageEntries as PolymarketTrade[]).map((t) => (
                    <div
                      key={t.id}
                      className="px-3 py-1 border-b border-pixel-border/20 text-[13px] font-mono hover:bg-pixel-white/5"
                    >
                      <div className="flex items-start gap-2">
                        <span className="text-pixel-gray shrink-0 w-[52px]">{formatTime(t.timestamp)}</span>
                        <span className={`shrink-0 w-[36px] text-[11px] font-bold ${t.side === "BUY" ? "text-green-400" : "text-red-400"}`}>
                          {t.side}
                        </span>
                        <span className="text-pixel-white truncate flex-1 min-w-0" title={t.market}>
                          {t.market}
                          {t.outcome ? <span className="text-pixel-gray-light"> · {t.outcome}</span> : null}
                        </span>
                        <span className="text-pixel-gray-light shrink-0 text-right tabular-nums" title="fill price">
                          @{(t.price * 100).toFixed(0)}¢
                        </span>
                        <span className="text-pixel-gray-light shrink-0 w-[56px] text-right tabular-nums" title="shares">
                          {t.size.toFixed(0)} sh
                        </span>
                        <span className="text-pixel-white shrink-0 w-[60px] text-right tabular-nums" title="notional (price × shares)">
                          ${(t.price * t.size).toFixed(2)}
                        </span>
                      </div>
                    </div>
                  ))
                : isUpstream
                ? (pageEntries as ObservedTrade[]).map((t) => {
                    const outcome = outcomeById.get(t.id);
                    // Map outcome → visible badge + color + error reason.
                    // No outcome yet = pending (the cycle hasn't processed
                    // this trade — usually because it landed mid-cycle and
                    // will be picked up next).
                    let badge = "·";
                    let badgeCls = "text-pixel-gray";
                    let badgeTitle = "Awaiting next cycle";
                    let reasonText: string | null = null;
                    if (outcome) {
                      if (outcome.type === "COPY_BUY" || outcome.type === "COPY_SELL") {
                        badge = "✓";
                        badgeCls = "text-green-400";
                        badgeTitle = `Copied · order ${outcome.orderResult?.orderID ?? "(no id)"} · ${outcome.orderResult?.status ?? "matched"}`;
                      } else if (outcome.type === "SKIP") {
                        badge = "⊘";
                        badgeCls = "text-pixel-gray";
                        badgeTitle = "Skipped — see reason below";
                        reasonText = outcome.reason ?? "SKIPPED";
                      } else if (outcome.type === "ERROR") {
                        badge = "✗";
                        badgeCls = "text-red-400";
                        badgeTitle = "Failed — see reason below";
                        reasonText =
                          outcome.orderResult?.errorMsg ??
                          outcome.reason ??
                          "FAILED";
                      }
                    }
                    return (
                      <div
                        key={t.id}
                        className="px-3 py-1 border-b border-pixel-border/20 text-[13px] font-mono hover:bg-pixel-white/5"
                      >
                        <div className="flex items-start gap-2">
                          <span className="text-pixel-gray shrink-0 w-[52px]">{formatTime(t.timestamp)}</span>
                          <span
                            className={`shrink-0 w-[16px] text-[14px] font-bold ${badgeCls}`}
                            title={badgeTitle}
                          >
                            {badge}
                          </span>
                          <span className={`shrink-0 w-[36px] text-[11px] font-bold ${t.side === "BUY" ? "text-green-400" : "text-red-400"}`}>
                            {t.side}
                          </span>
                          <span className="text-pixel-gray-light shrink-0 w-[88px] truncate" title={t.trader}>
                            {t.trader.slice(0, 6)}…{t.trader.slice(-4)}
                          </span>
                          <span className="text-pixel-white truncate flex-1 min-w-0" title={t.market}>
                            {t.market}
                          </span>
                          <span className="text-pixel-gray-light shrink-0 text-right tabular-nums">
                            @{(t.price * 100).toFixed(0)}¢
                          </span>
                          <span className="text-pixel-white shrink-0 text-right tabular-nums">
                            ${t.notional < 1 ? t.notional.toFixed(2) : t.notional < 10_000 ? t.notional.toFixed(0) : `${(t.notional / 1000).toFixed(1)}k`}
                          </span>
                          {/* Expected profit (EP = trader ROI × the dollars
                              we'd mirror) the engine ranks this candidate by.
                              Color-coded so the user can spot high-EP trades at
                              a glance. SELLs read "—" (always honored); buys
                              with no loaded stats read "—" (pending). */}
                          <span
                            className={`shrink-0 w-[68px] text-right tabular-nums text-[12px] ${
                              t.side === "SELL"
                                ? "text-pixel-gray"
                                : t.score > 5
                                  ? "text-green-400"
                                  : t.score > 1
                                    ? "text-yellow-400"
                                    : t.score > 0
                                      ? "text-pixel-gray-light"
                                      : "text-red-400/70"
                            }`}
                            title={
                              t.side === "SELL"
                                ? "SELL — EP N/A (always honored to close positions)"
                                : t.score > 0
                                  ? `Expected profit $${t.score.toFixed(2)} = ROI × mirror notional`
                                  : t.sharpe === 0 && t.score === 0
                                    ? "ROI not loaded yet — pending stats"
                                    : `No positive expected profit (EP $${t.score.toFixed(2)})`
                            }
                          >
                            {t.side === "SELL" ? "—" : `$${t.score.toFixed(2)}`}
                          </span>
                        </div>
                        {reasonText && (
                          <div className="pl-[72px] pr-2 text-[11px] text-red-400/80 break-words leading-snug">
                            {reasonText}
                          </div>
                        )}
                      </div>
                    );
                  })
                : (pageEntries as ExecutionLogEntry[]).map((entry) => (
                    <div
                      key={entry.id}
                      className="px-3 py-1 border-b border-pixel-border/20 flex items-start gap-2 text-[14px] font-mono hover:bg-pixel-white/5"
                    >
                      <span className="text-pixel-gray shrink-0 w-[52px]">{formatTime(entry.timestamp)}</span>
                      <span className="shrink-0 w-[28px]"><LogIcon type={entry.type} /></span>
                      <div className="min-w-0 flex-1">
                        {entry.market && (
                          <span className="text-pixel-white truncate block">{entry.market}</span>
                        )}
                        {entry.mirrorNotional !== undefined && entry.mirrorNotional > 0 && (
                          <span className={entry.side === "BUY" ? "text-red-400" : "text-green-400"}>
                            {entry.side === "BUY" ? "-" : "+"}${entry.mirrorNotional.toFixed(2)}
                          </span>
                        )}
                        {entry.reason && (
                          <span className="text-pixel-gray"> {entry.reason}</span>
                        )}
                        {entry.orderResult && !entry.orderResult.success && entry.orderResult.errorMsg && (
                          <span className="text-red-400/70 block truncate">{entry.orderResult.errorMsg}</span>
                        )}
                      </div>
                    </div>
                  ))}
              {pageEntries.length === 0 && (
                <div className="px-3 py-3 text-center text-[12px] text-pixel-gray">
                  {isFills
                    ? fillsLoading
                      ? "Loading on-chain fills…"
                      : fillsError
                        ? `Couldn't load fills: ${fillsError}`
                        : "No on-chain fills yet."
                    : tradesFilter === "upstream"
                      ? "Waiting for the next sync cycle to observe trades…"
                      : `No ${countLabel} yet.`}
                </div>
              )}
            </div>
            {totalPages > 1 && (
              <div className="px-3 py-1.5 border-t border-pixel-border/40 flex items-center justify-between">
                <span className="text-[11px] text-pixel-gray font-mono">
                  {start + 1}-{Math.min(start + TRADES_PAGE_SIZE, sorted.length)} of {sorted.length}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setTradesPage((p) => Math.max(0, p - 1))}
                    disabled={safePage === 0}
                    className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed"
                  >
                    PREV
                  </button>
                  <span className="text-[11px] text-pixel-gray font-mono px-1">
                    {safePage + 1} / {totalPages}
                  </span>
                  <button
                    onClick={() => setTradesPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={safePage >= totalPages - 1}
                    className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed"
                  >
                    NEXT
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })()}

      {/* ── Empty state when live but no log ── */}
      {liveTab === "trades" && isLive && engineState && engineState.log.length === 0 && tradesFilter !== "fills" && (
        <div className="pixel-panel border-2 border-pixel-border px-3 py-4 text-center">
          <span className="text-[15px] text-pixel-gray">WAITING FOR FIRST CYCLE...</span>
        </div>
      )}

      </div>{/* /flex-1 content area */}
      </div>{/* /sidebar row */}
    </div>
  );
}
