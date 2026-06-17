import { BrowserProvider, Contract, JsonRpcProvider, formatUnits, type JsonRpcSigner } from "ethers";
import { ClobCredentials, IndexTrader, PolymarketTrade, PolymarketPosition, TraderRoiStats } from "./types";
import { placeOrder, detectSigType, ClobOrderResult } from "./clobClient";
import { fetchWalletTradesUntil, fetchWalletTradesIncremental, fetchPositions, fetchTraderRoiStats } from "./polymarket";
import { getTradeCache } from "./cache";
import { Strat, TraderTrade as StratTraderTrade, SizeConstraints } from "./strats/base";
import { getStrat } from "./strats/registry";
import { networkById, ensureChain, withRpcFallback } from "./networks";
import { getProxyAddress } from "./polymarketProxy";
import { USDC_E } from "./polymarketContracts";

// ── Types ──────────────────────────────────────────────────────

export type CopyEngineStatus = "stopped" | "starting" | "running" | "paused" | "error";

export interface ExecutionLogEntry {
  id: string;
  timestamp: number;
  type: "COPY_BUY" | "COPY_SELL" | "SKIP" | "ERROR" | "BALANCE" | "CYCLE_START" | "CYCLE_END";
  traderAddress?: string;
  market?: string;
  conditionId?: string;
  tokenId?: string;
  side?: "BUY" | "SELL";
  traderSize?: number;
  mirrorSize?: number;
  mirrorNotional?: number;
  price?: number;
  orderResult?: ClobOrderResult;
  reason?: string;
  /// ObservedTrade.id this log entry corresponds to. Set on every per-
  /// trade outcome (COPY_BUY / COPY_SELL / SKIP / ERROR) so the UI can
  /// look up the mirror outcome for any upstream trade by id. Without
  /// this the user can only see a separate "execution log" feed and
  /// can't tell which upstream trade each error refers to.
  upstreamTradeId?: string;
  /// Sharpe-weighted EV score this candidate had when the engine made
  /// its decision (copy vs skip). Surfaced on COPY_BUY and SKIP rows
  /// so the user can see "we picked this $0.83-score trade over that
  /// $0.21-score trade" without reverse-engineering the math.
  score?: number;
  /// Trader's 30d Sharpe at decision time. Shown alongside score.
  sharpe?: number;
}

/** A single upstream trade the engine observed during a sync cycle. Distinct
    from the execution log (which is the engine's decisions) — this is the
    raw real-time stream of what watched traders did, regardless of whether
    the engine mirrored / skipped / errored. */
export interface ObservedTrade {
  id: string;
  timestamp: number;
  trader: string;
  market: string;
  conditionId: string;
  side: "BUY" | "SELL";
  size: number;
  price: number;
  notional: number;
  /** Sharpe-weighted EV score the engine assigned to this candidate.
      Computed as `trader_sharpe_30d * notional` — dollars of expected
      profit if the trader behaves like their 30d Sharpe predicts. 0
      for SELLs (always honored, score N/A) and for any BUY where the
      trader has too few 30d closed trades (<3) to derive a Sharpe. */
  score: number;
  /** Trader's 30d Sharpe at the time we scored. 0 = insufficient stats. */
  sharpe: number;
}

export interface CopyEngineState {
  status: CopyEngineStatus;
  lastCycleAt: number | null;
  nextCycleAt: number | null;
  cycleCount: number;
  totalOrdersPlaced: number;
  totalOrdersFailed: number;
  totalVolumeMirrored: number;
  balance: number | null;
  log: ExecutionLogEntry[];
  /** Ring buffer of upstream trades observed in recent cycles. Capped to
      RECENT_TRADES_LIMIT so memory stays bounded. Newest-first ordering. */
  observedTrades: ObservedTrade[];
  error: string | null;
  traderCursors: Record<string, number>;
  /** Per-trader timestamp of last successful Polymarket data-api response
      (ms epoch). Lets the UI flag traders the engine hasn't synced in a
      while (rate-limited, network blip, etc.) without waiting for an error. */
  traderLastSync: Record<string, number>;
  /** Per-trader 30d Sharpe stats. Exposed so the UI can render the score
      breakdown next to each trader and observed trade. Keyed by lowercased
      address. Refreshed every ROI_REFRESH_MS (30min) in the background. */
  traderRoiStats: Record<string, TraderRoiStats>;
}

export interface CopyEngineConfig {
  strategyId: string;
  traders: IndexTrader[];
  capital: number;
  intervalMs: number;
  creds: ClobCredentials;
  address: string;
  minOrderSize: number;
  /** Upper clamp for mirror notional. When proportional sizing produces a
      mirror larger than this, we cap it instead of placing the full amount.
      Pairs with minOrderSize to make TRADE SIZE feel like a real range
      ("every copy lands in [$1, $10]") instead of just a skip floor. */
  maxOrderSize?: number;
  maxSlippageBps: number;
  /** Lookback window (days) used to compute each trader's volume denominator
      for the proportional copy-ratio. Mirrors the BACKTEST tab's `backtestDays`
      so the live engine's sizing matches what the backtest preview shows.
      Defaults to 3 (the backtest default) when not provided. */
  backtestDays?: number;
  /** Top-N sampling cap: per cycle, score every observed BUY candidate as
      `score = trader_sharpe_30d * trade_notional` and copy only the top N.
      The single most important fee-control knob — without it the engine
      mirrors every observed trade (11k trades / 7d = $58 of gas burns the
      gross P&L). Defaults to 3. */
  maxPerCycle?: number;
}

// ── Scoring constants ──────────────────────────────────────────
// Window the engine uses for trader Sharpe stats. 30 days matches
// the leaderboard window most users sort by.
const ROI_WINDOW_DAYS = 30;
// How often to re-pull trader stats. Trades land continuously but
// 30d Sharpe is slow-moving; 30min keeps the stats fresh without
// hammering the data-api each cycle.
const ROI_REFRESH_MS = 30 * 60 * 1000;
const ROI_CACHE_KEY = "poly_copy_trader_roi";

const MAX_LOG_ENTRIES = 200;
const MAX_ORDERS_PER_CYCLE = 20;
const ORDER_DELAY_MS = 500;
// 0 — the matcher's fee schedule for binary markets returns 0, and a
// hardcoded 200 bps was making every CLOB POST reject with HTTP 400
// "Invalid order payload" because the signed feeRateBps didn't match
// what the matcher expected. Per-market fee lookup is a TODO but 0 is
// the safe default for taker-side fills on standard CTF markets.
const TAKER_FEE_BPS = 0;
// Polymarket's CLOB rejects any order below 5 SHARES (not $5 — five
// outcome tokens) with `order ... is invalid. Size (N) lower than the
// minimum: 5`. Notional alone isn't enough: $1 at 50¢ = 2 shares = REJECT.
// We must compute the floor in USD as `max($1, 5 × price)` per-trade so
// the resulting order always hits ≥5 shares regardless of trade price.
//
// User-configurable minOrderSize is the strat's per-trade USD floor;
// these constants are the hard API limit we MUST clamp to regardless
// of strat settings. Without them, users who dropped the floor to $0.01
// via the auto-fix banner produced cascades of rejected $0.01 orders.
const POLYMARKET_MIN_USD = 1.0;
const POLYMARKET_MIN_SHARES = 5;

/** Smallest mirror notional that CLOB will accept at the given price
 *  — combines the $1 notional floor and the 5-share share floor. */
function clobMinNotional(price: number): number {
  const sharesFloor = POLYMARKET_MIN_SHARES * Math.max(price, 1e-9);
  return Math.max(POLYMARKET_MIN_USD, sharesFloor);
}

// Polymarket binary markets use a 0.01 (1¢) tick size by default; some
// fine-grained markets use 0.001. Leader trade prices come back from the
// data-api with full f64 precision (e.g. 0.5099601593625498 from an
// implied-price derivation), which trips a "Price breaks minimum tick
// size" 400. Round every price we send to 2 decimals so it always lands
// on a tick — slight cost is we round to the nearest cent rather than
// the leader's exact fill, which is negligible for copy trading and
// avoids per-market tick lookups.
function tickRoundPrice(p: number): number {
  if (!Number.isFinite(p)) return 0;
  const clamped = Math.max(0.01, Math.min(0.99, p));
  return Math.round(clamped * 100) / 100;
}

// ── Token ID Cache ─────────────────────────────────────────────

const TOKEN_MAP_KEY = "poly_copy_tokenmap";
const NEG_RISK_MAP_KEY = "poly_copy_negriskmap";

function loadTokenMap(): Map<string, string[]> {
  try {
    const raw = localStorage.getItem(TOKEN_MAP_KEY);
    if (!raw) return new Map();
    const obj = JSON.parse(raw);
    return new Map(Object.entries(obj));
  } catch {
    return new Map();
  }
}

function saveTokenMap(map: Map<string, string[]>): void {
  try {
    const obj = Object.fromEntries(map);
    localStorage.setItem(TOKEN_MAP_KEY, JSON.stringify(obj));
  } catch {}
}

function loadNegRiskMap(): Map<string, boolean> {
  try {
    const raw = localStorage.getItem(NEG_RISK_MAP_KEY);
    if (!raw) return new Map();
    return new Map(Object.entries(JSON.parse(raw)));
  } catch {
    return new Map();
  }
}

