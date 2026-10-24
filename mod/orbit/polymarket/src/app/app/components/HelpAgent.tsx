"use client";

// THE CONSOLE AGENT — a LEFT-hand sidebar, opened by the logo. It used to be
// a guide with no hands (/_api/help-agent); it is now the DESK CHAT
// (/_api/agent/chat): ask it anything — console or not — and let it read the
// board, manage strats and run the copy desk through the module's own MCP
// tools, streamed into this column as it works.
//
// THE OWNER ALWAYS KNOWS. Money-moving and strat-changing tool calls never
// run on the agent's say-so: they park at an approval gate UNDER the agent
// (src/mcp.py), surface here as a card with APPROVE / DECLINE and a note
// box, and fail closed (TTL ~3 min) if ignored. Strat create/update/delete
// are executed by THIS browser at the APPROVE click — private strats are
// encrypted with a key only this browser holds — through the same
// indexStore/stratPatch paths every button in the console uses.
//
// The column framing (dock ≥1280px / overlay below, portal to <body>,
// logo-toggled) is unchanged — see the git history for the original
// reasoning; it was right.

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter } from "next/navigation";
import { getAccessToken } from "../lib/access";
import { applyStratOp, isConsoleOp } from "../lib/agentStratOps";

const CHAT_API = "/polymarket/_api/agent/chat";
const APPROVALS_API = "/polymarket/_api/agent/approvals";

/** Anything can ask for the agent column by name. detail.ask (optional)
    hands it a question to send immediately — see lib/agentAsk.ts. */
export const OPEN_AGENT_EVENT = "poly-open-agent";

const DOCK_MQ = "(min-width: 1280px)";

// The conversation survives reloads — the agent is the console's pilot, and
// a pilot with amnesia on every page turn (its own pm_console_open navs
// included!) is useless. Approval cards are stored as-is: a restored
// "pending" card renders expired on its own countdown, never alive.
const CHAT_STORE = "poly_desk_chat_v1";
const STORE_MAX_ITEMS = 80;

// The agent may only turn the console's own pages. Anything else that rides
// a nav event (or a link parsed out of model text) is dropped on the floor.
const NAV_OK = /^\/(traders|strats|copy|markets|trades|docs)(\/|\?|$)|^\/$/;
export function isConsolePath(p: string): boolean {
  return NAV_OK.test(p) && !p.includes("//") && !p.includes(":");
}

interface Approval {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  kind: "money" | "spend" | "strat";
  summary: string;
  expires_at: number;
}

