"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bento, BentoGrid, CidChip, GlassButton } from "./Bento";
import { shortAddress } from "./AddressChip";

// IDEAS tab: the low-friction half of collaborating on a module.
//
// A merge request wants a fork, a diff and two CIDs — that's for people who
// can write the change. A suggestion is the same intent in words: anyone
// signed in files one, everyone can discuss and second it, and the module's
// admin either triages it away or PLAYS it — which submits it to the agent as
// an ordinary edit job running on the ADMIN's own account. The contributor's
// text never touches the tree by itself.
//
// The discussion is open to EVERYONE — no wallet, no association with the
// module — so a thread is the one thing here that grows without a gate. Two
// consequences shape this panel: the queue only carries the tail of a thread
// (`comment_count` says how much is missing), and an open thread is refreshed
// whole, on a timer, from /suggestions/{id}/comments. Nothing is merged into a
// local copy — what you are reading is always the server's entire history as
// of a moment this panel names.

export type SuggestionComment = { author: string; body: string; action?: string; timestamp: number };

export type Suggestion = {
  id: string;
  module: string;
  author: string;
  title: string;
  body: string;
  status: "open" | "playing" | "played" | "play_failed" | "rejected" | "done";
  /// On a list response this is only the most recent comments — see
  /// `comment_count` for the real size and refresh the thread for the rest.
  comments: SuggestionComment[];
  comment_count?: number;
  comments_truncated?: boolean;
  votes: string[];
  plays: { job_id: string; played_by: string; timestamp: number; outcome?: string; cid?: string }[];
  created_at: number;
  updated_at: number;
};

/// The whole discussion on one suggestion, as of `at` (ms).
type Thread = { id: string; comments: SuggestionComment[]; at: number };

/// How often an open thread re-reads itself. Comments need no account, so the
/// interesting case is many people writing at once, not one person waiting.
const THREAD_POLL_MS = 6000;

const STATUS_META: Record<Suggestion["status"], { label: string; color: string }> = {
  open:        { label: "open",     color: "var(--crt-green)" },
  playing:     { label: "playing",  color: "var(--crt-amber)" },
  played:      { label: "played",   color: "var(--crt-blue)" },
  play_failed: { label: "failed",   color: "var(--crt-red)" },
  rejected:    { label: "rejected", color: "var(--text-tertiary)" },
  done:        { label: "done",     color: "var(--accent-color)" },
};

const FILTERS = [
  { k: "open", label: "OPEN", match: (s: Suggestion) => s.status === "open" || s.status === "playing" },
  { k: "played", label: "PLAYED", match: (s: Suggestion) => s.status === "played" || s.status === "play_failed" || s.status === "done" },
  { k: "closed", label: "REJECTED", match: (s: Suggestion) => s.status === "rejected" },
  { k: "all", label: "ALL", match: () => true },
] as const;

type FilterKey = (typeof FILTERS)[number]["k"];