function saveNegRiskMap(map: Map<string, boolean>): void {
  try {
    localStorage.setItem(NEG_RISK_MAP_KEY, JSON.stringify(Object.fromEntries(map)));
  } catch {}
}

// ── Helpers ────────────────────────────────────────────────────

function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// ── Engine ─────────────────────────────────────────────────────
//
// Per-trade copy engine. Each cycle polls every enabled trader's recent
// activity and places a mirror FOK order for any trade newer than the
// trader's last-seen cursor. Sizing is "proportional to trader": each
// trade is scaled by capital_alloc / trader_recent_buy_volume, so the
// follower's $ exposure across the trader's whole book matches the
// configured capital allocation. Identical to the backtest's per-trade
// replay — so what you backtest is what you live-trade.
//
// The "rebalance" minutes config is really a poll interval; the engine
// does not batch or net trades within a window.

export class CopyEngine {
  private config: CopyEngineConfig;
  private state: CopyEngineState;
  private timer: ReturnType<typeof setInterval> | null = null;
  private listeners = new Set<(s: CopyEngineState) => void>();
  private copiedIds = new Set<string>();
  // Expected-profit ledger for OPEN positions we mirrored, keyed by
  // `${conditionId}:${outcome}` (lowercased). `entryEP` = roi × mirrorNotional
  // at the instant we copied the BUY. Consulted during EP-driven rotation:
  // a position's FORWARD expected profit = max(0, entryEP − realized pnl), so
  // a position that has already earned its expected profit is the first to be
  // rotated out to fund a higher-EP new buy. Positions we didn't open have no
  // entry → forward EP 0 → freely rotatable.
  private positionEP: Record<string, { entryEP: number; mirrorNotional: number }> = {};
  private tokenMap: Map<string, string[]>;
  private negRiskMap: Map<string, boolean>;
  private running = false;
  // Cached signer; refreshed if the user switches account/chain in MetaMask.
  private signer: JsonRpcSigner | null = null;
  // Polymarket Proxy address for this user's EOA. Resolved lazily on first
  // cycle; persists for the engine's lifetime.
  private proxyAddress: string | null = null;
  // Legacy V1 sigType field. The backend now always overrides to
  // sigType=3 (POLY_1271) regardless of what we send, so this is
  // vestigial — kept on the type to avoid churning every placeOrder
  // call site, but its value no longer affects routing.
  private sigType: 0 | 1 | 2 | 3 = 3;
  // Guard against re-entering executeCycle when the previous cycle is still
  // mid-flight. Necessary at sub-minute poll cadences (5s) where a slow
  // cycle (token-id lookup + order placement RTT) can outlast a tick of
  // setInterval. Without this, a stuck cycle would silently spawn a parallel
  // copy that double-fires every trade.
  private cycleInFlight = false;
  // Per-trader 30d Sharpe cache. Keyed by lowercased address. Drives
  // top-N candidate sampling. Refreshed every ROI_REFRESH_MS so we
  // don't hammer the data-api each cycle. Persisted to localStorage
  // so engine restarts don't blank scores until the first refresh.
  private traderRoiStats: Record<string, TraderRoiStats> = {};
  private roiRefreshTimer: ReturnType<typeof setInterval> | null = null;
  private roiRefreshInFlight = false;
  // The strategy decides scoring + sizing per candidate. Swap by
  // pointing this at any class implementing Strat. Default = CopyTrader.
  // The engine handles everything else (cycle loop, balance check,
  // deposit wallet, CLOB submission, log persistence).
  private strat: Strat;

  constructor(config: CopyEngineConfig, strat?: Strat) {
    this.config = config;
    this.tokenMap = loadTokenMap();
    this.negRiskMap = loadNegRiskMap();
    // Active strategy. Pass `strat` to override, or change the default
    // in src/app/app/lib/strats/registry.ts to swap globally.
    this.strat = strat ?? getStrat(undefined, { maxPerCycle: config.maxPerCycle });
    this.state = {
      status: "stopped",
      lastCycleAt: null,
      nextCycleAt: null,
      cycleCount: 0,
      totalOrdersPlaced: 0,
      totalOrdersFailed: 0,
      totalVolumeMirrored: 0,
      balance: null,
      log: [],
      observedTrades: [],
      error: null,
      traderCursors: {},
      traderLastSync: {},
      traderRoiStats: {},
    };
    this.loadPersisted();
  }

  // ── Lifecycle ────────────────────────────────────────────────

  start(): void {
    if (this.running) return;
    this.running = true;
    this.setState({ status: "starting", error: null });

    // First cycle: set cursors to now (don't retroactively copy old trades)
    const hasCursors = Object.keys(this.state.traderCursors).length > 0;
    if (!hasCursors) {
      const now = Date.now();
      const cursors: Record<string, number> = {};
      for (const t of this.config.traders) {
        if (t.enabled !== false) cursors[t.address.toLowerCase()] = now;
      }
      this.setState({ traderCursors: cursors });
      this.saveCursors();
    }

    // Kick a ROI refresh in the background so the first scored cycle
    // has fresh stats. Don't await — the first cycle uses whatever we
    // persisted (or zeros, in which case sharpe=0 → trader skipped
    // by top-N until refresh lands).
    this.refreshTraderRoiStats();
    this.roiRefreshTimer = setInterval(() => {
      if (this.state.status === "running" || this.state.status === "paused") {
        this.refreshTraderRoiStats();
      }
    }, ROI_REFRESH_MS);

    // Run first cycle immediately then schedule
    this.executeCycle().then(() => {
      if (!this.running) return;
      this.timer = setInterval(() => {
        // Re-entrancy guard: at 5s polling a slow cycle (token-id
        // lookup + several FOK RTTs) can outlast the next tick. Skipping
        // here keeps the engine honest; we'll catch up on the tick that
        // fires after the previous cycle resolves.
        if (this.cycleInFlight) return;
        if (this.state.status === "running") {
          this.executeCycle();
        }
      }, this.config.intervalMs);
      const nextAt = Date.now() + this.config.intervalMs;
      this.setState({ status: "running", nextCycleAt: nextAt });
    });
  }

  stop(): void {
    this.running = false;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    if (this.roiRefreshTimer) {
      clearInterval(this.roiRefreshTimer);
      this.roiRefreshTimer = null;
    }
    this.signer = null;
    this.setState({ status: "stopped", nextCycleAt: null });
    this.saveLog();
    this.saveCursors();
  }

  pause(): void {
    this.setState({ status: "paused" });
  }

  resume(): void {
    if (this.state.status !== "paused") return;
    this.setState({ status: "running" });
  }

  getState(): CopyEngineState {
    return { ...this.state };
  }

