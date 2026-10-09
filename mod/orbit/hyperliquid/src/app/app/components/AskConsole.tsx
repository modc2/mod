"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { askStatus, askStream, AskEvent, AskStatus } from "../lib/api";
import { useWallet } from "../lib/wallet";

// The desk agent, transcript + composer. One implementation, several homes:
// the /ask page (full width), the right-hand dock (compact column), the /chat
// page (mode="chat": the general chatbot — any topic, read-only toolbox,
// multi-turn via the Claude session id the stream hands back), and the strats
// board (mode="strats": the strat copilot — multi-turn AND action-capable, so
// it can create and manage invest positions when actions are on).

// One transcript entry. Tool calls render inline between the model's text so
// you can see what the answer was actually built from.
type Turn =
  | { kind: "you"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool"; name: string; args: Record<string, any>; done?: boolean; error?: boolean }
  | { kind: "note"; text: string; bad?: boolean };

const EXAMPLES = [
  "who are the top 5 traders by 7-day ROI, and what are they holding?",
  "what's the BTC orderbook look like right now?",
  "which vaults have the best APR above $1M TVL?",
  "analyze 0x… — is this trader worth copying?",
];

const CHAT_EXAMPLES = [
  "explain funding rates like I'm new to perps",
  "what's the difference between cross and isolated margin?",
  "what's BTC trading at right now?",
  "how should I think about position sizing?",
];

const STRAT_EXAMPLES = [
  "what's the best strat on the board right now, and why?",
  "backtest $1,000 on the top recommended trader over 30 days",
  "how are my strats doing?",
  "put $500 paper on the best vault so I can watch it first",
];

// The model writes light markdown whatever you tell it, so render the marks
// it actually uses — **bold**, `code`, and [label](/route) — instead of
// leaking the syntax. A link whose target starts with "/" is one of our own
// pages (the NAV protocol the agent is taught), so it becomes real in-app
// navigation; any other link target is untrusted and renders as plain text.
function RichText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]\n]+\]\([^()\s]+\))/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**"))
          return <strong key={i} className="text-ink font-semibold">{p.slice(2, -2)}</strong>;
        if (p.startsWith("`") && p.endsWith("`"))
          return <code key={i} className="font-mono text-accent">{p.slice(1, -1)}</code>;
        const link = /^\[([^\]]+)\]\(([^()\s]+)\)$/.exec(p);
        if (link) {
          const [, label, href] = link;
          return href.startsWith("/") ? (
            <Link key={i} href={href}
              className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent transition-colors">
              {label}
            </Link>
          ) : (
            <span key={i}>{label}</span>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

function ToolChip({ t, compact }: { t: Extract<Turn, { kind: "tool" }>; compact?: boolean }) {
  const args = Object.entries(t.args)
    .filter(([, v]) => v !== "" && v != null)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" ");
  const state = t.error ? "border-loss/40 text-loss" : t.done ? "border-accent/30 text-accent" : "border-white/[0.08] text-muted";
  return (
    <div className={`inline-flex items-baseline gap-2 pill font-mono normal-case tracking-normal max-w-full ${state}`}>
      <span className={t.done || t.error ? "" : "animate-pulse"}>{t.error ? "✕" : t.done ? "✓" : "•"}</span>
      <span>{t.name}</span>
      {args && <span className={`text-dim truncate ${compact ? "max-w-[18ch]" : "max-w-[46ch]"}`}>{args}</span>}
    </div>
  );
}

export default function AskConsole({
  compact = false,
  mode = "desk",
}: {
  compact?: boolean;
  mode?: "desk" | "chat" | "strats";
}) {
  const chat = mode === "chat";
  const strats = mode === "strats";
  // Chat and the strat copilot are conversations; the desk is one-shot.
  const multiTurn = chat || strats;
  const { token } = useWallet();
  const [status, setStatus] = useState<AskStatus | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [act, setAct] = useState(false);
  const [running, setRunning] = useState(false);
  // The Claude session of this conversation — set by the first reply's
  // start/done event, sent back on every later turn so the thread remembers.
  const session = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);

  useEffect(() => { askStatus().then(setStatus).catch(() => setStatus({ ready: false })); }, []);
  useEffect(() => { tail.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const push = (t: Turn) => setTurns((prev) => [...prev, t]);

  const onEvent = (ev: AskEvent) => {
    if ((ev.type === "start" || ev.type === "done") && ev.session_id)
      session.current = ev.session_id;
    if (ev.type === "text") push({ kind: "text", text: ev.text });
    else if (ev.type === "tool") push({ kind: "tool", name: ev.name, args: ev.args });
    else if (ev.type === "tool_done")
      // Close out the most recent open tool call.
      setTurns((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          const t = next[i];
          if (t.kind === "tool" && !t.done) { next[i] = { ...t, done: true, error: ev.error }; break; }
        }
        return next;
      });
    else if (ev.type === "done")
      push({ kind: "note", text: `${ev.turns ?? "?"} turns · ${((ev.ms ?? 0) / 1000).toFixed(1)}s` });
    else if (ev.type === "error") push({ kind: "note", text: ev.error, bad: true });
  };

  const send = async () => {
    const question = q.trim();
    if (!question || running) return;
    push({ kind: "you", text: question });
    setQ("");
    setRunning(true);
    abort.current = new AbortController();
    try {
      await askStream(
        chat
          ? { question, mode: "chat", session: session.current ?? undefined }
          : strats
          ? { question, mode: "strats", act, session: session.current ?? undefined }
          : { question, act },
        onEvent,
        abort.current.signal,
      );
    } catch (e: any) {
      if (e?.name !== "AbortError") push({ kind: "note", text: String(e?.message ?? e), bad: true });
    } finally {
      setRunning(false);
      abort.current = null;
    }
  };

  const blocked = !token;

  return (
    <div className={compact ? "flex flex-col h-full min-h-0 gap-2" : "space-y-4"}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        {status && (
          <span className="pill font-mono" title={status.hint ?? undefined}>
            <span className={`h-1.5 w-1.5 rounded-full mr-1.5 ${status.ready ? "bg-accent live-dot" : "bg-warn"}`} />
            {status.ready
              ? `${compact ? "" : status.model + " · "}${(status.read_tools ?? 0) + (act ? status.write_tools ?? 0 : 0)} tools`
              : "agent offline"}
          </span>
        )}
        <div className="flex items-center gap-2">
          {turns.length > 0 && (
            <button
              className="btn !px-2"
              onClick={() => { setTurns([]); session.current = null; }}
              title={multiTurn ? "Start a fresh conversation" : "Clear transcript"}
            >
              {multiTurn ? "new chat" : "clear"}
            </button>
          )}
          {!chat && (
            <button
              className={act ? "btn-danger" : "btn"}
              disabled={blocked}
              title={
                blocked
                  ? "sign in to enable actions"
                  : act
                  ? strats
                    ? "actions ON — the copilot can create, resize, pause and close your strats"
                    : "write tools ON — the agent can place orders and move funds"
                  : strats
                  ? "read-only: the copilot can look and recommend, not invest — turn on to create and manage"
                  : "read-only: only GET-backed tools"
              }
              onClick={() => setAct((v) => !v)}
            >
              {act ? "actions on" : "read-only"}
            </button>
          )}
        </div>
      </div>

      {!status?.ready && status?.hint && (
        <div className="panel p-3 text-xs text-warn">{status.hint}</div>
      )}
      {act && !chat && (
        <div className="panel p-3 text-[11px] text-loss leading-snug">
          {strats
            ? <>Actions on: the copilot can invest real money, resize, pause and
              close positions with your wallet&apos;s agent key. Say &quot;paper&quot; to
              simulate instead of trade.</>
            : <>Action mode: the agent can place orders, move funds and edit follows
              with your wallet&apos;s agent key. It confirms nothing with you first.</>}
        </div>
      )}

      <div className={`panel p-4 overflow-y-auto space-y-3 ${compact ? "flex-1 min-h-0" : "min-h-[22rem] max-h-[60vh]"}`}>
        {turns.length === 0 && (
          <div className="space-y-2">
            <div className="text-xs text-dim uppercase tracking-wider">try</div>
            {(chat ? CHAT_EXAMPLES : strats ? STRAT_EXAMPLES : EXAMPLES).map((e) => (
              <button key={e} className={`block text-left text-muted hover:text-accent transition-colors ${compact ? "text-xs leading-snug" : "text-sm"}`}
                onClick={() => setQ(e)}>
                → {e}
              </button>
            ))}
          </div>
        )}
        {turns.map((t, i) =>
          t.kind === "you" ? (
            <div key={i} className={`text-ink border-l-2 border-accent/50 pl-3 ${compact ? "text-xs" : "text-sm"}`}>{t.text}</div>
          ) : t.kind === "text" ? (
            <div key={i} className={`text-muted whitespace-pre-wrap leading-relaxed ${compact ? "text-xs" : "text-sm"}`}>
              <RichText text={t.text} />
            </div>
          ) : t.kind === "tool" ? (
            <div key={i}><ToolChip t={t} compact={compact} /></div>
          ) : (
            <div key={i} className={`text-[11px] font-mono ${t.bad ? "text-loss" : "text-dim"}`}>{t.text}</div>
          ),
        )}
        <div ref={tail} />
      </div>

      <div className="flex items-center gap-2">
        <input
          className="input flex-1 min-w-0"
          placeholder={
            blocked
              ? "sign in with your wallet to ask"
              : chat
              ? "ask anything — the conversation keeps context…"
              : strats
              ? act
                ? "tell the copilot what to invest, pause, resize or close…"
                : "ask the copilot — find, compare, backtest strats…"
              : act
              ? "tell the agent what to do…"
              : compact
              ? "ask the desk — markets, or the code…"
              : "ask about traders, markets, vaults, your account — or how the code works…"
          }
          value={q}
          disabled={blocked}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        {running ? (
          <button className="btn-danger shrink-0" onClick={() => abort.current?.abort()}>stop</button>
        ) : (
          <button className="btn-primary shrink-0" disabled={blocked || !q.trim()} onClick={send}>{multiTurn ? "send" : "ask"}</button>
        )}
      </div>
    </div>
  );
}
