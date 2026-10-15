// The AGENT ROSTER — every AI agent in this app is its own mod under
// src/agents/<name>/ (config.json + mod.py + README.md). This file reads
// those folders; nothing is registered in code, so an agent is added by
// adding a folder and removed by deleting it.
//
// On/off is ONE shared state file, ~/.mod/polymarket/agents.json
// {"off": [...], "on": [...]} — the same file agents/base/mod.py writes, so
// the console switch and `python3 -m agents.<name>.mod off` agree. It is
// re-read on every call (tiny file), so a switch applies with no restart.
//
// Each agent route calls agentOff("<name>") first: null = go ahead, else a
// ready 409 that the console shows as "agent off".

import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from "fs";
import { join, resolve as resolvePath } from "path";
import { NextResponse } from "next/server";

import { stateDir } from "./ownerToken";

export interface AgentMod {
  name: string;
  mod: string;
  title: string;
  glyph: string;
  kind: "chat" | "background";
  route: string;
  query?: string;
  tools: string;
  description: string;
  default_enabled: boolean;
  order: number;
  version: string;
  enabled: boolean;
}

/** src/agents — the Next server runs from src/app. */
export function agentsDir(): string {
  return process.env.POLYMARKET_AGENTS_DIR || resolvePath(process.cwd(), "..", "agents");
}

const STATE = () => join(stateDir(), "agents.json");

function readState(): { off: string[]; on: string[] } {
  try {
    const raw = JSON.parse(readFileSync(STATE(), "utf8")) as { off?: unknown; on?: unknown };
    const list = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
    return { off: list(raw.off), on: list(raw.on) };
  } catch {
    return { off: [], on: [] };
  }
}

function isOn(cfg: { name: string; default_enabled?: boolean }, s = readState()): boolean {
  if (s.off.includes(cfg.name)) return false;
  if (cfg.default_enabled === false) return s.on.includes(cfg.name);
  return true;
}

export function listAgents(): AgentMod[] {
  const dir = agentsDir();
  let names: string[] = [];
  try {
    names = readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isDirectory() && d.name !== "base" && !d.name.startsWith("_"))
      .map((d) => d.name);
  } catch {
    return [];
  }
  const s = readState();
  const out: AgentMod[] = [];
  for (const n of names) {
    const p = join(dir, n, "config.json");
    if (!existsSync(p)) continue;
    try {
      const c = JSON.parse(readFileSync(p, "utf8")) as Partial<AgentMod>;
      if (c.name !== n) continue; // folder name IS the agent name
      out.push({
        name: n,
        mod: c.mod || `polymarket.agents.${n}`,
        title: c.title || n.toUpperCase(),
        glyph: c.glyph || "·",
        kind: c.kind === "background" ? "background" : "chat",
        route: c.route || "",
        query: c.query,
        tools: c.tools || "",
        description: c.description || "",
        default_enabled: c.default_enabled !== false,
        order: typeof c.order === "number" ? c.order : 99,
        version: c.version || "0.0.0",
        enabled: isOn({ name: n, default_enabled: c.default_enabled }, s),
      });
    } catch {
      // a broken config.json hides that one agent, never the roster
    }
  }
  return out.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
}

/** True when the agent's mod folder exists and it is switched on. A deleted
    folder is OFF — removing the mod removes the agent. */
export function agentEnabled(name: string): boolean {
  return listAgents().some((a) => a.name === name && a.enabled);
}

/** null when the agent may run; otherwise the 409 the route should return. */
export function agentOff(name: string): NextResponse | null {
  if (agentEnabled(name)) return null;
  return NextResponse.json(
    { error: `agent off — switch ${name} on in STRATS ▸ AGENTS`, agent: name, gate: "agent-off" },
    { status: 409 },
  );
}

export function setAgentEnabled(name: string, on: boolean): AgentMod | null {
  if (!listAgents().some((a) => a.name === name)) return null;
  const s = readState();
  s.off = s.off.filter((n) => n !== name);
  s.on = s.on.filter((n) => n !== name);
  (on ? s.on : s.off).push(name);
  mkdirSync(stateDir(), { recursive: true });
  const tmp = `${STATE()}.tmp`;
  writeFileSync(tmp, JSON.stringify({ off: [...new Set(s.off)].sort(), on: [...new Set(s.on)].sort() }, null, 2));
  renameSync(tmp, STATE());
  return listAgents().find((a) => a.name === name) ?? null;
}