function timeAgo(ts: number): string {
  const d = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function who(addr: string): string {
  if (addr === "local") return "local";
  // A commenter with no wallet still gets a stable pen name from the server,
  // so a thread reads as a conversation between someones.
  if (addr.startsWith("anon:")) return `anon ${addr.slice(5, 11)}`;
  return shortAddress(addr);
}

type Props = {
  apiBase: string;
  module: string;
  /// The signed-in address, "" when nobody is. Decides "your" rows.
  address: string;
  /// Bearer header for writes. Deliberately NOT the console's authFetch: a
  /// read-only viewer is exactly who suggestions are for, and authFetch
  /// short-circuits their POSTs.
  authHeader?: Record<string, string>;
  /// May this session triage + play this module's queue?
  isAdmin: boolean;
  models?: { value: string; label: string }[];
  defaultModel?: string;
  onOpenJob?: (jobId: string) => void;
  onCount?: (open: number) => void;
};

export function SuggestPanel({
  apiBase,
  module,
  address,
  authHeader,
  isAdmin,
  models,
  defaultModel,
  onOpenJob,
  onCount,
}: Props) {
  const [items, setItems] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterKey>("open");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // compose
  const [composing, setComposing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  // per-row drafts
  const [comment, setComment] = useState("");
  const [playFor, setPlayFor] = useState<string | null>(null);
  const [instructions, setInstructions] = useState("");
  const [model, setModel] = useState(defaultModel || "claude-fable-5");

  // the open thread, refreshed whole
  const [thread, setThread] = useState<Thread | null>(null);
  const [threadBusy, setThreadBusy] = useState(false);

  const signedIn = !!authHeader;
  const me = (address || "").toLowerCase();

  // The parent rebuilds `authHeader` on every render. Pin it to its contents,
  // or every fetch below re-fires each render — which for a polled thread
  // means a request storm instead of a refresh.
  const authKey = authHeader ? JSON.stringify(authHeader) : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const headers = useMemo<Record<string, string>>(() => (authHeader ? { ...authHeader } : {}), [authKey]);

  const load = useCallback(async () => {
    if (!module) return;
    try {
      const r = await fetch(`${apiBase}/modules/${encodeURIComponent(module)}/suggestions`, { headers });
      const d = await r.json().catch(() => ({}) as { suggestions?: Suggestion[] });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setItems(Array.isArray(d.suggestions) ? d.suggestions : []);
      setError(null);
    } catch (e) {
      setError(`load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [apiBase, module, headers]);

  useEffect(() => {
    setLoading(true);
    setItems([]);
    setExpanded(null);
    setComposing(false);
    load();
  }, [module, load]);

  // ── The thread, whole ──────────────────────────────────────────────
  //
  // Anyone can comment, so a list can only ever hand back the tail. Opening a
  // suggestion pulls its ENTIRE discussion, and keeps pulling it: the answer
  // replaces what was on screen rather than merging into it, so a comment
  // deleted with the suggestion, or twenty that arrived while you read, both
  // land the same way.
  const expandedRef = useRef<string | null>(null);
  expandedRef.current = expanded;

  const loadThread = useCallback(
    async (id: string) => {
      setThreadBusy(true);
      try {
        const r = await fetch(`${apiBase}/suggestions/${encodeURIComponent(id)}/comments`, { headers });
        // A refused thread often answers with no body at all (a 404 from the
        // router itself carries none), so parse defensively: the status is the
        // real error, and `r.json()` throwing on empty input would bury it
        // under a parser's complaint about the shape of nothing.
        const d = await r.json().catch(() => ({}) as Record<string, unknown>);
        if (!r.ok) throw new Error((d as { error?: string }).error || `HTTP ${r.status}`);
        // A slow answer for a thread nobody is looking at any more is stale by
        // definition — dropping it keeps the panel showing what it says it is.
        if (expandedRef.current !== id) return;
        setThread({ id, comments: Array.isArray(d.comments) ? d.comments : [], at: Date.now() });
      } catch (e) {
        setError(`thread: ${(e as Error).message}`);
      } finally {
        setThreadBusy(false);
      }
    },
    [apiBase, headers],
  );

  useEffect(() => {
    setThread(null);
    if (!expanded) return;
    loadThread(expanded);
    const t = setInterval(() => loadThread(expanded), THREAD_POLL_MS);
    return () => clearInterval(t);
  }, [expanded, loadThread]);

  // A playing suggestion is waiting on a job; poll until it lands.
  const anyPlaying = items.some((s) => s.status === "playing");
  useEffect(() => {
    if (!anyPlaying) return;
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [anyPlaying, load]);

  const openCount = useMemo(() => items.filter((s) => s.status === "open").length, [items]);
  useEffect(() => { onCount?.(openCount); }, [openCount, onCount]);

  const shown = useMemo(() => {
    const f = FILTERS.find((x) => x.k === filter) || FILTERS[0];
    const out = items.filter(f.match);
    // Most-seconded first inside the open queue — that's the triage order.
    if (filter === "open") out.sort((a, b) => b.votes.length - a.votes.length || b.created_at - a.created_at);
    return out;
  }, [items, filter]);

  const call = useCallback(
    async (path: string, init: RequestInit, okMsg: string) => {
      setError(null);
      setStatus(null);
      setBusy(path);
      try {
        const r = await fetch(`${apiBase}${path}`, {
          ...init,
          headers: { "Content-Type": "application/json", ...headers, ...(init.headers || {}) },
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        setStatus(okMsg);
        await load();
        return d;
      } catch (e) {
        setError((e as Error).message);
        return null;
      } finally {
        setBusy(null);
      }
    },
    [apiBase, headers, load],
  );

  const submitSuggestion = async () => {
    if (!title.trim()) return;
    const d = await call(
      `/modules/${encodeURIComponent(module)}/suggestions`,
      { method: "POST", body: JSON.stringify({ title: title.trim(), body: body.trim() }) },
      "suggestion filed — the module's admin sees it in this queue",
    );
    if (d) {
      setTitle("");
      setBody("");
      setComposing(false);
      setFilter("open");
    }
  };

  const play = async (s: Suggestion) => {
    const d = await call(
      `/suggestions/${s.id}/play`,
      { method: "POST", body: JSON.stringify({ instructions: instructions.trim() || undefined, model }) },
      "playing — running as an edit on your account",
    );
    if (d?.job_id) {
      setPlayFor(null);
      setInstructions("");
      onOpenJob?.(d.job_id);
    }
  };

  const setStatusOf = (s: Suggestion, next: string) =>
    call(`/suggestions/${s.id}/status`, { method: "POST", body: JSON.stringify({ status: next }) }, `marked ${next}`);

  const remove = async (s: Suggestion) => {
    if (!confirm(`Delete "${s.title}"?\n\nThis removes the suggestion and its discussion for everyone.`)) return;
    await call(`/suggestions/${s.id}`, { method: "DELETE" }, "deleted");
  };

  const vote = (s: Suggestion) => call(`/suggestions/${s.id}/vote`, { method: "POST", body: "{}" }, "");

  const sendComment = async (s: Suggestion) => {
    if (!comment.trim()) return;
    const d = await call(
      `/suggestions/${s.id}/comment`,
      { method: "POST", body: JSON.stringify({ body: comment.trim() }) },
      "",
    );
    if (!d) return;
    setComment("");
    // The reply answers with the whole thread as the server now holds it, so
    // posting is itself a refresh — you see what landed while you typed.
    if (Array.isArray(d.comments) && expandedRef.current === s.id) {
      setThread({ id: s.id, comments: d.comments, at: Date.now() });
    }
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--bg-primary)",
    border: "1px solid var(--border-color)",
    color: "var(--text-primary)",
    padding: "6px 8px",
    fontSize: 13,
    fontFamily: "inherit",
    borderRadius: 4,
  };

  return (
    <BentoGrid>
      <Bento
        title={`suggestions · ${module} · ${openCount} open`}
        span={3}
        action={
          <span className="bento-sub" style={{ opacity: 0.7 }}>
            {isAdmin ? "play one and it runs as an edit on your account" : "suggest an edit — no fork needed"}
          </span>
        }
      >
        {/* ── Compose ─────────────────────────────────────────────── */}
        {signedIn ? (
          composing ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
                marginBottom: 10,
                padding: 10,
                border: "1px solid var(--glass-border)",
                borderRadius: 8,
                background: "var(--glass-bg-strong, rgba(0,0,0,0.25))",
              }}
            >
              <input
                autoFocus
                value={title}
                maxLength={200}
                placeholder={`One line — what should ${module} do differently?`}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitSuggestion(); }}
                style={inputStyle}
              />
              <textarea
                value={body}
                maxLength={10000}
                rows={4}
                placeholder="The detail: what's wrong or missing, where you noticed it, what good would look like. This is the brief the agent gets if the admin plays it."
                onChange={(e) => setBody(e.target.value)}
                style={{ ...inputStyle, resize: "vertical" }}
              />
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <GlassButton variant="primary" disabled={!title.trim() || !!busy} onClick={submitSuggestion}>
                  suggest
                </GlassButton>
                <GlassButton variant="ghost" onClick={() => setComposing(false)}>cancel</GlassButton>
                <span className="bento-sub" style={{ opacity: 0.55 }}>⌘↵ to file</span>
              </div>
            </div>
          ) : (
            <div style={{ marginBottom: 10 }}>
              <GlassButton onClick={() => setComposing(true)}>+ suggest an edit</GlassButton>
            </div>
          )
        ) : (
          <div className="bento-sub" style={{ marginBottom: 10, opacity: 0.75 }}>
            connect a wallet to file a suggestion — reading the queue and replying in any thread are
            open to everyone
          </div>
        )}

        {/* ── Filters ─────────────────────────────────────────────── */}
        <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
          {FILTERS.map((f) => {
            const on = filter === f.k;
            const n = items.filter(f.match).length;
            return (
              <button
                key={f.k}
                onClick={() => setFilter(f.k)}
                className="text-[11px] px-2 py-[3px] border font-code uppercase"
                style={{
                  letterSpacing: "0.04em",
                  color: on ? "var(--text-primary)" : "var(--text-tertiary)",
                  borderColor: on ? "var(--crt-green)" : "transparent",
                  background: on ? "color-mix(in srgb, var(--crt-green) 8%, transparent)" : "transparent",
                  opacity: on ? 1 : 0.55,
                  borderRadius: 4,
                }}
              >
                {f.label} {n > 0 && <span style={{ opacity: 0.7 }}>{n}</span>}
              </button>
            );
          })}
        </div>

        {status && <div className="bento-sub" style={{ marginBottom: 6, color: "var(--crt-green)" }}>✓ {status}</div>}
        {error && <div className="bento-sub" style={{ marginBottom: 6, color: "var(--crt-red)" }}>✗ {error}</div>}
        {loading && <div className="bento-sub">loading…</div>}
        {!loading && shown.length === 0 && (
          <div className="bento-sub">
            {items.length === 0
              ? `nothing suggested for ${module} yet — anyone signed in can file the first one`
              : "nothing in this filter"}
          </div>
        )}

        {/* ── Queue ───────────────────────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {shown.map((s) => {
            const meta = STATUS_META[s.status] || STATUS_META.open;
            const isOpen = expanded === s.id;
            const mine = me && s.author === me;
            const seconded = me ? s.votes.includes(me) : false;
            const lastPlay = s.plays.length ? s.plays[s.plays.length - 1] : null;
            const canDelete = isAdmin || (mine && s.plays.length === 0);
            // The queue's copy of a thread is a tail; `thread` is the whole of
            // it, and is what's rendered the moment it arrives.
            const live = thread && thread.id === s.id ? thread : null;
            const comments = live ? live.comments : s.comments;
            const commentTotal = live ? live.comments.length : s.comment_count ?? s.comments.length;
            const humanComments = s.comments.filter((c) => c.action !== "system").length;
            const commentBadge = s.comments_truncated ? s.comment_count ?? humanComments : humanComments;
            return (
              <div key={s.id}>
                <div
                  className="version-row"
                  style={{ cursor: "pointer" }}
                  onClick={() => { setExpanded(isOpen ? null : s.id); setComment(""); setPlayFor(null); }}
                >
                  <span
                    className="dot"
                    title={meta.label}
                    style={{ background: meta.color, boxShadow: `0 0 0 2px ${meta.color}1a` }}
                  />
                  <div style={{ minWidth: 0, display: "flex", alignItems: "baseline", gap: 8, overflow: "hidden" }}>
                    <span className="msg" style={{ flex: "0 1 auto", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.title}
                    </span>
                    <span className="meta" style={{ flex: "0 0 auto" }}>
                      <span style={{ color: meta.color }}>{meta.label}</span>
                      {" · "}
                      {mine ? "you" : who(s.author)}
                      {" · "}
                      {timeAgo(s.created_at)}
                      {commentBadge > 0 && <> · {commentBadge}💬</>}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 4, alignItems: "center" }} onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => signedIn && vote(s)}
                      disabled={!signedIn || !!busy}
                      title={signedIn ? (seconded ? "You seconded this — click to take it back" : "Second this suggestion") : "Sign in to second a suggestion"}
                      className="text-[11px] px-1.5 py-[2px] border font-code"
                      style={{
                        borderRadius: 4,
                        cursor: signedIn ? "pointer" : "default",
                        color: seconded ? "var(--accent-color)" : "var(--text-tertiary)",
                        borderColor: seconded ? "var(--accent-color)" : "var(--border-color)",
                        background: seconded ? "color-mix(in srgb, var(--accent-color) 10%, transparent)" : "transparent",
                        opacity: signedIn ? 1 : 0.4,
                      }}
                    >
                      ▲ {s.votes.length}
                    </button>
                    {lastPlay?.cid && <CidChip cid={lastPlay.cid} />}
                    <span className="meta" style={{ opacity: 0.6, width: 14, textAlign: "center" }}>{isOpen ? "▾" : "▸"}</span>
                  </div>
                </div>

                {isOpen && (
                  <div
                    style={{
                      margin: "2px 0 6px 22px",
                      padding: "10px 12px",
                      border: "1px solid var(--glass-border)",
                      borderRadius: 8,
                      background: "var(--glass-bg-strong, rgba(0,0,0,0.25))",
                      display: "flex",
                      flexDirection: "column",
                      gap: 10,
                    }}
                  >
                    {s.body && (
                      <div style={{ fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--text-primary)" }}>
                        {s.body}
                      </div>
                    )}

                    {/* the play trail — each hand-off to the agent */}
                    {s.plays.length > 0 && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                        {s.plays.map((p) => (
                          <div key={p.job_id} className="bento-sub" style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                            <span style={{ color: "var(--crt-amber)", textTransform: "uppercase", fontSize: 11 }}>play</span>
                            <button
                              onClick={() => onOpenJob?.(p.job_id)}
                              style={{ color: "var(--crt-blue)", textDecoration: "underline", cursor: "pointer", background: "none", border: "none", padding: 0, font: "inherit" }}
                              title="Open the task this play ran as"
                            >
                              {p.job_id.slice(0, 8)}…
                            </button>
                            <span style={{ opacity: 0.7 }}>
                              by {who(p.played_by)} · {timeAgo(p.timestamp)} · {p.outcome || "running"}
                            </span>
                            {p.cid && <CidChip cid={p.cid} />}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* ── The discussion, entire ──────────────────── */}
                    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                      <div
                        className="bento-sub"
                        style={{ display: "flex", alignItems: "baseline", gap: 8, opacity: 0.7 }}
                      >
                        <span>
                          {commentTotal} {commentTotal === 1 ? "comment" : "comments"}
                          {!live && s.comments_truncated && ` · last ${s.comments.length} until it loads`}
                        </span>
                        <span style={{ opacity: 0.7 }}>
                          {threadBusy
                            ? "refreshing…"
                            : live
                              ? `whole thread as of ${timeAgo(Math.floor(live.at / 1000))}`
                              : "loading the whole thread…"}
                        </span>
                        <button
                          onClick={() => loadThread(s.id)}
                          disabled={threadBusy}
                          title="Re-read every comment from the server — anyone can post here, so there is usually more"
                          className="text-[11px] px-1.5 py-[1px] border font-code"
                          style={{
                            borderRadius: 4,
                            cursor: threadBusy ? "default" : "pointer",
                            color: "var(--text-tertiary)",
                            borderColor: "var(--border-color)",
                            background: "transparent",
                            opacity: threadBusy ? 0.4 : 1,
                          }}
                        >
                          ↻ refresh
                        </button>
                      </div>
                      {comments.map((c, i) => (
                        <div key={`${c.timestamp}-${i}`} style={{ fontSize: 12.5, minWidth: 0 }}>
                          <span
                            style={{
                              color: c.action === "system" ? "var(--text-tertiary)" : c.author.startsWith("anon:") ? "var(--text-secondary)" : "var(--accent-color)",
                              marginRight: 6,
                            }}
                            title={c.author.startsWith("anon:") ? "posted without signing in" : c.author}
                          >
                            {c.author === "agent" ? "agent" : who(c.author)}
                          </span>
                          <span style={{ color: c.action === "system" ? "var(--text-tertiary)" : "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                            {c.body}
                          </span>
                          <span className="meta" style={{ marginLeft: 6, opacity: 0.5 }}>{timeAgo(c.timestamp)}</span>
                        </div>
                      ))}
                    </div>

                    {/* Commenting is open — a wallet changes the name on the
                        reply, never whether you may leave one. */}
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <div style={{ display: "flex", gap: 6 }}>
                        <input
                          value={comment}
                          placeholder={signedIn ? "reply…" : "reply — no account needed…"}
                          maxLength={5000}
                          onChange={(e) => setComment(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") sendComment(s); }}
                          style={{ ...inputStyle, flex: 1 }}
                        />
                        <GlassButton variant="ghost" disabled={!comment.trim() || !!busy} onClick={() => sendComment(s)}>
                          reply
                        </GlassButton>
                      </div>
                      {!signedIn && (
                        <span className="bento-sub" style={{ opacity: 0.55 }}>
                          posting as a guest — anyone may comment here; sign in to post under your address, second it, or file one of your own
                        </span>
                      )}
                    </div>

                    {/* ── Admin controls ─────────────────────────── */}
                    {(isAdmin || canDelete) && (
                      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid var(--glass-border)", paddingTop: 8 }}>
                        {isAdmin && s.status !== "playing" && (
                          <GlassButton
                            variant="primary"
                            disabled={!!busy}
                            onClick={() => setPlayFor(playFor === s.id ? null : s.id)}
                            title="Hand this to the agent as an edit job on your account"
                          >
                            ▶ play as edit
                          </GlassButton>
                        )}
                        {isAdmin && s.status === "playing" && lastPlay && (
                          <GlassButton variant="ghost" onClick={() => onOpenJob?.(lastPlay.job_id)}>
                            ↗ open the running task
                          </GlassButton>
                        )}
                        {isAdmin && s.status !== "playing" && s.status !== "rejected" && (
                          <GlassButton variant="ghost" disabled={!!busy} onClick={() => setStatusOf(s, "rejected")}>
                            reject
                          </GlassButton>
                        )}
                        {isAdmin && s.status !== "playing" && s.status !== "done" && (
                          <GlassButton variant="ghost" disabled={!!busy} onClick={() => setStatusOf(s, "done")}>
                            mark done
                          </GlassButton>
                        )}
                        {isAdmin && (s.status === "rejected" || s.status === "done") && (
                          <GlassButton variant="ghost" disabled={!!busy} onClick={() => setStatusOf(s, "open")}>
                            reopen
                          </GlassButton>
                        )}
                        {canDelete && s.status !== "playing" && (
                          <GlassButton variant="ghost" disabled={!!busy} onClick={() => remove(s)} title="Delete this suggestion">
                            delete
                          </GlassButton>
                        )}
                      </div>
                    )}

                    {/* the play composer — what the admin adds on top */}
                    {isAdmin && playFor === s.id && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <div className="bento-sub" style={{ opacity: 0.75 }}>
                          runs in <span style={{ color: "var(--text-primary)" }}>{module}</span> as a normal edit task on your
                          account — it snapshots on completion and rolls back like any other edit
                        </div>
                        <textarea
                          value={instructions}
                          rows={2}
                          placeholder="Optional: your own instructions on top of the suggestion (these win where they conflict)"
                          onChange={(e) => setInstructions(e.target.value)}
                          style={{ ...inputStyle, resize: "vertical" }}
                        />
                        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                          {models && models.length > 0 && (
                            <select
                              value={model}
                              onChange={(e) => setModel(e.target.value)}
                              style={{ ...inputStyle, width: "auto" }}
                            >
                              {models.map((mo) => (
                                <option key={mo.value} value={mo.value}>{mo.label}</option>
                              ))}
                            </select>
                          )}
                          <GlassButton variant="primary" disabled={!!busy} onClick={() => play(s)}>
                            {busy ? "starting…" : "run it"}
                          </GlassButton>
                          <GlassButton variant="ghost" onClick={() => setPlayFor(null)}>cancel</GlassButton>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Bento>
    </BentoGrid>
  );
}

export default SuggestPanel;
