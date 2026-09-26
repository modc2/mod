"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { AgentApproval, AgentEvent, AgentStatus, StratProposal } from "../lib/types";
import {
  askAgent,
  createStrat,
  decideApproval,
  fetchAgentStatus,
  fetchPendingApprovals,
  fmtPct,
  fmtTao,
  shortSs58,
} from "../lib/api";
import { useSidebar } from "../context/SidebarContext";

/** Transcript rows. Tool calls are part of the record, not a spinner. */
type Item =
  | { kind: "you"; text: string }
  | { kind: "agent"; text: string }
  | { kind: "tool"; name: string; args: Record<string, unknown>; state: "run" | "ok" | "err" }
  | { kind: "strat"; strat: StratProposal; savedId?: string }
  // A write the agent is asking for. It has not run: the agent is blocked
  // on the server until this row is answered.
  | { kind: "approval"; approval: AgentApproval }
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

  // A reload does not cancel a parked write — the request lives on the
  // server. Re-read the pending set so a restored transcript is actionable
  // instead of a picture of a button, and so a card that was decided (or
  // expired) while the tab was gone stops pretending it is live.
  useEffect(() => {
    fetchPendingApprovals()
      .then(({ pending }) => {
        const live = new Map(pending.map((a) => [a.id, a]));
        setItems((cur) =>
          cur.map((row) =>
            row.kind === "approval" && row.approval.state === "pending"
              ? {
                  ...row,
                  approval:
                    live.get(row.approval.id) ??
                    { ...row.approval, state: "declined" as const,
                      note: row.approval.note || "expired while the console was closed" },
                }
              : row,
          ),
        );
      })
      .catch(() => {});
  }, []);

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
        case "approval":
          push({ kind: "approval", approval: ev.approval });
          break;
        case "approval_done":
          // Settle the card wherever the decision came from — this tab, a
          // second tab, or the request expiring on its own.
          setItems((cur) =>
            cur.map((row) =>
              row.kind === "approval" && row.approval.id === ev.id
                ? { ...row, approval: { ...row.approval, state: ev.state, note: ev.note } }
                : row,
            ),
          );
          break;
        case "ping":
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
        // Carry the resolved sleeve, so the basket opens in the strat maker
        // showing the same money the agent sized it with.
        alloc_tao: t.alloc_tao ?? null,
        enabled: true,
      })),
      sizing: strat.sizing ?? "tao",
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

  /** Answer a parked write. The agent is blocked on this call; the note is
      handed to it as the reason, which is what makes a decline a
      conversation rather than a dead end. */
  async function answer(id: string, approve: boolean, note: string) {
    const settle = (a: Partial<AgentApproval>) =>
      setItems((cur) =>
        cur.map((row) =>
          row.kind === "approval" && row.approval.id === id
            ? { ...row, approval: { ...row.approval, ...a } }
            : row,
        ),
      );
    settle({ state: approve ? "approved" : "declined", note });
    try {
      const got = await decideApproval(id, approve, note);
      settle(got);
    } catch (e: unknown) {
      settle({ state: "pending" });
      push({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    }
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
        {status?.ready && !!status.write_tools?.length && (
          <span
            className="pixel-badge border-amber-400 text-amber-400"
            title={`asks first, every time: ${status.write_tools.join(", ")}`}
          >
            {status.write_tools.length} NEED YOUR OK
          </span>
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

      {status?.ready && status.auth_note && (
        <div className="pixel-panel-amber p-3 arcade-prose arcade-prose-sm text-amber-400">
          {status.auth_note}
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
          item.kind === "approval" ? (
            <ApprovalCard
              key={item.approval.id}
              approval={item.approval}
              onAnswer={(ok, note) => { void answer(item.approval.id, ok, note); }}
            />
          ) : item.kind === "strat" ? (
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

        {busy && !items.some((x) => x.kind === "approval" && x.approval.state === "pending") && (
          <div className="font-mono text-[12px] text-green-400">▌ thinking…</div>
        )}
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

function Row({ item }: { item: Exclude<Item, { kind: "strat" } | { kind: "approval" }> }) {
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
        <span className="pixel-badge text-pixel-gray">{fmtTao(s.capital_tao)}</span>
        {item.savedId && (
          <span className="pixel-badge border-green-400 text-green-400">SAVED</span>
        )}
      </div>

      <p className="arcade-prose arcade-prose-sm">{s.thesis}</p>

      <ul className="space-y-1">
        {s.traders.map((t) => (
          <li key={t.ss58} className="border-t-2 border-pixel-border pt-1 first:border-t-0 first:pt-0">
            <div className="flex items-center gap-2 text-[12px] font-mono min-w-0">
              {/* The money, then the share of the book it represents. */}
              <span className="text-cyan-400 w-16 shrink-0" title={`${t.share_pct}% of the basket`}>
                {t.alloc_tao != null ? fmtTao(t.alloc_tao) : `${t.share_pct}%`}
              </span>
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
              <div className="arcade-prose arcade-prose-sm pl-16">{t.why}</div>
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

/** The whole point of this console: a write the agent wants to make, sitting
 *  still until you answer it.
 *
 *  The agent is blocked on the server while this renders — the call has not
 *  been made. DECLINE is not a cancel button either: the reason you type is
 *  handed back as the tool result, so the next thing the agent says is an
 *  answer to it.
 */
function ApprovalCard({ approval, onAnswer }: {
  approval: AgentApproval;
  onAnswer: (approve: boolean, note: string) => void;
}) {
  const [note, setNote] = useState("");
  // Server epoch vs browser epoch is not worth trusting — anchor the
  // countdown on the seconds the server said were left, when we said it.
  const [deadline] = useState(() => Date.now() + approval.expires_in * 1000);
  const [now, setNow] = useState(Date.now());
  const pending = approval.state === "pending";

  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [pending]);

  const left = Math.max(0, Math.round((deadline - now) / 1000));
  const clock = left >= 60 ? `${Math.floor(left / 60)}m ${left % 60}s` : `${left}s`;
  const frame =
    !pending ? "pixel-panel"
    : approval.risk === "high" ? "pixel-panel-red"
    : "pixel-panel-amber";
  const tone =
    !pending ? "text-pixel-gray"
    : approval.risk === "high" ? "text-red-400"
    : "text-amber-400";

  const args = Object.entries(approval.args).filter(([, v]) => v != null && v !== "");

  return (
    <div className={`${frame} p-3 space-y-2`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`font-display text-[13px] ${tone}`}>
          {pending ? "APPROVE?" : approval.state === "approved" ? "APPROVED" : "DECLINED"}
        </span>
        <span className="pixel-badge text-pixel-gray">{approval.tool}</span>
        {pending && approval.risk === "high" && (
          <span className="pixel-badge border-red-400 text-red-400">SPENDS TAO</span>
        )}
        {pending && (
          <span className="pixel-badge text-pixel-gray ml-auto" title="declines itself when it runs out">
            {clock}
          </span>
        )}
      </div>

      <p className="arcade-prose arcade-prose-sm text-pixel-white">{approval.summary}</p>

      {args.length > 0 && (
        <ul className="font-mono text-[11px] text-pixel-gray space-y-[2px]">
          {args.map(([k, v]) => (
            <li key={k} className="truncate" title={`${k}: ${JSON.stringify(v)}`}>
              {k} = {typeof v === "object" ? JSON.stringify(v) : String(v)}
            </li>
          ))}
        </ul>
      )}

      {pending ? (
        <>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); onAnswer(false, note); }
            }}
            placeholder="why not? (sent to the agent — 'halve it', 'not until I check the tape')"
            className="pixel-input-sm w-full font-mono text-[12px]"
          />
          <div className="flex flex-wrap gap-2">
            <button
              className="pixel-btn text-[10px] px-3 py-1 border-green-400 text-green-400"
              onClick={() => onAnswer(true, note)}
            >
              APPROVE
            </button>
            <button
              className="pixel-btn text-[10px] px-3 py-1"
              onClick={() => onAnswer(false, note)}
            >
              DECLINE
            </button>
          </div>
          <p className="arcade-prose arcade-prose-sm">
            Nothing has run. The agent is waiting on this answer — and takes a
            no for an answer.
          </p>
        </>
      ) : (
        approval.note && (
          <div className="font-mono text-[11px] text-pixel-gray break-words">
            &ldquo;{approval.note}&rdquo;
          </div>
        )
      )}
    </div>
  );
}
