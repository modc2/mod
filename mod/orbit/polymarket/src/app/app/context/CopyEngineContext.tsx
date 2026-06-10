"use client";

import { createContext, useContext, useState, useRef, useCallback, useEffect, ReactNode } from "react";
import { CopyEngine, CopyEngineState, CopyEngineConfig } from "../lib/copyEngine";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";

interface CopyEngineContextValue {
  engineState: CopyEngineState | null;
  isLive: boolean;
  /** Strategy id currently being run (null when stopped). Exposed so the
      global LIVE indicator can show which strat is running and let users
      switch from anywhere in the app. */
  activeStrategyId: string | null;
  /** Whether the backend long-running engine is active (survives tab close). */
  backendRunning: boolean;
  startLive: (config: CopyEngineConfig) => void;
  stopLive: () => void;
  pauseLive: () => void;
  resumeLive: () => void;
  clearLog: () => void;
  /** One-shot catch-up: scan the last N hours of trades for every
      enrolled trader and copy the ones above the notional floor.
      Resolves with placement stats. Requires a running engine. */
  catchUp: (opts: {
    lookbackHours: number;
    minNotional: number;
    topN?: number;
    sellWinners?: boolean;
    onProgress?: (msg: string) => void;
  }) => Promise<{ scanned: number; placed: number; failed: number; skipped: number; sold: number }>;
}

// Persisted live-session record. Holds only the *config* needed to rebuild
// the engine on reload; sensitive bits (clobCreds) are sourced from
// AuthContext's per-EOA cache, so we don't double-store them.
interface PersistedLive {
  strategyId: string;
  address: string;
  capital: number;
  intervalMs: number;
  minOrderSize: number;
  maxSlippageBps: number;
  startedAt: number;
}

const LIVE_KEY = "poly_live_session";

function loadPersistedLive(): PersistedLive | null {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(LIVE_KEY) : null;
    if (!raw) return null;
    const obj = JSON.parse(raw) as PersistedLive;
    if (!obj.strategyId || !obj.address || !obj.intervalMs) return null;
    return obj;
  } catch {
    return null;
  }
}

function savePersistedLive(rec: PersistedLive): void {
  try { localStorage.setItem(LIVE_KEY, JSON.stringify(rec)); } catch {}
}

function clearPersistedLive(): void {
  try { localStorage.removeItem(LIVE_KEY); } catch {}
}

export function getPersistedLive(): PersistedLive | null {
  return loadPersistedLive();
}

// ── Backend engine helpers ──────────────────────────────────────

