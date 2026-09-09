"use client";

// STRAT LAB — the sidebar's window onto /api/lab: an agent that researches the
// leaderboard, designs candidate strats, backtests each on the server bench
// and iterates until the data clears the confidence bar (or it says plainly
// that it doesn't). The agent never writes anything; a confident verdict shows
// up here as a card with ADOPT, and only that press creates a strat.

import { useCallback, useEffect, useRef, useState } from "react";

import { getAccessToken } from "../lib/access";
import { saveIndex } from "../lib/indexStore";
import type { IndexTrader, SavedIndex } from "../lib/types";

const LAB_API = "/polymarket/api/lab";
const POLL_MS = 4000;

interface LabStep {
  kind: "tool" | "note";
  tool?: string;
  input?: string;
  text?: string;
}

interface LabRun {
  id: string;
  goal: string;
  status: "running" | "done" | "died";
  startedAt: number;
  steps: LabStep[];
  verdict?: {
    confident?: boolean;
    best?: { name?: string; params?: Record<string, unknown> };
    evidence?: string[];
    notes?: string;
  };
  finalText?: string;
  costUsd?: number;
  error?: string;
}

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

/** The verdict's winning params, materialized as a saved strat. Only the
    shape is enforced here — the numbers already survived the bench. */
function adoptVerdict(run: LabRun): SavedIndex | null {
  const best = run.verdict?.best;
  if (!best?.params) return null;
  const p = best.params;
  const traders: IndexTrader[] = (Array.isArray(p.traders) ? p.traders : [])
    .map((t: unknown) => {
      const address = typeof t === "string" ? t : (t as { address?: string })?.address;
      if (typeof address !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(address)) return null;
      const weight = typeof t === "object" && t !== null ? Number((t as { weight?: number }).weight) : 1;
      return { address: address.toLowerCase(), weight: weight > 0 ? weight : 1 };
    })
    .filter((t): t is IndexTrader => t !== null);
  const now = Date.now();
  const idx: SavedIndex = {
    ...(p as Partial<SavedIndex>),
    id: now.toString(36),
    name: (best.name || (typeof p.name === "string" ? p.name : "") || "Lab strat").slice(0, 64),
    traders,
    liveEnabled: false, // adopting is saving, never starting
    createdAt: now,
    updatedAt: now,
  } as SavedIndex;
  saveIndex(idx);
  window.dispatchEvent(new Event("strat-updated"));
  return idx;
}

