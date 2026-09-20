"use client";

// VIBE — describe a strat in words, get a backtest.
//
// The whole point is that it is ONE box and ONE button. The two halves that
// make it work (a model turn that writes the params, the bench that replays
// them over 1/3/7 days) used to be two presses with a JSON blob in between,
// which meant the fastest path to "is this idea any good?" ran through
// reading a parameter object. Nobody wants that answer; they want the
// numbers. So: words in, numbers out, SAVE if the numbers are good.
//
// The JSON is still there — folded, editable, retestable — because the moment
// you like a result you want to nudge one knob. It is just no longer in the
// way of the first look.

import { useEffect, useRef, useState } from "react";

import { getAccessToken } from "../lib/access";
import {
  type BenchResult, type VibeResult, money, parseParams, saveParamsAsStrat,
} from "../lib/stratDraft";

const LAB_API = "/polymarket/_api/lab";

/** Anyone can summon the vibe box: dispatch this event (focusVibe below) and
    it scrolls itself into view and puts the cursor in the words box. The tab
    header's ✧ VIBE button and the grid's dashed tile both use it — with a
    long roster this block lives below the fold, and "describe a strat" must
    never require scrolling past sixteen cards to find the box. */
export const VIBE_FOCUS_EVENT = "polymarket:vibe-focus";
export function focusVibe() {
  window.dispatchEvent(new Event(VIBE_FOCUS_EVENT));
}

/** A bench over traders whose tape this deployment has never cached comes back
    as a FLOOR — the replay saw silence, and the same call queued the fetch.
    The real answer lands a couple of minutes later, so the box waits for it
    instead of handing the owner a zero and a RETEST button. */
const WAIT_MS = 75_000;
const MAX_WAITS = 5;

/** Starting points. A blank box is the hardest prompt in the world, and each
    of these exercises a different half of the drafter — picking traders off
    the board, gating by price, gating by market. */
const EXAMPLES = [
  "copy the 3 most consistent traders on the board with $500",
  "the biggest winners, but only their buys under 30¢",
  "consistent traders, crypto markets only, exit at +25%",
];

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

/** The one-line verdict a person actually reads. The 7-day window is the one
    the confidence bar cares about; a shorter window is a rumor. */
function headline(bench: BenchResult): { text: string; good: boolean } | null {
  const w = bench.windows.find((x) => x.days === 7) || bench.windows[bench.windows.length - 1];
  if (!w) return null;
  if (w.trades === 0) {
    return { text: `no trades in ${w.days} days — this strat would have sat still`, good: false };
  }
  const held = w.forward?.verdict === "held";
  return {
    text: `${w.roi >= 0 ? "+" : ""}${w.roi.toFixed(1)}% over ${w.days} days · ${money(w.pnl)} on ${w.trades} trades`
      + (held ? " · held up the week before too" : ""),
    good: w.pnl > 0,
  };
}

/** A small trade count is a result, not an explanation — and "4 trades" reads
    like the leaders sat still when what actually happened is that a gate ate
    their flow. The bench's own funnel knows which one; say it in one line, in
    the terms the owner can act on. */
function whyFew(bench: BenchResult): string | null {
  const w = bench.windows.find((x) => x.days === 7) || bench.windows[bench.windows.length - 1];
  const f = w?.funnel;
  if (!f) return null;
  const observed = f.observed ?? 0;
  const copied = f.copied ?? 0;
  if (observed === 0 || copied / observed > 0.25) return null;
  const missed = observed - copied;
  const top = Object.entries(f.reasons || {}).sort((a, b) => b[1] - a[1])[0];
  if ((f.unplaceable ?? 0) > 0 && (top?.[0] || "").includes("SUB_SCALE")) {
    return `${missed} of their ${observed} entries were too small to copy at $${bench.candidate.capital}`
      + ` — your share of their bet rounded under the exchange minimum. More capital copies more of them.`;
  }
  if ((f.blocked_by_filters ?? 0) > missed / 2) {
    return `${f.blocked_by_filters} of their ${observed} entries were cut by this strat's own filters — loosen them to copy more.`;
  }
  if (top) return `${missed} of their ${observed} entries weren't copied — biggest reason: ${top[0]}.`;
  return null;
}