async function backendStart(config: CopyEngineConfig): Promise<boolean> {
  try {
    const body = {
      eoa: config.address,
      strategyId: config.strategyId,
      address: config.address,
      traders: config.traders.map((t) => ({
        address: t.address,
        weight: t.weight ?? 1,
        enabled: t.enabled !== false,
      })),
      capital: config.capital,
      intervalMs: config.intervalMs,
      minOrderSize: config.minOrderSize,
      maxSlippageBps: config.maxSlippageBps,
    };
    const res = await fetch(`${API_URL}/live/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function backendStop(eoa: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/live/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ eoa }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

interface BackendStatus {
  running: boolean;
  config?: Record<string, unknown>;
  state?: {
    status: string;
    lastCycleAt: number | null;
    nextCycleAt: number | null;
    cycleCount: number;
    totalOrdersPlaced: number;
    totalOrdersFailed: number;
    totalVolumeMirrored: number;
    balance: number | null;
    log: Array<{ id: string; timestamp: number; type: string; reason?: string }>;
    observedTrades: Array<{
      id: string; timestamp: number; trader: string; market: string;
      conditionId: string; side: string; size: number; price: number; notional: number;
      score?: number; sharpe?: number;
    }>;
    error: string | null;
    traderCursors: Record<string, number>;
    traderLastSync: Record<string, number>;
  };
}

async function backendStatus(eoa: string): Promise<BackendStatus | null> {
  try {
    const res = await fetch(`${API_URL}/live/status?eoa=${encodeURIComponent(eoa)}`);
    if (!res.ok) return null;
    return await res.json() as BackendStatus;
  } catch {
    return null;
  }
}

// ── Context ─────────────────────────────────────────────────────

const CopyEngineContext = createContext<CopyEngineContextValue>({
  engineState: null,
  isLive: false,
  activeStrategyId: null,
  backendRunning: false,
  startLive: () => {},
  stopLive: () => {},
  pauseLive: () => {},
  resumeLive: () => {},
  clearLog: () => {},
  catchUp: async () => ({ scanned: 0, placed: 0, failed: 0, skipped: 0, sold: 0 }),
});

export function useCopyEngine() {
  return useContext(CopyEngineContext);
}

export function CopyEngineProvider({ children }: { children: ReactNode }) {
  const engineRef = useRef<CopyEngine | null>(null);
  const [engineState, setEngineState] = useState<CopyEngineState | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [activeStrategyId, setActiveStrategyId] = useState<string | null>(null);
  const [backendRunning, setBackendRunning] = useState(false);
  // EOA used for backend polling — set on start, cleared on stop.
  const backendEoaRef = useRef<string | null>(null);

  const startLive = useCallback((config: CopyEngineConfig) => {
    // Stop existing browser engine if any
    if (engineRef.current) {
      engineRef.current.stop();
    }

    const engine = new CopyEngine(config);
    engineRef.current = engine;

    engine.subscribe((s) => setEngineState(s));
    engine.start();
    setIsLive(true);
    setActiveStrategyId(config.strategyId);
    setEngineState(engine.getState());

    // Persist the session so a page reload can restart the browser engine.
    savePersistedLive({
      strategyId: config.strategyId,
      address: config.address,
      capital: config.capital,
      intervalMs: config.intervalMs,
      minOrderSize: config.minOrderSize,
      maxSlippageBps: config.maxSlippageBps,
      startedAt: Date.now(),
    });

    // Also start the backend long-running engine so it survives tab close.
    backendEoaRef.current = config.address;
    void backendStart(config).then((ok) => {
      setBackendRunning(ok);
    });
  }, []);

  const stopLive = useCallback(() => {
    // Stop browser engine
    if (engineRef.current) {
      engineRef.current.stop();
      setEngineState(engineRef.current.getState());
    }
    setIsLive(false);
    setActiveStrategyId(null);
    clearPersistedLive();

    // Stop backend engine too
    const eoa = backendEoaRef.current;
    if (eoa) {
      void backendStop(eoa);
      backendEoaRef.current = null;
    }
    setBackendRunning(false);
  }, []);

  const pauseLive = useCallback(() => {
    if (engineRef.current) {
      engineRef.current.pause();
      setEngineState(engineRef.current.getState());
    }
  }, []);

  const resumeLive = useCallback(() => {
    if (engineRef.current) {
      engineRef.current.resume();
      setEngineState(engineRef.current.getState());
    }
  }, []);

  const clearLog = useCallback(() => {
    setEngineState((prev) => prev ? { ...prev, log: [] } : null);
  }, []);

  // CATCH UP — delegates to the in-browser engine's one-shot backfill.
  // Returns zeros when there's no live engine yet so the UI can disable
  // the button cleanly instead of throwing.
  const catchUp = useCallback(async (opts: {
    lookbackHours: number;
    minNotional: number;
    topN?: number;
    sellWinners?: boolean;
    onProgress?: (msg: string) => void;
  }) => {
    if (!engineRef.current) {
      return { scanned: 0, placed: 0, failed: 0, skipped: 0, sold: 0 };
    }
    return engineRef.current.catchUp(opts);
  }, []);

  // Poll backend status every 5s while live. Merge backend observed trades
  // into the frontend engine state so the user sees trades the backend
  // picked up even when the browser engine missed them (e.g. tab was
  // backgrounded). Also detects backend running on page load.
  useEffect(() => {
    const eoa = backendEoaRef.current;
    if (!eoa && !isLive) return;

    let cancelled = false;
    const poll = async () => {
      const pollEoa = backendEoaRef.current;
      if (!pollEoa) return;
      const status = await backendStatus(pollEoa);
      if (cancelled) return;
      if (status?.running) {
        setBackendRunning(true);
        // Merge backend observed trades into engine state if the browser
        // engine isn't providing them (backend sees trades even when the
        // tab is minimized / CPU-throttled).
        if (status.state && engineRef.current) {
          const backendObs = status.state.observedTrades ?? [];
          if (backendObs.length > 0) {
            const current = engineRef.current.getState();
            const existingIds = new Set(current.observedTrades.map((t) => t.id));
            const newFromBackend = backendObs
              .filter((t) => !existingIds.has(t.id))
              .map((t) => ({
                id: t.id,
                timestamp: t.timestamp,
                trader: t.trader,
                market: t.market,
                conditionId: t.conditionId,
                side: t.side as "BUY" | "SELL",
                size: t.size,
                price: t.price,
                notional: t.notional,
                // Backend may not stamp scores yet — defer to engine's
                // own observed-trade enrichment for the score column.
                score: t.score ?? 0,
                sharpe: t.sharpe ?? 0,
              }));
            if (newFromBackend.length > 0) {
              const merged = [...newFromBackend, ...current.observedTrades]
                .sort((a, b) => b.timestamp - a.timestamp)
                .slice(0, 500);
              setEngineState((prev) => prev ? { ...prev, observedTrades: merged } : prev);
            }
          }
        }
      } else {
        setBackendRunning(false);
      }
    };

    void poll();
    const t = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(t); };
  }, [isLive]);

  // On mount: check if a backend engine is already running (from a previous
  // session / tab close). If so, mark as live so the UI reflects it.
  useEffect(() => {
    const persisted = loadPersistedLive();
    if (!persisted?.address) return;
    backendEoaRef.current = persisted.address;
    void backendStatus(persisted.address).then((status) => {
      if (status?.running) {
        setBackendRunning(true);
        // The browser engine will be restored separately by the existing
        // auto-resume logic in LivePanel (which checks persisted creds).
        // Here we just ensure the UI knows the backend is active.
      }
    });
  }, []);

  // Do NOT stop the browser engine on unmount — the backend keeps running.
  // The browser engine cleanup only happens on explicit stopLive().

  return (
    <CopyEngineContext.Provider value={{
      engineState, isLive, activeStrategyId, backendRunning,
      startLive, stopLive, pauseLive, resumeLive, clearLog, catchUp,
    }}>
      {children}
    </CopyEngineContext.Provider>
  );
}
