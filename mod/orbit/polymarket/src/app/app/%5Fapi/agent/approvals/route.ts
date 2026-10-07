// /polymarket/_api/agent/approvals — the owner's side of the chat agent's
// approval gate (the gate itself lives in src/mcp.py; see agentApprovals.ts
// for the file contract).
//
//   GET            the queue (pending first; ?run= narrows to one chat run)
//   POST {id, decision, note?, result?}
//                  record the owner's answer. `result` is only meaningful
//                  for console tools (pm_strat_*): it's what the browser
//                  applied, handed back to the model as the tool result.
//
// Owner-gated: an approval IS the owner's signature on a money/strat action,
// so only the owner token may read or write one. The first answer stands —
// mcp.py may already have acted on it — and nothing can be approved past
// its deadline.

import { NextResponse } from "next/server";

import { decideApproval, listApprovals } from "../../../lib/server/agentApprovals";
import { bearer, verifyOwnerToken } from "../../../lib/server/ownerToken";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const run = new URL(req.url).searchParams.get("run") || undefined;
  const all = listApprovals(run);
  return NextResponse.json({
    pending: all.filter((a) => !a.decision),
    decided: all.filter((a) => a.decision).slice(-20),
  });
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  let body: { id?: string; decision?: string; note?: string; result?: Record<string, unknown> };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  const decision = body.decision === "approve" ? "approve" : body.decision === "decline" ? "decline" : null;
  if (!body.id || !decision) {
    return NextResponse.json({ error: "need {id, decision: approve|decline}" }, { status: 400 });
  }
  const out = decideApproval(
    String(body.id),
    decision,
    typeof body.note === "string" ? body.note : "",
    body.result && typeof body.result === "object" ? body.result : null,
  );
  if (out.ok === false) return NextResponse.json({ error: out.error }, { status: 409 });
  return NextResponse.json({ ok: true, entry: out.entry });
}
