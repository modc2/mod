"use client";

// AUTO STRAT — the sidebar's window onto /api/autostrat: a factory agent that
// invents a copy-index recipe off the live board, proves it on the lab bench
// and registers it. Two triggers: the RANDOM NEW STRAT button (one run now)
// and the AUTO toggle (a run every minute, server-side, browser optional).
//
// Registration is finished HERE: the server can't reach localStorage, so this
// panel adopts finished runs into the strat roster — every clicked run (the
// human asked), and loop runs only when they passed the bench bar. Adopted
// run ids are remembered so a strat the user later deletes stays deleted.

import { useCallback, useEffect, useRef, useState } from "react";

import { getAccessToken } from "../lib/access";
import { loadIndexes, saveIndex } from "../lib/indexStore";
import type { SavedIndex } from "../lib/types";

const API = "/polymarket/api/autostrat";
const POLL_ACTIVE_MS = 5000;
const POLL_IDLE_MS = 30_000;
const SEEN_KEY = "poly8bit_autostrat_seen";

interface RunWindow {
  days: number;
  pnl: number;
  roi: number;
  trades: number;
  forward?: { verdict?: string };
}

interface AutoRun {
  id: string;
  at: number;
  origin: "loop" | "click";
  theme: string;
  status: "running" | "done" | "failed";
  error?: string;
  note?: string;
  strat?: SavedIndex;
  windows?: RunWindow[];
  passed?: boolean;
}

interface AutoState {
  settings: { enabled: boolean; intervalSecs: number };
  active: { id: string } | null;
  runs: AutoRun[];
}

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

const money = (n: number) => `${n < 0 ? "-" : "+"}$${Math.abs(n).toFixed(0)}`;

function loadSeen(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
    return new Set(Array.isArray(raw) ? raw.map(String) : []);
  } catch {
    return new Set();
  }
}

function markSeen(ids: string[]): void {
  try {
    const seen = [...loadSeen(), ...ids];
    localStorage.setItem(SEEN_KEY, JSON.stringify(seen.slice(-200)));
  } catch {
    // quota — worst case a strat re-imports after a delete
  }
}

/** Finished runs → the roster. Clicked runs always land (the human asked for
    that strat); loop runs only when they cleared the bench bar. A run already
    seen — including one whose strat the user deleted — never lands twice. */
function adoptNewRuns(runs: AutoRun[]): number {
  const seen = loadSeen();
  const existing = new Set(loadIndexes().map((i) => i.id));
  const adopted: string[] = [];
  for (const run of runs) {
    if (run.status !== "done" || !run.strat || seen.has(run.id)) continue;
    if (!(run.origin === "click" || run.passed)) continue;
    if (!existing.has(run.strat.id)) saveIndex(run.strat);
    adopted.push(run.id);
  }
  if (adopted.length > 0) {
    markSeen(adopted);
    window.dispatchEvent(new Event("strat-updated"));
  }
  return adopted.length;
}

export default function AutoStratPanel() {
  const [state, setState] = useState<AutoState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRuns, setShowRuns] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(API, { headers: authHeaders() });
      if (!res.ok) return; // signed out or gate closed — stay quiet
      const s = (await res.json()) as AutoState;
      setState(s);
      adoptNewRuns(s.runs);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void poll(),
        s.active || s.settings.enabled ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    } catch {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void poll(), POLL_IDLE_MS);
    }
  }, []);

  useEffect(() => {
    void poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  const randomRun = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API}?run=1`, {
        method: "POST", headers: authHeaders(), body: "{}",
      });
      const body = (await res.json()) as { error?: string };
      if (!res.ok) setError(body.error || `HTTP ${res.status}`);
      await poll();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleAuto = async () => {
    if (!state) return;
    setError(null);
    try {
      const res = await fetch(API, {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ enabled: !state.settings.enabled }),
      });
      if (res.ok) setState((await res.json()) as AutoState);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const running = !!state?.active;
  const enabled = state?.settings.enabled === true;
  const doneRuns = (state?.runs || []).filter((r) => r.status !== "running").slice(0, 5);
  const last = doneRuns[0];
  const w7 = (r: AutoRun) => r.windows?.find((w) => w.days === 7) || r.windows?.[r.windows.length - 1];

  return (
    <div className="mt-0.5">
      <div className="flex items-stretch gap-1">
        <button
          onClick={() => void randomRun()}
          disabled={busy || running}
          className="flex-1 rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-3 py-2 text-left text-[11px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors disabled:opacity-50"
          title="One factory run now: the agent picks traders off the live board, backtests the recipe over 1/3/7 days, and the strat lands above — paused, with its numbers"
        >
          {running ? "⚄ BUILDING…" : "⚄ RANDOM NEW STRAT"}
        </button>
        <button
          onClick={() => void toggleAuto()}
          className={`shrink-0 rounded-[var(--radius-sm)] border px-2 py-2 text-[9.5px] font-mono font-semibold tracking-[0.08em] transition-colors ${
            enabled
              ? "border-green-400/60 text-green-400"
              : "border-pixel-border text-pixel-gray hover:text-pixel-white"
          }`}
          title={enabled
            ? `AUTO is ON — a factory run every ${state?.settings.intervalSecs ?? 60}s; only strats that beat the bench land in the list. Click to stop.`
            : "AUTO is OFF — click to run the factory on a 1-minute loop (spends inference every run)"}
        >
          AUTO {enabled ? "ON" : "OFF"}
        </button>
      </div>

      {error && (
        <div className="px-3 pt-1 text-[10px] font-mono text-red-400 break-words">{error}</div>
      )}

      {last && (
        <button
          onClick={() => setShowRuns((v) => !v)}
          className="w-full px-3 pt-1 text-left text-[10px] font-mono text-pixel-gray hover:text-pixel-white"
          title="Recent factory runs — click to expand"
        >
          {last.status === "failed" ? (
            <span className="text-red-400/90">last: failed — {(last.error || "").slice(0, 60)}</span>
          ) : (
            <>
              last: <span className="text-pixel-white">{last.strat?.name || "?"}</span>
              {w7(last) && (
                <span className={(w7(last)!.pnl ?? 0) >= 0 ? " text-green-400" : " text-red-400"}>
                  {" "}{money(w7(last)!.pnl)} / {w7(last)!.days}d
                </span>
              )}
              {last.passed ? " · PASS" : last.origin === "click" ? " · saved" : " · skipped"}
            </>
          )}
          <span className="opacity-50"> {showRuns ? "▾" : "▸"}</span>
        </button>
      )}

      {showRuns && doneRuns.length > 1 && (
        <div className="px-3 pb-1">
          {doneRuns.slice(1).map((r) => (
            <div key={r.id} className="text-[9.5px] font-mono text-pixel-gray/80 truncate">
              {r.status === "failed"
                ? `✕ ${(r.error || "failed").slice(0, 50)}`
                : `${r.passed ? "✓" : "·"} ${r.strat?.name || "?"}${
                    w7(r) ? ` ${money(w7(r)!.pnl)}/${w7(r)!.days}d` : ""} (${r.origin})`}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
