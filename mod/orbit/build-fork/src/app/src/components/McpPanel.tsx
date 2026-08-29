"use client";

// MCP · ARENAS — the machine-facing half of this console, made visible.
//
// Everything here is read through the MCP endpoint itself rather than through
// a parallel set of REST calls, which means this panel breaks the moment the
// tool layer does. That is the point: the console and an outside agent are
// looking at exactly the same surface, so "it works in the UI but not over
// MCP" cannot happen quietly.
//
// The three round buttons in the header are the console's tinted-circle
// action style — blue / amber / green, filled at low alpha with a matching
// ring, colour carrying the meaning before the tooltip does.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiIcon, GraphIcon, NewIcon, SpinnerIcon } from "./Icons";

type Props = {
  apiUrl: string;
  /** Bearer token, "local" in local mode, or null when signed out. */
  token: string | null;
  isOwner?: boolean;
};

type Tool = { name: string; description: string; inputSchema?: unknown };
type Arena = {
  name: string;
  mcp: string;
  description: string;
  competitor_kinds: string[];
  console: string;
  reachable: boolean;
  entered: boolean;
};
type Entry = { arena: string; name: string; role: string; url: string; id: string; at: number };
type Competitor = { enabled: boolean; keyed: boolean; callback_base: string; entries: Entry[] };

const BLUE = "#60a5fa";
const AMBER = "#fbbf24";
const GREEN = "#4ade80";

const tint = (c: string, pct: number) => `color-mix(in srgb, ${c} ${pct}%, transparent)`;

/** The access tag call_tool stamps onto every description: "[owner] Merge it…". */
function splitAccess(description: string): { access: string; text: string } {
  const m = /^\[(public|auth|owner)\]\s*/.exec(description);
  return m ? { access: m[1], text: description.slice(m[0].length) } : { access: "public", text: description };
}

const ACCESS_COLOR: Record<string, string> = { public: GREEN, auth: BLUE, owner: AMBER };

/** A round tinted action button — the console's circle style. */
function RoundButton({
  color,
  title,
  onClick,
  busy,
  children,
}: {
  color: string;
  title: string;
  onClick: () => void;
  busy?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      title={title}
      aria-label={title}
      className="shrink-0 flex items-center justify-center transition-all hover:brightness-125 disabled:opacity-50"
      style={{
        width: 30,
        height: 30,
        borderRadius: 999,
        color,
        background: tint(color, 12),
        border: `1px solid ${tint(color, 40)}`,
        cursor: busy ? "wait" : "pointer",
      }}
    >
      {busy ? <SpinnerIcon size={14} /> : children}
    </button>
  );
}

