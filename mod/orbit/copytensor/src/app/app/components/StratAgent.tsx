"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { AgentEvent, AgentStatus, StratProposal } from "../lib/types";
import { askAgent, createStrat, fetchAgentStatus, fmtPct, fmtTao, shortSs58 } from "../lib/api";
import { useSidebar } from "../context/SidebarContext";

/** Transcript rows. Tool calls are part of the record, not a spinner. */
type Item =
  | { kind: "you"; text: string }
  | { kind: "agent"; text: string }
  | { kind: "tool"; name: string; args: Record<string, unknown>; state: "run" | "ok" | "err" }
  | { kind: "strat"; strat: StratProposal; savedId?: string }
  | { kind: "note"; text: string }
  | { kind: "error"; text: string };

const STORE_KEY = "copytensor:agent:v1";
// One origin is shared by every module on this host, so the transcript is
// capped rather than left to grow into the storage quota.
const KEEP = 60;

const EXAMPLES = [
  "Build me a 5-trader index of the best 7d performers with books over 1000 TAO",
  "Who is buying the top gainers right now? Make a strat that follows them",
  "I want low variance — mirror big diversified books only, 200 TAO",
];

export default function StratAgent() {
  const { openIndex } = useSidebar();
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const tailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchAgentStatus().then(setStatus).catch(() => {});
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) {
        const s = JSON.parse(raw);
        if (Array.isArray(s?.items)) setItems(s.items);
        if (typeof s?.sessionId === "string") setSessionId(s.sessionId);
      }
    } catch {}
  }, []);

  // Persist the transcript so a reload doesn't lose the basket you were
  // arguing about. The session id rides along — the conversation itself
  // lives on the server.
  useEffect(() => {
    try {
      localStorage.setItem(
        STORE_KEY,
        JSON.stringify({ items: items.slice(-KEEP), sessionId }),
      );
    } catch {}
  }, [items, sessionId]);

  useEffect(() => {
    tailRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [items]);

  const push = (item: Item) => setItems((cur) => [...cur, item]);

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    push({ kind: "you", text: q });
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const onEvent = (ev: AgentEvent) => {
      switch (ev.type) {
        case "start":
          setSessionId(ev.session_id);
          break;
        case "text":
          push({ kind: "agent", text: ev.text });
          break;
        case "tool":
          push({ kind: "tool", name: ev.name, args: ev.args, state: "run" });
          break;
        case "tool_done":
          // Mark the newest still-running call of that name — tools run in
          // parallel, and results come back out of order.
          setItems((cur) => {
            for (let i = cur.length - 1; i >= 0; i--) {
              const row = cur[i];
              if (row.kind !== "tool" || row.state !== "run") continue;
              if (ev.name && row.name !== ev.name) continue;
              const next = [...cur];
              next[i] = { ...row, state: ev.error ? "err" : "ok" };
              return next;
            }
            return cur;
          });
          break;
        case "strat":
          push({ kind: "strat", strat: ev.strat });
          break;
        case "done":
          setSessionId(ev.session_id);
          push({
            kind: "note",
            text: `${ev.turns} turns · ${(ev.ms / 1000).toFixed(1)}s${
              ev.cost_usd ? ` · $${ev.cost_usd.toFixed(3)}` : ""
            }`,
          });
          break;
        case "error":
          push({ kind: "error", text: ev.error });
          break;
      }
    };

    try {
      await askAgent(q, sessionId, onEvent, ctrl.signal);
    } catch (e: unknown) {
      if (!ctrl.signal.aborted)
        push({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  /** Land a proposal in the strat library — private, on the server, under
      this browser's owner key. Saving is not going live. */
  async function saveStrat(idx: number, strat: StratProposal): Promise<string> {
    const saved = await createStrat({
      name: strat.name,
      thesis: strat.thesis,
      traders: strat.traders.map((t) => ({
        ss58: t.ss58,
        label: t.label ?? null,
        weight: t.weight,
        enabled: true,
      })),
      daily_limit_tao: strat.capital_tao,
      max_tao_per_tx: strat.max_tao_per_tx,
      rebalance_threshold_pct: strat.rebalance_threshold_pct,
      poll_interval_sec: strat.poll_interval_sec,
    });
    setItems((cur) => {
      const next = [...cur];
      const row = next[idx];
      if (row?.kind === "strat") next[idx] = { ...row, savedId: saved.id };
      return next;
    });
    return saved.id;
  }

  function reset() {
    abortRef.current?.abort();
    setItems([]);
    setSessionId(null);
  }

  return (
    <div className="space-y-3">
      {/* Status strip */}
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`pixel-badge ${
            status?.ready ? "border-green-400 text-green-400" : "border-amber-400 text-amber-400"
          }`}
        >
          {status ? (status.ready ? `READY · ${status.model}` : "NO AUTH") : "…"}
        </span>
        {status?.ready && (
          <span className="pixel-badge text-pixel-gray">{status.tools.length} TOOLS</span>
        )}
        {sessionId && (
          <span className="pixel-badge text-pixel-gray" title={sessionId}>
            CHAT {sessionId.slice(0, 6)}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          {busy && (
            <button
              className="pixel-btn text-[10px] px-2 py-1"
              onClick={() => abortRef.current?.abort()}
            >
              STOP
            </button>
          )}
          {items.length > 0 && (
            <button className="pixel-btn text-[10px] px-2 py-1" onClick={reset}>
              NEW CHAT
            </button>
          )}
        </div>
      </div>

      {status && !status.ready && (
        <div className="pixel-panel-amber p-3 arcade-prose arcade-prose-sm text-amber-400">
          {status.hint}
        </div>
      )}

      {/* Transcript */}
      <div className="pixel-panel p-3 space-y-3 min-h-[280px]">
        {items.length === 0 && (
          <div className="space-y-3">
            {/* What it is and what it can't do is the page standfirst's job.
                Here, just the prompt — and three ways to start. */}
            <p className="arcade-prose arcade-prose-sm">
              Tell it what you want to own, or start from one of these:
            </p>
            <div className="flex flex-col gap-1">
              {EXAMPLES.map((e) => (
                <button
                  key={e}
                  className="pixel-btn text-[10px] px-2 py-1 text-left justify-start leading-4"
                  onClick={() => send(e)}
                  disabled={busy}
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}

        {items.map((item, i) =>
          item.kind === "strat" ? (
            <StratCard
              key={i}
              item={item}
              onSave={() => { void saveStrat(i, item.strat); }}
              onOpen={async () => openIndex(item.savedId || (await saveStrat(i, item.strat)))}
            />
          ) : (
            <Row key={i} item={item} />
          ),
        )}

        {busy && <div className="font-mono text-[12px] text-green-400">▌ thinking…</div>}
        <div ref={tailRef} />
      </div>

      {/* Composer */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="flex gap-2 items-stretch"
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; the shift-newline is there for a long brief.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          rows={2}
          placeholder={
            sessionId
              ? "follow up — 'drop the bottom two', 'make it 8 traders'…"
              : "what should this strat own?"
          }
          className="pixel-input flex-1 p-2 font-mono text-[13px] resize-y"
          disabled={busy || (status ? !status.ready : false)}
        />
        <button
          type="submit"
          className="pixel-btn px-4"
          disabled={busy || !input.trim() || (status ? !status.ready : false)}
        >
          ASK
        </button>
      </form>
    </div>
  );
}

// ── rows ─────────────────────────────────────────────────────────

function Row({ item }: { item: Exclude<Item, { kind: "strat" }> }) {
  if (item.kind === "you")
    return (
      <div className="font-mono text-[13px] text-green-400 break-words">
        <span className="text-pixel-gray">&gt; </span>
        {item.text}
      </div>
    );

  if (item.kind === "agent")
    return (
      <div className="arcade-prose arcade-prose-sm whitespace-pre-wrap break-words text-pixel-white">
        {item.text}
      </div>
    );

  if (item.kind === "tool") {
    const args = Object.entries(item.args)
      .map(([k, v]) => `${k}=${typeof v === "object" ? "…" : String(v)}`)
      .join(" ");
    const mark = item.state === "run" ? "▸" : item.state === "ok" ? "✓" : "✗";
    return (
      <div
        className={`font-mono text-[11px] truncate ${
          item.state === "err" ? "text-red-400" : "text-pixel-gray"
        }`}
        title={`${item.name} ${args}`}
      >
        {mark} {item.name} {args}
      </div>
    );
  }

  if (item.kind === "error")
    return (
      <div className="pixel-panel-red p-2 font-mono text-[11px] text-red-400 break-words">
        {item.text}
      </div>
    );

  return <div className="font-mono text-[10px] text-pixel-gray">{item.text}</div>;
}

function StratCard({ item, onSave, onOpen }: {
  item: Extract<Item, { kind: "strat" }>;
  onSave: () => void;
  onOpen: () => void;
}) {
  const s = item.strat;
  return (
    <div className="pixel-panel-cyan p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-display text-[13px] text-cyan-400">{s.name}</span>
        <span className="pixel-badge text-pixel-gray">{s.traders.length} TRADERS</span>
        <span className="pixel-badge text-pixel-gray">{fmtTao(s.capital_tao)}/DAY</span>
        {item.savedId && (
          <span className="pixel-badge border-green-400 text-green-400">SAVED</span>
        )}
      </div>

      <p className="arcade-prose arcade-prose-sm">{s.thesis}</p>

      <ul className="space-y-1">
        {s.traders.map((t) => (
          <li key={t.ss58} className="border-t-2 border-pixel-border pt-1 first:border-t-0 first:pt-0">
            <div className="flex items-center gap-2 text-[12px] font-mono min-w-0">
              <span className="text-cyan-400 w-12 shrink-0">{t.share_pct}%</span>
              <Link href={`/traders/${t.ss58}`} className="text-pixel-white truncate no-underline hover:text-green-400">
                {t.label || shortSs58(t.ss58)}
              </Link>
              <span className="ml-auto shrink-0 text-pixel-gray">
                {t.total_tao != null ? fmtTao(t.total_tao) : "—"}
              </span>
              <span
                className={`shrink-0 w-16 text-right ${
                  (t.change_7d ?? 0) >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {t.change_7d != null ? fmtPct(t.change_7d) : "—"}
              </span>
            </div>
            {t.why && (
              <div className="arcade-prose arcade-prose-sm pl-12">{t.why}</div>
            )}
          </li>
        ))}
      </ul>

      {s.warning && (
        <div className="font-mono text-[11px] text-amber-400">! {s.warning}</div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <button className="pixel-btn text-[10px] px-2 py-1" onClick={onSave} disabled={!!item.savedId}>
          {item.savedId ? "IN LIBRARY" : "SAVE TO LIBRARY"}
        </button>
        <button className="pixel-btn text-[10px] px-2 py-1" onClick={onOpen}>
          OPEN IN STRAT MAKER
        </button>
      </div>
      <p className="arcade-prose arcade-prose-sm">
        Saving is not going live — the strat maker is where you set the hotkey
        and hit ACTIVATE.
      </p>
    </div>
  );
}
