// The browser's half of the chat agent's pm_strat_* tools.
//
// Private strats are encrypted with a key that never leaves this browser, so
// the server can't author them — an APPROVED pm_strat_create/update/delete
// is executed HERE, by the owner's own console, through the exact code paths
// the UI uses (indexStore + the strat CHAT's validated patch contract). The
// object this returns is written into the approval decision and becomes the
// model's tool result, so rejects are named, never swallowed.
//
// Approving is never starting: creations land liveEnabled:false, and nothing
// here touches the live engine.

import { deleteIndex, loadIndexes, uniqueIndexName, upsertIndex } from "./indexStore";
import { saveParamsAsStrat } from "./stratDraft";
import { applyPatch, describeEntry, validatePatch } from "./stratPatch";

export type StratOpResult = Record<string, unknown> & { ok: boolean };

const ADDR = /^0x[0-9a-fA-F]{40}$/;

function findStrat(id: string) {
  const all = loadIndexes();
  return all.find((s) => s.id === id)
    // Models sometimes hand the name back instead of the id — accept an
    // exact, unambiguous name match rather than failing a correct intent.
    || (all.filter((s) => s.name === id).length === 1
      ? all.find((s) => s.name === id)
      : undefined);
}

function createStrat(args: Record<string, unknown>): StratOpResult {
  const raw = Array.isArray(args.traders) ? (args.traders as unknown[]) : [];
  const addrs = raw.map((a) => String(a).trim().toLowerCase()).filter((a) => ADDR.test(a));
  if (addrs.length === 0) return { ok: false, error: "no valid 0x trader addresses" };
  const weights = Array.isArray(args.weights) ? (args.weights as unknown[]).map(Number) : [];
  const traders = addrs.slice(0, 10).map((address, i) => ({
    address,
    weight: Number.isFinite(weights[i]) && weights[i] > 0 ? weights[i] : 1,
  }));

  const name = uniqueIndexName(String(args.name || "chat strat").slice(0, 64));
  // Optional params go through the SAME validation as the strat CHAT's
  // patches — the agent can't smuggle in a field the console would reject.
  const blank = { id: "", name, traders: [] } as unknown as Parameters<typeof validatePatch>[0];
  const { entries, rejected } = validatePatch(blank, args.params ?? {});
  const params: Record<string, unknown> = {};
  for (const e of entries) {
    const [head, tail] = e.path.split(".");
    if (tail === undefined) params[head] = e.to;
    else params[head] = { ...(params[head] as Record<string, unknown>), [tail]: e.to };
  }
  if (Number(args.capital) > 0) params.capital = Number(args.capital);

  const idx = saveParamsAsStrat({ ...params, traders }, name);
  return {
    ok: true,
    id: idx.id,
    name: idx.name,
    traders: traders.length,
    capital: idx.capital ?? 1000,
    liveEnabled: false,
    note: "saved PAUSED — starting it is a separate, human action",
    ...(rejected.length ? { rejected } : {}),
  };
}

function updateStrat(args: Record<string, unknown>): StratOpResult {
  const idx = findStrat(String(args.id || ""));
  if (!idx) return { ok: false, error: `no strat with id "${String(args.id)}" — ids come from pm_strats` };
  const { entries, rejected } = validatePatch(idx, args.patch);
  if (entries.length === 0) {
    return { ok: false, error: "nothing to change", ...(rejected.length ? { rejected } : {}) };
  }
  upsertIndex(applyPatch(idx, entries));
  window.dispatchEvent(new Event("strat-updated"));
  return {
    ok: true,
    id: idx.id,
    name: idx.name,
    applied: entries.map(describeEntry),
    ...(rejected.length ? { rejected } : {}),
  };
}

function deleteStrat(args: Record<string, unknown>): StratOpResult {
  const idx = findStrat(String(args.id || ""));
  if (!idx) return { ok: false, error: `no strat with id "${String(args.id)}" — ids come from pm_strats` };
  deleteIndex(idx.id);
  window.dispatchEvent(new Event("strat-updated"));
  return { ok: true, deleted: idx.id, name: idx.name };
}

/** Execute one approved console op. Throws nothing: every failure is an
    {ok:false} the model gets to read. */
export function applyStratOp(tool: string, args: Record<string, unknown>): StratOpResult {
  try {
    if (tool === "pm_strat_create") return createStrat(args);
    if (tool === "pm_strat_update") return updateStrat(args);
    if (tool === "pm_strat_delete") return deleteStrat(args);
    return { ok: false, error: `not a console op: ${tool}` };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** The tools this module executes at APPROVE time (must mirror mcp.py's
    CONSOLE_TOOLS). */
export function isConsoleOp(tool: string): boolean {
  return tool === "pm_strat_create" || tool === "pm_strat_update" || tool === "pm_strat_delete";
}