export function McpPanel({ apiUrl, token, isOwner = false }: Props) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [arenas, setArenas] = useState<Arena[]>([]);
  const [competitor, setCompetitor] = useState<Competitor | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [openArena, setOpenArena] = useState<string | null>(null);
  const [showTools, setShowTools] = useState(false);

  // The endpoint an OUTSIDE client should use. `apiUrl` may be the relative
  // gateway path (/api/build-fork), which is meaningless to anything that isn't
  // this browser — so absolutise it against the page's own origin.
  const endpoint = useMemo(() => {
    if (typeof window === "undefined") return `${apiUrl}/mcp`;
    try {
      return new URL(`${apiUrl}/mcp`, window.location.origin).toString();
    } catch {
      return `${apiUrl}/mcp`;
    }
  }, [apiUrl]);

  const rpc = useCallback(
    async (method: string, params: Record<string, unknown> = {}) => {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token && token !== "local") headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`${apiUrl}/mcp`, {
        method: "POST",
        headers,
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
      });
      const body = await res.json();
      if (body?.error) throw new Error(body.error.message || "JSON-RPC error");
      return body?.result ?? {};
    },
    [apiUrl, token],
  );

  const callTool = useCallback(
    async (name: string, args: Record<string, unknown> = {}) => {
      const result = await rpc("tools/call", { name, arguments: args });
      if (result?.isError) {
        throw new Error(result?.content?.[0]?.text || `${name} failed`);
      }
      return result?.structuredContent ?? {};
    },
    [rpc],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, arenaList, status] = await Promise.all([
        rpc("tools/list"),
        callTool("arena_list", { refresh: true }),
        callTool("arena_status"),
      ]);
      setTools(list?.tools || []);
      setArenas(arenaList?.arenas || []);
      setCompetitor(status as Competitor);
      setNote(null);
    } catch (e) {
      setNote({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  }, [rpc, callTool]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (key: string, run: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    setNote(null);
    try {
      await run();
      setNote({ kind: "ok", text: ok });
      await load();
    } catch (e) {
      setNote({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const copy = (text: string, what: string) => {
    void navigator.clipboard
      ?.writeText(text)
      .then(() => setNote({ kind: "ok", text: `${what} copied` }))
      .catch(() => setNote({ kind: "err", text: "clipboard refused" }));
  };

  const clientConfig = JSON.stringify(
    { mcpServers: { build: { type: "http", url: endpoint } } },
    null,
    2,
  );

  const byAccess = useMemo(() => {
    const groups: Record<string, Array<{ name: string; text: string }>> = { public: [], auth: [], owner: [] };
    for (const t of tools) {
      const { access, text } = splitAccess(t.description || "");
      (groups[access] ||= []).push({ name: t.name, text });
    }
    return groups;
  }, [tools]);

  const card: React.CSSProperties = {
    border: "1px solid var(--border-color)",
    background: "var(--bg-secondary)",
  };
  const label: React.CSSProperties = {
    color: "var(--text-tertiary)",
    letterSpacing: "0.12em",
  };

  return (
    <div className="flex-1 overflow-auto font-mono" style={{ minWidth: 0 }}>
      {/* ── header ── */}
      <div
        className="flex items-center gap-2 px-3 py-2 sticky top-0 z-10"
        style={{ borderBottom: "1px solid var(--border-color)", background: "var(--bg-primary)" }}
      >
        <span className="text-[11px] font-bold uppercase" style={label}>
          MCP
        </span>
        <span className="text-[10px] truncate" style={{ color: "var(--text-secondary)" }}>
          {endpoint}
        </span>
        <div className="flex-1" />
        <RoundButton color={BLUE} title="Copy the MCP endpoint URL" onClick={() => copy(endpoint, "endpoint")}>
          <ApiIcon size={15} />
        </RoundButton>
        <RoundButton
          color={AMBER}
          title="Copy an MCP client config for this server"
          onClick={() => copy(clientConfig, "client config")}
        >
          <GraphIcon size={15} />
        </RoundButton>
        <RoundButton color={GREEN} title="Re-probe the server and the arenas" onClick={() => void load()} busy={loading}>
          <NewIcon size={15} />
        </RoundButton>
      </div>

      {note && (
        <div
          className="px-3 py-1.5 text-[10px]"
          style={{
            color: note.kind === "ok" ? GREEN : "var(--crt-red, #f87171)",
            background: tint(note.kind === "ok" ? GREEN : "#f87171", 8),
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          {note.text}
        </div>
      )}

      <div className="p-3 flex flex-col gap-3">
        {/* ── transports ── */}
        <div style={card} className="p-3">
          <div className="text-[10px] font-bold uppercase mb-2" style={label}>
            Transports
          </div>
          <div className="grid gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }}>
            <div>
              <span style={{ color: BLUE }}>http</span> &nbsp;POST {endpoint} — Streamable HTTP, JSON-RPC 2.0
            </div>
            <div>
              <span style={{ color: BLUE }}>stdio</span> &nbsp;build-fork-jobs --stdio &nbsp;
              <span style={{ color: "var(--text-tertiary)" }}>(BUILD_FORK_TOKEN acts as you)</span>
            </div>
          </div>
          <div className="mt-2 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            Tools re-enter this server&apos;s own REST routes with your bearer token, so MCP and this
            console get the same gate. Signed out, you see the {byAccess.public.length} public tools.
          </div>
        </div>

        {/* ── tools ── */}
        <div style={card}>
          <button
            onClick={() => setShowTools((v) => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 text-left"
            style={{ background: "transparent", border: "none", cursor: "pointer" }}
          >
            <span className="text-[10px] font-bold uppercase" style={label}>
              Tools
            </span>
            <span className="text-[11px]" style={{ color: "var(--text-primary)" }}>
              {tools.length}
            </span>
            {(["public", "auth", "owner"] as const).map((a) => (
              <span
                key={a}
                className="text-[9px] px-1.5 rounded-full"
                style={{ color: ACCESS_COLOR[a], background: tint(ACCESS_COLOR[a], 12) }}
              >
                {byAccess[a].length} {a}
              </span>
            ))}
            <div className="flex-1" />
            <span style={{ color: "var(--text-tertiary)" }}>{showTools ? "▴" : "▾"}</span>
          </button>
          {showTools && (
            <div style={{ borderTop: "1px solid var(--border-color)" }}>
              {(["public", "auth", "owner"] as const).flatMap((a) =>
                byAccess[a].map((t) => (
                  <div key={t.name} className="px-3 py-1.5 flex gap-2 items-baseline">
                    <span
                      className="text-[8px] uppercase shrink-0 text-right"
                      style={{ color: ACCESS_COLOR[a], width: 38 }}
                    >
                      {a}
                    </span>
                    <span className="text-[11px] shrink-0" style={{ color: "var(--text-primary)", width: 150 }}>
                      {t.name}
                    </span>
                    <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                      {t.text}
                    </span>
                  </div>
                )),
              )}
            </div>
          )}
        </div>

        {/* ── arenas ── */}
        <div style={card}>
          <div className="px-3 py-2 flex items-center gap-2" style={{ borderBottom: "1px solid var(--border-color)" }}>
            <span className="text-[10px] font-bold uppercase" style={label}>
              Arenas
            </span>
            <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              every module on this fleet speaking arena/1.0
            </span>
          </div>

          {arenas.length === 0 && !loading && (
            <div className="px-3 py-3 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              None found. An arena announces itself with <code>protocol: &quot;arena/1.0&quot;</code> in its
              config.json — nothing here is hard-coded, so installing one is enough.
            </div>
          )}

          {arenas.map((a) => (
            <div key={a.name} style={{ borderBottom: "1px solid var(--border-color)" }}>
              <div className="px-3 py-2 flex items-center gap-2 flex-wrap">
                <span
                  title={a.reachable ? "answering" : "not answering"}
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: 999,
                    background: a.reachable ? GREEN : "var(--text-tertiary)",
                    opacity: a.reachable ? 1 : 0.4,
                  }}
                />
                <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>
                  {a.name}
                </span>
                {a.entered && (
                  <span
                    className="text-[9px] px-1.5 rounded-full"
                    style={{ color: GREEN, background: tint(GREEN, 12) }}
                  >
                    entered
                  </span>
                )}
                <span className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>
                  {a.competitor_kinds.join(" · ")}
                </span>
                <div className="flex-1" />
                <button
                  onClick={() => setOpenArena(openArena === a.name ? null : a.name)}
                  className="text-[10px] px-2 py-0.5"
                  style={{
                    color: "var(--text-secondary)",
                    background: "transparent",
                    border: "1px solid var(--border-color)",
                    cursor: "pointer",
                  }}
                >
                  {openArena === a.name ? "less" : "about"}
                </button>
                {isOwner &&
                  (a.entered ? (
                    <button
                      onClick={() =>
                        void act(a.name, () => callTool("arena_withdraw", { arena: a.name }), `withdrew from ${a.name}`)
                      }
                      disabled={busy === a.name}
                      className="text-[10px] px-2 py-0.5"
                      style={{
                        color: AMBER,
                        background: tint(AMBER, 10),
                        border: `1px solid ${tint(AMBER, 35)}`,
                        cursor: "pointer",
                      }}
                    >
                      {busy === a.name ? "…" : "withdraw"}
                    </button>
                  ) : (
                    <button
                      onClick={() =>
                        void act(a.name, () => callTool("arena_enter", { arena: a.name }), `entered ${a.name}`)
                      }
                      disabled={busy === a.name || !a.reachable}
                      className="text-[10px] px-2 py-0.5 disabled:opacity-40"
                      style={{
                        color: GREEN,
                        background: tint(GREEN, 10),
                        border: `1px solid ${tint(GREEN, 35)}`,
                        cursor: a.reachable ? "pointer" : "not-allowed",
                      }}
                      title={a.reachable ? "Enter this console as a competitor" : "That arena is not running"}
                    >
                      {busy === a.name ? "…" : "enter"}
                    </button>
                  ))}
              </div>
              {openArena === a.name && (
                <div className="px-3 pb-2 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                  <div className="mb-1">{a.description}</div>
                  <div>mcp: {a.mcp}</div>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* ── competitor ── */}
        <div style={card} className="p-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] font-bold uppercase" style={label}>
              Competing
            </span>
            <span
              className="text-[9px] px-1.5 rounded-full"
              style={{
                color: competitor?.enabled ? GREEN : "var(--text-tertiary)",
                background: tint(competitor?.enabled ? GREEN : "#888", 12),
              }}
            >
              {competitor?.enabled ? "answering" : "off"}
            </span>
          </div>
          {competitor?.entries?.length ? (
            <div className="flex flex-col gap-1">
              {competitor.entries.map((e) => (
                <div key={`${e.arena}:${e.id}`} className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                  <span style={{ color: "var(--text-primary)" }}>{e.name}</span> in {e.arena} as{" "}
                  <span style={{ color: e.role === "play" ? AMBER : BLUE }}>{e.role}</span> →{" "}
                  <span style={{ color: "var(--text-tertiary)" }}>{e.url}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              Not entered anywhere. Entering opens <code>/arena/solve</code> and <code>/arena/play</code> to the
              arena, and every call it makes runs a real agent job here — which is why it is owner-only and off
              by default.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default McpPanel;