export default function StratVibe() {
  const [ask, setAsk] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VibeResult | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [edit, setEdit] = useState("");
  /** How many times the cold-feed retest has fired for this result. */
  const [waits, setWaits] = useState(0);
  const retestRef = useRef<((p?: Record<string, unknown>) => Promise<void>) | null>(null);

  /** The params SAVE would write: whatever the last bench actually replayed. */
  const benched = result?.bench ? result.params : null;
  const bench = result?.bench;
  const verdict = bench ? headline(bench) : null;

  /** One press: the words become params, the params get benched. */
  const run = async (text: string) => {
    const words = text.trim();
    if (!words) return;
    setBusy(true);
    setError(null);
    setSaved(null);
    setResult(null);
    setWaits(0);
    try {
      const res = await fetch(`${LAB_API}?vibe=1`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ ask: words, windows: [1, 3, 7] }),
      });
      const data = (await res.json()) as VibeResult;
      if (!res.ok || !data.params) {
        setError(data.error || `vibe failed (${res.status})`);
        return;
      }
      setResult(data);
      setEdit(JSON.stringify(data.params, null, 2));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  /** Re-run the bench on hand-edited params — no model turn, no new draft. */
  const retest = async (params?: Record<string, unknown>) => {
    const p = params || parseParams(edit);
    if (!p) {
      setError("the params box is not valid JSON");
      return;
    }
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const res = await fetch(`${LAB_API}?candidate=1`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ params: p, windows: [1, 3, 7] }),
      });
      const data = (await res.json()) as BenchResult & { error?: string };
      if (!res.ok || !Array.isArray(data.windows)) {
        setError(data.error || `bench failed (${res.status})`);
        return;
      }
      setResult((r) => ({ ...(r || {}), params: p, bench: data, benchError: undefined }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  retestRef.current = retest;

  // Wait out a warming bench on the owner's behalf: every WAIT_MS, replay the
  // same params again, until the feeds are cached or MAX_WAITS says a trader
  // this deployment cannot fetch is not going to warm up.
  const warmingCount = bench?.warming.length ?? 0;
  useEffect(() => {
    if (warmingCount === 0 || busy || waits >= MAX_WAITS) return;
    const t = setTimeout(() => {
      setWaits((n) => n + 1);
      void retestRef.current?.(result?.params);
    }, WAIT_MS);
    return () => clearTimeout(t);
  }, [warmingCount, busy, waits, result]);

  return (
    <div className="mt-1 space-y-1.5">
      <textarea
        value={ask}
        onChange={(e) => setAsk(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !busy) void run(ask);
        }}
        placeholder="describe a strat in plain words — e.g. copy the three best traders, only their cheap bets"
        rows={2}
        spellCheck={false}
        disabled={busy}
        className="w-full resize-y rounded-[var(--radius-sm)] border border-pixel-border bg-transparent px-2.5 py-1.5 text-[11px] font-mono text-pixel-white placeholder:text-pixel-gray/50 focus:border-green-400/60 focus:outline-none disabled:opacity-60"
      />

      <div className="flex items-center gap-1.5">
        <button
          onClick={() => void run(ask)}
          disabled={!ask.trim() || busy}
          title="Writes the strat from your words — picking real traders off the live board — then backtests it over 1, 3 and 7 days. Nothing is saved and nothing trades."
          className="rounded-[var(--radius-sm)] border border-green-400/60 px-3 py-1.5 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 disabled:opacity-40 transition-colors"
        >
          {busy ? "WORKING…" : "✧ BUILD & TEST"}
        </button>
        {busy && (
          <span className="text-[9.5px] font-mono text-amber-400/90 animate-pulse">
            writing it, then replaying a week of their trades…
          </span>
        )}
        {!busy && !result && (
          <span className="text-[9px] font-mono text-pixel-gray/50">⌘↵</span>
        )}
      </div>

      {/* A blank box is the hardest prompt there is. */}
      {!result && !busy && (
        <div className="flex flex-wrap gap-1">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => { setAsk(ex); void run(ex); }}
              className="rounded-full border border-pixel-border px-2 py-0.5 text-[9.5px] font-mono text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      {error && <div className="text-[10.5px] font-mono text-red-400 break-words">{error}</div>}

      {result && (
        <div className="rounded-[var(--radius-sm)] border border-pixel-border px-2.5 py-2 space-y-1">
          {/* What it built, and how it did — in that order, one line each. */}
          <div className="flex items-center gap-2 text-[11px] font-mono">
            <span className="truncate text-pixel-white">
              {bench?.candidate.name || String(result.params?.name || "candidate")}
            </span>
            {bench && (
              <span className="shrink-0 text-[10px] text-pixel-gray/70">
                {bench.candidate.traders} trader{bench.candidate.traders === 1 ? "" : "s"} · ${bench.candidate.capital}
              </span>
            )}
          </div>

          {verdict && (
            <div className={`text-[11px] font-mono font-semibold ${verdict.good ? "text-green-400" : "text-red-400"}`}>
              {verdict.text}
            </div>
          )}

          {result.note && (
            <div className="text-[10px] font-mono text-pixel-gray/80 leading-snug">{result.note}</div>
          )}

          {result.benchError && (
            <div className="text-[10.5px] font-mono text-yellow-400/90 leading-snug">
              drafted, but not tested: {result.benchError}
            </div>
          )}

          {/* The windows, for anyone who wants more than the headline. */}
          {bench?.windows.map((w) => (
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

          {bench && (bench.warming.length === 0) && whyFew(bench) && (
            <div className="text-[9.5px] font-mono text-pixel-gray/80 leading-snug">{whyFew(bench)}</div>
          )}

          {(bench?.warming.length ?? 0) > 0 && (
            <div className="flex items-center gap-2 text-[9.5px] font-mono text-amber-400/90 leading-snug">
              <span className="flex-1">
                {bench!.warming.length} trader{bench!.warming.length === 1 ? "" : "s"} still loading — these numbers are a
                floor, not the answer.
                {waits < MAX_WAITS
                  ? " Fetching their tape and re-testing on its own…"
                  : " Gave up waiting — their history isn't reachable right now."}
              </span>
              <button
                onClick={() => void retest(result.params)}
                disabled={busy}
                className="shrink-0 rounded border border-amber-400/50 px-1.5 py-0.5 text-amber-400/90 hover:bg-amber-400/10 disabled:opacity-40"
              >
                RETEST
              </button>
            </div>
          )}

          {(result.dropped?.length ?? 0) > 0 && (
            <div className="text-[9.5px] font-mono text-pixel-gray/70">
              {result.dropped!.length} address{result.dropped!.length === 1 ? "" : "es"} it named weren&apos;t on the
              board — dropped.
            </div>
          )}

          <div className="flex items-center gap-2 pt-0.5">
            {benched && !saved && (
              <button
                onClick={() => setSaved(saveParamsAsStrat(benched).name)}
                title="Save exactly the params this backtest ran — the strat lands in your list above, paused"
                className="rounded-[var(--radius-sm)] border border-green-400/60 px-2.5 py-1 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 transition-colors"
              >
                SAVE AS STRAT
              </button>
            )}
            {saved && (
              <span className="text-[10px] font-mono text-green-400">saved as “{saved}” — it starts paused</span>
            )}
            <button
              onClick={() => { setResult(null); setSaved(null); setError(null); setParamsOpen(false); }}
              className="text-[10px] font-mono text-pixel-gray hover:text-pixel-white"
            >
              TRY ANOTHER
            </button>
            <button
              onClick={() => setParamsOpen((v) => !v)}
              className="ml-auto text-[9.5px] font-mono text-pixel-gray/60 hover:text-pixel-white"
              title="The exact parameters — edit and retest without spending another model turn"
            >
              {paramsOpen ? "▾" : "▸"} params
            </button>
          </div>

          {paramsOpen && (
            <div className="space-y-1 pt-0.5">
              <textarea
                value={edit}
                onChange={(e) => setEdit(e.target.value)}
                rows={10}
                spellCheck={false}
                className="w-full resize-y rounded-[var(--radius-sm)] border border-pixel-border bg-transparent px-2 py-1.5 text-[10px] font-mono text-pixel-white focus:border-green-400/60 focus:outline-none"
              />
              <button
                onClick={() => void retest()}
                disabled={busy}
                className="rounded-[var(--radius-sm)] border border-pixel-border px-2.5 py-1 text-[10px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 disabled:opacity-40 transition-colors"
              >
                {busy ? "BENCHING…" : "RETEST EDITS"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
