"use client";

// THE CONSOLE AGENT — a LEFT-hand sidebar, opened by the logo.
//
// It used to be a robot icon wedged into the top-right cluster (theme ·
// agent · panel handle · wallet) whose answers came out of a 380px popover.
// Four icons in one corner is a junk drawer, and the popover was the wrong
// shape for a conversation. So the agent got the left edge of the console to
// itself, and the MARK became its handle: click the logo, the agent slides
// out; click it again, it goes away. The right column is your money, the left
// column is the thing that explains it, and the board sits between them.
//
// Framing, mirrored from UserSidebar because the rules were right: on a wide
// viewport (≥1280px — the money column already claims the right edge, so the
// left one waits for a screen that can seat both) it DOCKS and the page
// insets by `--agent-dock`; below that there's no room for two columns, so it
// falls back to a modal drawer with Escape and click-out. Open/closed is
// remembered across navigation, and it's portaled to <body> because the
// TopBar's backdrop-blur makes the header a containing block for fixed
// children — rendered in place it would be clipped to a 48px strip.
//
// What it answers is unchanged: "how do I…" about the console itself. Where
// the search bar's TRADER SCOUT finds traders and each strat's CHAT tab tunes
// that strat, this one answers the question that comes BEFORE those — where
// things are and what they do. It's a guide with no hands: the route behind
// it (/_api/help-agent) has no tools and can only give directions.

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname } from "next/navigation";
import { getAccessToken } from "../lib/access";

const HELP_API = "/polymarket/_api/help-agent";

/** Anything can ask for the agent column by name. */
export const OPEN_AGENT_EVENT = "poly-open-agent";

const DOCK_MQ = "(min-width: 1280px)";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

// The panel opens already talking — a canned line, not an inference spend,
// so an idle open costs nothing.
const GREETING: Msg = {
  role: "assistant",
  content:
    "ask me anything about this console — where to put money in, how strats and backtests work, why something isn't trading. I'll point you at the right button.",
};

// Three doors into the thing, so an empty column isn't a blank prompt.
const SUGGESTIONS = [
  "how do I start copying a trader?",
  "what does the INDEX strat actually do?",
  "why isn't my live session trading?",
];

interface HelpAgentProps {
  /** Owned by TopBar, because the logo (NavMenu) is the toggle. */
  open: boolean;
  onClose: () => void;
}

export default function HelpAgent({ open, onClose }: HelpAgentProps) {
  const pathname = usePathname() || "";
  const [docked, setDocked] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([GREETING]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mq = window.matchMedia(DOCK_MQ);
    setDocked(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setDocked(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Inset the console for the docked column (read by .crt-screen in
  // layout.tsx and by BuildBadge, which otherwise floats over it).
  useEffect(() => {
    const el = document.documentElement;
    if (open && docked) el.dataset.agentDock = "open";
    else delete el.dataset.agentDock;
    return () => { delete el.dataset.agentDock; };
  }, [open, docked]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    // Escape belongs to the OVERLAY only — a docked column is furniture, and
    // Escape there belongs to whatever modal the page has open.
    if (docked) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, docked, onClose]);

  // Keep the newest turn in view — the column scrolls, the page doesn't.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, busy, open]);

  const ask = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || busy) return;
      const thread = [...messages, { role: "user", content: question } as Msg];
      setMessages(thread);
      setDraft("");
      setBusy(true);
      setError(null);
      try {
        const token = getAccessToken();
        const res = await fetch(HELP_API, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          // The greeting is furniture, not conversation — don't spend prompt on it.
          body: JSON.stringify({ messages: thread.slice(1), page: pathname }),
        });
        const data = (await res.json()) as { reply?: string; error?: string };
        if (!res.ok) {
          setError(
            res.status === 401
              ? "sign in first — the help agent runs on the owner's inference"
              : data.error || `the agent didn't answer (${res.status})`,
          );
          return;
        }
        setMessages([...thread, { role: "assistant", content: data.reply || "…" }]);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [busy, messages, pathname],
  );

  if (!open) return null;

  const column = (
    <>
      <div
        className="flex items-center justify-between px-3 h-12 shrink-0 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="text-[11px] tracking-[0.16em] text-pixel-gray font-mono flex items-center gap-1.5">
          {/* The same little agent the header icon used to carry — it moved
              in here with the panel rather than being retired. */}
          <svg className="w-[14px] h-[14px] text-green-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="4" y="8" width="16" height="12" rx="2" />
            <path d="M12 8V4" />
            <circle cx="12" cy="3" r="1" fill="currentColor" stroke="none" />
            <circle cx="9" cy="14" r="1" fill="currentColor" stroke="none" />
            <circle cx="15" cy="14" r="1" fill="currentColor" stroke="none" />
          </svg>
          AGENT
        </span>
        <button
          onClick={onClose}
          title="Close the agent (or click the logo again)"
          className="text-[13px] text-pixel-gray hover:text-pixel-white px-1"
        >
          x
        </button>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`px-3 py-2 text-[12px] font-mono leading-snug border-b last:border-b-0 ${
              m.role === "user" ? "text-pixel-white" : "text-pixel-gray-light"
            }`}
            style={{ borderColor: "var(--border)" }}
          >
            {m.role === "user" && <span className="text-green-400/80 mr-1.5">›</span>}
            {m.content}
          </div>
        ))}
        {busy && (
          <div className="px-3 py-2 text-[12px] font-mono text-amber-400 animate-pulse">
            thinking…
          </div>
        )}
        {error && !busy && (
          <div className="px-3 py-2 text-[12px] font-mono text-red-400 leading-snug">{error}</div>
        )}
        {/* Only while the column is still just the greeting — once there's a
            conversation, the starters are noise under it. */}
        {messages.length === 1 && !busy && (
          <div className="p-2 flex flex-col gap-1">
            {SUGGESTIONS.map((s) => (
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
          placeholder="how do I… + ENTER"
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
