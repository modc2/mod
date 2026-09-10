"use client";

import { useEffect, useRef, useState } from "react";
import { askStatus, askStream, AskEvent, AskStatus } from "../lib/api";
import { useWallet } from "../lib/wallet";

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

// The model writes light markdown whatever you tell it, so render the two
// marks it actually uses (**bold**, `code`) instead of leaking the asterisks.
function RichText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") && p.endsWith("**") ? (
          <strong key={i} className="text-ink font-semibold">{p.slice(2, -2)}</strong>
        ) : p.startsWith("`") && p.endsWith("`") ? (
          <code key={i} className="font-mono text-accent">{p.slice(1, -1)}</code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function ToolChip({ t }: { t: Extract<Turn, { kind: "tool" }> }) {
  const args = Object.entries(t.args)
    .filter(([, v]) => v !== "" && v != null)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" ");
  const state = t.error ? "border-loss/40 text-loss" : t.done ? "border-accent/30 text-accent" : "border-white/[0.08] text-muted";
  return (
    <div className={`inline-flex items-baseline gap-2 pill font-mono normal-case tracking-normal ${state}`}>
      <span className={t.done || t.error ? "" : "animate-pulse"}>{t.error ? "✕" : t.done ? "✓" : "•"}</span>
      <span>{t.name}</span>
      {args && <span className="text-dim truncate max-w-[46ch]">{args}</span>}
    </div>
  );
}

export default function AskPage() {
  const { token } = useWallet();
  const [status, setStatus] = useState<AskStatus | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [act, setAct] = useState(false);
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);

  useEffect(() => { askStatus().then(setStatus).catch(() => setStatus({ ready: false })); }, []);
  useEffect(() => { tail.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const push = (t: Turn) => setTurns((prev) => [...prev, t]);

  const onEvent = (ev: AskEvent) => {
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
      await askStream({ question, act }, onEvent, abort.current.signal);
    } catch (e: any) {
      if (e?.name !== "AbortError") push({ kind: "note", text: String(e?.message ?? e), bad: true });
    } finally {
      setRunning(false);
      abort.current = null;
    }
  };

  const blocked = !token;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight">Ask</h1>
          <p className="text-xs text-muted mt-1 max-w-2xl">
            An agent whose only toolbox is this module&apos;s own MCP server. It
            answers from live tool calls — never from memory — and every call
            goes back through the same API gate your wallet does, so it can
            never see or do more than you can.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {status && (
            <span className="pill font-mono" title={status.hint ?? undefined}>
              <span className={`h-1.5 w-1.5 rounded-full mr-1.5 ${status.ready ? "bg-accent live-dot" : "bg-warn"}`} />
              {status.ready
                ? `${status.model} · ${(status.read_tools ?? 0) + (act ? status.write_tools ?? 0 : 0)} tools`
                : "agent offline"}
            </span>
          )}
          <button
            className={act ? "btn-danger" : "btn"}
            disabled={blocked}
            title={
              blocked
                ? "sign in to enable actions"
                : act
                ? "write tools ON — the agent can place orders and move funds"
                : "read-only: only GET-backed tools"
            }
            onClick={() => setAct((v) => !v)}
          >
            {act ? "actions on" : "read-only"}
          </button>
        </div>
      </div>

      {!status?.ready && status?.hint && (
        <div className="panel p-3 text-xs text-warn">{status.hint}</div>
      )}
      {act && (
        <div className="panel p-3 text-xs text-loss">
          Action mode: the agent can place orders, move funds and edit follows
          with your wallet&apos;s agent key. It confirms nothing with you first.
        </div>
      )}

      <div className="panel p-4 min-h-[22rem] max-h-[60vh] overflow-y-auto space-y-3">
        {turns.length === 0 && (
          <div className="space-y-2">
            <div className="text-xs text-dim uppercase tracking-wider">try</div>
            {EXAMPLES.map((e) => (
              <button key={e} className="block text-left text-sm text-muted hover:text-accent transition-colors"
                onClick={() => setQ(e)}>
                → {e}
              </button>
            ))}
          </div>
        )}
        {turns.map((t, i) =>
          t.kind === "you" ? (
            <div key={i} className="text-sm text-ink border-l-2 border-accent/50 pl-3">{t.text}</div>
          ) : t.kind === "text" ? (
            <div key={i} className="text-sm text-muted whitespace-pre-wrap leading-relaxed">
              <RichText text={t.text} />
            </div>
          ) : t.kind === "tool" ? (
            <div key={i}><ToolChip t={t} /></div>
          ) : (
            <div key={i} className={`text-[11px] font-mono ${t.bad ? "text-loss" : "text-dim"}`}>{t.text}</div>
          ),
        )}
        <div ref={tail} />
      </div>

      <div className="flex items-center gap-2">
        <input
          className="input flex-1"
          placeholder={blocked ? "sign in with your wallet to ask" : act ? "tell the agent what to do…" : "ask about traders, markets, vaults, your account…"}
          value={q}
          disabled={blocked}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        {running ? (
          <button className="btn-danger" onClick={() => abort.current?.abort()}>stop</button>
        ) : (
          <button className="btn-primary" disabled={blocked || !q.trim()} onClick={send}>ask</button>
        )}
      </div>
      <p className="text-[11px] text-dim">
        Same tools over MCP: <span className="font-mono">claude mcp add hyperliquid -- hyperliquid-api --stdio</span>
      </p>
    </div>
  );
}
