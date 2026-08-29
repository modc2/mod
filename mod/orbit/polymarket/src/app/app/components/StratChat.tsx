"use client";

// STRAT CHAT — every strat has one, and it can change the strat.
//
// The console has a lot of knobs, and the honest problem with knobs is that
// knowing what you want ("stop buying longshots", "why did this only trade
// four times yesterday?") is not the same as knowing which of them to turn.
// This panel closes that gap: you say it in words, an agent answers in the
// strat's own terms, and when the ask implies a settings change it proposes a
// PATCH — a plain list of `param: old → new` lines.
//
// The patch is a PROPOSAL. Nothing changes until APPLY, the diff is shown in
// full before you press it, and every value has already been validated twice
// (server and here) against lib/stratPatch's whitelist — so the worst an agent
// can do is suggest something you decline. Anything it proposed that ISN'T a
// real parameter is shown as a rejection rather than hidden, because an agent
// confidently inventing a setting is exactly the thing you want to see.
//
// The thread is kept per strat in localStorage (small, capped) so the context
// of "we already discussed the stop-loss" survives a reload — the shared
// modc2 origin is quota-contended, so writes are best-effort and bounded.

import { useCallback, useEffect, useRef, useState } from "react";
import { getAccessToken } from "../lib/access";
import { fetchWorkerBacktests } from "../lib/hubCache";
import { HUB_BACKTEST_DAYS } from "../lib/hubReplay";
import { fetchLiveSessions } from "../lib/liveSessions";
import { updateIndex } from "../lib/indexStore";
import { applyPatch, describeEntry, validatePatch, type PatchEntry } from "../lib/stratPatch";
import type { SavedIndex } from "../lib/types";

const CHAT_API = "/polymarket/api/strat-chat";
/** Turns kept per strat. The server only sends the last dozen anyway. */
const MAX_KEPT = 24;

interface Turn {
  role: "user" | "assistant";
  content: string;
  /** Assistant turns only: the validated patch it proposed, and what was
      thrown away getting there. */
  entries?: PatchEntry[];
  rejected?: string[];
  rationale?: string;
  /** Set once the user applies (or dismisses) this turn's patch. */
  resolved?: "applied" | "dismissed";
}

function threadKey(id: string): string {
  return `poly_strat_chat_${id}`;
}

function loadThread(id: string): Turn[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(threadKey(id));
    return raw ? (JSON.parse(raw) as Turn[]) : [];
  } catch {
    return [];
  }
}

function saveThread(id: string, turns: Turn[]): void {
  try {
    localStorage.setItem(threadKey(id), JSON.stringify(turns.slice(-MAX_KEPT)));
  } catch {
    // Shared-origin quota. A lost thread costs context, not correctness.
  }
}

/** Openers that are actually about THIS strat — a copy strat and an
    origination strat have different first questions. */
function prompts(strat: SavedIndex): string[] {
  if (strat.momentum) {
    return [
      "Why isn't this trading more often?",
      "Make the entries stricter — I want fewer, higher-conviction trades.",
      "The stop-loss feels too tight for a 5-minute candle. What would you set it to?",
    ];
  }
  return [
    "Why did this only trade a handful of times?",
    "Stop buying longshots — I only want likely winners.",
    "Cut the risk per trade roughly in half.",
  ];
}

/** The strat's latest backtest, as a paragraph the agent can reason over.
 *
 *  Read from the background worker's cache — the same numbers the hub card
 *  shows, no replay and no upstream requests. This is what lets "why did this
 *  only trade four times?" get answered with the strat's own entry funnel
 *  rather than a plausible-sounding guess. */
async function backtestContext(strat: SavedIndex): Promise<string | undefined> {
  try {
    const cache = await fetchWorkerBacktests(HUB_BACKTEST_DAYS);
    const bt = cache?.results?.[strat.id];
    if (!bt) return undefined;
    const lines = [
      `Window: ${bt.days}d. P&L $${bt.pnl.toFixed(2)} (${bt.roi.toFixed(1)}% of $${bt.capital}) over ${bt.trades} trades, ${bt.skipped} skipped.`,
    ];
    if (bt.funnel) {
      const f = bt.funnel;
      lines.push(
        `Entry funnel: ${f.observed} candidates seen · ${f.executed} taken · ${f.gated} blocked by this strat's gates · ${f.outranked} outranked · ${f.skipped} unplaceable.`,
      );
      const reasons = Object.entries(f.reasons).sort((a, b) => b[1] - a[1]).slice(0, 4);
      if (reasons.length) {
        lines.push(`Why they were dropped: ${reasons.map(([r, n]) => `${r} ×${n}`).join(", ")}.`);
      }
    }
    if (bt.forward) {
      lines.push(
        `Walk-forward: the window before it made $${bt.forward.pnl.toFixed(2)} over ${bt.forward.trades} trades — verdict "${bt.forward.verdict}".`,
      );
    }
    if (bt.tape) {
      lines.push(
        `Price tape: ${bt.tape.markets}/${bt.tape.expected} ${bt.tape.mode === "candles" ? "candles" : "markets"} at ${Math.round(bt.tape.fidelityMs / 60_000)}-minute bars${bt.tape.note ? ` (${bt.tape.note})` : ""}.`,
      );
    }
    if (bt.note) lines.push(`Note: ${bt.note}`);
    return lines.join("\n");
  } catch {
    return undefined;
  }
}

