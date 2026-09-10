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

/** One replay window off the bench — the fields the panel reads. */
interface BenchWindow {
  days: number;
  pnl: number;
  roi: number;
  trades: number;
  fees?: number;
  note?: string;
  forward?: { verdict?: string };
  settlement?: { unverified_usd?: number };
}

interface BenchResult {
  candidate: { name: string; traders: number; capital: number };
  windows: BenchWindow[];
  warming: string[];
}

/** The VIBE box holds either plain words (→ DRAFT) or a params object
    (→ TEST). JSON is the disambiguator, same spirit as the top bar. */
function parseParams(text: string): Record<string, unknown> | null {
  const t = text.trim();
  if (!t.startsWith("{")) return null;
  try {
    const o = JSON.parse(t) as unknown;
    return o && typeof o === "object" && !Array.isArray(o) ? (o as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

const money = (n: number) => `${n < 0 ? "-" : "+"}$${Math.abs(n).toFixed(0)}`;

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

/** A param set, materialized as a saved strat. Only the shape is enforced
    here — the numbers already survived the bench. Shared by the agent
    verdict's ADOPT and the VIBE editor's SAVE: both are the same human act. */
function saveParamsAsStrat(p: Record<string, unknown>, name?: string): SavedIndex {
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
    name: (name || (typeof p.name === "string" ? p.name : "") || "Lab strat").slice(0, 64),
    traders,
    liveEnabled: false, // adopting is saving, never starting
    createdAt: now,
    updatedAt: now,
  } as SavedIndex;
  saveIndex(idx);
  window.dispatchEvent(new Event("strat-updated"));
  return idx;
}

function adoptVerdict(run: LabRun): SavedIndex | null {
  const best = run.verdict?.best;
  if (!best?.params) return null;
  return saveParamsAsStrat(best.params, best.name);
}

export default function StratLab() {
  const [open, setOpen] = useState(false);
  const [goal, setGoal] = useState("");
  const [run, setRun] = useState<LabRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adopted, setAdopted] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── VIBE — write one yourself ──────────────────────────────────
  const [vibeOpen, setVibeOpen] = useState(false);
  const [vibe, setVibe] = useState("");
  const [vibeNote, setVibeNote] = useState<string | null>(null);
  const [vibeBusy, setVibeBusy] = useState<"draft" | "test" | null>(null);
  const [vibeError, setVibeError] = useState<string | null>(null);
  const [bench, setBench] = useState<BenchResult | null>(null);
  const [benched, setBenched] = useState<Record<string, unknown> | null>(null);
  const [vibeSaved, setVibeSaved] = useState<string | null>(null);
  const vibeParams = parseParams(vibe);

  const draft = async () => {
    setVibeBusy("draft");
    setVibeError(null);
    setVibeNote(null);
    setVibeSaved(null);
    try {
      const res = await fetch(`${LAB_API}?draft=1`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ ask: vibe.trim() }),
      });
      const data = await res.json() as { note?: string; params?: Record<string, unknown>; error?: string };
      if (!res.ok || !data.params) {
        setVibeError(data.error || `draft failed (${res.status})`);
        return;
      }
      setVibe(JSON.stringify(data.params, null, 2));
      setVibeNote(data.note || null);
      setBench(null);
      setBenched(null);
    } catch (e) {
      setVibeError(e instanceof Error ? e.message : String(e));
    } finally {
      setVibeBusy(null);
    }
  };

  const test = async () => {
    if (!vibeParams) return;
    setVibeBusy("test");
    setVibeError(null);
    setVibeSaved(null);
    try {
      const res = await fetch(`${LAB_API}?candidate=1`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ params: vibeParams, windows: [1, 3, 7] }),
      });
      const data = await res.json() as BenchResult & { error?: string };
      if (!res.ok || !Array.isArray(data.windows)) {
        setVibeError(data.error || `bench failed (${res.status})`);
        return;
      }
      setBench(data);
      setBenched(vibeParams); // SAVE stores exactly what was tested
    } catch (e) {
      setVibeError(e instanceof Error ? e.message : String(e));
    } finally {
      setVibeBusy(null);
    }
  };

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

          {/* ── VIBE — write one yourself. Words get drafted into params by
              one model turn; params get replayed on the same bench the agent
              uses; SAVE stores exactly what was tested, paused. ── */}
          <div className="pt-1 border-t border-pixel-border/40">
            <button
              onClick={() => setVibeOpen((v) => !v)}
              className="w-full flex items-center gap-2 py-0.5 text-left text-[9px] font-mono font-semibold tracking-[0.14em] text-pixel-gray/70 hover:text-green-400 transition-colors"
            >
              <span className="flex-1">VIBE — WRITE ONE YOURSELF</span>
              <span>{vibeOpen ? "−" : "+"}</span>
            </button>
            {vibeOpen && (
              <div className="space-y-1.5 pt-1">
                <textarea
                  value={vibe}
                  onChange={(e) => { setVibe(e.target.value); setVibeSaved(null); }}
                  placeholder={"describe a strat in words — or paste/edit params JSON\ne.g. copy these two wallets but only entries under 60¢, exit at +25%"}
                  rows={vibeParams ? 8 : 3}
                  spellCheck={false}
                  className="w-full resize-y rounded-[var(--radius-sm)] border border-pixel-border bg-transparent px-2 py-1.5 text-[10.5px] font-mono text-pixel-white placeholder:text-pixel-gray/50 focus:border-green-400/60 focus:outline-none"
                />
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => void draft()}
                    disabled={!vibe.trim() || vibeBusy !== null}
                    title="One model turn turns your words into the exact params JSON the bench replays — nothing is tested or saved yet"
                    className="rounded-[var(--radius-sm)] border border-pixel-border px-2.5 py-1 text-[10px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 disabled:opacity-40 transition-colors"
                  >
                    {vibeBusy === "draft" ? "DRAFTING…" : "DRAFT PARAMS"}
                  </button>
                  <button
                    onClick={() => void test()}
                    disabled={!vibeParams || vibeBusy !== null}
                    title={vibeParams
                      ? "Replay these params over 1/3/7-day windows on the server bench — fees, funnel and walk-forward included; nothing is saved"
                      : "TEST needs params JSON — draft first, or paste a params object"}
                    className="rounded-[var(--radius-sm)] border border-green-400/60 px-2.5 py-1 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 disabled:opacity-40 transition-colors"
                  >
                    {vibeBusy === "test" ? "BENCHING…" : "TEST"}
                  </button>
                  {vibeBusy === "test" && (
                    <span className="text-[9px] font-mono text-amber-400/90 animate-pulse">
                      minutes on cold feeds…
                    </span>
                  )}
                </div>
                {vibeNote && (
                  <div className="text-[10px] font-mono text-pixel-gray/80 leading-snug">{vibeNote}</div>
                )}
                {vibeError && <div className="text-[10.5px] font-mono text-red-400">{vibeError}</div>}

                {bench && (
                  <div className="rounded-[var(--radius-sm)] border border-pixel-border px-2.5 py-2 space-y-1">
                    <div className="flex items-center gap-2 text-[10px] font-mono">
                      <span className="text-pixel-white truncate">{bench.candidate.name}</span>
                      <span className="text-pixel-gray/70 shrink-0">
                        {bench.candidate.traders} trader{bench.candidate.traders === 1 ? "" : "s"} · ${bench.candidate.capital}
                      </span>
                    </div>
                    {bench.windows.map((w) => (
                      <div key={w.days} className="flex items-baseline gap-2 text-[10px] font-mono">
                        <span className="w-6 shrink-0 text-pixel-gray">{w.days}D</span>
                        <span className={w.roi >= 0 ? "text-green-400" : "text-red-400"}>
                          {w.roi >= 0 ? "+" : ""}{w.roi.toFixed(1)}%
                        </span>
                        <span className="text-pixel-gray">{money(w.pnl)} · {w.trades} trades</span>
                        {typeof w.fees === "number" && w.fees > 0 && (
                          <span className="text-pixel-gray/70">fees ${w.fees.toFixed(0)}</span>
                        )}
                        {w.forward?.verdict && (
                          <span className={w.forward.verdict === "held" ? "text-green-400/90" : "text-yellow-400/80"}>
                            fwd {w.forward.verdict}
                          </span>
                        )}
                        {(w.settlement?.unverified_usd ?? 0) > 0 && (
                          <span className="text-yellow-400/70" title="Unresolved legs marked at last price — this pnl is provisional">
                            ~${(w.settlement!.unverified_usd as number).toFixed(0)} unverified
                          </span>
                        )}
                      </div>
                    ))}
                    {bench.warming.length > 0 && (
                      <div className="text-[9.5px] font-mono text-amber-400/90 leading-snug">
                        {bench.warming.length} trader{bench.warming.length === 1 ? "" : "s"} still warming — these numbers
                        are a floor; TEST again in a few minutes.
                      </div>
                    )}
                    <div className="flex items-center gap-2 pt-0.5">
                      {benched && !vibeSaved && (
                        <button
                          onClick={() => {
                            const idx = saveParamsAsStrat(benched);
                            setVibeSaved(idx.name);
                          }}
                          title="Save exactly the params this bench result tested — the strat starts paused"
                          className="rounded-[var(--radius-sm)] border border-green-400/60 px-2.5 py-1 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 transition-colors"
                        >
                          SAVE AS STRAT
                        </button>
                      )}
                      {vibeSaved && (
                        <span className="text-[10px] font-mono text-green-400">saved as “{vibeSaved}” — it starts paused</span>
                      )}
                      <span className="ml-auto text-[9px] font-mono text-pixel-gray/60">
                        same bench the agent uses
                      </span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