type Item =
  | { kind: "user"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool"; name: string; done: boolean; failed: boolean }
  | { kind: "nav"; path: string; label: string }
  | { kind: "approval"; a: Approval; state: "pending" | "approve" | "decline" | "expired"; outcome?: string }
  | { kind: "error"; text: string };

const GREETING: Item = {
  kind: "text",
  text:
    "ask me anything — this console, your strats, markets, whatever. I can take you to the right screen, research traders, create or change strats, and size the copy book. Anything that moves money or adds/removes a strat stops here first for your APPROVE.",
};

/** Suggestions follow the page — the agent should always offer the next
    sensible move from where the owner is standing. */
function suggestionsFor(pathname: string): string[] {
  if (/^\/traders\/0x/i.test(pathname)) {
    return [
      "should I copy this trader? backtest copying them first",
      "how much of their flow could I actually copy?",
      "add them to a strat for me",
    ];
  }
  if (pathname.startsWith("/strats")) {
    return [
      "which of my strats deserve the money right now?",
      "build me a strat from this month's steadiest traders",
      "clean up: which strats should I retire, and why?",
    ];
  }
  return [
    "what is my copy book doing right now?",
    "create a strat from the 3 steadiest traders this month",
    "show me where everything is — give me a tour",
  ];
}

function loadStoredChat(): { session: string | null; items: Item[] } | null {
  try {
    const raw = localStorage.getItem(CHAT_STORE);
    if (!raw) return null;
    const v = JSON.parse(raw) as { session?: unknown; items?: unknown };
    if (!Array.isArray(v.items) || v.items.length === 0) return null;
    return {
      session: typeof v.session === "string" ? v.session : null,
      items: v.items as Item[],
    };
  } catch {
    return null;
  }
}

function storeChat(session: string | null, items: Item[]) {
  try {
    if (items.length === 0) {
      localStorage.removeItem(CHAT_STORE);
      return;
    }
    localStorage.setItem(
      CHAT_STORE,
      JSON.stringify({ session, items: items.slice(-STORE_MAX_ITEMS) }),
    );
  } catch {
    // quota or private mode — the chat just won't survive the reload
  }
}

const KIND_STYLE: Record<Approval["kind"], { label: string; color: string }> = {
  money: { label: "MONEY", color: "#f87171" },
  strat: { label: "STRAT", color: "#60a5fa" },
  spend: { label: "SPEND", color: "#fbbf24" },
};

interface HelpAgentProps {
  /** Owned by TopBar, because the logo (NavMenu) is the toggle. */
  open: boolean;
  onClose: () => void;
}

export default function HelpAgent({ open, onClose }: HelpAgentProps) {
  const pathname = usePathname() || "";
  const router = useRouter();
  const [docked, setDocked] = useState(false);
  const [items, setItems] = useState<Item[]>([GREETING]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const sessionRef = useRef<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const restoredRef = useRef(false);

  // Restore the conversation (and the CLI session, so the model still
  // remembers) before the first paint with items.
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const stored = loadStoredChat();
    if (stored) {
      sessionRef.current = stored.session;
      setItems([GREETING, ...stored.items.filter((it) => it && typeof it === "object")]);
    }
  }, []);

  // Persist everything after the greeting (which is re-added on restore).
  useEffect(() => {
    if (!restoredRef.current) return;
    storeChat(sessionRef.current, items.slice(1));
  }, [items]);

  useEffect(() => {
    const mq = window.matchMedia(DOCK_MQ);
    setDocked(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setDocked(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const el = document.documentElement;
    if (open && docked) el.dataset.agentDock = "open";
    else delete el.dataset.agentDock;
    return () => { delete el.dataset.agentDock; };
  }, [open, docked]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    if (docked) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, docked, onClose]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [items, busy, open]);

  // One ticking clock for every pending card's countdown.
  const hasPending = items.some((i) => i.kind === "approval" && i.state === "pending");
  useEffect(() => {
    if (!hasPending) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [hasPending]);

  const push = useCallback((it: Item) => setItems((prev) => [...prev, it]), []);

  const patchApproval = useCallback((id: string, fn: (prev: Extract<Item, { kind: "approval" }>) => Item) => {
    setItems((prev) => prev.map((it) => (it.kind === "approval" && it.a.id === id ? fn(it) : it)));
  }, []);

  const ask = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || busy) return;
      push({ kind: "user", text: question });
      setDraft("");
      setBusy(true);
      try {
        const token = getAccessToken();
        const res = await fetch(CHAT_API, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ message: question, session: sessionRef.current || undefined, page: pathname }),
        });
        if (!res.ok || !res.body) {
          push({
            kind: "error",
            text: res.status === 401
              ? "sign in first — the desk agent runs on the owner's inference"
              : `the agent didn't answer (${res.status})`,
          });
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let sep: number;
          while ((sep = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, sep);
            buf = buf.slice(sep + 2);
            const line = frame.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            let ev: Record<string, unknown>;
            try {
              ev = JSON.parse(line.slice(6)) as Record<string, unknown>;
            } catch {
              continue;
            }
            handleEvent(ev);
          }
        }
      } catch (e) {
        push({ kind: "error", text: e instanceof Error ? e.message : String(e) });
      } finally {
        setBusy(false);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [busy, pathname, push],
  );

  function handleEvent(ev: Record<string, unknown>) {
    const type = ev.type;
    if (type === "start" || type === "done") {
      if (typeof ev.session_id === "string" && ev.session_id) sessionRef.current = ev.session_id;
      return;
    }
    if (type === "text" && typeof ev.text === "string") {
      push({ kind: "text", text: ev.text });
    } else if (type === "tool") {
      push({ kind: "tool", name: String(ev.name || "tool"), done: false, failed: false });
    } else if (type === "tool_done") {
      setItems((prev) => {
        const i = prev.map((x) => x).reverse().findIndex((x) => x.kind === "tool" && !x.done);
        if (i < 0) return prev;
        const idx = prev.length - 1 - i;
        const t = prev[idx] as Extract<Item, { kind: "tool" }>;
        return [...prev.slice(0, idx), { ...t, done: true, failed: Boolean(ev.error) }, ...prev.slice(idx + 1)];
      });
    } else if (type === "approval" && ev.approval && typeof ev.approval === "object") {
      const a = ev.approval as Approval;
      setItems((prev) =>
        prev.some((x) => x.kind === "approval" && x.a.id === a.id)
          ? prev
          : [...prev, { kind: "approval", a, state: "pending" }],
      );
    } else if (type === "approval_done") {
      const id = String(ev.id || "");
      const decision = ev.decision === "approve" ? "approve" : ev.decision === "expired" ? "expired" : "decline";
      patchApproval(id, (prevItem) =>
        prevItem.state === "pending" ? { ...prevItem, state: decision } : prevItem,
      );
    } else if (type === "nav") {
      // The agent turned the page (pm_console_open). Validated here too —
      // the browser is the last line, whatever rode the file channel.
      const path = String(ev.path || "");
      if (isConsolePath(path)) {
        push({ kind: "nav", path, label: String(ev.label || path) });
        router.push(path);
      }
    } else if (type === "error") {
      push({ kind: "error", text: String(ev.error || "something went wrong") });
    }
  }

  /** The APPROVE/DECLINE click. For strat ops, approving EXECUTES here —
      this browser holds the strat key — and the applied result rides the
      decision back to the model as its tool result. */
  const decide = useCallback(
    async (a: Approval, decision: "approve" | "decline", note: string) => {
      // Don't apply a strat op the gate has already given up on.
      if (decision === "approve" && Date.now() / 1000 > a.expires_at - 1) {
        patchApproval(a.id, (it) => ({ ...it, state: "expired" }));
        return;
      }
      let result: Record<string, unknown> | undefined;
      let outcome: string | undefined;
      if (decision === "approve" && isConsoleOp(a.tool)) {
        result = applyStratOp(a.tool, a.args);
        outcome = result.ok === false ? `couldn't apply: ${String(result.error || "unknown")}` : undefined;
      }
      try {
        const token = getAccessToken();
        const res = await fetch(APPROVALS_API, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ id: a.id, decision, note: note || undefined, result }),
        });
        if (!res.ok) {
          const err = (await res.json().catch(() => ({}))) as { error?: string };
          patchApproval(a.id, (it) => ({
            ...it,
            state: err.error === "expired" ? "expired" : it.state,
            outcome: `not recorded: ${err.error || res.status}${result ? " (change was already applied locally)" : ""}`,
          }));
          return;
        }
        patchApproval(a.id, (it) => ({ ...it, state: decision, outcome }));
      } catch (e) {
        patchApproval(a.id, (it) => ({ ...it, outcome: `not recorded: ${e instanceof Error ? e.message : String(e)}` }));
      }
    },
    [patchApproval],
  );

  const newChat = useCallback(() => {
    sessionRef.current = null;
    setItems([GREETING]);
    try { localStorage.removeItem(CHAT_STORE); } catch { /* fine */ }
  }, []);

  // Anywhere in the console can hand the agent a question (lib/agentAsk.ts):
  // the OPEN_AGENT_EVENT that opens the column may carry detail.ask. Ref'd so
  // the one listener always calls the current ask.
  const askRef = useRef(ask);
  askRef.current = ask;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const onAsk = (e: Event) => {
      const q = (e as CustomEvent).detail?.ask;
      if (typeof q !== "string" || !q.trim()) return;
      if (busyRef.current) setDraft(q); // mid-run: park it in the input instead of dropping it
      else void askRef.current(q);
    };
    window.addEventListener(OPEN_AGENT_EVENT, onAsk);
    return () => window.removeEventListener(OPEN_AGENT_EVENT, onAsk);
  }, []);

  if (!open) return null;

  const column = (
    <>
      <div
        className="flex items-center justify-between px-3 h-12 shrink-0 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="text-[11px] tracking-[0.16em] text-pixel-gray font-mono flex items-center gap-1.5">
          <svg className="w-[14px] h-[14px] text-green-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="4" y="8" width="16" height="12" rx="2" />
            <path d="M12 8V4" />
            <circle cx="12" cy="3" r="1" fill="currentColor" stroke="none" />
            <circle cx="9" cy="14" r="1" fill="currentColor" stroke="none" />
            <circle cx="15" cy="14" r="1" fill="currentColor" stroke="none" />
          </svg>
          AGENT
        </span>
        <span className="flex items-center gap-2">
          {items.length > 1 && (
            <button
              onClick={newChat}
              disabled={busy}
              title="Forget this conversation and start over"
              className="text-[10px] tracking-[0.1em] font-mono text-pixel-gray hover:text-green-400 disabled:opacity-40"
            >
              NEW CHAT
            </button>
          )}
          <button
            onClick={onClose}
            title="Close the agent (or click the logo again)"
            className="text-[13px] text-pixel-gray hover:text-pixel-white px-1"
          >
            x
          </button>
        </span>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto">
        {items.map((it, i) => {
          if (it.kind === "user" || it.kind === "text") {
            return (
              <div
                key={i}
                className={`px-3 py-2 text-[12px] font-mono leading-snug border-b whitespace-pre-wrap ${
                  it.kind === "user" ? "text-pixel-white" : "text-pixel-gray-light"
                }`}
                style={{ borderColor: "var(--border)" }}
              >
                {it.kind === "user" && <span className="text-green-400/80 mr-1.5">›</span>}
                {it.kind === "text" ? <AgentText text={it.text} onOpen={(p) => router.push(p)} /> : it.text}
              </div>
            );
          }
          if (it.kind === "nav") {
            return (
              <div key={i} className="px-3 py-1 text-[11px] font-mono border-b flex items-center gap-1.5"
                style={{ borderColor: "var(--border)" }}>
                <span className="text-green-400/70">→</span>
                <button
                  onClick={() => isConsolePath(it.path) && router.push(it.path)}
                  title={it.path}
                  className="text-pixel-gray hover:text-green-400 underline decoration-dotted underline-offset-2"
                >
                  opened {it.label}
                </button>
              </div>
            );
          }
          if (it.kind === "tool") {
            return (
              <div key={i} className="px-3 py-1 text-[11px] font-mono border-b flex items-center gap-1.5"
                style={{ borderColor: "var(--border)" }}>
                <span className={it.failed ? "text-red-400" : it.done ? "text-green-400/70" : "text-amber-400 animate-pulse"}>
                  {it.failed ? "✗" : it.done ? "✓" : "▸"}
                </span>
                <span className="text-pixel-gray">{it.name}</span>
              </div>
            );
          }
          if (it.kind === "error") {
            return (
              <div key={i} className="px-3 py-2 text-[12px] font-mono text-red-400 leading-snug border-b"
                style={{ borderColor: "var(--border)" }}>
                {it.text}
              </div>
            );
          }
          return (
            <ApprovalCard key={it.a.id} item={it} now={now} onDecide={decide} />
          );
        })}
        {busy && (
          <div className="px-3 py-2 text-[12px] font-mono text-amber-400 animate-pulse">
            working…
          </div>
        )}
        {items.length === 1 && !busy && (
          <div className="p-2 flex flex-col gap-1">
            {suggestionsFor(pathname).map((s) => (
              <button
                key={s}
                onClick={() => void ask(s)}
                className="text-left text-[11px] font-mono leading-snug text-pixel-gray hover:text-green-400 border rounded-[4px] px-2 py-1.5 transition-colors"
                style={{ borderColor: "var(--border)" }}
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="p-2 border-t shrink-0" style={{ borderColor: "var(--border)" }}>
        <input
          ref={inputRef}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void ask(draft);
          }}
          placeholder="ask or instruct… + ENTER"
          disabled={busy}
          className="pixel-input-sm w-full font-mono text-[13px]"
        />
      </div>
    </>
  );

  const dockedColumn = (
    <aside
      className="fixed inset-y-0 left-0 z-30 w-[var(--agent-dock)] flex flex-col backdrop-blur-md"
      style={{
        background:
          "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.97), rgb(var(--pixel-bg-rgb)/0.95))",
        borderRight: "1px solid var(--border)",
        animation: "drawer-in-left 0.18s ease-out",
      }}
    >
      {column}
    </aside>
  );

  const overlayColumn = (
    <div className="fixed inset-0 z-50" onClick={onClose}>
      <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.35)" }} />
      <aside
        onClick={(e) => e.stopPropagation()}
        className="absolute inset-y-0 left-0 flex flex-col backdrop-blur-md w-[320px] max-w-[85vw]"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.97), rgb(var(--pixel-bg-rgb)/0.95))",
          borderRight: "1px solid var(--border)",
          boxShadow: "12px 0 32px rgba(0,0,0,0.45)",
          animation: "drawer-in-left 0.18s ease-out",
        }}
      >
        {column}
      </aside>
    </div>
  );

  return createPortal(docked ? dockedColumn : overlayColumn, document.body);
}

