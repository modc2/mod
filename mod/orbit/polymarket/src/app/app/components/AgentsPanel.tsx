"use client";

// STRATS ▸ AGENTS — every AI agent in this console, one row each, with its
// on/off switch. Each agent is its own mod (src/agents/<name>/ — config.json
// + mod.py + README.md); this panel is just the roster GET /_api/agents
// builds from those folders, so a new agent folder shows up here with no
// code change. Switching one OFF makes its route answer 409 "agent off" (and
// a background agent's loop skip its ticks) — it never touches money: live
// sessions, exits and the desk's approval gate run regardless.
//
// The filter pills (ALL · CHAT · BACKGROUND) copy the build console's
// AGENT ▸ USAGE · TASKS · BACKGROUND row, count badge included.

import { useCallback, useEffect, useState } from "react";

import { getAccessToken } from "../lib/access";

const API = "/polymarket/_api/agents";

interface AgentMod {
  name: string;
  mod: string;
  title: string;
  glyph: string;
  kind: "chat" | "background";
  route: string;
  query?: string;
  tools: string;
  description: string;
  version: string;
  enabled: boolean;
}

type Filter = "all" | "chat" | "background";

const FILTERS: [Filter, string, string][] = [
  ["all", "◎", "ALL"],
  ["chat", "▤", "CHAT"],
  ["background", "∞", "BACKGROUND"],
];

export default function AgentsPanel() {
  const [agents, setAgents] = useState<AgentMod[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(API, { cache: "no-store" });
      const j = (await r.json()) as { agents?: AgentMod[] };
      setAgents(j.agents ?? []);
    } catch (e) {
      setError(`couldn't read the roster: ${e instanceof Error ? e.message : String(e)}`);
      setAgents([]);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 15_000);
    return () => clearInterval(t);
  }, [load]);

  const toggle = useCallback(async (a: AgentMod) => {
    setError(null);
    setBusy(a.name);
    try {
      const token = getAccessToken();
      const r = await fetch(API, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ name: a.name, enabled: !a.enabled }),
      });
      const j = (await r.json()) as { agents?: AgentMod[]; error?: string };
      if (!r.ok) throw new Error(r.status === 401 ? "sign in as the owner to switch agents" : j.error || `HTTP ${r.status}`);
      if (j.agents) setAgents(j.agents);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, []);

  const list = agents ?? [];
  const shown = filter === "all" ? list : list.filter((a) => a.kind === filter);
  const count = (f: Filter) => (f === "all" ? list : list.filter((a) => a.kind === f)).filter((a) => a.enabled).length;

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 px-1">
        {FILTERS.map(([f, glyph, label]) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-[10px] font-semibold tracking-[0.14em] transition-colors ${
              filter === f
                ? "border-green-400/50 bg-green-400/10 text-green-400"
                : "border-transparent text-pixel-gray hover:text-pixel-white"
            }`}
            title={f === "all" ? "Every agent mod" : f === "chat" ? "Agents you ask from the console" : "Agents that run on their own"}
          >
            <span>{glyph}</span>
            {label}
            {agents && (
              <span className="rounded-full border border-current px-1.5 text-[9px] leading-[14px]">{count(f)}</span>
            )}
          </button>
        ))}
        <span className="ml-auto text-[9.5px] font-mono text-pixel-gray">
          each agent = a mod in src/agents/
        </span>
      </div>

      {error && <div className="px-1 text-[10px] font-mono text-red-400 break-words">{error}</div>}

      {agents === null ? (
        <div className="px-1 text-[10.5px] font-mono text-pixel-gray">loading agents…</div>
      ) : shown.length === 0 ? (
        <div className="px-1 text-[10.5px] font-mono text-pixel-gray">No agents here — add a folder under src/agents/.</div>
      ) : (
        <div className="space-y-1.5">
          {shown.map((a) => (
            <div
              key={a.name}
              className={`flex items-start gap-3 rounded-[var(--radius-sm)] border px-3 py-2 ${
                a.enabled ? "border-pixel-border" : "border-pixel-border/50 opacity-60"
              }`}
            >
              <span className={`mt-0.5 w-4 text-center text-[13px] ${a.enabled ? "text-green-400" : "text-pixel-gray"}`}>{a.glyph}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-[11px] font-semibold tracking-[0.14em] text-pixel-white">{a.title}</span>
                  <span className={`text-[9px] font-mono tracking-[0.1em] ${a.kind === "background" ? "text-amber-400" : "text-pixel-gray"}`}>
                    {a.kind.toUpperCase()}
                  </span>
                  <span className="text-[9px] font-mono text-pixel-gray" title="the mod this agent lives in">
                    {a.mod} · v{a.version}
                  </span>
                </div>
                <div className="text-[10px] font-mono leading-snug text-pixel-gray">{a.description}</div>
                <div className="text-[9px] font-mono text-pixel-gray/80 truncate" title={a.tools}>
                  tools: {a.tools || "none"} · {a.route}{a.query ? `?${a.query}` : ""}
                </div>
              </div>
              <button
                onClick={() => void toggle(a)}
                disabled={busy === a.name}
                role="switch"
                aria-checked={a.enabled}
                title={a.enabled ? `${a.title} is ON — click to switch it off (its route refuses until switched back)` : `${a.title} is OFF — click to switch it on`}
                className={`shrink-0 rounded-full border px-2.5 py-1 text-[9.5px] font-mono font-semibold tracking-[0.1em] transition-colors disabled:opacity-50 ${
                  a.enabled
                    ? "border-green-400/60 text-green-400 hover:bg-green-400/10"
                    : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                }`}
              >
                {busy === a.name ? "…" : a.enabled ? "ON" : "OFF"}
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
