// /polymarket/_api/agents — the AGENT ROSTER (STRATS ▸ AGENTS).
//
//   GET                        every agent mod under src/agents/ with on/off
//                              (public — names and descriptions only)
//   POST {name, enabled}       switch one on/off (owner-gated)
//
// Each agent is its own mod; see lib/server/agentMods.ts and src/agents/README.md.

import { NextResponse } from "next/server";

import { listAgents, setAgentEnabled } from "../../lib/server/agentMods";
import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  return NextResponse.json({ agents: listAgents() });
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) {
    return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
  }
  let body: { name?: unknown; enabled?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  if (typeof body.name !== "string" || typeof body.enabled !== "boolean") {
    return NextResponse.json({ error: "need {name, enabled}" }, { status: 400 });
  }
  const agent = setAgentEnabled(body.name, body.enabled);
  if (!agent) return NextResponse.json({ error: `unknown agent "${body.name}"` }, { status: 404 });
  return NextResponse.json({ agent, agents: listAgents() });
}