// ── the approval card ──
//
// Risk-coloured, summary first, raw args on demand, a note box whose text
// reaches the model either way (an explained decline is steerable; a bare
// one is just a wall), and a live countdown anchored on expires_at — a card
// restored after a reload must not look alive when the gate has moved on.

function ApprovalCard({
  item,
  now,
  onDecide,
}: {
  item: Extract<Item, { kind: "approval" }>;
  now: number;
  onDecide: (a: Approval, decision: "approve" | "decline", note: string) => void;
}) {
  const [note, setNote] = useState("");
  const [showArgs, setShowArgs] = useState(false);
  const { a, state } = item;
  const style = KIND_STYLE[a.kind] || KIND_STYLE.spend;
  const left = Math.max(0, Math.floor(a.expires_at - now));
  const pending = state === "pending" && left > 0;
  const effState = state === "pending" && left <= 0 ? "expired" : state;

  return (
    <div className="px-3 py-2 border-b font-mono" style={{ borderColor: "var(--border)" }}>
      <div className="border rounded-[4px] p-2" style={{ borderColor: style.color }}>
        <div className="flex items-center justify-between">
          <span className="text-[10px] tracking-[0.14em]" style={{ color: style.color }}>
            {style.label} · NEEDS YOUR OK
          </span>
          {pending ? (
            <span className="text-[10px] text-pixel-gray">{left}s</span>
          ) : (
            <span className="text-[10px] uppercase" style={{ color: effState === "approve" ? "#4ade80" : style.color }}>
              {effState === "approve" ? "approved" : effState === "decline" ? "declined" : "expired"}
            </span>
          )}
        </div>
        <div className="text-[12px] text-pixel-white leading-snug mt-1">{a.summary}</div>
        <button
          onClick={() => setShowArgs((v) => !v)}
          className="text-[10px] text-pixel-gray hover:text-pixel-white mt-1"
        >
          {showArgs ? "hide" : "show"} exact call
        </button>
        {showArgs && (
          <pre className="text-[10px] text-pixel-gray-light mt-1 whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
            {a.tool}({JSON.stringify(a.args, null, 1)})
          </pre>
        )}
        {item.outcome && (
          <div className="text-[11px] text-amber-400 mt-1 leading-snug">{item.outcome}</div>
        )}
        {pending && (
          <>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="note to the agent (optional)"
              className="pixel-input-sm w-full font-mono text-[11px] mt-2"
            />
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => onDecide(a, "approve", note)}
                className="flex-1 text-[11px] tracking-[0.1em] border rounded-[4px] py-1 text-green-400 border-green-400/60 hover:bg-green-400/10"
              >
                APPROVE
              </button>
              <button
                onClick={() => onDecide(a, "decline", note)}
                className="flex-1 text-[11px] tracking-[0.1em] border rounded-[4px] py-1 text-red-400 border-red-400/60 hover:bg-red-400/10"
              >
                DECLINE
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── linkified agent text ──
//
// The agent talks in addresses and console paths; both should be one click,
// not a copy-paste. Only two token shapes are linked — a full 0x address
// (→ that trader's profile) and a console-internal path that passes the same
// whitelist nav events do. Everything else renders verbatim.

const LINK_RE = /(0x[a-fA-F0-9]{40})|(\/(?:traders|strats|copy|markets|trades|docs)(?:[\w\-./]|\?[\w\-.=&%]*|#[\w-]*)*)/g;

function AgentText({ text, onOpen }: { text: string; onOpen: (path: string) => void }) {
  const parts: Array<string | { label: string; path: string }> = [];
  let last = 0;
  for (const m of text.matchAll(LINK_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) parts.push(text.slice(last, idx));
    const tok = m[0];
    const path = m[1] ? `/traders/${tok.toLowerCase()}` : tok;
    if (isConsolePath(path)) parts.push({ label: tok, path });
    else parts.push(tok);
    last = idx + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return (
    <>
      {parts.map((p, i) =>
        typeof p === "string" ? (
          <span key={i}>{p}</span>
        ) : (
          <button
            key={i}
            onClick={() => onOpen(p.path)}
            title={p.path}
            className="text-green-400/90 hover:text-green-300 underline decoration-dotted underline-offset-2 break-all"
          >
            {p.label.startsWith("0x") && p.label.length === 42
              ? `${p.label.slice(0, 6)}…${p.label.slice(-4)}`
              : p.label}
          </button>
        ),
      )}
    </>
  );
}