/** What this strat's live session is doing right now, if it has one. */
async function liveContext(strat: SavedIndex, eoa: string | null): Promise<string | undefined> {
  if (!eoa) return undefined;
  try {
    const sessions = await fetchLiveSessions(eoa);
    const s = sessions.find((x) => x.strategyId === strat.id);
    if (!s) return "No engine session for this strat.";
    const st = s.state ?? {};
    // Same two words the console shows, so the assistant's answer and the
    // header switch can't describe the same session differently.
    return [
      `Running: ${s.running ? "yes" : "no"}. Mode: ${s.config?.autoExecute ? "LIVE (real orders)" : "TEST (no orders placed)"}.`,
      `Cycles: ${st.cycleCount ?? 0}. Orders placed: ${st.totalOrdersPlaced ?? 0}. Account value: ${st.accountValue == null ? "unknown" : `$${st.accountValue.toFixed(2)}`}.`,
      `Open positions: ${Object.keys(st.positions ?? {}).length}.`,
    ].join("\n");
  } catch {
    return undefined;
  }
}

export default function StratChat({
  strat,
  eoa,
  onClose,
  onApplied,
}: {
  strat: SavedIndex;
  /** Signed-in wallet, for reading this strat's live session. Optional — the
      chat works without one, it just can't talk about a running session. */
  eoa?: string | null;
  onClose: () => void;
  /** Fired after a patch is written, so the opener can re-render / re-backtest. */
  onApplied?: (next: SavedIndex) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Evidence, fetched once when the panel opens: the worker's latest backtest
  // for this strat and its live session. Both are cheap reads of things the
  // server already computed, and both are optional — the chat opens instantly
  // and gains context a beat later.
  const [context, setContext] = useState<{ backtest?: string; live?: string }>({});
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setTurns(loadThread(strat.id)); }, [strat.id]);
  useEffect(() => {
    let alive = true;
    void Promise.all([backtestContext(strat), liveContext(strat, eoa ?? null)])
      .then(([backtest, live]) => { if (alive) setContext({ backtest, live }); });
    return () => { alive = false; };
    // Keyed by id: the strat object identity churns on every list poll, and
    // re-fetching the same context each time would be pure noise.
  }, [strat.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, busy]);

  const send = useCallback(async (text: string) => {
    const body = text.trim();
    if (!body || busy) return;
    setError(null);
    setDraft("");
    const withUser: Turn[] = [...turns, { role: "user", content: body }];
    setTurns(withUser);
    saveThread(strat.id, withUser);
    setBusy(true);
    try {
      const token = getAccessToken();
      const res = await fetch(CHAT_API, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          strat,
          messages: withUser.map((t) => ({ role: t.role, content: t.content })),
          context,
        }),
      });
      const data = await res.json() as {
        reply?: string; entries?: PatchEntry[]; rejected?: string[];
        rationale?: string; error?: string;
      };
      if (!res.ok) {
        setError(data.error || `chat failed (${res.status})`);
        return;
      }
      // Validate again on this side: the diff the user reads must be produced
      // by the same rules that will write it, not by the server's word.
      const checked = validatePatch(strat, Object.fromEntries((data.entries ?? []).map((e) => [e.path, e.to])));
      const next: Turn[] = [...withUser, {
        role: "assistant",
        content: data.reply ?? "",
        entries: checked.entries,
        rejected: [...(data.rejected ?? []), ...checked.rejected],
        rationale: data.rationale,
      }];
      setTurns(next);
      saveThread(strat.id, next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [turns, busy, strat, context]);

  const apply = useCallback((i: number) => {
    const turn = turns[i];
    if (!turn?.entries?.length) return;
    const next = applyPatch(strat, turn.entries);
    updateIndex(strat.id, next);
    // Same broadcast every other mutation uses — the sidebar, the hub, the
    // BACKTEST tab and the LIVE checklist all listen for it.
    window.dispatchEvent(new Event("strat-updated"));
    const marked = turns.map((t, j) => (j === i ? { ...t, resolved: "applied" as const } : t));
    setTurns(marked);
    saveThread(strat.id, marked);
    onApplied?.(next);
  }, [turns, strat, onApplied]);

  const dismiss = useCallback((i: number) => {
    const marked = turns.map((t, j) => (j === i ? { ...t, resolved: "dismissed" as const } : t));
    setTurns(marked);
    saveThread(strat.id, marked);
  }, [turns, strat.id]);

  const clear = useCallback(() => {
    setTurns([]);
    saveThread(strat.id, []);
  }, [strat.id]);

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div
        className="relative flex h-[min(80vh,640px)] w-full max-w-[560px] flex-col rounded-[var(--radius-md)] border border-pixel-border bg-pixel-black shadow-2xl"
        style={{ width: "min(560px, 100%)" }}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-pixel-border px-3 py-2">
          <div className="min-w-0">
            <div className="truncate text-[11.5px] font-mono font-semibold tracking-[0.1em] text-pixel-white">
              CHAT · {strat.name}
            </div>
            <div className="text-[9.5px] font-mono text-pixel-gray">
              Ask for a change in words — it proposes, you apply.
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {turns.length > 0 && (
              <button onClick={clear} className="text-[10px] font-mono text-pixel-gray hover:text-amber-400" title="Clear this thread">
                CLEAR
              </button>
            )}
            <button onClick={onClose} className="text-[15px] leading-none text-pixel-gray hover:text-red-400" title="Close">
              ×
            </button>
          </div>
        </div>

        {/* Thread */}
        <div ref={scrollRef} className="flex-1 space-y-2.5 overflow-y-auto px-3 py-3">
          {turns.length === 0 && (
            <div className="space-y-2">
              <div className="text-[10.5px] font-mono leading-relaxed text-pixel-gray">
                This agent can read {strat.name}&apos;s settings, its latest backtest and its live
                session, and can propose changes to its parameters. It can&apos;t edit the
                watchlist, place an order, or start a session.
              </div>
              {prompts(strat).map((p) => (
                <button
                  key={p}
                  onClick={() => void send(p)}
                  className="block w-full rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-2.5 py-1.5 text-left text-[10.5px] font-mono text-pixel-gray hover:border-green-400/60 hover:text-green-400"
                >
                  {p}
                </button>
              ))}
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} className={t.role === "user" ? "flex justify-end" : ""}>
              <div
                className={`max-w-[92%] rounded-[var(--radius-sm)] px-2.5 py-1.5 text-[11px] font-mono leading-relaxed whitespace-pre-wrap ${
                  t.role === "user"
                    ? "bg-pixel-white/[0.08] text-pixel-white"
                    : "text-pixel-gray"
                }`}
              >
                {t.content}

                {t.role === "assistant" && !!t.entries?.length && (
                  <div className="mt-2 rounded-[var(--radius-sm)] border border-green-400/40 bg-green-400/[0.06] p-2">
                    <div className="text-[9.5px] font-semibold tracking-[0.12em] text-green-400">
                      PROPOSED CHANGE
                    </div>
                    <div className="mt-1 space-y-0.5">
                      {t.entries.map((e) => (
                        <div key={e.path} className="text-[10.5px] text-pixel-white tabular-nums">
                          {describeEntry(e)}
                        </div>
                      ))}
                    </div>
                    {t.rationale && (
                      <div className="mt-1 text-[10px] text-pixel-gray">{t.rationale}</div>
                    )}
                    {t.resolved ? (
                      <div className={`mt-1.5 text-[10px] ${t.resolved === "applied" ? "text-green-400" : "text-pixel-gray"}`}>
                        {t.resolved === "applied" ? "✓ APPLIED" : "DISMISSED"}
                      </div>
                    ) : (
                      <div className="mt-1.5 flex gap-2">
                        <button
                          onClick={() => apply(i)}
                          className="rounded-[var(--radius-sm)] border border-green-400/60 px-2 py-0.5 text-[10px] font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10"
                        >
                          APPLY
                        </button>
                        <button
                          onClick={() => dismiss(i)}
                          className="rounded-[var(--radius-sm)] border border-pixel-border px-2 py-0.5 text-[10px] tracking-[0.1em] text-pixel-gray hover:text-pixel-white"
                        >
                          DISMISS
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {t.role === "assistant" && !!t.rejected?.length && (
                  <div className="mt-1.5 space-y-0.5">
                    {t.rejected.map((r, j) => (
                      <div key={j} className="text-[10px] text-amber-400/80">· {r}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          {busy && (
            <div className="text-[10.5px] font-mono text-pixel-gray">thinking…</div>
          )}
          {error && (
            <div className="rounded-[var(--radius-sm)] border border-red-400/50 px-2.5 py-1.5 text-[10.5px] font-mono text-red-400">
              {error}
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="shrink-0 border-t border-pixel-border p-2">
          <div className="flex gap-2">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(draft); } }}
              placeholder={busy ? "waiting for the agent…" : "What should this strat do differently?"}
              disabled={busy}
              className="flex-1 rounded-[var(--radius-sm)] border border-pixel-border bg-transparent px-2.5 py-1.5 text-[11px] font-mono text-pixel-white placeholder:text-pixel-gray/70 focus:border-green-400/60 focus:outline-none disabled:opacity-50"
            />
            <button
              onClick={() => void send(draft)}
              disabled={busy || !draft.trim()}
              className="rounded-[var(--radius-sm)] border border-pixel-border px-3 py-1.5 text-[10.5px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:border-green-400/60 hover:text-green-400 disabled:opacity-40"
            >
              SEND
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
