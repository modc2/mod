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
import { getOwnerAddress } from "../lib/access";
import { DEFAULT_STOP_LOSS, DEFAULT_TAKE_PROFIT } from "../lib/strats/strat";
import PortfolioPanel from "./PortfolioPanel";
import PositionsHistoryPanel from "./PositionsHistoryPanel";

const ERC20_BAL_ABI = [
  "function balanceOf(address) view returns (uint256)",
];

// A run of consecutive MY-FILLS rows (same market/outcome/side/price) merged
// into one display row. `size`/`price*size` are the batched totals, `count`
// is how many fills were merged, `firstTs` is the oldest fill's time (the
// group's `timestamp` stays the newest).
type BatchedFill = PolymarketTrade & { count: number; firstTs: number };

// Live monitoring poll cadence — configurable per-strat via `livePollMinutes`.
// Defaults to 1 minute. The BACKTEST tab has its own `rebalanceMinutes` field
// for historical-simulation aggregation; the two are decoupled so a slow
// backtest cadence doesn't silently throttle real-time copy.
const LIVE_POLL_OPTIONS: { minutes: number; label: string }[] = [
  { minutes: 0.5, label: "30S" },
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
// fetch → no trades observed → no copies. 30s halves mirror lag vs. the
// old 1-minute floor while a 10-trader watchlist stays near ~0.3 req/s —
// well under the limit (the engine spaces per-trader fetches 400ms apart).
// Anything the strat configures below this floor gets clamped up; the Rust
// engine enforces the same 30s floor (default_min_interval_ms).
const DEFAULT_LIVE_POLL_MIN = 0.5;
const MIN_LIVE_POLL_MIN = 0.5;

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

// ── LIVE page section tabs ──
// The stacked LIVE page (portfolio chart + positions table + stats grid +
// trades feed + wallet help) grew past two screens tall; everything below
// the header/alert banners lives behind ONE tab strip instead. Alerts
// (checklist, CLOB, funding, engine error) stay OUTSIDE the tabs so nothing
// critical can hide, and the strip echoes free cash + next-cycle countdown
// so the key numbers stay visible from any tab.
export type LiveTab = "portfolio" | "stats" | "trades" | "help";
const LIVE_TABS: { id: LiveTab; label: string; title: string }[] = [
  { id: "portfolio", label: "PORTFOLIO", title: "Equity + performance curve over time" },
  { id: "stats", label: "STATS", title: "Engine stats — free cash, orders, volume, cycles, last sync/fetch" },
  // POSITIONS folded into TRADES — same on-chain record, just position- vs
  // fill-level. One tab, toggle between the two views (plus copied-trader feed
  // + engine log).
  { id: "trades", label: "TRADES", title: "My positions · my fills · copied-trader feed · engine log" },
  { id: "help", label: "HELP", title: "Which wallet do I use?" },
];
const LIVE_TAB_KEY = "polyLiveTab";

function LogIcon({ type }: { type: ExecutionLogEntry["type"] }) {
  switch (type) {
    case "COPY_BUY": return <span className="text-red-400">BUY</span>;
    case "COPY_SELL": return <span className="text-green-400">SELL</span>;
    case "SKIP": return <span className="text-pixel-gray">SKIP</span>;
    case "ERROR": return <span className="text-red-400">ERR</span>;
    case "BALANCE": return <span className="text-amber-400">BAL</span>;
    case "CYCLE_START": return <span className="text-pixel-gray">---</span>;
    case "CYCLE_END": return <span className="text-green-400">END</span>;
    case "REDEEM": return <span className="text-amber-400">RDM</span>;
    case "WATCHLIST": return <span className="text-amber-400">WLST</span>;
    default: return <span className="text-pixel-gray">???</span>;
  }
}

export default function LivePanel({ onFundNow, tab, onTabChange }: {
  onFundNow?: () => void;
  // Controlled mode — when `tab` is passed the section tabs live in the
  // parent's subtab rail (CopyIndex header) instead of an in-body strip,
  // so LIVE reads as ONE nav: main tabs → subtabs → content.
  tab?: LiveTab;
  onTabChange?: (t: LiveTab) => void;
} = {}) {
  const { auth, authenticate, loading: authLoading } = useAuth();
  const { engineState, isLive, startLive, stopLive, pauseLive, resumeLive, backendRunning, autoExecute, setAutoExecute, catchUp } = useCopyEngine();
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
  // Four-way view toggle inside the TRADES tab. POSITIONS = my holdings
  // (open + closed) with per-position P&L — folded in from the old standalone
  // POSITIONS tab since it's the same on-chain record at a coarser grain.
  // MY TRADES = my actual on-chain fills (ground truth). COPIED TRADERS = the
  // raw feed of what the watched traders are doing (with the ✓/⊘/✗ mirror
  // outcome inline). LOG stays as a small pill for debugging only. POSITIONS
  // leads and is the default — it's the "how am I doing" glance.
  const [tradesFilter, setTradesFilter] = useState<"positions" | "upstream" | "all" | "fills">("positions");
  const TRADES_PAGE_SIZE = 25;

  // Active section tab — persisted so the console reopens on the view the
  // user actually works from. Inactive tabs are NOT mounted: each panel runs
  // its own poller, so CSS-hiding would double data-api traffic for nothing.
  const [internalTab, setInternalTab] = useState<LiveTab>(() => {
    if (typeof window === "undefined") return "portfolio";
    const saved = window.localStorage.getItem(LIVE_TAB_KEY);
    return LIVE_TABS.some((t) => t.id === saved) ? (saved as LiveTab) : "portfolio";
  });
  const liveTab = tab ?? internalTab;
  const pickTab = useCallback((t: LiveTab) => {
    if (onTabChange) { onTabChange(t); return; } // controlled — parent persists
    setInternalTab(t);
    // Shared-origin localStorage can be at quota — never let a tab click throw.
    try { window.localStorage.setItem(LIVE_TAB_KEY, t); } catch {}
  }, [onTabChange]);


  // ── Actual on-chain fills (FILLS filter) ──────────────────────
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
    // Owner-only console: fills belong to the signed-in owner's funded wallet.
    const eoa = getOwnerAddress() ?? auth.address;
    if (!eoa) return;
    setFillsLoading(true);
    setFillsError(null);
    try {
      // Resolve the deposit wallet (where V2 trades actually land), then
      // paginate its activity history (cutoff 0 = as far back as the
      // global MAX_LOOKBACK_DAYS ceiling allows).
      const info = await fetch(
        `/api/polymarket/deposit-wallet/info?eoa=${eoa}`,
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

  // Fetch on-chain fills while the FILLS filter is selected; refresh every
  // 60s. Skipped on the other filters (they read engine state and need no
  // data-api call).
  useEffect(() => {
    if (tradesFilter !== "fills" || liveTab !== "trades") return;
    void loadFills();
    const t = setInterval(() => void loadFills(), 60_000);
    return () => clearInterval(t);
  }, [tradesFilter, liveTab, loadFills]);

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

  // Wrap setLiveCapital so the CAPITAL CAP picker (in the sidebar's
  // WalletFundingPanel) flips the override ref — any manual pick disables
  // proxy auto-sync.
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
  // then the 30s default. Strats still carrying the old 1-minute legacy
  // default (`=== 1`) are auto-upgraded to 30s so nobody stays stuck behind
  // the stale cadence (CopyIndex's POLL EVERY select applies the same `1 ⇒
  // legacy` remap). Anything else (explicit 5m, 30m, …) is honored as-is.
  const rawLivePollMin =
    activeStrat?.rebalanceMinutes ??
    activeStrat?.livePollMinutes ??
    DEFAULT_LIVE_POLL_MIN;
  const upgradedLivePollMin = rawLivePollMin === 1 ? DEFAULT_LIVE_POLL_MIN : rawLivePollMin;
  // Clamp any sub-30s cadence (incl. the old 5s default) up to the floor so
  // a stale strat config can't keep hammering the data-api into rate limits.
  const livePollMin = Math.max(MIN_LIVE_POLL_MIN, upgradedLivePollMin);

  // Preconditions
  const hasWallet = auth.connected && !!auth.address;
  const hasCreds = auth.authenticated && !!auth.clobCreds;
  // Momentum strats originate from market prices and run fine with an
  // empty watchlist — the TRADERS precondition only applies to copy strats.
  const originates = !!activeStrat?.momentum;
  const hasTraders = (activeStrat?.traders.filter((t) => t.enabled !== false).length ?? 0) > 0
    || originates;
  // Always true now — LIVE is hard-pinned to 1-minute polling. The strat's
  // rebalanceMinutes only affects BACKTEST cadence and isn't a live precondition.
  const hasRebalance = true;
  const hasCapital = liveCapital > 0;
  // Funds are NOT a start precondition. An unfunded wallet starts in
  // dry-run so any strat can still be tested against live flow — mirrors
  // are sized with paper capital (the strat's BACKTEST capital, $1K
  // default) until real USDC shows up. A FUNDED wallet starts EXECUTING:
  // GO LIVE means real orders, no separate toggle hunt (the pill still
  // flips a running session back to dry-run).
  const paperCapital = (activeStrat?.capital ?? 0) > 0 ? activeStrat!.capital! : 1000;
  const effectiveCapital = hasCapital ? liveCapital : paperCapital;
  const canStart = hasWallet && hasCreds && hasTraders && hasRebalance;

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
      capital: effectiveCapital,
      intervalMs: livePollMin * 60_000,
      creds: auth.clobCreds,
      address: auth.address,
      // Honor the strat's TRADE SIZE floor — was hardcoded to $1 before,
      // causing every dust mirror to skip with BELOW_MIN_SIZE even when the
      // user had set MIN TRADE to 0.1 in BACKTEST. Falls back to $5.
      minOrderSize: activeStrat.minTrade ?? 5,
      // Concurrent open-positions cap — the engine skips a BUY that would
      // open a NEW token while this many are already held.
      maxOpenPositions: activeStrat.maxOpenPositions ?? 10,
      // Per-cycle BUY budget — the same top-N knob the backtest's rank gate
      // applies (`maxPerCycle`), so a live cycle places at most what the sim
      // keeps. Exits (leader-sell mirrors, stop-losses) don't count against it.
      maxPerCycle: activeStrat.maxPerCycle ?? 3,
      // Per-position stop-loss — the engine sells a hold once its book bid
      // decays to ≤ this fraction of entry. Defaults to 0.75 (defend three
      // quarters of entry) so no strat rides a market to 0 unless the user
      // explicitly set STOP to 0 (sent as 0 → engine treats as off).
      stopLoss: activeStrat.stopLoss ?? DEFAULT_STOP_LOSS,
      // Per-position take-profit — the engine liquidates a hold once its bid
      // runs to this level. Defaults to 0.99 (the top tick): a market at
      // 100% is decided, sell it instead of parking capital until
      // resolution + auto-redeem.
      takeProfit: activeStrat.takeProfit ?? DEFAULT_TAKE_PROFIT,
      // Ceiling for the proportional sizing. Without this, a single whale
      // trade from a high-volume trader could blow the proportional mirror
      // past the user's TRADE SIZE max and chew through capital in one shot.
      maxOrderSize: activeStrat.maxTrade,
      // Same lookback the BACKTEST tab uses to compute the per-trader
      // volume denominator — keeps live copyRatio == backtest scale, so
      // the preview predicts execution.
      backtestDays: activeStrat.backtestDays ?? 3,
      maxSlippageBps: 300,
      // Strat market-topic filter — only mirror trades in matching markets.
      marketQuery: activeStrat.marketQuery,
      // Semantic per-trade filters (side / price band / size band / category).
      tradeFilters: activeStrat.tradeFilters,
      // Price-momentum origination — buys rising outcomes, no watchlist needed.
      momentum: activeStrat.momentum,
      // Funded wallet → GO LIVE trades for real. Unfunded → dry-run paper
      // session. Without this the server default (false) made every fresh
      // session a silent dry-run until the DRY RUN pill was clicked.
      autoExecute: hasCapital,
    });

    updateIndex(activeStrat.id, {
      liveEnabled: true,
      // Never persist a $0 balance into the strat — strat.capital doubles as
      // the BACKTEST tab's simulated capital, and 0 would zero out every
      // simulated trade there.
      capital: effectiveCapital,
      // Persist both for backwards compat — `rebalanceMinutes` is the
      // canonical field the STRAT panel writes; we mirror it into
      // `livePollMinutes` so older code paths keep working.
      rebalanceMinutes: livePollMin,
      livePollMinutes: livePollMin,
      updatedAt: Date.now(),
    });
  }, [isLive, auth, activeStrat, effectiveCapital, hasCapital, livePollMin, startLive, stopLive]);

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
      capital: effectiveCapital,
      intervalMs,
      creds: auth.clobCreds,
      address: auth.address,
      minOrderSize: activeStrat.minTrade ?? 5,
      maxOpenPositions: activeStrat.maxOpenPositions ?? 10,
      stopLoss: activeStrat.stopLoss ?? DEFAULT_STOP_LOSS,
      takeProfit: activeStrat.takeProfit ?? DEFAULT_TAKE_PROFIT,
      maxOrderSize: activeStrat.maxTrade,
      backtestDays: activeStrat.backtestDays ?? 3,
      maxSlippageBps: 300,
      marketQuery: activeStrat.marketQuery,
      tradeFilters: activeStrat.tradeFilters,
      momentum: activeStrat.momentum,
    });
  }, [livePollMin, isLive, status, activeStrat, auth.clobCreds, auth.address, effectiveCapital, startLive, stopLive]);

  return (
    <div className="space-y-1">
      {/* Trading wallet (deposit/withdraw) lives in the STRAT page's WALLET
          tab — not duplicated here. */}

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
            {/* SCAN dropdown removed — the STRAT panel's POLL EVERY field
                is the single source of truth for poll cadence now. The
                engine hot-restarts when that field changes (see the
                lastAppliedIntervalRef effect above). Display-only echo
                so the user can see at-a-glance what the engine is using. */}
            <span className="text-[11px] text-pixel-gray tracking-wider" title="Poll cadence — change in PARAMS panel POLL EVERY">
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
            {(isLive || backendRunning) && (
              <button
                onClick={() => { void setAutoExecute(!autoExecute); }}
                title={
                  autoExecute
                    ? "LIVE EXECUTION — the engine is placing REAL orders with wallet funds. Click to fall back to dry-run (mirrors logged, nothing sent)."
                    : "DRY RUN — mirrors are logged but NO orders are sent. Click to enable real order placement with wallet funds."
                }
                className={`pixel-btn text-[13px] px-1.5 py-0.5 transition-colors ${
                  autoExecute
                    ? "border-red-400 text-red-400 hover:bg-red-400/10"
                    : "border-amber-400/60 text-amber-400 hover:bg-amber-400/10"
                }`}
              >
                {autoExecute ? "EXECUTING ●" : "DRY RUN"}
              </button>
            )}
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
                  : canStart
                    ? (hasCapital
                        ? "Start the live copy engine — places real orders"
                        : `Start in paper mode — wallet is unfunded, mirrors are sized against $${paperCapital} simulated capital and no real orders fill`)
                    : "Complete the checklist above to enable"
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

      {/* ── Layout ──
          Alerts first (checklist, CLOB, funding, engine error — always
          visible so nothing critical hides), then ONE tab strip for the
          bulky sections: PORTFOLIO / POSITIONS / STATS / TRADES / HELP.
          The always-visible stack was tried before this and grew past two
          screens; the earlier tab regression ("where are my trades?") is
          answered by keeping alerts outside the tabs and echoing free cash
          + next-cycle countdown on the strip itself. */}
      <div className="space-y-3">

      {/* ── Preconditions ──
          Shown whenever the engine is stopped so the user knows what's
          blocking GO LIVE. Compact pill row: each step is green-filled when
          satisfied, muted when not. The summary count tells you "5/6 ready"
          at a glance. */}
      {!isLive && (() => {
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
          // The strat's market keywords — never a blocker, but part of what
          // "going live" means: with keywords set the engine only mirrors
          // matching markets, so the checklist says so out loud.
          ...(activeStrat?.marketQuery?.trim()
            ? [{ ok: true, label: `⌕ ${activeStrat.marketQuery.trim()}`, action: null }]
            : []),
          { ok: hasRebalance, label: "REBALANCE", action: null },
          // Capital never blocks — with $0 on-chain the engine starts in
          // paper sizing (strat's backtest capital) and dry-run does the
          // rest. The pill just tells the user which mode they're in.
          { ok: true, label: hasCapital ? "CAPITAL" : "CAPITAL·PAPER", action: null },
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
                    <span className="max-w-[240px] truncate" title={item.label}>{item.label}</span>
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
            onClick={() => {
              // Deposit/withdraw is the STRAT page's WALLET tab now — switch
              // tabs via the parent; fall back to scrolling if unhosted.
              if (onFundNow) onFundNow();
              else document.getElementById("sidebar-wallet-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
            className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white font-bold text-sm rounded"
          >
            FUND NOW →
          </button>
        </div>
      )}

      {/* ── Engine error ──
          Hoisted OUT of the stats grid so a live failure is visible from any
          tab — the whole point of keeping alerts outside the tab strip. */}
      {isLive && engineState?.error && (
        <div className="pixel-panel border-2 border-red-400/40 bg-red-400/5 px-3 py-1.5 text-[14px] text-red-400 font-mono">
          {engineState.error}
        </div>
      )}

      {/* ── Section tabs ──
          One strip for everything below the alerts. The right side echoes
          free cash + next-cycle countdown while live, so hiding the stats
          grid behind a tab never blinds the user to the two numbers that
          matter minute-to-minute. In controlled mode (tab prop set) the
          buttons live in the parent's subtab rail — skip the whole strip;
          the parent echoes the numbers instead. */}
      {tab === undefined && (
      <div className="pixel-panel border-2 border-pixel-border px-2 py-1.5 flex items-center gap-2 flex-wrap">
        <div className="flex items-center rounded-md border border-pixel-border/70 bg-pixel-black/40 p-0.5">
          {LIVE_TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => pickTab(t.id)}
              className={`px-2.5 py-0.5 text-[11px] font-mono tracking-[0.14em] rounded transition-colors ${
                liveTab === t.id
                  ? "bg-green-400/15 text-green-400 border border-green-400/50"
                  : "border border-transparent text-pixel-gray hover:text-pixel-white"
              }`}
              title={t.title}
            >
              {t.label}
            </button>
          ))}
        </div>
        {isLive && engineState && (
          <span className="ml-auto text-[12px] font-mono text-pixel-gray shrink-0" title="Free cash · time to next poll cycle — full breakdown in STATS">
            <span className="text-pixel-white">
              {engineState.balance !== null ? `$${engineState.balance.toFixed(2)}` : "$—"}
            </span>
            {" · "}
            <span className="text-green-400">{formatCountdown(nextIn)}</span>
          </span>
        )}
      </div>
      )}

      {/* ── PORTFOLIO tab — equity + performance curve ── */}
      {liveTab === "portfolio" && auth.connected && <PortfolioPanel strategyId={activeStrat?.id} />}

      {/* BackendSignerPanel removed — the V1 Safe-co-owner flow that
          panel managed isn't needed for V2 trading. The V2 deposit
          wallet is CREATE2-derived from the per-user backend signer's
          own EOA, so the backend is *inherently* the wallet's
          authorized signer — no separate "TURN ON AUTO-TRADING" step
          to flip. Auto-trading is on by construction. */}

      {/* ── STATS tab (when live) ──
          Card grid; LAST SYNC tracks the most recent successful Polymarket
          data-api pull, LAST FETCH surfaces the CYCLE_END summary. */}
      {liveTab === "stats" && isLive && engineState && (
        <div className="pixel-panel border-2 border-pixel-border p-2">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5">
            <StatCard
              label="FREE CASH"
              value={engineState.balance !== null ? `$${engineState.balance.toFixed(2)}` : "—"}
              tone="white"
              title="Uninvested USDC the engine can spend. Equity (cash + open-position value) and per-position P&L live in the PORTFOLIO panel above."
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

      {/* ── Trades (MY TRADES ⇄ COPIED TRADERS toggle + paginated) ──
          ONE trades panel with a plain two-sided toggle: MY TRADES (your
          executed on-chain fills) vs COPIED TRADERS (what the traders you
          copy are doing, mirror outcome inline). LOG keeps the raw engine
          feed for debugging. Pagination keeps the panel a fixed height
          instead of growing across the page. */}
      {liveTab === "trades" && auth.connected && (() => {
        const isPositions = tradesFilter === "positions";
        const isUpstream = tradesFilter === "upstream";
        const isFills = tradesFilter === "fills";
        // POSITIONS + MY TRADES are on-chain ground truth (viewable even when
        // the engine is stopped); COPIED TRADERS + LOG are engine-only feeds.
        const engineReady = isLive && !!engineState;

        // View toggle — shared header across every view. POSITIONS leads (the
        // "how am I doing" glance), then my fills, then the engine feeds.
        const FILTERS: { id: typeof tradesFilter; label: string; title: string }[] = [
          { id: "positions", label: "POSITIONS", title: "My holdings — open AND closed, with per-position P&L (open/closed sub-filter inside)" },
          { id: "fills", label: "MY TRADES", title: "Your ACTUAL executed trades (on-chain fills from Polymarket — ground truth, survives restarts)" },
          { id: "upstream", label: "COPIED TRADERS", title: "Live feed of what the traders you copy are doing — each row shows whether the engine copied (✓), skipped (⊘) or failed (✗) it" },
          { id: "all", label: "LOG", title: "Raw engine log: every decision, cycle heartbeats, errors (debugging)" },
        ];
        const ToggleHeader = ({ right }: { right?: ReactNode }) => (
          <div className="px-3 py-1.5 border-b border-pixel-border flex items-center gap-2 flex-wrap">
            <span className="text-[14px] text-pixel-white tracking-wider shrink-0">TRADES</span>
            <div className="flex items-center rounded-md border border-pixel-border/70 bg-pixel-black/40 p-0.5 shrink-0">
              {FILTERS.map((f) => (
                <button
                  key={f.id}
                  onClick={() => { setTradesFilter(f.id); setTradesPage(0); }}
                  className={`px-2.5 py-0.5 text-[11px] font-mono tracking-[0.14em] rounded transition-colors ${
                    tradesFilter === f.id
                      ? "bg-green-400/15 text-green-400 border border-green-400/50"
                      : "border border-transparent text-pixel-gray hover:text-pixel-white"
                  }`}
                  title={f.title}
                >
                  {f.label}
                </button>
              ))}
            </div>
            {right && <span className="ml-auto shrink-0">{right}</span>}
          </div>
        );

        // POSITIONS view — the folded-in positions history (own header/filters).
        if (isPositions) {
          return (
            <div className="pixel-panel border-2 border-pixel-border">
              <ToggleHeader />
              <PositionsHistoryPanel />
            </div>
          );
        }

        // Engine-only feeds with no running engine — say so instead of blank.
        if ((isUpstream || tradesFilter === "all") && !engineReady) {
          return (
            <div className="pixel-panel border-2 border-pixel-border">
              <ToggleHeader />
              <div className="px-3 py-4 text-center text-[13px] text-pixel-gray tracking-wider">
                ENGINE STOPPED — GO LIVE to see the {isUpstream ? "copied-trader feed" : "engine log"}
              </div>
            </div>
          );
        }

        // MY TRADES pulls ground-truth on-chain fills; COPIED TRADERS pulls
        // from the engine's observed-trades ring buffer (what the watched
        // traders are doing, with the mirror outcome joined in below); LOG
        // is the raw engine log incl. cycle heartbeats.
        const items = isFills
          ? fills
          : isUpstream
          ? engineState!.observedTrades
          : engineState!.log;

        // Index every trade-tagged log entry by its upstreamTradeId so each
        // UPSTREAM row can render its mirror outcome inline (✓ copied, ⊘
        // skipped, ✗ failed) with the exact reason. Previously the user
        // could see a trader's trade in the upstream feed but had to dig
        // through the MIRROR / ALL tabs to find out why it didn't fire —
        // and even then the join was implicit (trader + market + timestamp).
        const outcomeById = new Map<string, ExecutionLogEntry>();
        for (const entry of engineState?.log ?? []) {
          if (entry.upstreamTradeId) {
            // Keep the LATEST entry per upstream id (engine may emit both
            // a SKIP and a later retry; we want the final state).
            const prev = outcomeById.get(entry.upstreamTradeId);
            if (!prev || entry.timestamp > prev.timestamp) {
              outcomeById.set(entry.upstreamTradeId, entry);
            }
          }
        }
        // Join MY FILLS back to the EP score the engine ranked the copy by.
        // Raw on-chain fills don't carry a score, so we index every observed
        // BUY by market (conditionId) WITH its timestamp, then match each fill
        // to the observed trade closest in time for that market — so a fill
        // gets ITS score even when the same market was traded several times at
        // different scores. "—" when no observed trade for that market remains
        // in the live buffer.
        const scoresByMarket = new Map<string, { ts: number; score: number }[]>();
        for (const ot of engineState?.observedTrades ?? []) {
          if (ot.side !== "BUY") continue;
          const k = ot.conditionId?.toLowerCase();
          if (!k) continue;
          const arr = scoresByMarket.get(k);
          if (arr) arr.push({ ts: ot.timestamp, score: ot.score });
          else scoresByMarket.set(k, [{ ts: ot.timestamp, score: ot.score }]);
        }
        const scoreForFill = (conditionId: string, ts: number): number | undefined => {
          const arr = scoresByMarket.get((conditionId || "").toLowerCase());
          if (!arr || arr.length === 0) return undefined;
          let best = arr[0];
          let bestDelta = Math.abs(arr[0].ts - ts);
          for (const e of arr) {
            const d = Math.abs(e.ts - ts);
            if (d < bestDelta) { best = e; bestDelta = d; }
          }
          return best.score;
        };

        const sorted = [...items].sort((a, b) => b.timestamp - a.timestamp);

        // Batch MY FILLS: a single copy often fills in many tiny pieces (and
        // the same mirror can be placed repeatedly), flooding the list with
        // identical rows. Collapse consecutive fills that share market /
        // outcome / side / price into one ×N row.
        const displayItems = isFills
          ? (() => {
              const groups: BatchedFill[] = [];
              for (const r of sorted as PolymarketTrade[]) {
                const prev = groups[groups.length - 1];
                if (
                  prev &&
                  prev.side === r.side &&
                  prev.price === r.price &&
                  (prev.conditionId || prev.market) === (r.conditionId || r.market) &&
                  prev.outcome === r.outcome
                ) {
                  prev.size += r.size;        // sorted desc → keep newest ts on the group
                  prev.count += 1;
                  prev.firstTs = r.timestamp; // r is older; track span start
                } else {
                  groups.push({ ...r, count: 1, firstTs: r.timestamp });
                }
              }
              return groups;
            })()
          : sorted;

        const totalPages = Math.max(1, Math.ceil(displayItems.length / TRADES_PAGE_SIZE));
        const safePage = Math.min(tradesPage, totalPages - 1);
        const start = safePage * TRADES_PAGE_SIZE;
        const pageEntries = displayItems.slice(start, start + TRADES_PAGE_SIZE);
        const countLabel =
          tradesFilter === "upstream" ? "from copied traders" :
          tradesFilter === "fills" ? "my trades" : "log entries";
        return (
          <div className="pixel-panel border-2 border-pixel-border">
            <ToggleHeader
              right={
                <span className="text-[12px] text-pixel-gray font-mono">
                  {isFills && displayItems.length !== sorted.length
                    ? `${displayItems.length} trades · ${sorted.length} fills batched`
                    : `${sorted.length} ${countLabel}`}
                </span>
              }
            />
            <div className="max-h-[300px] overflow-y-auto">
              {/* UPSTREAM rows look like the BACKTEST trade feed — trader,
                  side, market, notional. MIRROR/ALL rows use the existing
                  engine-log shape (with icons + reason text). */}
              {isFills
                ? (pageEntries as BatchedFill[]).map((t) => (
                    <div
                      key={t.id}
                      className="px-3 py-1 border-b border-pixel-border/20 text-[13px] font-mono hover:bg-pixel-white/5"
                    >
                      <div className="flex items-start gap-2">
                        <span
                          className="text-pixel-gray shrink-0 w-[68px] tabular-nums"
                          title={t.count > 1 ? `${t.count} fills batched · ${formatTime(t.firstTs)}–${formatTime(t.timestamp)}` : `traded at ${formatTime(t.timestamp)} UTC`}
                        >
                          {formatTime(t.timestamp)}
                        </span>
                        <span className={`shrink-0 w-[36px] text-[11px] font-bold ${t.side === "BUY" ? "text-green-400" : "text-red-400"}`}>
                          {t.side}
                        </span>
                        <span className="text-pixel-white truncate flex-1 min-w-0" title={t.market}>
                          {t.market}
                          {t.outcome ? <span className="text-pixel-gray-light"> · {t.outcome}</span> : null}
                          {t.count > 1 && (
                            <span
                              className="ml-1.5 text-[10px] text-amber-400 font-bold"
                              title={`${t.count} fills batched into one row`}
                            >
                              ×{t.count}
                            </span>
                          )}
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
                        {/* EP score the engine ranked this copy by, joined
                            from the observed-trades buffer by market. SELLs and
                            aged-out trades read "—". */}
                        {(() => {
                          const score = t.side === "SELL"
                            ? undefined
                            : scoreForFill(t.conditionId, t.timestamp);
                          return (
                            <span
                              className={`shrink-0 w-[60px] text-right tabular-nums text-[12px] ${
                                score === undefined
                                  ? "text-pixel-gray"
                                  : score > 5
                                    ? "text-green-400"
                                    : score > 1
                                      ? "text-yellow-400"
                                      : score > 0
                                        ? "text-pixel-gray-light"
                                        : "text-red-400/70"
                              }`}
                              title={
                                t.side === "SELL"
                                  ? "SELL — EP N/A (closes a position)"
                                  : score === undefined
                                    ? "Score N/A — originating trade aged out of the live buffer"
                                    : `Expected profit $${score.toFixed(2)} = trader ROI × mirror notional`
                              }
                            >
                              {score === undefined ? "—" : `$${score.toFixed(2)}`}
                            </span>
                          );
                        })()}
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
                          {/* P(success) the playbook priced this candidate at —
                              the trader's smoothed 30d win rate. SELLs read "—"
                              (always honored, not ranked). */}
                          <span
                            className={`shrink-0 w-[44px] text-right tabular-nums text-[12px] ${
                              t.side === "SELL" || t.successProb === undefined
                                ? "text-pixel-gray"
                                : t.successProb >= 0.6
                                  ? "text-green-400"
                                  : t.successProb >= 0.5
                                    ? "text-pixel-gray-light"
                                    : "text-red-400/70"
                            }`}
                            title={
                              t.side === "SELL"
                                ? "SELL — always honored to close positions, not ranked"
                                : t.successProb === undefined
                                  ? "P(success) pending — engine hasn't stamped this trade yet"
                                  : `P(success) ${(t.successProb * 100).toFixed(0)}% — trader's Laplace-smoothed 30d win rate`
                            }
                          >
                            {t.side === "SELL" || t.successProb === undefined
                              ? "—"
                              : `${Math.round(t.successProb * 100)}%`}
                          </span>
                          {/* Rank score (P × ROI × mirror$) the engine sorts
                              candidates by. Color-coded so the user can spot
                              high-edge trades at a glance. SELLs read "—"
                              (always honored); buys with no loaded stats read
                              "—" (pending). */}
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
                                ? "SELL — score N/A (always honored to close positions)"
                                : t.score > 0
                                  ? `Rank score $${t.score.toFixed(2)} = P(success) × ROI × mirror notional`
                                  : t.sharpe === 0 && t.score === 0
                                    ? "ROI not loaded yet — pending stats"
                                    : `No positive expected edge (score $${t.score.toFixed(2)}) — live engine skips these (NO_EDGE), same as the backtest`
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
                        {/* EP score the engine ranked this copy by. */}
                        {entry.score !== undefined && entry.side !== "SELL" && (
                          <span
                            className={`ml-2 ${
                              entry.score > 5
                                ? "text-green-400"
                                : entry.score > 1
                                  ? "text-yellow-400"
                                  : entry.score > 0
                                    ? "text-pixel-gray-light"
                                    : "text-red-400/70"
                            }`}
                            title="Expected profit the engine ranked this copy by (trader ROI × mirror notional)"
                          >
                            EP ${entry.score.toFixed(2)}
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
                  {start + 1}-{Math.min(start + TRADES_PAGE_SIZE, displayItems.length)} of {displayItems.length}
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

      {/* STATS only exists while the engine runs — say so instead of an
          unexplained blank tab. (TRADES renders POSITIONS/MY TRADES even when
          stopped, and shows its own per-view "engine stopped" note for the
          engine-only feeds.) */}
      {liveTab === "stats" && !(isLive && engineState) && (
        <div className="pixel-panel border-2 border-pixel-border px-3 py-4 text-center">
          <span className="text-[13px] text-pixel-gray tracking-wider">
            ENGINE STOPPED — GO LIVE to see engine stats
          </span>
        </div>
      )}

      {/* ══ CONFIG ══
          Custom strats and wallet admin moved BELOW the plots + trades so
          the chart and execution feed lead the view. */}

      {/* ── Active strat params ──
          Removed: the STRAT params panel here was a read-only mirror of
          the STRAT → PARAMS subtab — pure duplication. That subtab is the
          single place to view + edit strat config. */}

      {/* ── Custom strats / Strategy Hub moved to the STRATS tab top bar ──
          (publish · fork · fund). See CopyIndex STRATS-mode hub bar. */}

      {/* ── HELP tab — which-wallet explainer ──
          Was an always-on collapsible banner at the bottom of the stack;
          now it only renders on its own tab, so the collapse state matters
          less but still persists. */}
      {liveTab === "help" && auth.connected && (() => {
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
                into TRADING WALLET in the sidebar. The panel auto-hides once empty.
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

      {/* Legacy V1 Safe migration panel also lives in the sidebar now
          (auto-hides itself when balance is 0). */}

      </div>{/* /content column */}
    </div>
  );
}
