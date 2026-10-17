// The chat agent's approval queue, server side — the TS half of the file
// contract whose other half is "the approval gate" in src/mcp.py.
//
// One gated tool call = one JSON file under <state>/approvals/. The mcp.py
// dispatcher writes the entry and polls it; this module is how the Next
// routes LIST the queue for the console and WRITE the owner's decision into
// it. Nothing here executes anything: approving a money tool lets mcp.py
// proceed to the Rust API, approving a console tool (pm_strat_*) records the
// result the browser already applied. Files are pruned after a day — the
// queue is a conversation artifact, not a ledger.

import { mkdirSync, readdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "fs";
import { join } from "path";

import { stateDir } from "./ownerToken";

export interface ApprovalEntry {
  id: string;
  /** The chat run that parked it (POLYMARKET_AGENT_RUN). */
  run: string;
  tool: string;
  args: Record<string, unknown>;
  /** nav = a pre-decided pm_console_open entry: never pending, the chat
      route forwards it as a `nav` event and the browser navigates. */
  kind: "money" | "spend" | "strat" | "nav";
  summary: string;
  at: number; // epoch seconds
  expires_at: number; // epoch seconds
  decision: "approve" | "decline" | "expired" | null;
  note: string;
  /** Console tools only: what the browser applied, handed back as the tool result. */
  result: Record<string, unknown> | null;
  decided_at?: number;
}

const PRUNE_AFTER_SECS = 24 * 3600;

export function approvalsDir(): string {
  const d = join(stateDir(), "approvals");
  mkdirSync(d, { recursive: true });
  return d;
}

function readEntry(path: string): ApprovalEntry | null {
  try {
    const e = JSON.parse(readFileSync(path, "utf8")) as ApprovalEntry;
    return e && typeof e.id === "string" && typeof e.tool === "string" ? e : null;
  } catch {
    return null; // mid-write or garbage — skip, never throw
  }
}

/** Every entry in the queue, oldest first, pruning anything older than a
    day. `run` narrows to one chat run. */
export function listApprovals(run?: string): ApprovalEntry[] {
  const dir = approvalsDir();
  const now = Date.now() / 1000;
  const out: ApprovalEntry[] = [];
  let names: string[];
  try {
    names = readdirSync(dir).filter((f) => f.endsWith(".json"));
  } catch {
    return [];
  }
  for (const name of names) {
    const path = join(dir, name);
    const e = readEntry(path);
    if (!e) continue;
    if (e.at && now - e.at > PRUNE_AFTER_SECS) {
      try { unlinkSync(path); } catch { /* next pass */ }
      continue;
    }
    if (run && e.run !== run) continue;
    out.push(e);
  }
  return out.sort((a, b) => a.at - b.at);
}

export type DecideOutcome =
  | { ok: true; entry: ApprovalEntry }
  | { ok: false; error: string };

/** Write the owner's answer. Refuses to re-decide (the first answer stands —
    mcp.py may already have acted on it) and refuses to approve past the
    deadline mcp.py has already given up on. */
export function decideApproval(
  id: string,
  decision: "approve" | "decline",
  note = "",
  result: Record<string, unknown> | null = null,
): DecideOutcome {
  if (!/^ap_[a-z0-9_]+$/i.test(id)) return { ok: false, error: "bad approval id" };
  const path = join(approvalsDir(), `${id}.json`);
  const entry = readEntry(path);
  if (!entry) return { ok: false, error: "approval not found" };
  if (entry.decision) return { ok: false, error: `already ${entry.decision}` };
  if (Date.now() / 1000 > entry.expires_at) return { ok: false, error: "expired" };
  const decided: ApprovalEntry = {
    ...entry,
    decision,
    note: String(note || "").slice(0, 500),
    result,
    decided_at: Date.now() / 1000,
  };
  // Atomic like mcp.py's writes — its poller must never read half a file.
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, JSON.stringify(decided, null, 2));
  renameSync(tmp, path);
  return { ok: true, entry: decided };
}

/** Fail closed when a chat stream dies: everything still parked for that
    run is declined so mcp.py unblocks with a real answer instead of waiting
    out the TTL against a closed socket. */
export function declineRunLeftovers(run: string, note: string): number {
  let n = 0;
  for (const e of listApprovals(run)) {
    if (!e.decision && decideApproval(e.id, "decline", note).ok) n += 1;
  }
  return n;
}