  subscribe(fn: (s: CopyEngineState) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  // ── Catch-up (one-shot backfill) ─────────────────────────────
  //
  // Replaces "wait for next live trade" with "look at the last N hours
  // and copy the most confident recent trades NOW." Used by the CATCH UP
  // button on LIVE — runs once, doesn't touch the cycle loop, ignores the
  // cursor + dedup set so it can re-evaluate previously skipped trades
  // under the new clamp sizing.
  //
  // Filter logic per trade:
  //   - trade.notional (trader's USD position) ≥ minNotional
  //   - skip SELLs (we can't sell what we don't hold)
  //   - all surviving trades sent through the existing per-trade pipeline
  //     (clamp → token-id resolution → backend-signed placeOrder)
  //
  // Price-drift filter is a TODO — would need a per-conditionId midpoint
  // fetch, which slows the catch-up considerably. Leaving it as a future
  // tightening once the basic flow is proven.
  async catchUp(opts: {
    lookbackHours: number;
    minNotional: number;
    /** Maximum number of buys to copy across the entire lookback window
        (ALL traders combined). Trades are scored by trader notional and
        the top N picked. Avoids the previous "fire 143 mirrors and burn
        the budget" failure mode. */
    topN?: number;
    /** Liquidate any open position whose `pnlUsd > 0` before the buy
        loop. Recoups USDC into the proxy so the catch-up buys actually
        have budget to spend. */
    sellWinners?: boolean;
    onProgress?: (msg: string) => void;
  }): Promise<{ scanned: number; placed: number; failed: number; skipped: number; sold: number }> {
    const lookbackSec = Math.max(60, Math.floor(opts.lookbackHours * 3600));
    const cutoffSec = Math.floor(Date.now() / 1000) - lookbackSec;
    let scanned = 0;
    let placed = 0;
    let failed = 0;
    let skipped = 0;
    let sold = 0;
    // Tracks back-to-back failures so we can halt after a small run of
    // them — the alternative is a 100+ entry log of identical rejections
    // before the user notices something is structurally wrong.
    let consecutiveFailures = 0;
    const topN = opts.topN ?? Number.POSITIVE_INFINITY;

    const enabled = this.config.traders.filter((t) => t.enabled !== false);
    const totalWeight = enabled.reduce((s, t) => s + t.weight, 0);
    if (totalWeight <= 0) return { scanned, placed, failed, skipped, sold };
    // CATCH UP uses the same backtest-aligned window for the copyRatio
    // denominator as the live cycle — keeps the catch-up sizing consistent
    // with what the backtest preview showed for the same lookback.
    const backtestDays = this.config.backtestDays ?? 3;
    const denomCutoffMs = Date.now() - backtestDays * 86400_000;

    this.addLog({
      id: uid(),
      timestamp: Date.now(),
      type: "BALANCE",
      reason: `CATCH UP started · lookback ${opts.lookbackHours}h · top ${Number.isFinite(topN) ? topN : "∞"} · min $${opts.minNotional.toFixed(0)} · sellWinners ${opts.sellWinners ? "on" : "off"}`,
    });

    // ── Phase 1: free liquidity (delegate to shared helper) ──
    if (opts.sellWinners) {
      opts.onProgress?.("freeing liquidity…");
      const res = await this.freeLiquidity({
        reasonPrefix: "FREE LIQUIDITY",
        onProgress: opts.onProgress,
      });
      sold += res.sold;
    }

    opts.onProgress?.(`scanning ${enabled.length} traders…`);

    // ── Phase 2a: collect candidates across all traders ──
    // The previous version processed each trader's full sorted list in
    // turn — a single high-volume trader could fire 50+ mirrors before
    // we got to the second trader's best signal. By collecting GLOBALLY
    // and sorting, "top N" reflects the highest-conviction trades in
    // the whole lookback window regardless of which trader sent them.
    type Candidate = {
      trader: IndexTrader;
      copyRatio: number;
      trade: PolymarketTrade & { notional: number };
      tradeKey: string;
    };
    const candidates: Candidate[] = [];
    for (const trader of enabled) {
      if (!this.running) break;
      try {
        const trades = await fetchWalletTradesUntil(trader.address, cutoffSec);
        // Denominator uses the same `backtestDays` window as the backtest
        // preview and the live cycle — keeps catch-up sizing in sync.
        const denomWindow = trades.filter((t) => t.timestamp >= denomCutoffMs);
        const buyVol = denomWindow
          .filter((t) => t.side === "BUY")
          .reduce((s, t) => s + t.price * t.size, 0);
        const sellVol = denomWindow
          .filter((t) => t.side === "SELL")
          .reduce((s, t) => s + t.price * t.size, 0);
        const traderVol = Math.max(buyVol, sellVol, 1);
        const capitalAlloc = this.config.capital * (trader.weight / totalWeight);
        const copyRatio = capitalAlloc / traderVol;
        for (const t of trades) {
          if (t.side !== "BUY") continue;
          const notional = t.price * t.size;
          if (notional < opts.minNotional) continue;
          const tradeKey = `${trader.address.toLowerCase()}:${t.conditionId}:${t.timestamp}`;
          if (this.catchUpSeen.has(tradeKey)) continue;
          candidates.push({ trader, copyRatio, trade: { ...t, notional }, tradeKey });
        }
      } catch (e) {
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "ERROR",
          traderAddress: trader.address,
          reason: `CATCH UP fetch failed: ${e instanceof Error ? e.message : String(e)}`,
        });
      }
    }
    candidates.sort((a, b) => b.trade.notional - a.trade.notional);
    const picked = candidates.slice(0, topN);
    opts.onProgress?.(`${candidates.length} candidates · firing top ${picked.length}…`);

