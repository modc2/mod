"use client";

import { useState, useCallback, useMemo, useEffect, useRef, type ReactNode } from "react";
import { useAuth } from "../context/AuthContext";
import { useCopyEngine } from "../context/CopyEngineContext";
import { loadIndexes, getActiveIndexId, updateIndex } from "../lib/indexStore";
import type { SavedIndex, PolymarketTrade } from "../lib/types";
import type { ExecutionLogEntry, ObservedTrade } from "../lib/copyEngine";
import { fetchWalletTradesUntil } from "../lib/polymarket";
import { getOwnerAddress } from "../lib/access";
import { startLiveSession } from "../lib/liveSessions";
import {
  MODE, armedDefault, autoExecuteFor, confirmGoLive, modeOf, type TradingMode,
} from "../lib/tradingMode";
import { ModeSwitch, ModeLegend, NotTradingBanner } from "./ModeControl";
import { DEFAULT_STOP_LOSS, DEFAULT_TAKE_PROFIT, MIN_POLL_MINUTES, DEFAULT_MIN_MINUTES_TO_CLOSE } from "../lib/strats/strat";
import PortfolioPanel from "./PortfolioPanel";
import PositionsHistoryPanel from "./PositionsHistoryPanel";

// A run of consecutive MY-FILLS rows (same market/outcome/side/price) merged
// into one display row. `size`/`price*size` are the batched totals, `count`
// is how many fills were merged, `firstTs` is the oldest fill's time (the
// group's `timestamp` stays the newest).
type BatchedFill = PolymarketTrade & { count: number; firstTs: number };

// Live sync cadence — how often the engine re-polls every watched trader's
// activity. Owned by the strat (`rebalanceMinutes`, mirrored to the legacy
// `livePollMinutes`) and editable from the SYNC panel below, which shows the
// cost of each choice against the currently-selected traders. Nothing below
// the 30s floor is offered: both the frontend and the Rust engine clamp there,
// so a "5s" option would just be a lie on screen.
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
// Per-trader spacing the engine uses inside a cycle, plus the wall-clock it
// budgets per data-api fetch. Mirrors `default_inter_request_delay_ms` and
// `TRADER_FETCH_ALLOWANCE_MS` in api/src/live_engine.rs — the SYNC panel
// re-derives the engine's fan-out floor from them so the picker can warn
// BEFORE the engine silently widens the period.
const INTER_REQUEST_DELAY_MS = 400;
const TRADER_FETCH_ALLOWANCE_MS = 600;

// Plain-language reading of each gate the engine can block an entry with,
// plus what to change to unblock it. Keys are the reason strings
// `trade_filter_reject` and the copy loop's skips emit — keep them in sync.
// Every gate the engine can report, with the label, the explanation, and the
// wording of the button that turns it OFF. `off` is non-optional on purpose:
// each gate must be clearable from the warning that names it. A console that
// can say "this filter blocked all 511 of your mirrors" but offers no way to
// act on it just relocates the dead end.
const GATE_LABELS: Record<string, { name: string; fix: string; off: string }> = {
  price: {
    name: "price band",
    fix: "these entries fall outside the MIN/MAX PRICE your strat set — clear it to copy the flow whole.",
    off: "CLEAR PRICE BAND",
  },
  size: {
    name: "size band",
    fix: "the leader's stake falls outside your MIN/MAX NOTIONAL.",
    off: "CLEAR SIZE BAND",
  },
  side: {
    name: "side filter",
    fix: "your strat only mirrors one side of the book.",
    off: "COPY BOTH SIDES",
  },
  category: {
    name: "category filter",
    fix: "the market isn't in any category you selected.",
    off: "ALL CATEGORIES",
  },
  "market query": {
    name: "keyword filter",
    fix: "the market title doesn't match your KEYWORDS — widen or clear them.",
    off: "CLEAR KEYWORDS",
  },
  "resolves too soon": {
    name: "time-to-close gate",
    fix: "the market resolves inside your MIN TIME TO CLOSE window; short-dated Up/Down candles are HFT turf and cost this console $253 the last time they were copied.",
    off: "GATE OFF",
  },
  stale: {
    name: "trade age gate",
    fix: "this strat sets MAX TRADE AGE and the leader traded longer ago than that. The gate is off by default — clear maxTradeAgeSec on the strat to copy the flow whole.",
    off: "GATE OFF",
  },
};

// The cadence the engine will actually run at for `traderCount` traders —
// same rule as `effective_interval_for` in the Rust engine. A watchlist whose
// fan-out can't finish inside the requested period widens it.
function effectiveIntervalMs(requestedMs: number, traderCount: number): number {
  const fanoutMs = traderCount * (INTER_REQUEST_DELAY_MS + TRADER_FETCH_ALLOWANCE_MS);
  return Math.max(requestedMs, MIN_POLL_MINUTES * 60_000, fanoutMs);
}

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

