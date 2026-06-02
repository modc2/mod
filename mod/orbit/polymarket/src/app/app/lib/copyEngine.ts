import { BrowserProvider, Contract, JsonRpcProvider, formatUnits, type JsonRpcSigner } from "ethers";
import { ClobCredentials, IndexTrader, PolymarketTrade } from "./types";
import { placeOrder, detectSigType, ClobOrderResult } from "./clobClient";
import { fetchWalletTradesUntil, fetchPositions } from "./polymarket";
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
}

const MAX_LOG_ENTRIES = 200;
const MAX_ORDERS_PER_CYCLE = 20;
const ORDER_DELAY_MS = 500;
// 0 — the matcher's fee schedule for binary markets returns 0, and a
// hardcoded 200 bps was making every CLOB POST reject with HTTP 400
// "Invalid order payload" because the signed feeRateBps didn't match
// what the matcher expected. Per-market fee lookup is a TODO but 0 is
// the safe default for taker-side fills on standard CTF markets.
const TAKER_FEE_BPS = 0;
// Polymarket's CLOB rejects any order below $1 notional with the generic
// "Invalid order payload" error (no specific reason returned). The user-
// configurable minOrderSize is for the strat's per-trade floor; this is
// the hard API limit we MUST clamp to regardless of strat settings.
// Without this, users who dropped the floor to $0.01 via the auto-fix
// banner produced cascades of rejected $0.01 orders.
const POLYMARKET_MIN_USD = 1.0;

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
  private tokenMap: Map<string, string[]>;
  private negRiskMap: Map<string, boolean>;
  private running = false;
  // Cached signer; refreshed if the user switches account/chain in MetaMask.
  private signer: JsonRpcSigner | null = null;
  // Polymarket Proxy address for this user's EOA. Resolved lazily on first
  // cycle; persists for the engine's lifetime.
  private proxyAddress: string | null = null;
  // Auto-detected sigType for this user's apiKey binding. Resolved once
  // per engine session via detectSigType().
  private sigType: 0 | 1 | 2 = 2;
  // Guard against re-entering executeCycle when the previous cycle is still
  // mid-flight. Necessary at sub-minute poll cadences (5s) where a slow
  // cycle (token-id lookup + order placement RTT) can outlast a tick of
  // setInterval. Without this, a stuck cycle would silently spawn a parallel
  // copy that double-fires every trade.
  private cycleInFlight = false;

  constructor(config: CopyEngineConfig) {
    this.config = config;
    this.tokenMap = loadTokenMap();
    this.negRiskMap = loadNegRiskMap();
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
      if (rawMirrorNotional < userFloor) {
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "SKIP",
          traderAddress: trader.address,
          market: trade.market,
          conditionId: trade.conditionId,
          side: trade.side,
          mirrorNotional: Math.round(rawMirrorNotional * 100) / 100,
          reason: `CATCH UP · BELOW_MIN_SIZE · proportional $${rawMirrorNotional < 0.01 ? rawMirrorNotional.toExponential(2) : rawMirrorNotional.toFixed(2)} < floor $${userFloor.toFixed(2)}`,
        });
        skipped++;
        continue;
      }
      if (rawMirrorNotional < POLYMARKET_MIN_USD) {
        this.addLog({
          id: uid(),
          timestamp: Date.now(),
          type: "SKIP",
          traderAddress: trader.address,
          market: trade.market,
          conditionId: trade.conditionId,
          side: trade.side,
          mirrorNotional: Math.round(rawMirrorNotional * 100) / 100,
          reason: `CATCH UP · BELOW_CLOB_MIN · $${rawMirrorNotional.toFixed(2)} < Polymarket $${POLYMARKET_MIN_USD.toFixed(2)}`,
        });
        skipped++;
        continue;
      }
      const mirrorNotional = Math.min(rawMirrorNotional, ceiling);
      // Whole shares for the 1¢ tick grid — see executeCycle for rationale.
      const mirrorSize = Math.max(
        1,
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
            price: trade.price,
            size: Math.round(mirrorSize * 100) / 100,
            side: trade.side,
            type: "FAK",
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
  } = {}): Promise<{ sold: number; freedUsd: number }> {
    const maxToSell = opts.maxToSell ?? Number.POSITIVE_INFINITY;
    const reasonPrefix = opts.reasonPrefix ?? "FREE LIQUIDITY";
    let sold = 0;
    let freedUsd = 0;
    try {
      const proxy =
        this.proxyAddress ?? (this.proxyAddress = await getProxyAddress(this.config.address));
      const positions = await fetchPositions(proxy);
      const winners = positions
        .filter((p) => p.size > 0 && p.pnlUsd > 0)
        .sort((a, b) => b.pnlUsd - a.pnlUsd);
      opts.onProgress?.(`${winners.length} winning positions → selling`);
      for (const pos of winners) {
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
          const sellPrice = Math.max(0.01, Math.min(0.99, pos.currentPrice));
          const result = await placeOrder(
            this.config.creds,
            signer,
            maker,
            {
              tokenID: tokenId,
              price: sellPrice,
              size: Math.round(pos.size * 100) / 100,
              side: "SELL",
              type: "FAK",
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
            opts.onProgress?.(`sold ${sold} winners · freed $${freedUsd.toFixed(2)}`);
            await delay(ORDER_DELAY_MS);
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
      // Resolve the user's Polymarket Proxy address once. Polymarket binds
      // every newly-minted apiKey to this proxy — that's the address that
      // holds funds and signs orders, not the connected EOA.
      if (!this.proxyAddress) {
        this.proxyAddress = await getProxyAddress(this.config.address);
      }

      // Probe all three sig_types and pick whichever Polymarket reports a
      // non-zero balance for. The CLOB's apiKey binding is opaque from the
      // client side — best we can do is try and use the winner.
      const detected = await detectSigType(this.config.creds, this.config.address);
      this.sigType = detected.sigType;
      const bal = detected.bal;

      // Cross-check against the real on-chain USDC.e balance at the proxy.
      // Polymarket's CLOB only counts funds at a *deployed + approved*
      // proxy — for a brand-new user with USDC sitting at an undeployed
      // Safe address, this will be 0 even though funds exist. The proxy
      // deploys atomically on first order settlement, so we let the engine
      // try the trade as long as on-chain funds clear the capital gate.
      const polygon = networkById("polygon")!;
      let onchainProxyBal = 0;
      try {
        const raw = await withRpcFallback(polygon, async (url) => {
          const provider = new JsonRpcProvider(url);
          const c = new Contract(USDC_E, ["function balanceOf(address) view returns (uint256)"], provider);
          return (await c.balanceOf(this.proxyAddress!)) as bigint;
        });
        onchainProxyBal = Number(formatUnits(raw, 6));
      } catch {}

      // Trust whichever is larger — typically on-chain pre-deployment,
      // CLOB post-deployment once funds are "live."
      const effective = Math.max(bal.balance, onchainProxyBal);
      this.setState({ balance: effective });
      const balDetail =
        `$${effective.toFixed(2)} usable · proxy $${onchainProxyBal.toFixed(2)} on-chain · CLOB $${bal.balance.toFixed(2)} (sigType=${this.sigType})`;
      this.addLog({
        id: uid(),
        timestamp: Date.now(),
        type: "BALANCE",
        reason: balDetail,
      });

      if (effective < this.config.capital * 0.05) {
        // ── Auto free-liquidity ──
        // Before erroring out, try selling the top winning open position to
        // make room. This matches the catch-up "sellWinners" behavior but
        // fires automatically when:
        //   (a) balance dipped below the 5%-of-capital gate, AND
        //   (b) there's at least one watched-trader BUY candidate this
        //       cycle — i.e. a real opportunity worth freeing for, not a
        //       quiet poll. Without the candidate check we'd churn open
        //       positions every cycle just to hold dust.
        // The criterion roughly approximates "the trade is ranked to have
        // a higher probability of profit": we sort candidates by trader
        // notional below and only run free-liquidity when one of those
        // candidates exists — high notional = trader's conviction = our
        // proxy for expected edge.
        let hasHighConvictionCandidate = false;
        for (const trader of this.config.traders.filter((t) => t.enabled !== false)) {
          try {
            const cursor = this.state.traderCursors[trader.address.toLowerCase()] ?? 0;
            const trades = await fetchWalletTradesUntil(
              trader.address,
              Math.floor((cursor - 60_000) / 1000),
            );
            if (
              trades.some(
                (t) => t.side === "BUY" && t.timestamp > cursor && !this.copiedIds.has(t.id),
              )
            ) {
              hasHighConvictionCandidate = true;
              break;
            }
          } catch {
            // ignore per-trader fetch failures during the candidate check —
            // the main loop below logs them properly.
          }
        }

        let freedAnything = false;
        if (hasHighConvictionCandidate) {
          this.addLog({
            id: uid(),
            timestamp: Date.now(),
            type: "BALANCE",
            reason: `Balance $${effective.toFixed(2)} < 5% gate · candidate BUY pending → auto-selling top winner`,
          });
          const { sold, freedUsd } = await this.freeLiquidity({
            maxToSell: 1,
            reasonPrefix: "AUTO FREE LIQUIDITY",
          });
          if (sold > 0) {
            freedAnything = true;
            // Re-check on-chain balance — the SELL settles atomically on
            // Polymarket so the proxy USDC.e bumps before this returns.
            try {
              const raw = await withRpcFallback(polygon, async (url) => {
                const provider = new JsonRpcProvider(url);
                const c = new Contract(USDC_E, ["function balanceOf(address) view returns (uint256)"], provider);
                return (await c.balanceOf(this.proxyAddress!)) as bigint;
              });
              onchainProxyBal = Number(formatUnits(raw, 6));
            } catch {}
            const newEffective = Math.max(bal.balance, onchainProxyBal);
            this.setState({ balance: newEffective });
            this.addLog({
              id: uid(),
              timestamp: Date.now(),
              type: "BALANCE",
              reason: `Freed $${freedUsd.toFixed(2)} · proxy now $${onchainProxyBal.toFixed(2)} → continuing cycle`,
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
          const proxy = this.proxyAddress!;
          const proxyShort = `${proxy.slice(0, 6)}…${proxy.slice(-4)}`;
          const need = this.config.capital * 0.05;
          // Read the EOA balance so we can give a precise next-step in the
          // common case where the user funded the EOA but never deposited.
          let eoaBal = 0;
          try {
            const raw = await withRpcFallback(polygon, async (url) => {
              const provider = new JsonRpcProvider(url);
              const c = new Contract(USDC_E, ["function balanceOf(address) view returns (uint256)"], provider);
              return (await c.balanceOf(this.config.address)) as bigint;
            });
            eoaBal = Number(formatUnits(raw, 6));
          } catch {}
          const reason = !hasHighConvictionCandidate
            ? `Balance $${effective.toFixed(2)} < 5% gate; no new BUY candidates → idling without freeing liquidity. Will retry next cycle.`
            : eoaBal >= need
            ? `Polymarket trades from a proxy address, not your wallet. Your wallet has $${eoaBal.toFixed(2)} USDC.e but proxy ${proxyShort} has only $${onchainProxyBal.toFixed(2)}. Open POLYMARKET ACCOUNT → DEPOSIT to move at least $${need.toFixed(2)} from wallet → proxy, then press RUN.`
            : `Proxy ${proxyShort} has $${onchainProxyBal.toFixed(2)} (need ≥$${need.toFixed(2)} = 5% of capital). Wallet also has only $${eoaBal.toFixed(2)} — fund USDC.e to your wallet first, then DEPOSIT to proxy.`;
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

      for (const trader of enabledTraders) {
        if (ordersThisCycle >= MAX_ORDERS_PER_CYCLE) break;
        if (!this.running) break;

        const addr = trader.address.toLowerCase();
        // First-cycle cursor defaults to one poll interval ago (NOT now —
        // a `Date.now()` default makes `t.timestamp > cursor` reject every
        // trade in the lookup window, so the engine sees nothing until the
        // SECOND cycle. The 60s overlap below additionally guards against
        // boundary races where a trade lands microseconds before sync.
        const cursor = this.state.traderCursors[addr]
          || Date.now() - this.config.intervalMs;
        // Fetch trades since cursor with 60s overlap
        const untilTs = Math.floor((cursor - 60_000) / 1000);

        try {
          const trades = await fetchWalletTradesUntil(
            trader.address,
            untilTs,
          );
          // Stamp the per-trader last-sync time the moment the fetch resolves
          // — even if zero new trades came back. Empty result = "they're not
          // trading", which is still a successful sync. Without this stamp,
          // a quiet trader would look perpetually stale in the UI.
          this.setState({
            traderLastSync: {
              ...this.state.traderLastSync,
              [addr]: Date.now(),
            },
          });

          // Filter to new trades only, then rank by trader notional desc.
          // High notional ≈ high conviction → highest expected-edge trades
          // get the first crack at remaining capital before MAX_ORDERS_PER_CYCLE
          // or balance constraints close the cycle out. This is the same
          // ranking signal the auto-free-liquidity branch uses to decide
          // whether to sell winners.
          const newTrades = trades
            .filter((t) => t.timestamp > cursor && !this.copiedIds.has(t.id))
            .sort((a, b) => b.price * b.size - a.price * a.size);

          if (newTrades.length === 0) continue;
          tradersWithNewActivity++;
          totalNewTradesSeen += newTrades.length;

          // Push every observed upstream trade into the ring buffer BEFORE
          // we filter/clamp/place — so users see the raw real-time feed of
          // what their watched traders are doing, regardless of whether the
          // engine acts on it. Buffer caps at RECENT_TRADES_LIMIT to keep
          // memory bounded over long live sessions.
          const observed: ObservedTrade[] = newTrades.map((t) => ({
            id: t.id,
            timestamp: t.timestamp,
            trader: trader.address,
            market: t.market,
            conditionId: t.conditionId,
            side: t.side,
            size: t.size,
            price: t.price,
            notional: t.price * t.size,
          }));
          const RECENT_TRADES_LIMIT = 500;
          const merged = [...observed, ...this.state.observedTrades]
            .sort((a, b) => b.timestamp - a.timestamp)
            .slice(0, RECENT_TRADES_LIMIT);
          this.setState({ observedTrades: merged });

          // Per-trade sizing (proportional) — must match the BACKTEST tab
          // exactly so the preview the user signs off on predicts live
          // behavior. Backtest computes:
          //   scale = (capital * weight/totalWeight) / max(buyVol, sellVol, 1)
          // over trades within the backtestDays window. We replicate that
          // here. Using max(buy, sell) instead of buy-only keeps the
          // denominator stable when a trader is mostly closing positions,
          // and using a fixed-days window (not "whatever is in the hourly
          // cache") keeps the ratio reproducible across cycles.
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

          for (const trade of newTrades) {
            if (ordersThisCycle >= MAX_ORDERS_PER_CYCLE) break;
            if (!this.running) break;

            const traderNotional = trade.price * trade.size;
            const rawMirrorNotional = traderNotional * copyRatio;
            // SKIP below the user's TRADE SIZE floor — matches the BACKTEST
            // tab's `if (rawAmount < minTrade) continue`. Previously we
            // clamped up to the floor, which made live fire MANY more (and
            // smaller) trades than the backtest preview predicted, so users
            // could never trust what the preview said they'd execute. The
            // user-configured floor IS the dust gate.
            //
            // POLYMARKET_MIN_USD ($1) is the CLOB's hard rejection threshold
            // — we apply it as an additional skip reason so the user sees
            // exactly why an in-range backtest trade can't fire on-chain.
            const userFloor = this.config.minOrderSize;
            const polymarketFloor = POLYMARKET_MIN_USD;
            const ceiling = this.config.maxOrderSize ?? Number.POSITIVE_INFINITY;
            if (rawMirrorNotional < userFloor) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "SKIP",
                traderAddress: trader.address,
                market: trade.market,
                conditionId: trade.conditionId,
                side: trade.side,
                mirrorNotional: Math.round(rawMirrorNotional * 100) / 100,
                reason: `BELOW_MIN_SIZE · proportional $${rawMirrorNotional < 0.01 ? rawMirrorNotional.toExponential(2) : rawMirrorNotional.toFixed(2)} < floor $${userFloor.toFixed(2)}`,
                upstreamTradeId: trade.id,
              });
              this.copiedIds.add(trade.id);
              continue;
            }
            if (rawMirrorNotional < polymarketFloor) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "SKIP",
                traderAddress: trader.address,
                market: trade.market,
                conditionId: trade.conditionId,
                side: trade.side,
                mirrorNotional: Math.round(rawMirrorNotional * 100) / 100,
                reason: `BELOW_CLOB_MIN · proportional $${rawMirrorNotional.toFixed(2)} < Polymarket $${polymarketFloor.toFixed(2)} hard floor`,
                upstreamTradeId: trade.id,
              });
              this.copiedIds.add(trade.id);
              continue;
            }
            // Clamp DOWN to the user's ceiling — matches backtest's
            // `Math.min(rawAmount, maxTrade)`. Outlier whale trades get
            // capped instead of blowing the budget.
            let mirrorNotional = rawMirrorNotional;
            let clampedReason: string | null = null;
            if (rawMirrorNotional > ceiling) {
              mirrorNotional = ceiling;
              clampedReason = `clamped down: proportional $${rawMirrorNotional.toFixed(2)} → ceiling $${ceiling.toFixed(2)}`;
            }
            // Ceil to WHOLE shares so the resulting makerAmount/takerAmount ratio
            // lands exactly on Polymarket's 1¢ tick price grid. With fractional
            // shares the implied price drifts off-tick (e.g. 1/1.86 = 0.5376…)
            // and CLOB rejects with "Invalid order payload". Whole shares ×
            // 1¢-tick price = clean 1¢ implied price. Backtest uses fractional
            // — this is a live-only on-chain constraint.
            const mirrorSize = Math.max(
              1,
              Math.ceil(mirrorNotional / Math.max(trade.price, 1e-9)),
            );
            if (clampedReason) {
              this.addLog({
                id: uid(),
                timestamp: Date.now(),
                type: "BALANCE",
                traderAddress: trader.address,
                market: trade.market,
                reason: clampedReason,
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
                  price: trade.price,
                  size: Math.round(mirrorSize * 100) / 100,
                  side: trade.side,
                  type: "FAK",
                  feeRateBps: TAKER_FEE_BPS,
                },
                negRisk,
                this.sigType,
              );

              const logType = result.success
                ? (trade.side === "BUY" ? "COPY_BUY" : "COPY_SELL")
                : "ERROR";

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
                  : undefined,
                upstreamTradeId: trade.id,
              });

              if (result.success) {
                this.setState({
                  totalOrdersPlaced: this.state.totalOrdersPlaced + 1,
                  totalVolumeMirrored: this.state.totalVolumeMirrored + mirrorNotional,
                });
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

          // Update cursor to latest trade
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
      const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";
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
