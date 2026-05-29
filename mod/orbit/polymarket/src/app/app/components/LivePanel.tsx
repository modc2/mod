"use client";

import { useState, useCallback, useMemo, useEffect, useRef, type ReactNode } from "react";
import { Contract, JsonRpcProvider, formatUnits } from "ethers";
import { useAuth } from "../context/AuthContext";
import { useCopyEngine } from "../context/CopyEngineContext";
import { loadIndexes, getActiveIndexId, updateIndex } from "../lib/indexStore";
import { getProxyAddress } from "../lib/polymarketProxy";
import { USDC_E } from "../lib/polymarketContracts";
import { networkById, withRpcFallback } from "../lib/networks";
import type { SavedIndex } from "../lib/types";
import type { ExecutionLogEntry, ObservedTrade } from "../lib/copyEngine";
import WalletFundingPanel from "./WalletFundingPanel";
import LoadedBadge from "./LoadedBadge";
import EnableTradingPanel from "./EnableTradingPanel";
import PolymarketAccountPanel from "./PolymarketAccountPanel";
import BackendSignerPanel from "./BackendSignerPanel";
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
const DEFAULT_LIVE_POLL_MIN = 1;

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
  const { engineState, isLive, startLive, stopLive, pauseLive, resumeLive, backendRunning } = useCopyEngine();
  // confirm-start flow removed — user wants direct start/stop.
  const [liveCapital, setLiveCapital] = useState(100);
  // Proxy USDC.e balance — this is the on-chain "BALANCE" the engine should
  // size mirrors against. Polled every 15s while the LIVE tab is mounted.
  const [proxyBalance, setProxyBalance] = useState<number | null>(null);
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
  const [tradesFilter, setTradesFilter] = useState<"upstream" | "trades" | "all">("upstream");
  const TRADES_PAGE_SIZE = 25;

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
  // field (`rebalanceMinutes`). Previously LivePanel had its own SCAN
  // dropdown writing to a separate `livePollMinutes`, which let the
  // STRAT setting (15s) and the engine setting (1MIN) silently disagree.
  // Fallback to livePollMinutes for older strats that only set that
  // field, then the 1m default. Supports fractional minutes (5s = 0.0833).
  const livePollMin =
    activeStrat?.rebalanceMinutes ??
    activeStrat?.livePollMinutes ??
    DEFAULT_LIVE_POLL_MIN;

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
      // Ceiling for the new clamp-instead-of-skip sizing. Without this,
      // a single whale trade from a high-volume trader could blow the
      // proportional mirror past the user's TRADE SIZE max and chew
      // through capital in one shot.
      maxOrderSize: activeStrat.maxTrade,
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
      // Ceiling for the new clamp-instead-of-skip sizing. Without this,
      // a single whale trade from a high-volume trader could blow the
      // proportional mirror past the user's TRADE SIZE max and chew
      // through capital in one shot.
      maxOrderSize: activeStrat.maxTrade,
      maxSlippageBps: 300,
    });
  }, [livePollMin, isLive, status, activeStrat, auth.clobCreds, auth.address, liveCapital, startLive, stopLive]);

  return (
    <div className="space-y-1">
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
            {/* Polygon USDC ready-to-trade indicator — visible before GO LIVE */}
            {auth.connected && <LoadedBadge capital={liveCapital} />}
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

      {/* ── Preconditions ──
          Moved to the TOP so the user knows what's blocking GO LIVE before
          scrolling through funding panels. Compact pill row: each step is
          green-filled when satisfied, amber-outline when actionable, muted
          when stale. The summary count tells you "5/6 ready" at a glance. */}
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

      {/* ── Wallet + Funds + Capital ── */}
      {!isLive && (
        <WalletFundingPanel capital={liveCapital} onCapitalChange={handleManualCapital} />
      )}

      {/* ── Polymarket proxy account (trading address) ──
          Shown even when live so the user can top up the proxy without
          stopping the engine. */}
      {auth.connected && <PolymarketAccountPanel />}

      {/* ── Backend signer authorization ──
          One-time setup: add the backend EOA as a co-signer on the user's
          Safe so the live engine can place orders without a MetaMask popup
          per trade. See BackendSignerPanel.tsx for the full rationale. */}
      {auth.connected && <BackendSignerPanel />}

      {/* ── Stats (when live) ──
          Card grid replaces the old text-only flex rows. Each stat now has
          its own rounded mini-panel with a label + big mono value so the
          eye lands on numbers, not labels. LAST SYNC is the most recent
          successful Polymarket data-api pull (engine `lastCycleAt`) —
          tracks freshness directly instead of the cache snapshot age. */}
      {isLive && engineState && (
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

          {/* ── Skip-floor banner ──
              Pops up when most of what the engine sees is dust. Surfaces
              the exact floor and a one-click "drop to N" action that hot-
              restarts the engine with a lower min so orders start flowing
              without leaving this tab. Threshold tuned to "more than 3
              skips with zero orders" so it never appears for healthy runs. */}
          {(() => {
            const skipCount = engineState.log.filter((e) => e.type === "SKIP").length;
            const ordersCount = engineState.totalOrdersPlaced;
            const noOrdersButSkipping = skipCount > 3 && ordersCount === 0;
            if (!noOrdersButSkipping) return null;
            // Suggest the largest skipped mirror as the new floor — that's
            // the smallest value that would have placed *at least one* order.
            const skippedSizes = engineState.log
              .filter((e) => e.type === "SKIP" && typeof e.mirrorNotional === "number")
              .map((e) => Math.abs(e.mirrorNotional as number));
            const maxSkipped = skippedSizes.length > 0 ? Math.max(...skippedSizes) : 0.1;
            // Round down to a tidy chip value (0.01 / 0.05 / 0.10 / 0.50).
            const tidyFloors = [0.01, 0.05, 0.10, 0.25, 0.5, 1];
            const suggested = tidyFloors.find((f) => f >= maxSkipped) ?? 1;
            const currentFloor = activeStrat?.minTrade ?? 1;
            const applyFloor = (newFloor: number) => {
              if (!activeStrat) return;
              updateIndex(activeStrat.id, { minTrade: newFloor, updatedAt: Date.now() });
              // Re-trigger startLive with the fresh config so the running
              // engine picks up the new floor. handleToggle skips the
              // confirm step when already confirmed/live; we have to stop
              // first then start to swap config cleanly.
              stopLive();
              setTimeout(() => {
                if (!auth.clobCreds || !auth.address) return;
                startLive({
                  strategyId: activeStrat.id,
                  traders: activeStrat.traders.filter((t) => t.enabled !== false),
                  capital: liveCapital,
                  intervalMs: livePollMin * 60_000,
                  creds: auth.clobCreds,
                  address: auth.address,
                  minOrderSize: newFloor,
                  maxOrderSize: activeStrat.maxTrade,
                  maxSlippageBps: 300,
                });
              }, 100);
            };
            return (
              <div className="mt-2 px-3 py-2 border border-amber-400/40 bg-amber-400/5 rounded">
                <div className="text-[12px] text-amber-400 font-mono mb-1.5">
                  {skipCount} skipped · 0 orders · mirrors averaging $
                  {(skippedSizes.reduce((s, v) => s + v, 0) / Math.max(skippedSizes.length, 1)).toFixed(2)}
                  {" "}vs floor ${currentFloor.toFixed(2)}
                </div>
                <div className="text-[11px] text-pixel-gray font-mono mb-1.5">
                  Your capital is too thin OR the floor is too high. Drop the floor to start filling:
                </div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {[0.01, 0.05, 0.10, 0.25, 0.50].map((f) => (
                    <button
                      key={f}
                      onClick={() => applyFloor(f)}
                      className={`pixel-btn text-[11px] px-2 py-0.5 ${
                        f === suggested
                          ? "border-green-400 text-green-400 bg-green-400/10"
                          : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                      }`}
                      title={f === suggested ? "Recommended — clears the largest skipped mirror" : `Drop floor to $${f.toFixed(2)} and restart`}
                    >
                      ${f.toFixed(2)}
                    </button>
                  ))}
                </div>
              </div>
            );
          })()}

          {engineState.error && (
            <div className="mt-2 px-2 py-1 border border-red-400/40 bg-red-400/5 text-[14px] text-red-400 font-mono rounded">
              {engineState.error}
            </div>
          )}
        </div>
      )}

      {/* ── Trades / Execution Log (filterable + paginated) ──
          The full engine log is dominated by CYCLE_START/END heartbeats — the
          "TRADES" view filters to just the entries you actually copy or skip,
          which is what the user usually wants when monitoring. Pagination
          keeps the panel a fixed height instead of growing across the page. */}
      {isLive && engineState && (() => {
        const isUpstream = tradesFilter === "upstream";
        // UPSTREAM tab pulls from the engine's observed-trades ring buffer
        // (real-time mirror of what watched traders are doing); the other
        // tabs filter the engine log (copy decisions / cycle heartbeats).
        const items = isUpstream
          ? engineState.observedTrades
          : engineState.log.filter((e) =>
              tradesFilter === "trades"
                ? e.type === "COPY_BUY" || e.type === "COPY_SELL" || e.type === "SKIP"
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
          tradesFilter === "upstream" ? "UPSTREAM TRADES" :
          tradesFilter === "trades" ? "MIRROR ACTIVITY" : "EXECUTION LOG";
        const countLabel =
          tradesFilter === "upstream" ? "real-time trades" :
          tradesFilter === "trades" ? "copy events" : "entries";
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
                  title="Raw real-time trades from watched traders — independent of copy decisions"
                >
                  UPSTREAM
                </button>
                <button
                  onClick={() => { setTradesFilter("trades"); setTradesPage(0); }}
                  className={`pixel-btn text-[11px] px-2 py-0.5 ${
                    tradesFilter === "trades"
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                  title="Show only copy events (BUY / SELL / SKIP)"
                >
                  MIRROR
                </button>
                <button
                  onClick={() => { setTradesFilter("all"); setTradesPage(0); }}
                  className={`pixel-btn text-[11px] px-2 py-0.5 ${
                    tradesFilter === "all"
                      ? "border-green-400 text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                  title="Show every log entry, including cycle heartbeats"
                >
                  ALL
                </button>
              </div>
            </div>
            <div className="max-h-[300px] overflow-y-auto">
              {/* UPSTREAM rows look like the BACKTEST trade feed — trader,
                  side, market, notional. MIRROR/ALL rows use the existing
                  engine-log shape (with icons + reason text). */}
              {isUpstream
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
                  {tradesFilter === "upstream"
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
      {isLive && engineState && engineState.log.length === 0 && (
        <div className="pixel-panel border-2 border-pixel-border px-3 py-4 text-center">
          <span className="text-[15px] text-pixel-gray">WAITING FOR FIRST CYCLE...</span>
        </div>
      )}
    </div>
  );
}