    // ── Phase 2b: place orders for the top-N picks ──
    for (const { trader, copyRatio, trade, tradeKey } of picked) {
      if (!this.running) break;
      scanned++;
      this.catchUpSeen.add(tradeKey);

      const rawMirrorNotional = trade.notional * copyRatio;
      // SKIP below floor to mirror the backtest preview (which drops dust
      // mirrors via `if (rawAmount < minTrade) continue`). Polymarket's
      // $1 CLOB hard floor is an additional skip reason on top of the
      // user's TRADE SIZE setting.
      const userFloor = this.config.minOrderSize;
      const ceiling = this.config.maxOrderSize ?? Number.POSITIVE_INFINITY;
      // CLOB floor is max($1 notional, 5 shares × price) — per-trade.
      const polymarketFloor = clobMinNotional(trade.price);
      if (ceiling < polymarketFloor) {
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "SKIP",
          traderAddress: trader.address,
          market: trade.market,
          conditionId: trade.conditionId,
          side: trade.side,
          mirrorNotional: Math.round(rawMirrorNotional * 100) / 100,
          reason: `CATCH UP · CEILING_BELOW_CLOB_FLOOR · ceiling $${ceiling.toFixed(2)} < CLOB min $${polymarketFloor.toFixed(2)} (5 shares × ${(trade.price * 100).toFixed(0)}¢)`,
        });
        skipped++;
        continue;
      }
      // Effective minimum order: larger of the user's TRADE SIZE floor and
      // the CLOB per-price hard floor. Proportional dust is clamped UP to
      // this floor (not skipped) so small-but-real leader trades still copy.
      // When the leader's OWN trade is below the CLOB floor we skip — that's
      // not real signal.
      const minNotional = Math.max(userFloor, polymarketFloor);
      let mirrorNotional: number;
      if (rawMirrorNotional < minNotional) {
        if (trade.notional < polymarketFloor) {
          this.addLog({
            id: uid(),
            timestamp: Date.now(),
            type: "SKIP",
            traderAddress: trader.address,
            market: trade.market,
            conditionId: trade.conditionId,
            side: trade.side,
            mirrorNotional: Math.round(rawMirrorNotional * 100) / 100,
            reason: `CATCH UP · LEADER_DUST · leader $${trade.notional.toFixed(2)} < Polymarket $${polymarketFloor.toFixed(2)} hard floor (5 shares × ${(trade.price * 100).toFixed(0)}¢)`,
          });
          skipped++;
          continue;
        }
        mirrorNotional = Math.min(minNotional, ceiling);
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "BALANCE",
          traderAddress: trader.address,
          market: trade.market,
          conditionId: trade.conditionId,
          side: trade.side,
          mirrorNotional: Math.round(mirrorNotional * 100) / 100,
          reason: `CATCH UP · clamped up: proportional $${rawMirrorNotional < 0.01 ? rawMirrorNotional.toExponential(2) : rawMirrorNotional.toFixed(2)} → $${mirrorNotional.toFixed(2)} (min order: max floor $${userFloor.toFixed(2)}, CLOB $${polymarketFloor.toFixed(2)})`,
        });
      } else {
        mirrorNotional = Math.min(rawMirrorNotional, ceiling);
      }
      // Whole shares for the 1¢ tick grid — see executeCycle for rationale.
      // Enforce the 5-share CLOB floor here too as a defensive backstop.
      const mirrorSize = Math.max(
        POLYMARKET_MIN_SHARES,
        Math.ceil(mirrorNotional / Math.max(trade.price, 1e-9)),
      );

      const tokenId = await this.resolveTokenId(
        trade.conditionId,
        (trade.outcome as string | undefined) || "Yes",
      );
      if (!tokenId) {
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "SKIP",
          traderAddress: trader.address,
          market: trade.market,
          conditionId: trade.conditionId,
          side: trade.side,
          reason: "CATCH_UP · TOKEN_ID_NOT_FOUND",
        });
        skipped++;
        continue;
      }

      try {
        const signer = await this.getSigner();
        const negRisk = await this.resolveNegRisk(trade.conditionId);
        const proxy =
          this.proxyAddress ?? (this.proxyAddress = await getProxyAddress(this.config.address));
        const maker = this.sigType === 0 ? this.config.address : proxy;
        const result = await placeOrder(
          this.config.creds,
          signer,
          maker,
          {
            tokenID: tokenId,
            price: tickRoundPrice(trade.price),
            size: Math.round(mirrorSize * 100) / 100,
            side: trade.side,
            type: "GTC",
            feeRateBps: TAKER_FEE_BPS,
          },
          negRisk,
          this.sigType,
        );
        // Classify before logging — a FOK that the book couldn't fully
        // fill at the requested price/size is a market-state SKIP, not an
        // ERROR. Tagging it ERROR (and halting the loop) used to mean one
        // thin market killed the whole catch-up batch.
        const errMsg = (result.errorMsg ?? "").toLowerCase();
        const isThinBookSkip = !result.success && (
          errMsg.includes("couldn't be fully filled") ||
          errMsg.includes("could not be fully filled") ||
          errMsg.includes("fok orders are fully filled or killed")
        );
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: result.success
            ? trade.side === "BUY"
              ? "COPY_BUY"
              : "COPY_SELL"
            : isThinBookSkip
              ? "SKIP"
              : "ERROR",
          traderAddress: trader.address,
          market: trade.market,
          conditionId: trade.conditionId,
          tokenId,
          side: trade.side,
          traderSize: trade.size,
          mirrorSize: Math.round(mirrorSize * 100) / 100,
          mirrorNotional: Math.round(mirrorNotional * 100) / 100,
          price: trade.price,
          orderResult: result,
          reason: result.success
            ? `CATCH UP · top pick · $${trade.notional.toFixed(0)} notional`
            : isThinBookSkip
              ? `CATCH UP · THIN_BOOK · FOK kill — book didn't have full size at @${(trade.price * 100).toFixed(0)}¢`
              : (result.errorMsg ?? "REJECTED"),
        });
        if (result.success) {
          placed++;
          consecutiveFailures = 0;
          this.setState({
            totalOrdersPlaced: this.state.totalOrdersPlaced + 1,
            totalVolumeMirrored: this.state.totalVolumeMirrored + mirrorNotional,
          });
          opts.onProgress?.(`placed ${placed}/${picked.length}…`);
          await delay(ORDER_DELAY_MS);
        } else if (isThinBookSkip) {
          // Book moved or partial liquidity — don't tick failed /
          // consecutiveFailures, keep walking the picked list.
          skipped++;
          opts.onProgress?.(`placed ${placed} · skipped ${skipped} (thin book) · failed ${failed}`);
          continue;
        } else {
          failed++;
          consecutiveFailures++;
          this.setState({
            totalOrdersFailed: this.state.totalOrdersFailed + 1,
          });
          opts.onProgress?.(`placed ${placed} · failed ${failed} · ${result.errorMsg ?? "rejected"}`);
          const msg = errMsg;
          // True fatal signals — account/auth/payload-shape problems that
          // re-trying the next trade can't help. A bare "http 400" used to
          // be in here but the CLOB returns 400 for transient book issues
          // too, so we now only halt on explicit account/auth failures.
          const isFatal =
            msg.includes("insufficient_balance") ||
            msg.includes("unauthenticated") ||
            msg.includes("backend_signer_not_authorized") ||
            msg.includes("invalid order payload") ||
            msg.includes("invalid_signature");
          if (isFatal || consecutiveFailures >= 5) {
            const reason = isFatal
              ? `CATCH UP halted (fatal): ${result.errorMsg}`
              : `CATCH UP halted after ${consecutiveFailures} consecutive failures: ${result.errorMsg}`;
            this.addLog({
              id: uid(),
              timestamp: Date.now(),
              type: "ERROR",
              reason,
            });
            this.setState({ error: result.errorMsg ?? null });
            return { scanned, placed, failed, skipped, sold };
          }
        }
      } catch (e) {
        failed++;
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "ERROR",
          traderAddress: trader.address,
          market: trade.market,
          reason: e instanceof Error ? e.message : String(e),
        });
      }
    }

    this.addLog({
      id: uid(),
      timestamp: Date.now(),
      type: "CYCLE_END",
      reason: `CATCH UP done · sold ${sold} winners · ${candidates.length} candidates → top ${picked.length} · placed ${placed} · failed ${failed} · skipped ${skipped}`,
    });
    return { scanned, placed, failed, skipped, sold };
  }

  // Per-session dedup for catch-up — keeps a single click from
  // re-evaluating the same trade twice if the user keeps the panel open.
  private catchUpSeen = new Set<string>();

  // ── Free liquidity (auto-sell winning open positions) ──────────
  //
  // Closes the top-N profitable positions at current market price so the
  // proxy gets fresh USDC to back the next round of copy buys. Used by:
  //   • CATCH UP (manual, sellWinners toggle)
  //   • executeCycle (auto, triggered when balance is below the gate AND
  //     we have at least one new BUY candidate this cycle — i.e. an
  //     opportunity worth freeing capital for)
  //
  // Returns the count of winners actually filled + total USD freed so
  // callers can decide whether to retry the balance check.
  private async freeLiquidity(opts: {
    maxToSell?: number;
    reasonPrefix?: string;
    onProgress?: (msg: string) => void;
    /** When true (default), if no winners are available we'll fall back to
     *  selling the WORST loser to clear stuck capital — better to take a
     *  realized loss on a dead-weight position than miss every new
     *  candidate because cash is frozen in a slow market. Set false to
     *  preserve the legacy "winners only" behavior. */
    sellLosersIfNoWinners?: boolean;
    /** EP-driven rotation mode. When set, only positions whose FORWARD
     *  expected profit clears the churn guard against this new-buy EP are
     *  sold — lowest forward-EP first. This replaces the pnl-based winner
     *  selection above. The new buy must beat a position's forward EP by
     *  the round-trip fee + 20% before we rotate it (avoids fee churn). */
    targetEP?: number;
    /** Stop selling once this much USD has been freed (EP mode). */
    fundingNeededUsd?: number;
  } = {}): Promise<{ sold: number; freedUsd: number }> {
    const maxToSell = opts.maxToSell ?? Number.POSITIVE_INFINITY;
    const reasonPrefix = opts.reasonPrefix ?? "FREE LIQUIDITY";
    const allowLosers = opts.sellLosersIfNoWinners ?? true;
    let sold = 0;
    let freedUsd = 0;
    try {
      const proxy =
        this.proxyAddress ?? (this.proxyAddress = await getProxyAddress(this.config.address));
      const positions = await fetchPositions(proxy);
      let candidates: PolymarketPosition[];
      let kindLabel: string;
      if (opts.targetEP != null) {
        // ── EP-driven rotation ──
        // Rank open positions by forward expected profit (lowest first) and
        // keep only those the new buy beats by the churn guard. The guard:
        //   targetEP ≥ (forwardEP + roundTripFee) × 1.20
        // round-trip fee = exit fee on this position + entry fee on the
        // replacement buy. TAKER_FEE_BPS is 0 today, so this reduces to a
        // 20% margin over forward EP — kept general for when fees return.
        const feeRate = TAKER_FEE_BPS / 10_000;
        const ranked = positions
          .filter((p) => p.size > 0)
          .map((p) => {
            const stored = this.positionEP[this.posKey(p.conditionId, p.outcome)];
            const forwardEP = stored ? Math.max(0, stored.entryEP - p.pnlUsd) : 0;
            const roundTripFee =
              2 * feeRate * p.value + feeRate * (opts.fundingNeededUsd ?? p.value);
            const hurdle = (forwardEP + roundTripFee) * 1.2;
            return { p, forwardEP, hurdle };
          })
          .filter((x) => opts.targetEP! >= x.hurdle)
          .sort((a, b) => a.forwardEP - b.forwardEP || b.p.pnlUsd - a.p.pnlUsd);
        candidates = ranked.map((x) => x.p);
        kindLabel = ranked.length > 0
          ? `${ranked.length} positions below new-buy EP $${opts.targetEP.toFixed(2)} (forward EP ≤ $${ranked[ranked.length - 1].forwardEP.toFixed(2)}) → rotating`
          : `no open position cheaper than new-buy EP $${opts.targetEP.toFixed(2)} — holding all`;
      } else {
        const winners = positions
          .filter((p) => p.size > 0 && p.pnlUsd > 0)
          .sort((a, b) => b.pnlUsd - a.pnlUsd);
        // No winners → try the worst loser (largest negative PnL). Realizing
        // the loss + recycling the capital into a fresh signal is usually
        // higher EV than holding dead weight that's been bleeding for a while.
        const losers = winners.length === 0 && allowLosers
          ? positions
              .filter((p) => p.size > 0 && p.pnlUsd <= 0)
              .sort((a, b) => a.pnlUsd - b.pnlUsd)
              .slice(0, 1)
          : [];
        candidates = winners.length > 0 ? winners : losers;
        kindLabel = winners.length > 0
          ? `${winners.length} winning positions`
          : losers.length > 0
            ? `0 winners — rotating worst loser ($${losers[0].pnlUsd.toFixed(2)} PnL)`
            : `nothing to sell`;
      }
      opts.onProgress?.(`${kindLabel} → selling`);
      for (const pos of candidates) {
        if (!this.running) break;
        if (sold >= maxToSell) break;
        try {
          const tokenId = await this.resolveTokenId(pos.conditionId, pos.outcome || "Yes");
          if (!tokenId) {
            this.addLog({
              id: uid(),
              timestamp: Date.now(),
              type: "SKIP",
              market: pos.market,
              conditionId: pos.conditionId,
              side: "SELL",
              reason: `${reasonPrefix} · TOKEN_ID_NOT_FOUND for ${pos.outcome}`,
            });
            continue;
          }
          const signer = await this.getSigner();
          const negRisk = await this.resolveNegRisk(pos.conditionId);
          const maker = this.sigType === 0 ? this.config.address : proxy;
          const sellPrice = tickRoundPrice(pos.currentPrice);
          const result = await placeOrder(
            this.config.creds,
            signer,
            maker,
            {
              tokenID: tokenId,
              price: sellPrice,
              size: Math.round(pos.size * 100) / 100,
              side: "SELL",
              type: "GTC",
              feeRateBps: TAKER_FEE_BPS,
            },
            negRisk,
            this.sigType,
          );
          const notional = Math.round(pos.size * sellPrice * 100) / 100;
          this.addLog({
            id: uid(),
            timestamp: Date.now(),
            type: result.success ? "COPY_SELL" : "ERROR",
            market: pos.market,
            conditionId: pos.conditionId,
            tokenId,
            side: "SELL",
            traderSize: pos.size,
            mirrorSize: pos.size,
            mirrorNotional: notional,
            price: sellPrice,
            orderResult: result,
            reason: result.success
              ? `${reasonPrefix} · +$${pos.pnlUsd.toFixed(2)} P&L at @${(sellPrice * 100).toFixed(0)}¢`
              : (result.errorMsg ?? "REJECTED"),
          });
          if (result.success) {
            sold++;
            freedUsd += notional;
            // Position closed — drop it from the EP ledger so a re-open
            // starts a fresh entry EP.
            delete this.positionEP[this.posKey(pos.conditionId, pos.outcome)];
            this.savePositionEP();
            opts.onProgress?.(`sold ${sold} · freed $${freedUsd.toFixed(2)}`);
            await delay(ORDER_DELAY_MS);
            // EP mode: stop as soon as we've freed enough to fund the buy.
            if (opts.fundingNeededUsd != null && freedUsd >= opts.fundingNeededUsd) break;
          }
        } catch (e) {
          this.addLog({
            id: uid(),
            timestamp: Date.now(),
            type: "ERROR",
            market: pos.market,
            conditionId: pos.conditionId,
            reason: `${reasonPrefix} sell failed: ${e instanceof Error ? e.message : String(e)}`,
          });
        }
      }
    } catch (e) {
      this.addLog({
        id: uid(),
        timestamp: Date.now(),
        type: "ERROR",
        reason: `${reasonPrefix} fetch failed: ${e instanceof Error ? e.message : String(e)}`,
      });
    }
    return { sold, freedUsd };
  }

  // ── Core Cycle ───────────────────────────────────────────────

  private async executeCycle(): Promise<void> {
    if (this.cycleInFlight) return;
    this.cycleInFlight = true;
    const cycleId = uid();
    this.addLog({
      id: cycleId,
      timestamp: Date.now(),
      type: "CYCLE_START",
    });

    // Outer try/finally guarantees the cycle counters advance even when the
    // body early-returns (INSUFFICIENT_BALANCE, zero-weight) or throws.
    // Previously these paths left lastCycleAt: null and cycleCount: 0
    // forever, so the LIVE panel looked permanently frozen at "CYCLES 0 ·
    // LAST SYNC never · NEXT IN NOW" with no visible cause. The user
    // assumed the engine was stalled when it had actually run + failed.
    try {
      // V2 trading routes through the deposit wallet (POLY_1271,
      // sigType=3) auto-derived from the backend signer EOA. Pull its
      // address + on-chain V2 collateral balance from the backend — the
      // old V1 Safe path (`getProxyAddress` + USDC.e balance) is dead
      // for matching purposes now and will always report $0 even when
      // the deposit wallet is fully funded.
      let depositWallet: string | null = null;
      let onchainProxyBal = 0;
      // `null` balance = the read failed (backend/RPC unreachable), which is
      // NOT the same as a confirmed $0. Coercing a failed read to 0 was the
      // phantom "wallet empty / deposit now" bug — a flaky Polygon RPC made
      // a funded wallet look drained. Track unknown-ness explicitly.
      let balanceKnown = false;
      try {
        const r = await fetch(
          `/api/polymarket/deposit-wallet/info?eoa=${this.config.address}`,
          { cache: "no-store" },
        );
        if (r.ok) {
          const j = (await r.json()) as {
            depositWallet?: string;
            usdcBalance?: string | null;
            balanceUnavailable?: boolean;
          };
          if (j.depositWallet) depositWallet = j.depositWallet;
          // Balance is known only when the backend actually returned one.
          // `balanceUnavailable` or a null/missing usdcBalance = RPC read
          // failed upstream → keep last-known, don't trade on a phantom 0.
          if (!j.balanceUnavailable && j.usdcBalance != null) {
            onchainProxyBal = Number(j.usdcBalance) / 1_000_000;
            balanceKnown = Number.isFinite(onchainProxyBal);
          }
        }
      } catch {}
      this.proxyAddress = depositWallet ?? this.proxyAddress;
      // We're always on sigType=3 now (backend overrides regardless of
      // what we send), but keep the field set so downstream log shapes
      // stay consistent.
      // Vestigial — backend always overrides to POLY_1271.
      this.sigType = 3 as 0 | 1 | 2 | 3;

      // ── Balance read failed → skip cycle, keep last-known balance ──
      // Don't zero out state.balance (that flips the UI to "$0.00 / empty"
      // and fires the funding banner) and don't run the trade pipeline on
      // an unknown balance. The next cycle retries; the RPC fallback in the
      // backend makes this rare, but a total outage shouldn't masquerade as
      // an empty wallet.
      if (!balanceKnown) {
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "BALANCE",
          reason:
            `Balance read unavailable (backend/RPC unreachable) — skipping cycle, ` +
            `keeping last known $${(this.state.balance ?? 0).toFixed(2)}. Will retry next cycle.`,
        });
        return;
      }

      const effective = onchainProxyBal;
      this.setState({ balance: effective });
      const balDetail =
        `$${effective.toFixed(2)} usable · V2 deposit wallet $${onchainProxyBal.toFixed(2)}`;
      this.addLog({
        id: uid(),
        timestamp: Date.now(),
        type: "BALANCE",
        reason: balDetail,
      });

      if (effective < this.config.capital * 0.05) {
        // ── Auto free-liquidity (EP-driven rotation) ──
        // Before erroring out, free cash by SELLING held positions — but only
        // to fund a NEW buy that's genuinely better. Fires when:
        //   (a) balance dipped below the 5%-of-capital gate, AND
        //   (b) there's a pending watched-trader BUY with positive expected
        //       profit (EP = roi × mirror$).
        // We find the single highest-EP pending buy, then rotate out only the
        // positions whose FORWARD expected profit is below it by the churn
        // guard (round-trip fee + 20%) — lowest forward-EP first. This sells
        // matured / low-edge positions to chase higher-edge ones instead of
        // blindly dumping the biggest winner.
        const gateTotalWeight = this.config.traders
          .filter((t) => t.enabled !== false)
          .reduce((s, t) => s + t.weight, 0);
        const bestCandidate = await this.bestBuyCandidateEP(gateTotalWeight);
        const hasHighConvictionCandidate = bestCandidate != null;

        let freedAnything = false;
        if (bestCandidate) {
          // Free enough to clear the gate AND cover the new buy's size.
          const fundingNeeded = Math.max(
            this.config.capital * 0.05 - effective,
            bestCandidate.mirrorNotional,
          );
          this.addLog({
            id: uid(),
            timestamp: Date.now(),
            type: "BALANCE",
            reason: `Balance $${effective.toFixed(2)} < 5% gate · best pending BUY EP $${bestCandidate.ep.toFixed(2)} ("${bestCandidate.market}") → rotating lower-EP positions to free ~$${fundingNeeded.toFixed(2)}`,
          });
          const { sold, freedUsd } = await this.freeLiquidity({
            maxToSell: 3,
            reasonPrefix: "EP ROTATION",
            targetEP: bestCandidate.ep,
            fundingNeededUsd: fundingNeeded,
          });
          if (sold > 0) {
            freedAnything = true;
            // Re-check the V2 deposit wallet balance — the SELL settles
            // atomically on Polymarket so the wallet's V2-collateral
            // bumps before this returns.
            try {
              const r = await fetch(
                `/api/polymarket/deposit-wallet/info?eoa=${this.config.address}`,
                { cache: "no-store" },
              );
              if (r.ok) {
                const j = (await r.json()) as { usdcBalance?: string };
                if (j.usdcBalance) onchainProxyBal = Number(j.usdcBalance) / 1_000_000;
              }
            } catch {}
            const newEffective = onchainProxyBal;
            this.setState({ balance: newEffective });
            this.addLog({
              id: uid(),
              timestamp: Date.now(),
              type: "BALANCE",
              reason: `Freed $${freedUsd.toFixed(2)} · wallet now $${onchainProxyBal.toFixed(2)} → continuing cycle`,
            });
            // If the freed amount lifts us above the gate, fall through to
            // the normal BUY pipeline. Otherwise still error out — the
            // next cycle will try another sell.
            if (newEffective >= this.config.capital * 0.05) {
              // Continue normal processing
            } else {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "ERROR",
                reason: `Still below gate after freeing $${freedUsd.toFixed(2)} — next cycle will free more.`,
              });
              this.setState({ error: "INSUFFICIENT_BALANCE" });
              return;
            }
          }
        }

        if (!freedAnything) {
          const proxy = this.proxyAddress ?? "(deriving…)";
          const proxyShort = proxy.length >= 10
            ? `${proxy.slice(0, 6)}…${proxy.slice(-4)}`
            : proxy;
          const need = this.config.capital * 0.05;
          const reason = !hasHighConvictionCandidate
            ? `Balance $${effective.toFixed(2)} < 5% gate; no positive-EP BUY candidates → idling without freeing liquidity. Will retry next cycle.`
            : `Balance $${effective.toFixed(2)} < 5% gate and every open position has higher forward EP than the best pending buy ($${bestCandidate!.ep.toFixed(2)}) — holding rather than rotating into a worse trade. Trading wallet ${proxyShort} has $${onchainProxyBal.toFixed(2)} (need ≥$${need.toFixed(2)}); open the TRADING WALLET panel and DEPOSIT to add fresh capital.`;
          this.addLog({
            id: uid(),
            timestamp: Date.now(),
            type: hasHighConvictionCandidate ? "ERROR" : "BALANCE",
            reason,
          });
          // Don't transition to status="error" for the no-candidate case
          // — we want the engine to keep polling. Only real funding gaps
          // (with a candidate present) escalate to an error state.
          if (hasHighConvictionCandidate) {
            this.setState({ error: "INSUFFICIENT_BALANCE" });
          }
          return;
        }
      }

      const enabledTraders = this.config.traders.filter((t) => t.enabled !== false);
      const totalWeight = enabledTraders.reduce((s, t) => s + t.weight, 0);
      if (totalWeight <= 0) return;

      let ordersThisCycle = 0;
      let pollFailures = 0;
      let tradersWithNewActivity = 0;
      let totalNewTradesSeen = 0;

      // ── Phase 1: collect candidates across all traders ──
      // We split observation from execution so we can score every BUY
      // candidate globally and copy only the top N — the single biggest
      // fee-control lever. SELLs are always honored (they close positions
      // we already hold; skipping them strands capital).
      type Candidate = {
        trader: IndexTrader;
        trade: PolymarketTrade;
        traderNotional: number;
        rawMirrorNotional: number;
        copyRatio: number;
        // Filled in scoring phase:
        score: number;
        sharpe: number;
        stats: TraderRoiStats | null;
      };
      const buyCandidates: Candidate[] = [];
      const sellCandidates: Candidate[] = [];

      for (const trader of enabledTraders) {
        if (!this.running) break;

        const addr = trader.address.toLowerCase();
        // First-cycle cursor defaults to one poll interval ago (NOT now —
        // a `Date.now()` default makes `t.timestamp > cursor` reject every
        // trade in the lookup window, so the engine sees nothing until the
        // SECOND cycle. The 60s overlap below additionally guards against
        // boundary races where a trade lands microseconds before sync.
        const cursor = this.state.traderCursors[addr]
          || Date.now() - this.config.intervalMs;
        const untilTs = Math.floor((cursor - 60_000) / 1000);

        try {
          // INCREMENTAL, not cache-first. `fetchWalletTradesUntil` returns the
          // hourly cache verbatim when present, so at 5s polling the engine
          // re-saw the same frozen snapshot for the whole clock-hour and never
          // observed a fill newer than the start cursor → zero copies. The
          // incremental path pages only the new tail off the API, merges it
          // into the cache, and returns it so `t.timestamp > cursor` can match.
          const trades = await fetchWalletTradesIncremental(
            trader.address,
            getTradeCache(trader.address) ?? [],
            untilTs,
          );
          // Stamp the per-trader last-sync time the moment the fetch resolves
          // — empty result = "they're not trading", still a successful sync.
          this.setState({
            traderLastSync: {
              ...this.state.traderLastSync,
              [addr]: Date.now(),
            },
          });

          const newTrades = trades
            .filter((t) => t.timestamp > cursor && !this.copiedIds.has(t.id));

          if (newTrades.length === 0) continue;
          tradersWithNewActivity++;
          totalNewTradesSeen += newTrades.length;

          // Per-trade sizing (proportional) — must match the BACKTEST tab.
          //   scale = (capital * weight/totalWeight) / max(buyVol, sellVol, 1)
          // over trades within the backtestDays window.
          const backtestDays = this.config.backtestDays ?? 3;
          const windowCutoffMs = Date.now() - backtestDays * 86400_000;
          const windowTrades = trades.filter((t) => t.timestamp >= windowCutoffMs);
          const buyVol = windowTrades
            .filter((t) => t.side === "BUY")
            .reduce((s, t) => s + t.price * t.size, 0);
          const sellVol = windowTrades
            .filter((t) => t.side === "SELL")
            .reduce((s, t) => s + t.price * t.size, 0);
          const traderVol = Math.max(buyVol, sellVol, 1);
          const capitalAlloc = this.config.capital * (trader.weight / totalWeight);
          const copyRatio = capitalAlloc / traderVol;

          // Push every observed trade into the UI ring buffer with its
          // score, even ones we won't copy — gives the user a real view
          // of "what we saw vs what we picked".
          const observed: ObservedTrade[] = newTrades.map((t) => {
            const stratTrade = this.buildStratTrade(t, trader, copyRatio, totalWeight);
            const sc = t.side === "BUY"
              ? this.scoreStratTrade(stratTrade)
              : { score: 0, sharpe: 0, stats: null };
            return {
              id: t.id,
              timestamp: t.timestamp,
              trader: trader.address,
              market: t.market,
              conditionId: t.conditionId,
              side: t.side,
              size: t.size,
              price: t.price,
              notional: stratTrade.notional,
              score: sc.score,
              sharpe: sc.sharpe,
            };
          });
          const RECENT_TRADES_LIMIT = 500;
          const merged = [...observed, ...this.state.observedTrades]
            .sort((a, b) => b.timestamp - a.timestamp)
            .slice(0, RECENT_TRADES_LIMIT);
          this.setState({ observedTrades: merged });

          // Bucket into candidates with their proportional sizing.
          for (const trade of newTrades) {
            const stratTrade = this.buildStratTrade(trade, trader, copyRatio, totalWeight);
            // Strat pre-filter — runs before scoring/sizing per the Strat
            // contract (base.ts). Default CopyTrader passes everything.
            if (!this.strat.shouldMirror(stratTrade)) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "SKIP",
                traderAddress: trader.address,
                market: trade.market,
                conditionId: trade.conditionId,
                side: trade.side,
                reason: "STRAT_FILTERED · shouldMirror returned false",
                upstreamTradeId: trade.id,
              });
              this.copiedIds.add(trade.id);
              continue;
            }
            const sc = this.scoreStratTrade(stratTrade);
            const cand: Candidate = {
              trader,
              trade,
              traderNotional: stratTrade.notional,
              rawMirrorNotional: stratTrade.notional * copyRatio,
              copyRatio,
              score: sc.score,
              sharpe: sc.sharpe,
              stats: sc.stats,
            };
            if (trade.side === "BUY") buyCandidates.push(cand);
            else sellCandidates.push(cand);
          }

          // Advance cursor regardless of whether we end up copying any of
          // these trades — top-N skips should NOT make us re-evaluate the
          // same trade next cycle (we'd just keep skipping it).
          const latestTs = Math.max(...newTrades.map((t) => t.timestamp));
          if (latestTs > cursor) {
            this.state.traderCursors[addr] = latestTs;
          }
        } catch (e) {
          pollFailures++;
          this.addLog({
            id: uid(),
            timestamp: Date.now(),
            type: "ERROR",
            traderAddress: trader.address,
            reason: `FETCH_FAILED: ${e instanceof Error ? e.message : String(e)}`,
          });
        }
      }

      // ── Phase 2: rank BUYs by EXPECTED PROFIT and slice top N ──
      // SELLs always execute. BUYs compete for the strat's maxPerCycle
      // budget (a CopyTrader knob, overridable per-strat). score = EP in
      // dollars (roi × mirror$). Non-positive-EP buys never execute — no
      // edge means no reason to spend capital or rotate a position for them.
      const maxPerCycle = this.strat.maxPerCycle();
      buyCandidates.sort((a, b) => b.score - a.score);
      const positiveEP = buyCandidates.filter((c) => c.score > 0);
      const selectedBuys = positiveEP.slice(0, maxPerCycle);
      const selectedSet = new Set(selectedBuys);
      const skippedBuys = buyCandidates.filter((c) => !selectedSet.has(c));

      // Log every loser explicitly — the whole point of this feature is
      // transparency. The user should see WHY each skipped trade lost.
      for (let i = 0; i < skippedBuys.length; i++) {
        const c = skippedBuys[i];
        const roiPct = c.stats ? (c.stats.roi * 100).toFixed(1) : "n/a";
        const reason = c.score > 0
          ? `EP_RANK · EP $${c.score.toFixed(2)} (roi ${roiPct}% × mirror $${c.rawMirrorNotional.toFixed(2)}) — below top ${maxPerCycle} of ${positiveEP.length} positive-EP buys`
          : c.stats
            ? `NO_EDGE · EP $${c.score.toFixed(2)} ≤ 0 (roi ${roiPct}%) — trader has no positive expected profit`
            : `NO_STATS · ROI not loaded yet — skipped pending stats`;
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "SKIP",
          traderAddress: c.trader.address,
          market: c.trade.market,
          conditionId: c.trade.conditionId,
          side: c.trade.side,
          reason,
          upstreamTradeId: c.trade.id,
          score: c.score,
          sharpe: c.sharpe,
        });
        this.copiedIds.add(c.trade.id);
      }

      // ── Phase 3: execute selected candidates ──
      // Sells first so freed capital is available for the chosen buys.
      const toExecute: Candidate[] = [...sellCandidates, ...selectedBuys];

      for (const cand of toExecute) {
        if (ordersThisCycle >= MAX_ORDERS_PER_CYCLE) break;
        if (!this.running) break;

        const trader = cand.trader;
        const trade = cand.trade;
        const traderNotional = cand.traderNotional;
        const rawMirrorNotional = cand.rawMirrorNotional;

            // ── Strat-driven sizing ──
            // Engine assembles the per-trade context + constraints;
            // strat returns final notional + limit price + an optional
            // log reason for clamps/skips. Same builder as scoring.
            const stratTrade = this.buildStratTrade(trade, trader, cand.copyRatio, totalWeight);
            const constraints: SizeConstraints = {
              userFloor: this.config.minOrderSize,
              userCeiling: this.config.maxOrderSize ?? Number.POSITIVE_INFINITY,
              clobFloor: clobMinNotional(trade.price),
              capital: this.config.capital,
            };
            const decision = this.strat.sizeAndPrice(stratTrade, constraints);
            if (decision.mirrorNotional <= 0) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "SKIP",
                traderAddress: trader.address,
                market: trade.market,
                conditionId: trade.conditionId,
                side: trade.side,
                mirrorNotional: Math.round(rawMirrorNotional * 100) / 100,
                reason: decision.reason ?? "STRAT_SKIPPED",
                upstreamTradeId: trade.id,
              });
              this.copiedIds.add(trade.id);
              continue;
            }
            const mirrorNotional = decision.mirrorNotional;
            // Ceil to WHOLE shares so makerAmount/takerAmount ratio lands
            // on the 1¢ tick. Defensive 5-share floor protects against
            // edge cases where a strat returned notional < clob floor.
            const mirrorSize = Math.max(
              POLYMARKET_MIN_SHARES,
              Math.ceil(mirrorNotional / Math.max(trade.price, 1e-9)),
            );
            if (decision.reason) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "BALANCE",
                traderAddress: trader.address,
                market: trade.market,
                reason: decision.reason,
                upstreamTradeId: trade.id,
              });
            }

            // Resolve token ID
            const tokenId = await this.resolveTokenId(
              trade.conditionId,
              trade.outcome || "Yes",
            );
            if (!tokenId) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "SKIP",
                traderAddress: trader.address,
                market: trade.market,
                conditionId: trade.conditionId,
                side: trade.side,
                reason: "TOKEN_ID_NOT_FOUND",
                upstreamTradeId: trade.id,
              });
              this.copiedIds.add(trade.id);
              continue;
            }

            // Place order — maker is the Polymarket Proxy (funds live
            // there), signer is the connected EOA, signatureType matches
            // what Polymarket's apiKey is bound to (auto-detected above).
            try {
              const signer = await this.getSigner();
              const negRisk = await this.resolveNegRisk(trade.conditionId);
              const proxy = this.proxyAddress ?? (this.proxyAddress = await getProxyAddress(this.config.address));
              // For sigType=0 (EOA), maker = EOA. For 1/2, maker = proxy.
              const maker = this.sigType === 0 ? this.config.address : proxy;
              const result = await placeOrder(
                this.config.creds,
                signer,
                maker,
                {
                  tokenID: tokenId,
                  // Use the strat's limit price (already tick-rounded
                  // by the strat). For BUYs the default CopyTrader widens
                  // toward fillable by `slippageBps`; SELLs widen down.
                  price: decision.limitPrice,
                  size: Math.round(mirrorSize * 100) / 100,
                  side: trade.side,
                  type: "GTC",
                  feeRateBps: TAKER_FEE_BPS,
                },
                negRisk,
                this.sigType,
              );

              const logType = result.success
                ? (trade.side === "BUY" ? "COPY_BUY" : "COPY_SELL")
                : "ERROR";

              const roiPct = cand.stats ? (cand.stats.roi * 100).toFixed(1) : "n/a";
              const scoreSuffix = trade.side === "BUY"
                ? `EP $${cand.score.toFixed(2)} (roi ${roiPct}% × mirror $${rawMirrorNotional.toFixed(2)})`
                : null;
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: logType as ExecutionLogEntry["type"],
                traderAddress: trader.address,
                market: trade.market,
                conditionId: trade.conditionId,
                tokenId,
                side: trade.side,
                traderSize: trade.size,
                mirrorSize: Math.round(mirrorSize * 100) / 100,
                mirrorNotional: Math.round(mirrorNotional * 100) / 100,
                price: trade.price,
                orderResult: result,
                reason: !result.success
                  ? (result.errorMsg ?? "REJECTED")
                  : (scoreSuffix ?? undefined),
                upstreamTradeId: trade.id,
                score: cand.score,
                sharpe: cand.sharpe,
              });

              if (result.success) {
                this.setState({
                  totalOrdersPlaced: this.state.totalOrdersPlaced + 1,
                  totalVolumeMirrored: this.state.totalVolumeMirrored + mirrorNotional,
                });
                // Record / clear the EP ledger so rotation can reason about
                // this position's forward expected profit later.
                const key = this.posKey(trade.conditionId, trade.outcome || "Yes");
                if (trade.side === "BUY") {
                  this.positionEP[key] = {
                    entryEP: Math.max(0, cand.score),
                    mirrorNotional,
                  };
                } else {
                  delete this.positionEP[key];
                }
                this.savePositionEP();
              } else {
                this.setState({
                  totalOrdersFailed: this.state.totalOrdersFailed + 1,
                });
              }

              this.copiedIds.add(trade.id);
              ordersThisCycle++;

              // Rate limit between orders
              await delay(ORDER_DELAY_MS);
            } catch (e) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "ERROR",
                traderAddress: trader.address,
                market: trade.market,
                conditionId: trade.conditionId,
                side: trade.side,
                reason: e instanceof Error ? e.message : String(e),
                upstreamTradeId: trade.id,
              });
              // Count the failure so the ORDERS stat shows it.
              this.setState({
                totalOrdersFailed: this.state.totalOrdersFailed + 1,
              });
              this.copiedIds.add(trade.id);
            }
      }

      this.setState({
        lastCycleAt: Date.now(),
        cycleCount: this.state.cycleCount + 1,
        nextCycleAt: Date.now() + this.config.intervalMs,
      });

      // Build a heartbeat summary so even quiet cycles (no trader activity)
      // produce a visible log line — otherwise the user sees long stretches
      // of "BAL → END 0 orders" with no signal that the engine is alive.
      const summaryParts = [
        `polled ${enabledTraders.length} traders`,
        `${tradersWithNewActivity} active`,
        `${totalNewTradesSeen} new trades`,
        `${ordersThisCycle} orders`,
      ];
      if (pollFailures > 0) summaryParts.push(`${pollFailures} fetch errors`);

      this.addLog({
        id: uid(),
        timestamp: Date.now(),
        type: "CYCLE_END",
        reason: summaryParts.join(" · "),
      });

      // Persist
      this.saveCursors();
      this.saveLog();
      this.pruneDedup();
    } catch (e) {
      const raw = e instanceof Error ? e.message : String(e);
      // 401 from balance-allowance almost always means the in-memory CLOB
      // creds no longer match the active wallet (account switched in
      // MetaMask, or page was reloaded and only partial state restored).
      // Surface a clear "click AUTHENTICATE again" prompt instead of the
      // raw HTTP error, which doesn't tell the user what to do.
      const is401 = /HTTP 401|Unauthorized|Invalid api key/i.test(raw);
      const reason = is401
        ? `CLOB rejected the request (401). Your saved API key doesn't match the active wallet — disconnect & reconnect in MetaMask, then click AUTHENTICATE again to mint fresh creds.`
        : raw;
      // Don't transition to status="error" — that halts the setInterval
      // (LivePanel only fires cycles while status === "running"). For a
      // transient cycle failure we want the engine to keep retrying and
      // the user to see the most recent error inline. INSUFFICIENT_BALANCE
      // / UNAUTHENTICATED still need surfacing, but as state.error rather
      // than a hard stop.
      this.setState({ error: is401 ? "UNAUTHENTICATED" : raw });
      this.addLog({
        id: uid(),
        timestamp: Date.now(),
        type: "ERROR",
        reason,
      });
    } finally {
      // Always advance the cycle counters, even on early return or throw,
      // so the UI shows the engine IS firing. Without this the LIVE panel
      // shows "CYCLES 0 · LAST SYNC never · NEXT IN NOW" forever and the
      // user can't tell whether the engine is running or wedged.
      this.setState({
        lastCycleAt: Date.now(),
        cycleCount: this.state.cycleCount + 1,
        nextCycleAt: Date.now() + this.config.intervalMs,
      });
      this.cycleInFlight = false;
    }
  }

  // ── Token ID Resolution ──────────────────────────────────────

  private async resolveTokenId(conditionId: string, outcome: string): Promise<string | null> {
    const cached = this.tokenMap.get(conditionId);
    if (cached && cached.length >= 2) {
      const idx = outcome.toLowerCase() === "no" ? 1 : 0;
      return cached[idx] || null;
    }

    // Try fetching market data from API
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || "/polymarket/api";
      const res = await fetch(`${API_URL}?endpoint=markets&condition_id=${conditionId}`);
      if (!res.ok) return null;
      const data = await res.json();
      const markets = Array.isArray(data) ? data : [data];
      for (const m of markets) {
        let tokenIds: string[] = [];
        if (Array.isArray(m.clobTokenIds)) {
          tokenIds = m.clobTokenIds.map(String);
        } else if (typeof m.clobTokenIds === "string") {
          try { tokenIds = JSON.parse(m.clobTokenIds).map(String); } catch {}
        }
        // Stash negRisk alongside tokenIds — saves a refetch when signing
        // each order against the right exchange contract.
        const negRisk = Boolean(m.negRisk ?? m.neg_risk);
        this.negRiskMap.set(conditionId, negRisk);
        saveNegRiskMap(this.negRiskMap);
        if (tokenIds.length >= 2) {
          this.tokenMap.set(conditionId, tokenIds);
          saveTokenMap(this.tokenMap);
          const idx = outcome.toLowerCase() === "no" ? 1 : 0;
          return tokenIds[idx] || null;
        }
      }
    } catch {}

    return null;
  }

  /** Negative-risk flag for a market, with localStorage cache. Defaults to
   *  false if unknown — a wrong default just produces a "bad signature"
   *  error on the order, which we surface in the log. */
  private async resolveNegRisk(conditionId: string): Promise<boolean> {
    const cached = this.negRiskMap.get(conditionId);
    if (cached !== undefined) return cached;
    // resolveTokenId populates negRiskMap as a side effect; reuse it.
    await this.resolveTokenId(conditionId, "Yes");
    return this.negRiskMap.get(conditionId) ?? false;
  }

  /** Lazily create (and cache) a JsonRpcSigner from window.ethereum,
   *  ensuring the wallet is on Polygon first. Re-created on every cycle
   *  start to handle account/chain changes in MetaMask. */
  private async getSigner(): Promise<JsonRpcSigner> {
    if (this.signer) return this.signer;
    if (typeof window === "undefined" || !window.ethereum) {
      throw new Error("NO_WALLET — window.ethereum unavailable");
    }
    const polygon = networkById("polygon")!;
    await ensureChain(window.ethereum as never, polygon);
    const provider = new BrowserProvider(window.ethereum as never);
    this.signer = await provider.getSigner(this.config.address);
    return this.signer;
  }

  // ── State Management ─────────────────────────────────────────

  private setState(patch: Partial<CopyEngineState>): void {
    this.state = { ...this.state, ...patch };
    for (const fn of this.listeners) {
      try { fn(this.getState()); } catch {}
    }
  }

  private addLog(entry: ExecutionLogEntry): void {
    const log = [entry, ...this.state.log].slice(0, MAX_LOG_ENTRIES);
    this.setState({ log });
  }

  // ── Persistence ──────────────────────────────────────────────

  private saveCursors(): void {
    try {
      localStorage.setItem(
        `poly_copy_cursors_${this.config.strategyId}`,
        JSON.stringify(this.state.traderCursors),
      );
    } catch {}
  }

  /** Stable key for the EP ledger / position lookups. */
  private posKey(conditionId: string, outcome?: string): string {
    return `${conditionId.toLowerCase()}:${(outcome || "Yes").toLowerCase()}`;
  }

  private savePositionEP(): void {
    try {
      localStorage.setItem(
        `poly_copy_positionep_${this.config.strategyId}`,
        JSON.stringify(this.positionEP),
      );
    } catch {}
  }

  private saveLog(): void {
    try {
      localStorage.setItem(
        `poly_copy_log_${this.config.strategyId}`,
        JSON.stringify(this.state.log.slice(0, 50)),
      );
    } catch {}
  }

  private loadPersisted(): void {
    try {
      const cursors = localStorage.getItem(`poly_copy_cursors_${this.config.strategyId}`);
      if (cursors) this.state.traderCursors = JSON.parse(cursors);
    } catch {}
    try {
      const log = localStorage.getItem(`poly_copy_log_${this.config.strategyId}`);
      if (log) this.state.log = JSON.parse(log);
    } catch {}
    try {
      const dedup = localStorage.getItem(`poly_copy_dedup_${this.config.strategyId}`);
      if (dedup) {
        const arr = JSON.parse(dedup);
        if (Array.isArray(arr)) for (const id of arr) this.copiedIds.add(id);
      }
    } catch {}
    try {
      const roi = localStorage.getItem(`${ROI_CACHE_KEY}_${this.config.strategyId}`);
      if (roi) {
        const parsed = JSON.parse(roi);
        if (parsed && typeof parsed === "object") this.traderRoiStats = parsed;
      }
    } catch {}
    try {
      const ep = localStorage.getItem(`poly_copy_positionep_${this.config.strategyId}`);
      if (ep) {
        const parsed = JSON.parse(ep);
        if (parsed && typeof parsed === "object") this.positionEP = parsed;
      }
    } catch {}
  }

  private saveRoiStats(): void {
    try {
      localStorage.setItem(
        `${ROI_CACHE_KEY}_${this.config.strategyId}`,
        JSON.stringify(this.traderRoiStats),
      );
    } catch {}
  }

  // ── ROI / Sharpe refresh ─────────────────────────────────────
  //
  // Pulls 30d trade history per enabled trader, computes realized
  // returns via FIFO, derives Sharpe. Runs in parallel across
  // traders to amortize data-api latency. Per-trader failures are
  // swallowed (we keep the previous stats); the engine only stops
  // scoring a trader if their stats never load at all.
  private async refreshTraderRoiStats(): Promise<void> {
    if (this.roiRefreshInFlight) return;
    this.roiRefreshInFlight = true;
    try {
      const enabled = this.config.traders.filter((t) => t.enabled !== false);
      await Promise.all(enabled.map(async (t) => {
        try {
          const stats = await fetchTraderRoiStats(t.address, ROI_WINDOW_DAYS);
          this.traderRoiStats[t.address.toLowerCase()] = stats;
        } catch (e) {
          // Soft-fail: keep stale stats if we had them.
        }
      }));
      this.saveRoiStats();
      // Publish to subscribers so the UI re-renders the trader table
      // with new scores.
      this.setState({ traderRoiStats: { ...this.traderRoiStats } });
    } finally {
      this.roiRefreshInFlight = false;
    }
  }

  // Build the canonical StratTraderTrade shape the strat methods consume.
  // Used by both observed-trade enrichment (score for UI display) and the
  // execution path (score + sizing). One builder = one source of truth
  // for the engine→strat hand-off.
  private buildStratTrade(
    trade: PolymarketTrade,
    trader: IndexTrader,
    copyRatio: number,
    totalWeight: number,
  ): StratTraderTrade {
    return {
      ...trade,
      trader: trader.address,
      weight: trader.weight,
      weightFraction: totalWeight > 0 ? trader.weight / totalWeight : 0,
      copyRatio,
      notional: trade.price * trade.size,
    };
  }

  // Score a built strat-trade. Returns the engine-side score record
  // (score + sharpe + raw stats) so the UI rows + log entries can
  // render the breakdown regardless of which strat is active.
  private scoreStratTrade(stratTrade: StratTraderTrade): {
    score: number;
    sharpe: number;
    stats: TraderRoiStats | null;
  } {
    const stats = this.traderRoiStats[stratTrade.trader.toLowerCase()] ?? null;
    const score = this.strat.scoreCandidate(stratTrade, stats);
    return { score, sharpe: stats?.sharpe ?? 0, stats };
  }

  /** Best pending BUY across all enabled traders, by expected profit (EP =
   *  roi × mirror$). Used by the balance gate to decide whether — and how
   *  much — to rotate existing positions for. Mirrors the copyRatio + scoring
   *  the main poll loop computes, but only to surface the single best EP so we
   *  don't sell positions for a worse opportunity. Returns null if no positive
   *  -EP buy is pending. Cheap: reuses the data-api trade cache. */
  private async bestBuyCandidateEP(
    totalWeight: number,
  ): Promise<{ ep: number; mirrorNotional: number; market: string } | null> {
    if (totalWeight <= 0) return null;
    let best: { ep: number; mirrorNotional: number; market: string } | null = null;
    for (const trader of this.config.traders.filter((t) => t.enabled !== false)) {
      try {
        const cursor = this.state.traderCursors[trader.address.toLowerCase()] ?? 0;
        const trades = await fetchWalletTradesIncremental(
          trader.address,
          getTradeCache(trader.address) ?? [],
          Math.floor((cursor - 60_000) / 1000),
        );
        const newBuys = trades.filter(
          (t) => t.side === "BUY" && t.timestamp > cursor && !this.copiedIds.has(t.id),
        );
        if (newBuys.length === 0) continue;
        // Same proportional copy-ratio the main loop uses (see Phase 1).
        const backtestDays = this.config.backtestDays ?? 3;
        const windowCutoffMs = Date.now() - backtestDays * 86400_000;
        const windowTrades = trades.filter((t) => t.timestamp >= windowCutoffMs);
        const buyVol = windowTrades
          .filter((t) => t.side === "BUY")
          .reduce((s, t) => s + t.price * t.size, 0);
        const sellVol = windowTrades
          .filter((t) => t.side === "SELL")
          .reduce((s, t) => s + t.price * t.size, 0);
        const traderVol = Math.max(buyVol, sellVol, 1);
        const copyRatio = (this.config.capital * (trader.weight / totalWeight)) / traderVol;
        for (const t of newBuys) {
          const stratTrade = this.buildStratTrade(t, trader, copyRatio, totalWeight);
          const ep = this.scoreStratTrade(stratTrade).score;
          const mirrorNotional = stratTrade.notional * copyRatio;
          if (ep > 0 && (!best || ep > best.ep)) {
            best = { ep, mirrorNotional, market: t.market };
          }
        }
      } catch {
        // Per-trader fetch failures are logged by the main loop; ignore here.
      }
    }
    return best;
  }

  private pruneDedup(): void {
    // Keep only last 1000 copied trade IDs
    if (this.copiedIds.size > 1000) {
      const arr = Array.from(this.copiedIds);
      this.copiedIds = new Set(arr.slice(arr.length - 1000));
    }
    try {
      localStorage.setItem(
        `poly_copy_dedup_${this.config.strategyId}`,
        JSON.stringify(Array.from(this.copiedIds)),
      );
    } catch {}
  }
}
