import { NextRequest, NextResponse } from "next/server";
import { buildAuthorize, exchangeCode, writeCredentials } from "@/lib/claudeOAuth";
import { isOwnerRequest, OWNER_ONLY } from "@/lib/ownerAuth";

// "Log in with Claude" OAuth, manual-code variant. Runs server-side as the
// `node` user inside the build container (same context as the paste route),
// so the credentials it writes are immediately the ones the job runner uses.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET → start a login: hand the client an authorize URL plus the PKCE verifier
// and state it must echo back on completion. The verifier round-trips through
// the owner's own browser (single-use, short-lived) — no server session store.
export async function GET(req: NextRequest) {
  if (!isOwnerRequest(req)) return NextResponse.json(OWNER_ONLY, { status: 401 });
  const { url, verifier, state } = buildAuthorize();
  return NextResponse.json({ url, verifier, state });
}

// POST → complete a login: exchange the pasted code for tokens and persist them.
// Owner-only: this writes the credentials every job on this host then runs on.
export async function POST(req: NextRequest) {
  if (!isOwnerRequest(req)) return NextResponse.json(OWNER_ONLY, { status: 401 });
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }
  const { code, verifier, state } = body || {};
  if (typeof code !== "string" || typeof verifier !== "string" || typeof state !== "string") {
    return NextResponse.json(
      { ok: false, error: "provide `code`, `verifier`, and `state`" },
      { status: 400 }
    );
  }
  try {
    const creds = await exchangeCode(code, verifier, state);
    writeCredentials(creds);
    return NextResponse.json({
      ok: true,
      expiresAt: creds.expiresAt,
      subscriptionType: creds.subscriptionType,
      scopes: creds.scopes,
    });
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e?.message || "login failed" }, { status: 400 });
  }
}