export default function StratLab() {
  const [open, setOpen] = useState(false);
  const [goal, setGoal] = useState("");
  const [run, setRun] = useState<LabRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adopted, setAdopted] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchRun = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${LAB_API}?id=${encodeURIComponent(id)}`, { headers: authHeaders() });
      if (res.ok) setRun(await res.json() as LabRun);
    } catch {
      // transient — next poll retries
    }
  }, []);

  // On first open, surface the latest run (a finished verdict is worth
  // showing even days later; a running one resumes its live feed).
  useEffect(() => {
    if (!open || run) return;
    void (async () => {
      try {
        const res = await fetch(LAB_API, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json() as { runs?: Array<{ id: string }> };
        if (data.runs?.[0]) void fetchRun(data.runs[0].id);
      } catch {
        // nothing to restore
      }
    })();
  }, [open, run, fetchRun]);

  // Live feed while a run is going.
  useEffect(() => {
    if (run?.status !== "running") return;
    timer.current = setTimeout(() => void fetchRun(run.id), POLL_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [run, fetchRun]);

  const start = async () => {
    setBusy(true);
    setError(null);
    setAdopted(null);
    try {
      const res = await fetch(LAB_API, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(goal.trim() ? { goal: goal.trim() } : {}),
      });
      const data = await res.json() as { run?: { id: string }; error?: string };
      if (!res.ok || !data.run) {
        setError(data.error || `lab failed (${res.status})`);
        return;
      }
      void fetchRun(data.run.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!run) return;
    await fetch(`${LAB_API}?id=${encodeURIComponent(run.id)}`, { method: "DELETE", headers: authHeaders() }).catch(() => {});
    void fetchRun(run.id);
  };

  const running = run?.status === "running";
  const verdict = run?.status === "done" ? run.verdict : undefined;
  const tools = run?.steps.filter((s) => s.kind === "tool") ?? [];
  const lastNote = [...(run?.steps ?? [])].reverse().find((s) => s.kind === "note");

  return (
    <div className="mt-1 pt-1.5 border-t border-pixel-border/60">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1 text-left text-[9.5px] font-mono font-semibold tracking-[0.14em] text-pixel-gray/80 hover:text-green-400 transition-colors"
      >
        <span className="flex-1">STRAT LAB — AGENT FINDS &amp; PROVES A STRAT</span>
        {running && <span className="h-1.5 w-1.5 rounded-full bg-green-400 animate-pulse" title="run in progress" />}
        <span>{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="px-3 pb-2 space-y-2">
          {!running && (
            <div className="flex gap-1.5">
              <input
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !busy) void start(); }}
                placeholder="goal (optional) — e.g. crypto markets only"
                className="flex-1 min-w-0 rounded-[var(--radius-sm)] border border-pixel-border bg-transparent px-2 py-1.5 text-[11px] font-mono text-pixel-white placeholder:text-pixel-gray/50 focus:border-green-400/60 focus:outline-none"
              />
              <button
                onClick={() => void start()}
                disabled={busy}
                title="The agent surveys existing backtests, researches the leaderboard, designs candidates and backtests each with walk-forward until the evidence clears the confidence bar — then reports here."
                className="shrink-0 rounded-[var(--radius-sm)] border border-green-400/60 px-2.5 py-1.5 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 disabled:opacity-40 transition-colors"
              >
                {busy ? "…" : "RUN"}
              </button>
            </div>
          )}

          {error && <div className="text-[10.5px] font-mono text-red-400">{error}</div>}

          {running && run && (
            <div className="rounded-[var(--radius-sm)] border border-pixel-border px-2.5 py-2 space-y-1.5">
              <div className="flex items-center gap-2 text-[10px] font-mono text-pixel-gray">
                <span className="text-green-400">RESEARCHING</span>
                <span className="flex-1 truncate" title={run.goal}>{run.goal}</span>
                <button onClick={() => void stop()} className="text-pixel-gray hover:text-red-400">STOP</button>
              </div>
              <div className="flex flex-wrap gap-1">
                {tools.slice(-10).map((s, i) => (
                  <span key={i} title={s.input} className="rounded border border-pixel-border/70 px-1.5 py-0.5 text-[9px] font-mono text-pixel-gray">
                    {s.tool}
                  </span>
                ))}
              </div>
              {lastNote?.text && (
                <div className="text-[10.5px] font-mono text-pixel-gray/90 whitespace-pre-wrap max-h-24 overflow-y-auto">
                  {lastNote.text}
                </div>
              )}
              <div className="text-[9px] font-mono text-pixel-gray/60">
                {tools.length} tool call{tools.length === 1 ? "" : "s"} · backtests run on the server bench · nothing is changed until you adopt
              </div>
            </div>
          )}

          {run?.status === "died" && (
            <div className="text-[10.5px] font-mono text-red-400">
              the agent process died before finishing — start a new run
            </div>
          )}

          {run?.status === "done" && (
            <div className={`rounded-[var(--radius-sm)] border px-2.5 py-2 space-y-1.5 ${verdict?.confident ? "border-green-400/60" : "border-pixel-border"}`}>
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-mono font-semibold tracking-[0.12em] ${verdict?.confident ? "text-green-400" : "text-yellow-400/90"}`}>
                  {verdict ? (verdict.confident ? "CONFIDENT" : "NOT CONFIDENT") : "NO VERDICT"}
                </span>
                {verdict?.best?.name && (
                  <span className="flex-1 truncate text-[11px] font-mono text-pixel-white">{verdict.best.name}</span>
                )}
                {typeof run.costUsd === "number" && (
                  <span className="text-[9px] font-mono text-pixel-gray/60">${run.costUsd.toFixed(2)}</span>
                )}
              </div>
              {(verdict?.evidence ?? []).slice(0, 6).map((line, i) => (
                <div key={i} className="text-[10px] font-mono text-pixel-gray leading-snug">· {line}</div>
              ))}
              {verdict?.notes && (
                <div className="text-[10px] font-mono text-pixel-gray/70 leading-snug">{verdict.notes}</div>
              )}
              {!verdict && run.error && (
                <div className="text-[10px] font-mono text-red-400">{run.error}</div>
              )}
              {!verdict && !run.error && run.finalText && (
                <div className="text-[10px] font-mono text-pixel-gray/80 whitespace-pre-wrap max-h-32 overflow-y-auto">
                  {run.finalText.slice(0, 1500)}
                </div>
              )}
              <div className="flex items-center gap-2 pt-0.5">
                {verdict?.confident && verdict.best?.params && !adopted && (
                  <button
                    onClick={() => {
                      const idx = adoptVerdict(run);
                      setAdopted(idx ? idx.name : null);
                      if (!idx) setError("verdict had no adoptable params");
                    }}
                    className="rounded-[var(--radius-sm)] border border-green-400/60 px-2.5 py-1 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 transition-colors"
                  >
                    ADOPT AS STRAT
                  </button>
                )}
                {adopted && (
                  <span className="text-[10px] font-mono text-green-400">saved as “{adopted}” — it starts paused</span>
                )}
                <button
                  onClick={() => { setRun(null); setAdopted(null); }}
                  className="ml-auto text-[10px] font-mono text-pixel-gray hover:text-pixel-white"
                >
                  NEW RUN
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