// Terse duration, no "ago" suffix — for the copy-lag chip on my own fills
// ("how long after the leader did I get in"), where the row already reads as
// a delta. Sub-second lags round to 0s rather than "just now".
function formatAgoShort(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  const sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  return `${Math.floor(min / 60)}h${min % 60}m`;
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
// PORTFOLIO / STATS / TRADES were three tabs over ONE story — "is the engine
// alive, what is it doing, and did any of it reach my wallet". Splitting that
// across tabs meant the answer was never on screen at once: you'd read a flat
// equity curve on one tab and have to go hunting on another for the reason.
// They're now a single DESK: curve → engine vitals → trades, top to bottom.
export type LiveTab = "desk" | "help";
const LIVE_TABS: { id: LiveTab; label: string; title: string }[] = [
  { id: "desk", label: "DESK", title: "Equity + engine vitals + every trade — mine vs the traders I copy, copied vs filtered out" },
  { id: "help", label: "HELP", title: "Which wallet do I use?" },
];
const LIVE_TAB_KEY = "polyLiveTab";
// Old persisted subtab ids all folded into DESK — migrate instead of falling
// back to the default, so a returning user lands where they left off.
const DESK_ALIASES = ["portfolio", "stats", "trades", "positions"];
export function normalizeLiveTab(raw: string | null | undefined): LiveTab {
  if (raw === "help") return "help";
  if (raw === "desk" || DESK_ALIASES.includes(raw ?? "")) return "desk";
  return "desk";
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
  const { engineState, isLive, startLive, stopLive, pauseLive, resumeLive, backendRunning, backendTraderSync, backendIntervalMs, backendGates, backendDryRuns, autoExecute, setAutoExecute, attachStrategy, catchUp } = useCopyEngine();
  // confirm-start flow removed — user wants direct start/stop.
  const [liveCapital, setLiveCapital] = useState(100);
  // Trading-wallet USDC balance — the on-chain "BALANCE" the engine sizes
  // mirrors against. Polled every 15s while the LIVE tab is mounted.
  const [tradingBalance, setTradingBalance] = useState<number | null>(null);
  // Catch-up status — "running" disables the button, "result" surfaces
  // the placed/failed count after the one-shot scan completes. Declared
  // after tradingBalance so the useCallback dep array doesn't hit a TDZ
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
  const effectiveMinNotional = catchUpMinNotional ?? Math.max(1, tradingBalance ?? 1);
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
  // Pagination is per COLUMN — the leader feed runs hundreds of rows deep
  // while my own fills are a handful, so one shared page index would have
  // paged an empty column.
  const [leaderPage, setLeaderPage] = useState(0);
  const [minePage, setMinePage] = useState(0);
  // TRADES body view. FEED is the two-column "them vs me" board and the
  // default; HISTORY is closed-position P&L; LOG is the raw engine feed kept
  // for debugging only.
  const [tradesView, setTradesView] = useState<"feed" | "history" | "log">("feed");
  const [logPage, setLogPage] = useState(0);
  // Which slice of the leader feed the left column shows. "in" = the engine
  // mirrored it, "out" = a gate rejected it (or the order failed). The whole
  // point of the split: "why am I not trading" is answered by the rejects.
  const [leaderFilter, setLeaderFilter] = useState<"all" | "in" | "out">("all");
  const TRADES_PAGE_SIZE = 25;
  // Attribute one of my fills to the leader trade it mirrored: same market,
  // same side, closest in time within this window. Fills come from the
  // data-api and carry no leader tag, so this join is the only link — keep
  // it tight enough that an unrelated re-entry hours later can't claim it.
  const FILL_MATCH_MS = 30 * 60_000;

  // Active section tab — persisted so the console reopens on the view the
  // user actually works from. Inactive tabs are NOT mounted: each panel runs
  // its own poller, so CSS-hiding would double data-api traffic for nothing.
  const [internalTab, setInternalTab] = useState<LiveTab>(() => {
    if (typeof window === "undefined") return "desk";
    return normalizeLiveTab(window.localStorage.getItem(LIVE_TAB_KEY));
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

  // Fetch on-chain fills whenever the DESK's trade board is showing; refresh
  // every 60s. My fills are now a permanent column of that board (not a tab
  // you opt into), so the poll follows the FEED view rather than a filter.
  useEffect(() => {
    if (liveTab !== "desk" || tradesView !== "feed") return;
    void loadFills();
    const t = setInterval(() => void loadFills(), 60_000);
    return () => clearInterval(t);
  }, [tradesView, liveTab, loadFills]);

  // Tick for countdown
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // Poll the TRADING wallet's balance every 15s. This is the number used to
  // auto-sync CAPITAL — keeps the engine's mirror sizing aligned with the
  // funds actually available rather than an arbitrary CAPITAL CAP default.
  //
  // The wallet is the deposit wallet the engine trades from (see
  // `resolveTradingWallet` in copyEngine.ts / order_place.rs), read through
  // the same endpoint as WalletPanel's TRADING tile — NOT the legacy V1 Safe
  // proxy, which holds none of the bot's funds and reads $0 no matter how
  // well funded the session is.
  useEffect(() => {
    if (!auth.address) {
      setTradingBalance(null);
      return;
    }
    let cancelled = false;
    const fetchBal = async () => {
      try {
        const res = await fetch(
          `/api/polymarket/deposit-wallet/info?eoa=${auth.address}`,
          { cache: "no-store" },
        );
        if (!res.ok) return;
        const info = await res.json() as {
          usdcBalance?: string | null;
          balanceUnavailable?: boolean;
        };
        // usdcBalance is RAW 6-decimal token units. `null` / balanceUnavailable
        // = the on-chain read failed, which is NOT a confirmed $0 — keep the
        // last known value instead of reporting a funded wallet as empty.
        if (info.balanceUnavailable || info.usdcBalance == null) return;
        const bal = Number(info.usdcBalance) / 1_000_000;
        if (!cancelled && Number.isFinite(bal)) setTradingBalance(bal);
      } catch { /* keep last known on RPC hiccup */ }
    };
    void fetchBal();
    const t = setInterval(fetchBal, 15_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [auth.address]);

  // Auto-sync CAPITAL → trading balance unless the user has explicitly capped.
  // Without this, CAPITAL stays at the $100 default while BALANCE shows the
  // real on-chain amount ($302+), and the engine mirrors against the wrong
  // budget. User can override via the CAPITAL CAP picker (sets the ref);
  // they can re-enable auto-tracking by hitting the MAX preset in that picker.
  useEffect(() => {
    if (tradingBalance === null) return;
    if (userOverrodeCapitalRef.current) return;
    const rounded = Math.floor(tradingBalance);
    if (rounded > 0 && rounded !== liveCapital) setLiveCapital(rounded);
  }, [tradingBalance, liveCapital]);

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

  // Everything this panel reads off the backend — TEST/LIVE mode, the gate
  // tally, per-trader sync ages — belongs to ONE session. Pin the poll to the
  // strat on screen: without this it answers for whatever session the wallet
  // last started, and the panel reports another strat's numbers under this
  // strat's name.
  useEffect(() => {
    attachStrategy(activeStrat?.id ?? null);
  }, [activeStrat?.id, attachStrategy]);

  // Single source of truth for poll cadence: the strat's `rebalanceMinutes`
  // (written by the SYNC panel below and the STRAT panel's POLL EVERY field).
  // Falls back to livePollMinutes (legacy) and then the 30s default. Strats
  // still carrying the old 1-minute legacy default (`=== 1`) are auto-upgraded
  // to 30s so nobody stays stuck behind the stale cadence (CopyIndex's POLL
  // EVERY select applies the same `1 ⇒ legacy` remap). Anything else (explicit
  // 5m, 30m, …) is honored as-is.
  const rawLivePollMin =
    activeStrat?.rebalanceMinutes ??
    activeStrat?.livePollMinutes ??
    MIN_POLL_MINUTES;
  const upgradedLivePollMin = rawLivePollMin === 1 ? MIN_POLL_MINUTES : rawLivePollMin;
  // Clamp any sub-30s cadence (incl. the old 5s default) up to the floor so
  // a stale strat config can't keep hammering the data-api into rate limits.
  const livePollMin = Math.max(MIN_POLL_MINUTES, upgradedLivePollMin);

  // Traders this strat is actually tracking — the set the SYNC panel reports
  // freshness for and sizes the cadence against.
  const watchedTraders = useMemo(
    () => activeStrat?.traders.filter((t) => t.enabled !== false) ?? [],
    [activeStrat],
  );

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
  // Funds are NOT a start precondition. An unfunded wallet runs in TEST so
  // any strat can still be exercised against live flow — mirrors are sized
  // with paper capital (the strat's BACKTEST capital, $1K default) until real
  // USDC shows up.
  const paperCapital = (activeStrat?.capital ?? 0) > 0 ? activeStrat!.capital! : 1000;
  const effectiveCapital = hasCapital ? liveCapital : paperCapital;

  // ── Mode ──
  // The mode a STOPPED engine will start in. Null = "haven't touched it",
  // which resolves to the capital-derived default (funded ⇒ LIVE). Same rule
  // as the copy desk, from the same function.
  //
  // The important change from the old header is that this is now SHOWN before
  // START rather than decided inside it. `GO LIVE` used to mean "start", and
  // whether that start placed real orders was inferred silently from the
  // wallet balance — so a funded wallet armed real money with no confirm, and
  // an unfunded one sat in a dry run nobody had asked for.
  //
  // `canGoLive` is deliberately NOT `hasCapital`. `hasCapital` is a BUDGET —
  // it starts at the $100 default and only later syncs down to the chain — so
  // it reads true for a wallet holding nothing, and arming real money off it
  // would offer LIVE to an empty wallet. Real money means real USDC in the
  // trading wallet: `tradingBalance`, which is null until the chain answers.
  // Unknown counts as unfunded; the switch says why and unlocks on the next
  // 15s poll.
  const canGoLive = (tradingBalance ?? 0) > 0;
  const [armedMode, setArmedMode] = useState<TradingMode | null>(null);
  const running = isLive || backendRunning;
  const mode: TradingMode = running
    ? modeOf(autoExecute)
    : armedMode ?? armedDefault(canGoLive);

  /** Mode switch. A running engine re-arms in place through /live/execution
      (the switch has already confirmed); a stopped one only records the arm,
      and START does the confirming. */
  const pickMode = useCallback((m: TradingMode) => {
    if (running) void setAutoExecute(autoExecuteFor(m));
    else setArmedMode(m);
  }, [running, setAutoExecute]);

  // Write the cadence through to the strat. `rebalanceMinutes` is canonical;
  // `livePollMinutes` is mirrored for the legacy readers. A browser-attached
  // session picks the change up through the configSig hot-restart effect
  // below, so the user never has to STOP/START to re-time their sync.
  //
  // When only the BACKEND session is running (the normal state after a reload
  // without CLOB creds in hand), that effect can't fire — so re-post the
  // config directly. `inheritExecution` keeps a deliberate TEST session dry.
  // Edit the live strat from wherever the problem is reported: persist the
  // patch, then re-post the config so a session that's ALREADY running picks
  // it up without a STOP/START round trip. `inheritExecution` keeps a
  // deliberate TEST session dry.
  const patchStrat = useCallback((patch: Partial<SavedIndex>) => {
    if (!activeStrat) return;
    const patched = { ...activeStrat, ...patch };
    updateIndex(activeStrat.id, { ...patch, updatedAt: Date.now() });
    setStratTick((n) => n + 1);
    if (auth.address && (backendRunning || isLive)) {
      void startLiveSession(auth.address, patched, effectiveCapital, { inheritExecution: true });
    }
  }, [activeStrat, backendRunning, isLive, auth.address, effectiveCapital]);

  // Relax (or restore) the time-to-close gate from the gate warning itself.
  const updateMinMinutesToClose = useCallback(
    (minutes: number) => patchStrat({ minMinutesToClose: minutes }),
    [patchStrat],
  );

  // Turn OFF whichever gate is blocking everything, from the warning that
  // names it. Every gate the engine can report has an entry here — a gate the
  // console can name but not clear is a dead end, and that dead end is how a
  // filter nobody remembered setting silently held a session at zero trades.
  // Each patch is the gate's documented "off" value (see lib/types.ts), so
  // clearing is always reversible from the STRAT panel's own fields.
  const clearGate = useCallback((gate: string) => {
    if (!activeStrat) return;
    const tf = activeStrat.tradeFilters ?? {};
    switch (gate) {
      case "price":
        patchStrat({ tradeFilters: { ...tf, minPrice: undefined, maxPrice: undefined } });
        break;
      case "size":
        patchStrat({ tradeFilters: { ...tf, minNotional: undefined, maxNotional: undefined } });
        break;
      case "side":
        patchStrat({ tradeFilters: { ...tf, sides: "both" } });
        break;
      case "category":
        patchStrat({ tradeFilters: { ...tf, categories: undefined } });
        break;
      case "market query":
        patchStrat({ marketQuery: "" });
        break;
      case "resolves too soon":
        patchStrat({ minMinutesToClose: 0 });
        break;
      case "stale":
        patchStrat({ maxTradeAgeSec: 0 });
        break;
      default:
        break;
    }
  }, [activeStrat, patchStrat]);

  // Mute a leader straight from the gate warning. A bot whose entire flow one
  // gate refuses is dead weight — it costs a fetch every cycle and copies
  // nothing. Dropping it is the fix that KEEPS the gate; same write-through
  // shape as the cadence/gate edits above, and reversible from the TRADERS
  // list (it only flips `enabled`).
  const dropLeader = useCallback((address: string) => {
    if (!activeStrat) return;
    const traders = activeStrat.traders.map((t) =>
      t.address.toLowerCase() === address.toLowerCase() ? { ...t, enabled: false } : t,
    );
    const patched = { ...activeStrat, traders };
    updateIndex(activeStrat.id, { traders, updatedAt: Date.now() });
    setStratTick((n) => n + 1);
    if (auth.address && (backendRunning || isLive)) {
      void startLiveSession(auth.address, patched, effectiveCapital, { inheritExecution: true });
    }
  }, [activeStrat, backendRunning, isLive, auth.address, effectiveCapital]);

  const updateSyncMinutes = useCallback((minutes: number) => {
    if (!activeStrat) return;
    const patched = { ...activeStrat, rebalanceMinutes: minutes, livePollMinutes: minutes };
    updateIndex(activeStrat.id, {
      rebalanceMinutes: minutes,
      livePollMinutes: minutes,
      updatedAt: Date.now(),
    });
    setStratTick((n) => n + 1);
    if (backendRunning && !isLive && auth.address) {
      void startLiveSession(auth.address, patched, effectiveCapital, { inheritExecution: true });
    }
  }, [activeStrat, backendRunning, isLive, auth.address, effectiveCapital]);
  const canStart = hasWallet && hasCreds && hasTraders && hasRebalance;

  // STOP is always one click — never confirmed, never blocked. START is one
  // click too in TEST; in LIVE it goes through the same confirm as every
  // other route to real money on this console (the desk's row switch, the
  // desk's START ALL, the "you are not trading" banner). Stopping is the safe
  // direction and starting a simulation is free — friction belongs on exactly
  // one transition, and this is it.
  const handleToggle = useCallback(() => {
    if (isLive) {
      stopLive();
      if (activeStrat) updateIndex(activeStrat.id, { liveEnabled: false, updatedAt: Date.now() });
      return;
    }

    if (!auth.clobCreds || !auth.address || !activeStrat) return;

    if (mode === "LIVE" && !confirmGoLive(activeStrat.name || "This strat", effectiveCapital)) {
      return;
    }

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
      // SIZING → UPSCALE. Only sent when the strat sets it, so the Strat's own
      // 2× default stays the source of truth otherwise.
      ...(activeStrat.maxUpscale !== undefined && { maxUpscale: activeStrat.maxUpscale }),
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
      // Trader FILTER — copy only the top-ranked traders on the watchlist.
      filter: activeStrat.filter,
      // Price-momentum origination — buys rising outcomes, no watchlist needed.
      momentum: activeStrat.momentum,
      // Whatever the TEST|LIVE switch above the button says — never inferred
      // here. The server's own default (false) is deliberately not relied on:
      // an omitted flag is how a funded session ends up silently in TEST.
      autoExecute: autoExecuteFor(mode),
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
  }, [isLive, auth, activeStrat, effectiveCapital, hasCapital, livePollMin, mode, startLive, stopLive]);

  const status = engineState?.status || "stopped";
  const nextIn = engineState?.nextCycleAt ? engineState.nextCycleAt - now : 0;

  // Auto-restart the engine when a param that only takes effect at START
  // changes mid-run. Two classes of them:
  //   • STRAT POLL EVERY — the engine keeps polling at whatever interval it
  //     was started with, so the user sees POLL EVERY 15s but NEXT IN still
  //     counting down from 60s.
  //   • The strat's GATES (market keywords + the semantic TRADE FILTER) —
  //     the running session holds an immutable config snapshot, so editing
  //     the filter mid-run silently did nothing and the engine kept copying
  //     the unfiltered flow.
  // Tracks the last-applied signature in a ref so we don't re-trigger on
  // every render. Skips while paused (the engine is intentionally idle
  // then, no point bouncing).
  const configSig = JSON.stringify({
    intervalMs: Math.max(1000, Math.round(livePollMin * 60_000)),
    marketQuery: activeStrat?.marketQuery ?? "",
    tradeFilters: activeStrat?.tradeFilters ?? {},
    filter: activeStrat?.filter ?? null,
    // UPSCALE decides how much of the filtered flow reaches the book, so a
    // mid-run edit has to hot-restart like the filters do.
    maxUpscale: activeStrat?.maxUpscale === undefined ? "default" : activeStrat.maxUpscale,
  });
  const lastAppliedSigRef = useRef<string | null>(null);
  useEffect(() => {
    if (!isLive || status !== "running") return;
    if (!auth.clobCreds || !auth.address || !activeStrat) return;
    const intervalMs = Math.max(1000, Math.round(livePollMin * 60_000));
    if (lastAppliedSigRef.current === null) {
      lastAppliedSigRef.current = configSig;
      return;
    }
    if (lastAppliedSigRef.current === configSig) return;
    lastAppliedSigRef.current = configSig;
    // Hot-restart: stop the existing engine, start a fresh one with the
    // new config. The CopyEngineContext preserves cursors via local
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
      ...(activeStrat.maxUpscale !== undefined && { maxUpscale: activeStrat.maxUpscale }),
      maxOpenPositions: activeStrat.maxOpenPositions ?? 10,
      stopLoss: activeStrat.stopLoss ?? DEFAULT_STOP_LOSS,
      takeProfit: activeStrat.takeProfit ?? DEFAULT_TAKE_PROFIT,
      maxOrderSize: activeStrat.maxTrade,
      backtestDays: activeStrat.backtestDays ?? 3,
      maxSlippageBps: 300,
      marketQuery: activeStrat.marketQuery,
      tradeFilters: activeStrat.tradeFilters,
      filter: activeStrat.filter,
      momentum: activeStrat.momentum,
    });
  }, [configSig, livePollMin, isLive, status, activeStrat, auth.clobCreds, auth.address, effectiveCapital, startLive, stopLive]);

  // Same write-through for a BACKEND-only session (the normal state after a
  // reload without CLOB creds in hand): the effect above can't hot-restart an
  // engine it isn't attached to, so re-post the config instead. Without this a
  // sizing/filter edit silently applied to the backtest only, and the running
  // engine kept its start-time config until STOP/START.
  const lastPostedSigRef = useRef<string | null>(null);
  useEffect(() => {
    if (isLive || !backendRunning || !auth.address || !activeStrat) return;
    if (lastPostedSigRef.current === null) {
      lastPostedSigRef.current = configSig;
      return;
    }
    if (lastPostedSigRef.current === configSig) return;
    lastPostedSigRef.current = configSig;
    void startLiveSession(auth.address, activeStrat, effectiveCapital, { inheritExecution: true });
  }, [configSig, isLive, backendRunning, auth.address, activeStrat, effectiveCapital]);

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
            {/* Not "LIVE COPY". The panel is the engine; LIVE is one of the
                two modes it can run in, and a panel titled LIVE sitting over
                a TEST session is the same category error the switch fixes. */}
            <span className="text-[16px] text-pixel-white tracking-wider">COPY ENGINE</span>
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
            {/* Cadence echo only — the SYNC panel below is where it's set,
                next to the traders it costs requests against. */}
            <span className="text-[11px] text-pixel-gray tracking-wider" title="Poll cadence — set it in the SYNC panel below">
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
                ever needed (e.g. after a long pause), STOP + START
                rebuilds the cursor — no separate UI needed. */}
            {/* MODE, then RUN — the same pair, in the same order, as the copy
                desk's header and every one of its rows. The switch is present
                whether or not the engine is up: stopped it arms what START
                will do, running it re-arms in place. It used to appear only
                once a session existed, which meant the single most
                consequential setting on the console was invisible at exactly
                the moment you were deciding it. */}
            <ModeSwitch
              size="sm"
              mode={mode}
              onPick={pickMode}
              running={running}
              canGoLive={canGoLive}
              subject={activeStrat?.name || "This strat"}
              amountUsd={effectiveCapital}
              disabled={!isLive && !canStart}
            />
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
            {/* Not "GO LIVE" any more. That name belonged to the OTHER axis:
                it started the engine, and whether it went live was decided
                elsewhere — so pressing GO LIVE could leave you in a dry run,
                which is exactly how a funded session sat for a week placing
                nothing. START starts; the switch to its left decides whether
                the money is real, and says so on the button. */}
            <button
              onClick={handleToggle}
              disabled={!isLive && !canStart}
              title={
                isLive
                  ? "Stop the engine. Open positions are left alone."
                  : canStart
                    ? `Start the copy engine on ${MODE[mode].label} — ${MODE[mode].meaning}` +
                      (canGoLive ? "" : `. Mirrors are sized against $${paperCapital} of simulated capital.`)
                    : "Complete the checklist above to enable"
              }
              className={`pixel-btn text-[14px] px-2.5 py-1 transition-colors ${
                isLive
                  ? "border-red-400 text-red-400 hover:bg-red-400/10"
                  : "border-green-400 text-green-400 hover:bg-green-400/10 disabled:opacity-30 disabled:cursor-not-allowed"
              }`}
            >
              {isLive ? "STOP" : `START · ${MODE[mode].label}`}
            </button>
          </div>
        </div>
        {/* What the two words mean, permanently, one line under the switch
            that uses them. Same line the desk carries. */}
        <div className="px-3 pb-1.5 -mt-0.5">
          <ModeLegend />
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

      {/* ── SYNC ──
          The cadence control, sitting with the traders it applies to: pick how
          often the engine re-polls, see what that costs in data-api requests
          for THIS watchlist, and see per-trader how long ago each one was
          actually pulled. Freshness comes from the backend engine (it keeps
          polling with the tab closed), so a stale chip means that trader is
          genuinely not being synced — not that the browser looked away. */}
      {activeStrat && (watchedTraders.length > 0 || isLive || backendRunning) && (() => {
        const requestedMs = livePollMin * 60_000;
        const plannedMs = effectiveIntervalMs(requestedMs, watchedTraders.length);
        // Sustained request rate this cadence implies — one /activity pull per
        // trader per cycle. Cloudflare starts 429ing the data-api in the
        // low single digits, so this is the number that matters.
        const reqPerSec = watchedTraders.length / (plannedMs / 1000);
        const widened = plannedMs > requestedMs;
        // What the engine reports it's running at, when a session is live.
        const runningMs = backendIntervalMs;
        return (
          <div className="pixel-panel border-2 border-pixel-border px-3 py-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-pixel-gray tracking-[0.18em] shrink-0">SYNC</span>
              <select
                value={livePollMin}
                onChange={(e) => updateSyncMinutes(Number(e.target.value))}
                title="How often the engine re-polls every watched trader. Applies to a running session without a restart."
                className="bg-pixel-black/40 border border-pixel-border/60 rounded px-1.5 py-0.5 font-mono text-[13px] text-pixel-white outline-none cursor-pointer"
              >
                {LIVE_POLL_OPTIONS.map((o) => (
                  <option key={o.minutes} value={o.minutes}>{o.label}</option>
                ))}
              </select>
              <span className="text-[12px] font-mono text-pixel-gray">
                {watchedTraders.length} trader{watchedTraders.length === 1 ? "" : "s"}
                {" · "}
                <span className={widened ? "text-amber-400" : "text-green-400"}>
                  every {formatLivePoll(plannedMs / 60_000)}
                </span>
                {" · "}
                ~{reqPerSec.toFixed(2)} req/s
              </span>
              {widened && (
                <span
                  className="text-[11px] font-mono text-amber-400"
                  title={`Polling ${watchedTraders.length} traders takes longer than ${formatLivePoll(livePollMin)} — the engine spaces requests ${INTER_REQUEST_DELAY_MS}ms apart to stay under Polymarket's rate limit. Drop traders or pick a slower cadence to make the requested interval reachable.`}
                >
                  ⚠ widened — {formatLivePoll(livePollMin)} can&apos;t fit {watchedTraders.length} traders
                </span>
              )}
              {runningMs !== null && (
                <span
                  className={`ml-auto text-[11px] font-mono shrink-0 ${
                    Math.abs(runningMs - plannedMs) < 1000 ? "text-green-400" : "text-amber-400"
                  }`}
                  title="Cadence the backend engine reports it is actually running at. Amber means the running session predates your latest change — it re-arms within a few seconds."
                >
                  ENGINE {formatLivePoll(runningMs / 60_000)}
                </span>
              )}
            </div>
            {watchedTraders.length > 0 && (
              <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                {watchedTraders.map((t) => {
                  const last = backendTraderSync[t.address.toLowerCase()];
                  const age = last ? now - last : null;
                  // One missed cycle is noise (a fetch can just be slow);
                  // three consecutive misses is a trader going unwatched.
                  const tone = age === null
                    ? "border-pixel-border/60 text-pixel-gray bg-pixel-black/40"
                    : age < plannedMs * 1.5
                      ? "border-green-400/60 text-green-400 bg-green-400/10"
                      : age < plannedMs * 3
                        ? "border-amber-400/60 text-amber-400 bg-amber-400/10"
                        : "border-red-400/60 text-red-400 bg-red-400/10";
                  return (
                    <span
                      key={t.address}
                      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-mono border ${tone}`}
                      title={`${t.address} · weight ${t.weight}${
                        last ? ` · last synced ${new Date(last).toLocaleTimeString()}` : " · not synced yet"
                      }`}
                    >
                      <span>{t.address.slice(0, 6)}…{t.address.slice(-4)}</span>
                      <span className="opacity-70">{age === null ? "—" : formatAgo(age)}</span>
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        );
      })()}

      {/* ── Preconditions ──
          Shown whenever the engine is stopped so the user knows what's
          blocking START. Compact pill row: each step is green-filled when
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
          // Capital never blocks START — with $0 on-chain the engine runs in
          // TEST against paper sizing (the strat's backtest capital). It DOES
          // block LIVE, which is what the switch's locked segment says, so the
          // pill names the same condition in the same words.
          { ok: true, label: canGoLive ? "CAPITAL" : `CAPITAL — ${MODE.TEST.label} ONLY`, action: null },
        ];
        const okCount = items.filter((i) => i.ok).length;
        return (
          <div className="pixel-panel border-2 border-pixel-border px-3 py-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-pixel-gray tracking-[0.18em] shrink-0">CHECKLIST</span>
              <span className={`text-[12px] font-mono tracking-wider shrink-0 ${
                okCount === items.length ? "text-green-400" : "text-amber-400"
              }`}>
                {/* "ready to START", not "ready to go live" — the checklist
                    gates the run axis. Whether that run is LIVE is the
                    switch's business, and CAPITAL says so on its own pill. */}
                {okCount}/{items.length} {okCount === items.length ? `· ready to start in ${mode}` : "· not ready"}
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
              // Money is the SIDE PANEL's MONEY block — the host (CopyIndex)
              // dispatches OPEN_MONEY_EVENT so the drawer opens OVER this
              // running session instead of navigating away from it. Fall back
              // to scrolling when this panel is rendered unhosted.
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

      {/* ── TEST is eating every mirror ──
          The counterpart to the gate warning below, and the failure it can
          never catch: these mirrors cleared EVERY filter and were thrown away
          because the session is in TEST. There was no standing signal for
          this — only a pill in the header, one tab away — and a session sat
          like that from 2026-08-01 to 08-08 logging hundreds of "would BUY"
          lines a day and placing nothing. Red, not amber: unlike a gate (a
          decision the strat made on purpose), this is almost always someone
          who meant to be live. Shared component so the desk can raise the
          same warning in the same words. */}
      {(isLive || backendRunning) && !autoExecute && (backendDryRuns?.count ?? 0) > 0 && (
        <NotTradingBanner
          count={backendDryRuns!.count}
          subject={activeStrat?.name || "This strat"}
          amountUsd={effectiveCapital}
          onGoLive={() => { void setAutoExecute(true); }}
        />
      )}

      {/* ── Everything gated ──
          The engine can be perfectly healthy, polling on schedule, seeing
          leader flow every cycle, and still place zero orders because the
          strat's own filters exclude 100% of that flow. That is the single
          most common "live trading is broken" report, and until now the only
          evidence was a sentence in a scrolling cycle log. Say it standing
          still, name the gate, and say what to change. The engine clears the
          tally the moment an entry does land, so this only ever shows while
          NOTHING is getting through — a strat that trades some flow and
          filters the rest is working as designed and stays quiet. */}
      {(isLive || backendRunning) && (() => {
        const gates = Object.entries(backendGates)
          .filter(([, t]) => t.count > 0)
          .sort((a, b) => b[1].count - a[1].count);
        if (!gates.length) return null;
        const total = gates.reduce((n, [, t]) => n + t.count, 0);
        return (
          <div className="pixel-panel border-2 border-amber-400/70 bg-amber-400/10 p-3 flex items-start gap-3 flex-wrap">
            <span className="text-amber-400 text-xl leading-none mt-0.5">⚠</span>
            <div className="flex-1 min-w-[240px]">
              <div className="text-sm font-bold text-amber-400">
                {total} leader {total === 1 ? "entry" : "entries"} seen, none copied — your
                filters blocked {total === 1 ? "it" : "them all"}
              </div>
              <div className="mt-1 space-y-0.5">
                {gates.map(([gate, t]) => (
                  <div key={gate} className="text-xs text-pixel-muted font-mono">
                    <span className="text-amber-300">{t.count}×</span>{" "}
                    <span className="text-pixel-white">{GATE_LABELS[gate]?.name ?? gate}</span>
                    {" — "}
                    {GATE_LABELS[gate]?.fix ?? "check this filter in the strat's settings."}
                    {/* One click turns THIS gate off. Every gate gets one —
                        see GATE_LABELS.off. */}
                    {GATE_LABELS[gate] && activeStrat && (
                      <button
                        onClick={() => clearGate(gate)}
                        title={`Turn the ${GATE_LABELS[gate].name} off for this strat and re-post the running session. Reversible from the STRAT panel.`}
                        className="ml-1.5 px-1.5 py-0.5 rounded border border-amber-400/50 text-amber-300 hover:bg-amber-400/15 align-middle"
                      >
                        {GATE_LABELS[gate].off}
                      </button>
                    )}
                    {/* Name the leaders the gate refused. Loosening a gate
                        that pays for itself is the wrong lever when the flow
                        hitting it comes from two bots you could just mute. */}
                    {!!t.traders?.length && (
                      <span className="ml-1 inline-flex items-center gap-1 flex-wrap align-middle">
                        <span className="text-pixel-muted/70">from</span>
                        {t.traders!.map((addr) => (
                          <button
                            key={addr}
                            onClick={() => dropLeader(addr)}
                            title={`${addr} — every entry of theirs this gate refused. Click to mute this leader on the strat (reversible from the TRADERS list).`}
                            className="px-1.5 py-0.5 rounded border border-amber-400/40 text-amber-300 hover:bg-amber-400/15"
                          >
                            {addr.slice(0, 6)}…{addr.slice(-4)}
                            <span className="ml-1 opacity-60">DROP</span>
                          </button>
                        ))}
                      </span>
                    )}
                  </div>
                ))}
              </div>
              <div className="text-[11px] text-pixel-muted/80 mt-1">
                Counted over the last 30 minutes. The engine is running normally — this is a
                filter decision, not a fault.
              </div>
              {/* Answer the gate where it's reported. A warning that names a
                  setting the console has no field for is a dead end, and the
                  time-to-close gate is the one that blocks whole watchlists at
                  once — a leader who only trades 5-minute candles has 100% of
                  their flow refused, every cycle, forever. */}
              {backendGates["resolves too soon"]?.count > 0 && activeStrat && (
                <div className="mt-2 flex items-center gap-2 flex-wrap">
                  <span className="text-[11px] font-mono text-pixel-muted">
                    MIN TIME TO CLOSE is{" "}
                    <span className="text-pixel-white">
                      {activeStrat.minMinutesToClose ?? DEFAULT_MIN_MINUTES_TO_CLOSE}m
                    </span>
                    {" — copy shorter-dated flow:"}
                  </span>
                  {[
                    { m: 15, label: "15M" },
                    { m: 5, label: "5M" },
                    { m: 0, label: "OFF" },
                  ].map(({ m, label }) => (
                    <button
                      key={m}
                      onClick={() => updateMinMinutesToClose(m)}
                      title={
                        m === 0
                          ? "Copy every market regardless of how soon it resolves — including the sub-hour Up/Down candles. Those are HFT turf: mirrored one poll late they realized −$253 across 1064 copies on this console. Your call."
                          : `Only refuse markets resolving within ${m} minutes.`
                      }
                      className="px-2 py-0.5 rounded border border-amber-400/50 text-amber-300 hover:bg-amber-400/15 text-[11px] font-mono tracking-[0.1em]"
                    >
                      {label}
                    </button>
                  ))}
                  <span className="text-[10.5px] font-mono text-pixel-muted/70">
                    (the leaders you copy may simply not trade anything longer-dated — check
                    their flow before turning it off)
                  </span>
                </div>
              )}
            </div>
          </div>
        );
      })()}

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

      {/* ── DESK ▸ equity + performance curve ── */}
      {liveTab === "desk" && auth.connected && <PortfolioPanel strategyId={activeStrat?.id} />}

      {/* BackendSignerPanel removed — the V1 Safe-co-owner flow that
          panel managed isn't needed for V2 trading. The V2 deposit
          wallet is CREATE2-derived from the per-user backend signer's
          own EOA, so the backend is *inherently* the wallet's
          authorized signer — no separate "TURN ON AUTO-TRADING" step
          to flip. Auto-trading is on by construction. */}

      {/* ── DESK ▸ engine vitals (when live) ──
          Card grid; LAST SYNC tracks the most recent successful Polymarket
          data-api pull, LAST FETCH surfaces the CYCLE_END summary. */}
      {liveTab === "desk" && isLive && engineState && (
        <div className="pixel-panel border-2 border-pixel-border p-2">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5">
            <StatCard
              label="FREE CASH"
              value={engineState.balance !== null ? `$${engineState.balance.toFixed(2)}` : "—"}
              tone="white"
              title="Uninvested USDC the engine can spend. Per-position P&L lives in the PORTFOLIO panel above."
            />
            {/* The number every mirror is sized as a fraction of — see
                ACCOUNT VALUE in the copy-sizing docs. */}
            <StatCard
              label="ACCOUNT"
              value={engineState.accountValue != null ? `$${engineState.accountValue.toFixed(2)}` : "—"}
              tone="white"
              title="Account value = free cash + mark value of this strat's open positions. Every mirror is sized as the same fraction of THIS that the leader risked of their own bankroll, so position sizes track the account as it grows or draws down."
            />
            {/* Only show CAPITAL when it's actually different from BALANCE —
                i.e. the user has manually capped below the proxy balance.
                When auto-tracking, CAPITAL == BALANCE makes a separate card
                pure noise (the user's complaint). */}
            {userOverrodeCapitalRef.current && tradingBalance !== null && liveCapital < tradingBalance && (
              <StatCard
                label="CAP"
                value={`$${liveCapital.toLocaleString()}`}
                tone="amber"
                title={`You capped at $${liveCapital} below the full trading balance ($${tradingBalance.toFixed(2)}). Hit MAX in the CAPITAL CAP picker to clear and use the full balance.`}
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

          {/* ── Blocked-strat banner ──
              LAST FETCH says "N new trades observed" and the fill count
              stays 0 — the engine IS working, every candidate is just
              getting vetoed by a gate. The evidence was there all along
              (one SKIP row per trade, reason first) but buried under
              hundreds of cycle heartbeats, so it read as a dead engine.
              Aggregate it: name the dominant gate and say what to change.
              Only fires when the session has seen real flow and mirrored
              none of it — a strat that is trading needs no explanation. */}
          {(() => {
            const skips = engineState.log.filter((e) => e.type === "SKIP" && e.reason);
            const fills = engineState.log.filter(
              (e) => e.type === "COPY_BUY" || e.type === "COPY_SELL",
            ).length;
            if (fills > 0 || skips.length < 5) return null;
            const byCode = new Map<string, number>();
            for (const e of skips) {
              const code = e.reason!.split("·")[0].trim();
              byCode.set(code, (byCode.get(code) ?? 0) + 1);
            }
            const ranked = [...byCode.entries()].sort((a, b) => b[1] - a[1]);
            // What to actually DO about the gate that is blocking most flow.
            // Keyed on the engine's own skip codes (live_engine.rs).
            const FIX: Record<string, string> = {
              TOO_SOON:
                "your leaders trade markets that close sooner than MIN MINUTES TO CLOSE. That gate exists because mirroring 5-min bots with a lag is a structural loss — so the fix is usually new leaders, not a lower gate.",
              STALE:
                "this strat sets MAX TRADE AGE and trades are older than that by the time we see them. The gate ships off — clear it, or poll faster.",
              LEADER_DUST:
                "your leaders' own trades are under Polymarket's order floor — there is nothing big enough to mirror.",
              SUB_SCALE:
                "your capital makes each mirror smaller than Polymarket's order floor, so proportionality can't be honored. YOUR SCALE on the STRATS board says how much capital would clear it; or raise MAX UPSCALE, or switch SIZING to conviction.",
              FILTER:
                "your trade filters (side / price band / notional) reject this flow. Widen them in RISK.",
              NO_CASH: "the trading wallet is empty — open MONEY in the side panel and top it up.",
              NO_EDGE: "the scorer found no positive edge on these trades.",
              LEADER_FLAT: "your leaders opened and closed before we could mirror them.",
              MAX_POSITIONS:
                "every position slot is full. Raise MAX OPEN POSITIONS or wait for some to resolve.",
              CEILING_BELOW_FLOOR:
                "MAX ORDER SIZE is below Polymarket's $1 floor — no legal order size exists.",
            };
            const [topCode, topCount] = ranked[0];
            return (
              <div className="mt-2 px-3 py-2 border border-amber-400/40 bg-amber-400/5 rounded">
                <div className="text-[12px] text-amber-400 font-mono mb-1">
                  NOTHING IS GETTING THROUGH — {skips.length} observed trades, 0 mirrored
                </div>
                <div className="text-[11px] text-pixel-gray font-mono">
                  {ranked
                    .slice(0, 3)
                    .map(([code, n]) => `${code} ×${n}`)
                    .join(" · ")}
                </div>
                <div className="mt-1 text-[11px] text-pixel-white font-mono">
                  {Math.round((100 * topCount) / skips.length)}% blocked by{" "}
                  <span className="text-amber-400">{topCode}</span> —{" "}
                  {FIX[topCode] ?? "see the LOG tab for the full reason text."}
                </div>
              </div>
            );
          })()}

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

      {/* Engine vitals only exist while the engine runs. On the merged DESK
          that's one slim line between the curve and the trades — not a
          full-height "nothing here" panel, since the rest of the desk (curve,
          my fills, position history) is real whether or not the engine is up. */}
      {liveTab === "desk" && !(isLive && engineState) && (
        <div className="pixel-panel border-2 border-pixel-border px-3 py-1.5 text-center">
          <span className="text-[12px] text-pixel-gray tracking-wider">
            ENGINE STOPPED — press START for engine vitals + the copied-trader feed
          </span>
        </div>
      )}

      {/* ── DESK ▸ TRADES — them vs me, in vs out ──
          ONE board, two columns. LEFT: every trade the traders you copy made,
          each tagged with what the engine did about it — ✓ COPIED, ⊘ FILTERED
          OUT (the gate's own reason on the row) or ✗ FAILED. RIGHT: what
          actually landed in YOUR wallet — on-chain fills from the data-api,
          ground truth that survives restarts and TEST mode, each attributed
          back to the leader trade it mirrored.
          Reading across the two answers the only question this page exists to
          answer: of everything they did, what did I get — and what stopped the
          rest? HISTORY (closed-position P&L) and LOG (raw engine feed) hang off
          the header toggle. */}
      {liveTab === "desk" && auth.connected && (() => {
        const engineReady = isLive && !!engineState;

        // Index every trade-tagged log entry by upstreamTradeId so each leader
        // row carries its mirror outcome inline. Keep the LATEST entry per id —
        // the engine can emit a SKIP and then a later retry.
        const outcomeById = new Map<string, ExecutionLogEntry>();
        for (const entry of engineState?.log ?? []) {
          if (entry.upstreamTradeId) {
            const prev = outcomeById.get(entry.upstreamTradeId);
            if (!prev || entry.timestamp > prev.timestamp) {
              outcomeById.set(entry.upstreamTradeId, entry);
            }
          }
        }
        type Tag = "in" | "out" | "failed" | "pending";
        const tagOf = (o?: ExecutionLogEntry): Tag => {
          if (!o) return "pending";
          if (o.type === "COPY_BUY" || o.type === "COPY_SELL") return "in";
          if (o.type === "SKIP") return "out";
          if (o.type === "ERROR") return "failed";
          return "pending";
        };
        const tagged = [...(engineState?.observedTrades ?? [])]
          .sort((a, b) => b.timestamp - a.timestamp)
          .map((t) => {
            const outcome = outcomeById.get(t.id);
            return { t, outcome, tag: tagOf(outcome) };
          });
        const counts = { all: tagged.length, in: 0, out: 0, failed: 0, pending: 0 };
        for (const r of tagged) counts[r.tag] += 1;
        // ⊘ and ✗ share the OUT slice: both mean "they traded, I didn't".
        const leaderRows = tagged.filter((r) =>
          leaderFilter === "all"
            ? true
            : leaderFilter === "in"
              ? r.tag === "in"
              : r.tag === "out" || r.tag === "failed",
        );

        // Why the OUT pile looks the way it does — the dominant gate, named.
        const skipCodes = new Map<string, number>();
        for (const r of tagged) {
          if (r.tag !== "out" || !r.outcome?.reason) continue;
          const code = r.outcome.reason.split("·")[0].trim();
          skipCodes.set(code, (skipCodes.get(code) ?? 0) + 1);
        }
        const topSkip = [...skipCodes.entries()].sort((a, b) => b[1] - a[1])[0];

        // ── My fills ──────────────────────────────────────────────
        // Attribute each fill to the leader trade it mirrored: same market,
        // same side, closest in time within FILL_MATCH_MS. Fills carry no
        // leader tag and no EP score, so this join is the only link back.
        const observedByMarket = new Map<string, ObservedTrade[]>();
        for (const ot of engineState?.observedTrades ?? []) {
          const k = (ot.conditionId || "").toLowerCase();
          if (!k) continue;
          const arr = observedByMarket.get(k);
          if (arr) arr.push(ot);
          else observedByMarket.set(k, [ot]);
        }
        const leaderForFill = (
          conditionId: string,
          side: "BUY" | "SELL",
          ts: number,
        ): ObservedTrade | undefined => {
          const arr = observedByMarket.get((conditionId || "").toLowerCase());
          if (!arr) return undefined;
          let best: ObservedTrade | undefined;
          let bestDelta = FILL_MATCH_MS;
          for (const ot of arr) {
            if (ot.side !== side) continue;
            const d = Math.abs(ot.timestamp - ts);
            if (d <= bestDelta) { best = ot; bestDelta = d; }
          }
          return best;
        };

        // A single copy often fills in many tiny pieces (and the same mirror
        // can be re-placed), flooding the column with identical rows. Collapse
        // consecutive fills sharing market / outcome / side / price into ×N.
        const sortedFills = [...fills].sort((a, b) => b.timestamp - a.timestamp);
        const myRows: BatchedFill[] = [];
        for (const r of sortedFills) {
          const prev = myRows[myRows.length - 1];
          if (
            prev &&
            prev.side === r.side &&
            prev.price === r.price &&
            (prev.conditionId || prev.market) === (r.conditionId || r.market) &&
            prev.outcome === r.outcome
          ) {
            prev.size += r.size;        // sorted desc → group keeps the newest ts
            prev.count += 1;
            prev.firstTs = r.timestamp; // r is older; track the span start
          } else {
            myRows.push({ ...r, count: 1, firstTs: r.timestamp });
          }
        }
        const myBuyUsd = sortedFills
          .filter((f) => f.side === "BUY")
          .reduce((s, f) => s + f.price * f.size, 0);

        // Per-column pagination — the leader feed runs hundreds of rows deep
        // while my fills are a handful; one shared index would page an empty
        // column.
        const page = <T,>(rows: T[], p: number) => {
          const totalPages = Math.max(1, Math.ceil(rows.length / TRADES_PAGE_SIZE));
          const safe = Math.min(p, totalPages - 1);
          const start = safe * TRADES_PAGE_SIZE;
          return { totalPages, safe, start, slice: rows.slice(start, start + TRADES_PAGE_SIZE) };
        };
        const L = page(leaderRows, leaderPage);
        const M = page(myRows, minePage);

        // A plain function, NOT a component: declaring a component inside the
        // render body gives it a fresh identity every render, so React would
        // unmount/remount the pager on each tick and PREV/NEXT would lose
        // keyboard focus mid-click. Called inline, the element types stay
        // stable.
        const pager = (
          total: number, totalPages: number, safe: number, start: number,
          onPage: (fn: (p: number) => number) => void,
        ) =>
          totalPages > 1 ? (
            <div className="px-3 py-1.5 border-t border-pixel-border/40 flex items-center justify-between">
              <span className="text-[11px] text-pixel-gray font-mono">
                {start + 1}-{Math.min(start + TRADES_PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => onPage((p) => Math.max(0, p - 1))}
                  disabled={safe === 0}
                  className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed"
                >
                  PREV
                </button>
                <span className="text-[11px] text-pixel-gray font-mono px-1">{safe + 1} / {totalPages}</span>
                <button
                  onClick={() => onPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={safe >= totalPages - 1}
                  className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed"
                >
                  NEXT
                </button>
              </div>
            </div>
          ) : null;

        // Header: view toggle + the one-line "them → me" scoreboard.
        const VIEWS: { id: typeof tradesView; label: string; title: string }[] = [
          { id: "feed", label: "FEED", title: "Two columns — what the traders you copy did, and what you actually filled" },
          { id: "history", label: "HISTORY", title: "My positions — open AND closed, with per-position P&L" },
          { id: "log", label: "LOG", title: "Raw engine log: every decision, cycle heartbeats, errors (debugging)" },
        ];

        return (
          <div className="pixel-panel border-2 border-pixel-border">
            <div className="px-3 py-1.5 border-b border-pixel-border flex items-center gap-2 flex-wrap">
              <span className="text-[14px] text-pixel-white tracking-wider shrink-0">TRADES</span>
              <div className="flex items-center rounded-md border border-pixel-border/70 bg-pixel-black/40 p-0.5 shrink-0">
                {VIEWS.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setTradesView(v.id)}
                    className={`px-2.5 py-0.5 text-[11px] font-mono tracking-[0.14em] rounded transition-colors ${
                      tradesView === v.id
                        ? "bg-green-400/15 text-green-400 border border-green-400/50"
                        : "border border-transparent text-pixel-gray hover:text-pixel-white"
                    }`}
                    title={v.title}
                  >
                    {v.label}
                  </button>
                ))}
              </div>
              {/* The scoreboard — their flow on the left of the arrow, mine on
                  the right, so the funnel reads in one glance from any view. */}
              <span className="ml-auto text-[12px] font-mono text-pixel-gray shrink-0 flex items-center gap-1.5">
                <span title="Trades observed from the traders you copy (this session)">
                  THEM <span className="text-pixel-white">{counts.all}</span>
                </span>
                <span className="text-green-400" title="Filtered IN — the engine mirrored these">✓{counts.in}</span>
                <span className="text-amber-400" title="Filtered OUT — a gate rejected these">⊘{counts.out}</span>
                {counts.failed > 0 && (
                  <span className="text-red-400" title="Order failed at the exchange">✗{counts.failed}</span>
                )}
                <span className="text-pixel-border">→</span>
                <span title="My actual on-chain fills (batched) and total BUY notional">
                  ME <span className="text-pixel-white">{myRows.length}</span>
                  {myBuyUsd > 0 && <span className="text-pixel-gray-light"> · ${myBuyUsd.toFixed(0)}</span>}
                </span>
              </span>
            </div>

            {tradesView === "history" && <PositionsHistoryPanel />}

            {tradesView === "log" && (!engineReady ? (
              <div className="px-3 py-4 text-center text-[13px] text-pixel-gray tracking-wider">
                ENGINE STOPPED — press START to see the engine log
              </div>
            ) : (() => {
              const rows = [...engineState!.log].sort((a, b) => b.timestamp - a.timestamp);
              const P = page(rows, logPage);
              return (
                <>
                  <div className="max-h-[300px] overflow-y-auto">
                    {(P.slice as ExecutionLogEntry[]).map((entry) => (
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
                          {entry.reason && <span className="text-pixel-gray"> {entry.reason}</span>}
                          {entry.orderResult && !entry.orderResult.success && entry.orderResult.errorMsg && (
                            <span className="text-red-400/70 block truncate">{entry.orderResult.errorMsg}</span>
                          )}
                        </div>
                      </div>
                    ))}
                    {P.slice.length === 0 && (
                      <div className="px-3 py-3 text-center text-[12px] text-pixel-gray">No log entries yet.</div>
                    )}
                  </div>
                  {pager(rows.length, P.totalPages, P.safe, P.start, setLogPage)}
                </>
              );
            })())}

            {tradesView === "feed" && (
              <div className="grid xl:grid-cols-2 divide-y xl:divide-y-0 xl:divide-x divide-pixel-border/60">
                {/* ── LEFT: the traders I copy ── */}
                <div className="min-w-0">
                  <div className="px-3 py-1.5 border-b border-pixel-border/40 flex items-center gap-2 flex-wrap bg-pixel-black/20">
                    <span
                      className="text-[11px] font-mono tracking-[0.14em] text-pixel-gray-light shrink-0"
                      title="Every trade the traders in this strat made while the engine was watching"
                    >
                      THE TRADERS I COPY
                    </span>
                    <div className="flex items-center rounded-md border border-pixel-border/70 bg-pixel-black/40 p-0.5 shrink-0">
                      {([
                        { id: "all" as const, label: `ALL ${counts.all}`, cls: "text-pixel-white border-pixel-white/50 bg-pixel-white/10", title: `Everything they did while the engine watched${counts.pending > 0 ? ` · ${counts.pending} still awaiting a ruling (·)` : ""}` },
                        { id: "in" as const, label: `✓ COPIED ${counts.in}`, cls: "text-green-400 border-green-400/50 bg-green-400/15", title: "FILTERED IN — the engine mirrored these into my wallet" },
                        { id: "out" as const, label: `⊘ FILTERED OUT ${counts.out + counts.failed}`, cls: "text-amber-400 border-amber-400/50 bg-amber-400/15", title: "FILTERED OUT — a gate rejected these (or the order failed). The reason is on each row." },
                      ]).map((f) => (
                        <button
                          key={f.id}
                          onClick={() => { setLeaderFilter(f.id); setLeaderPage(0); }}
                          className={`px-2 py-0.5 text-[10px] font-mono tracking-[0.1em] rounded border transition-colors ${
                            leaderFilter === f.id ? f.cls : "border-transparent text-pixel-gray hover:text-pixel-white"
                          }`}
                          title={f.title}
                        >
                          {f.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Why the OUT pile exists, in one line — named gate, not a
                      hunt through the log. */}
                  {leaderFilter === "out" && topSkip && (
                    <div className="px-3 py-1 border-b border-pixel-border/30 text-[11px] font-mono text-pixel-gray">
                      mostly <span className="text-amber-400">{topSkip[0]}</span> ×{topSkip[1]}
                      {counts.out > 0 && ` · ${Math.round((100 * topSkip[1]) / counts.out)}% of rejects`}
                      {" — loosen it in the STRAT panel (RISK) or copy different traders"}
                    </div>
                  )}

                  <div className="max-h-[340px] overflow-y-auto">
                    {!engineReady ? (
                      <div className="px-3 py-4 text-center text-[13px] text-pixel-gray tracking-wider">
                        ENGINE STOPPED — press START to watch the traders you copy
                      </div>
                    ) : L.slice.length === 0 ? (
                      <div className="px-3 py-3 text-center text-[12px] text-pixel-gray">
                        {counts.all === 0
                          ? "Waiting for the next sync cycle to observe trades…"
                          : leaderFilter === "in"
                            ? "Nothing copied yet — check the ⊘ FILTERED OUT pile for why."
                            : leaderFilter === "out"
                              ? "Nothing filtered out — every observed trade got through."
                              : "No trades in this slice."}
                      </div>
                    ) : (
                      L.slice.map(({ t, outcome, tag }) => {
                        const badge = tag === "in" ? "✓" : tag === "out" ? "⊘" : tag === "failed" ? "✗" : "·";
                        const badgeCls =
                          tag === "in" ? "text-green-400"
                            : tag === "out" ? "text-amber-400"
                              : tag === "failed" ? "text-red-400"
                                : "text-pixel-gray";
                        const edgeCls =
                          tag === "in" ? "border-l-green-400/60"
                            : tag === "out" ? "border-l-amber-400/50"
                              : tag === "failed" ? "border-l-red-400/60"
                                : "border-l-transparent";
                        const badgeTitle =
                          tag === "in"
                            ? `FILTERED IN — copied · order ${outcome?.orderResult?.orderID ?? "(no id)"} · ${outcome?.orderResult?.status ?? "matched"}`
                            : tag === "out"
                              ? "FILTERED OUT — a strat gate rejected this trade (reason below)"
                              : tag === "failed"
                                ? "FAILED — the engine tried to copy this and the order bounced (reason below)"
                                : "Awaiting next cycle — the engine hasn't ruled on this trade yet";
                        const reasonText =
                          tag === "out"
                            ? outcome?.reason ?? "SKIPPED"
                            : tag === "failed"
                              ? outcome?.orderResult?.errorMsg ?? outcome?.reason ?? "FAILED"
                              : null;
                        return (
                          <div
                            key={t.id}
                            className={`px-3 py-1 border-b border-pixel-border/20 border-l-2 ${edgeCls} text-[13px] font-mono hover:bg-pixel-white/5 ${tag === "out" ? "opacity-80" : ""}`}
                          >
                            <div className="flex items-start gap-2">
                              <span className="text-pixel-gray shrink-0 w-[66px] tabular-nums">{formatTime(t.timestamp)}</span>
                              <span className={`shrink-0 w-[16px] text-[14px] font-bold ${badgeCls}`} title={badgeTitle}>
                                {badge}
                              </span>
                              <span className={`shrink-0 w-[34px] text-[11px] font-bold ${t.side === "BUY" ? "text-green-400" : "text-red-400"}`}>
                                {t.side}
                              </span>
                              <span className="text-pixel-gray-light shrink-0 w-[78px] truncate" title={`leader ${t.trader}`}>
                                {t.trader.slice(0, 6)}…{t.trader.slice(-4)}
                              </span>
                              <span className="text-pixel-white truncate flex-1 min-w-0" title={t.market}>
                                {t.market}
                              </span>
                              <span className="text-pixel-gray-light shrink-0 text-right tabular-nums" title="leader's fill price">
                                @{(t.price * 100).toFixed(0)}¢
                              </span>
                              <span className="text-pixel-white shrink-0 w-[52px] text-right tabular-nums" title="leader's notional — what THEY put in, not what I mirrored">
                                ${t.notional < 1 ? t.notional.toFixed(2) : t.notional < 10_000 ? t.notional.toFixed(0) : `${(t.notional / 1000).toFixed(1)}k`}
                              </span>
                              {/* Rank score (P × ROI × mirror$) the engine sorts
                                  candidates by; SELLs are always honored. */}
                              <span
                                className={`shrink-0 w-[56px] text-right tabular-nums text-[12px] ${
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
                                      ? `Rank score $${t.score.toFixed(2)} = P(success) ${t.successProb !== undefined ? `${Math.round(t.successProb * 100)}%` : "—"} × ROI × mirror notional`
                                      : t.sharpe === 0 && t.score === 0
                                        ? "ROI not loaded yet — pending stats"
                                        : `No positive expected edge (score $${t.score.toFixed(2)}) — the engine filters these out (NO_EDGE), same as the backtest`
                                }
                              >
                                {t.side === "SELL" ? "—" : `$${t.score.toFixed(2)}`}
                              </span>
                            </div>
                            {reasonText && (
                              <div className={`pl-[84px] pr-2 text-[11px] break-words leading-snug ${tag === "failed" ? "text-red-400/80" : "text-amber-400/80"}`}>
                                {reasonText}
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                  {pager(leaderRows.length, L.totalPages, L.safe, L.start, setLeaderPage)}
                </div>

                {/* ── RIGHT: my own fills ── */}
                <div className="min-w-0">
                  <div className="px-3 py-1.5 border-b border-pixel-border/40 flex items-center gap-2 flex-wrap bg-pixel-black/20">
                    <span
                      className="text-[11px] font-mono tracking-[0.14em] text-cyan-300 shrink-0"
                      title="My ACTUAL executed trades — on-chain fills pulled from Polymarket for the wallet the engine trades through. Ground truth: survives restarts, and stays empty on PAPER no matter how busy the left column looks."
                    >
                      THE TRADES I MADE
                    </span>
                    <span className="text-[10px] font-mono text-pixel-gray truncate" title={fillsWallet ?? undefined}>
                      {fillsWallet ? `${fillsWallet.slice(0, 6)}…${fillsWallet.slice(-4)}` : "resolving wallet…"}
                    </span>
                    <span className="ml-auto text-[11px] font-mono text-pixel-gray shrink-0">
                      {myRows.length !== sortedFills.length
                        ? `${myRows.length} trades · ${sortedFills.length} fills batched`
                        : `${sortedFills.length} fills`}
                      {fillsLoading && <span className="text-amber-400"> · loading…</span>}
                    </span>
                  </div>

                  {/* TEST mode is the #1 reason this column is empty while
                      the left one scrolls — say it here, where it's noticed,
                      in the same word the header switch uses. */}
                  {!autoExecute && (
                    <div
                      className="px-3 py-1 border-b border-pixel-border/30 text-[11px] font-mono text-amber-400"
                      title={MODE.TEST.active + ` No fills can appear here until the header switch is flipped to ${MODE.LIVE.label}.`}
                    >
                      {MODE.TEST.label} {MODE.TEST.dot} — copies are simulated, so this column stays empty
                    </div>
                  )}

                  <div className="max-h-[340px] overflow-y-auto">
                    {M.slice.length === 0 ? (
                      <div className="px-3 py-3 text-center text-[12px] text-pixel-gray">
                        {fillsLoading
                          ? "Loading on-chain fills…"
                          : fillsError
                            ? `Couldn't load fills: ${fillsError}`
                            : "No on-chain fills yet."}
                      </div>
                    ) : (
                      (M.slice as BatchedFill[]).map((t) => {
                        const lead = leaderForFill(t.conditionId, t.side, t.timestamp);
                        return (
                          <div
                            key={t.id}
                            className="px-3 py-1 border-b border-pixel-border/20 border-l-2 border-l-cyan-400/50 text-[13px] font-mono hover:bg-pixel-white/5"
                          >
                            <div className="flex items-start gap-2">
                              <span
                                className="text-pixel-gray shrink-0 w-[66px] tabular-nums"
                                title={t.count > 1 ? `${t.count} fills batched · ${formatTime(t.firstTs)}–${formatTime(t.timestamp)}` : `filled at ${formatTime(t.timestamp)} UTC`}
                              >
                                {formatTime(t.timestamp)}
                              </span>
                              <span className={`shrink-0 w-[34px] text-[11px] font-bold ${t.side === "BUY" ? "text-green-400" : "text-red-400"}`}>
                                {t.side}
                              </span>
                              <span className="text-pixel-white truncate flex-1 min-w-0" title={`${t.market}${t.outcome ? ` · ${t.outcome}` : ""} · ${t.size.toFixed(2)} shares`}>
                                {t.market}
                                {t.outcome ? <span className="text-pixel-gray-light"> · {t.outcome}</span> : null}
                                {t.count > 1 && (
                                  <span className="ml-1.5 text-[10px] text-amber-400 font-bold" title={`${t.count} fills batched into one row`}>
                                    ×{t.count}
                                  </span>
                                )}
                              </span>
                              <span className="text-pixel-gray-light shrink-0 text-right tabular-nums" title="my fill price">
                                @{(t.price * 100).toFixed(0)}¢
                              </span>
                              <span className="text-pixel-white shrink-0 w-[56px] text-right tabular-nums" title={`my notional (price × ${t.size.toFixed(2)} shares)`}>
                                ${(t.price * t.size).toFixed(2)}
                              </span>
                              {/* EP score, joined from the leader trade this
                                  fill mirrored. SELLs and aged-out trades: "—". */}
                              <span
                                className={`shrink-0 w-[56px] text-right tabular-nums text-[12px] ${
                                  t.side === "SELL" || !lead
                                    ? "text-pixel-gray"
                                    : lead.score > 5
                                      ? "text-green-400"
                                      : lead.score > 1
                                        ? "text-yellow-400"
                                        : lead.score > 0
                                          ? "text-pixel-gray-light"
                                          : "text-red-400/70"
                                }`}
                                title={
                                  t.side === "SELL"
                                    ? "SELL — EP N/A (closes a position)"
                                    : !lead
                                      ? "Score N/A — the originating leader trade aged out of the live buffer"
                                      : `Expected profit $${lead.score.toFixed(2)} = trader ROI × mirror notional`
                                }
                              >
                                {t.side === "SELL" || !lead ? "—" : `$${lead.score.toFixed(2)}`}
                              </span>
                            </div>
                            {/* Which leader trade this mirrored, and how much
                                of theirs I actually took on. With an empty
                                observed buffer (engine stopped / just started)
                                NOTHING can match, so the "no match" note would
                                be one line of noise per row — say nothing then;
                                it only means something once the feed is live. */}
                            {(lead || counts.all > 0) && (
                            <div className="pl-[66px] pr-2 text-[11px] leading-snug">
                              {lead ? (
                                <span
                                  className="text-pixel-gray"
                                  title={`Matched to ${lead.trader} — same market, same side, ${Math.round(Math.abs(lead.timestamp - t.timestamp) / 1000)}s apart. Attribution is by market+side+time (fills carry no leader tag), so a manual trade in a copied market can match too.`}
                                >
                                  ↳ copied <span className="text-pixel-gray-light">{lead.trader.slice(0, 6)}…{lead.trader.slice(-4)}</span>
                                  {" · their "}${lead.notional < 1 ? lead.notional.toFixed(2) : lead.notional.toFixed(0)}
                                  {lead.notional > 0 && (
                                    <span className="text-pixel-gray"> ({((t.price * t.size * 100) / lead.notional).toFixed(1)}% of theirs)</span>
                                  )}
                                  {" · lag "}{formatAgoShort(t.timestamp - lead.timestamp)}
                                </span>
                              ) : (
                                <span className="text-pixel-gray/70" title="No leader trade in the live buffer matches this fill (same market + side within 30 min). Either it aged out, or this was a manual / redeem / rotation trade rather than a copy.">
                                  ↳ no matching leader trade in the buffer
                                </span>
                              )}
                            </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                  {pager(myRows.length, M.totalPages, M.safe, M.start, setMinePage)}
                </div>
              </div>
            )}
          </div>
        );
      })()}


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
