import { NextRequest, NextResponse } from "next/server";
import { buildAuthorize, exchangeCode, writeCredentials } from "@/lib/claudeOAuth";
import { isOwnerRequest, signedInAddress, OWNER_ONLY } from "@/lib/ownerAuth";

// "Log in with Claude" OAuth, manual-code variant. Runs server-side as the
// `node` user inside the build container (same context as the paste route),
// so the credentials it writes are immediately the ones the job runner uses.
//
// Two destinations, one flow:
//   mode "server"  (default, OWNER-only) — writes ~/.claude/.credentials.json,
//                  the fallback every job on this host runs under.
//   mode "session" (any signed-in wallet) — connects the caller's OWN agent
//                  slot (POST /agent/auth/claude on the API), so the jobs THEY
//                  submit run on THEIR account. Nothing is written to the
//                  host's credentials file and the tokens never reach the
//                  browser: the exchange result goes straight to the API.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SIGN_IN_FIRST = {
  ok: false,
  error: "sign in first — a session is connected to one wallet",
} as const;

// The Rust API, reached over loopback. This is a server-to-server hop, so a
// localhost URL is correct here (unlike the browser's, which must go through
// the gateway path).
function apiBase(): string {
  const explicit = process.env.BUILD_FORK_API_INTERNAL_URL?.trim();
  if (explicit) return explicit.replace(/\/+$/, "");
  return `http://127.0.0.1:${process.env.BUILD_FORK_API_PORT || "8894"}`;
}

// GET → start a login: hand the client an authorize URL plus the PKCE verifier
// and state it must echo back on completion. The verifier round-trips through
// the owner's own browser (single-use, short-lived) — no server session store.
// Any signed-in wallet may start one; where the result LANDS is decided on
// POST, and gated there.
export async function GET(req: NextRequest) {
  if (!signedInAddress(req)) return NextResponse.json(SIGN_IN_FIRST, { status: 401 });
  const { url, verifier, state } = buildAuthorize();
  return NextResponse.json({ url, verifier, state });
}

// POST → complete a login: exchange the pasted code for tokens, then either
// install them host-wide (owner) or connect them to the caller's own slot.
export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }
  const mode = body?.mode === "session" ? "session" : "server";
  const address = signedInAddress(req);
  if (mode === "session") {
    if (!address) return NextResponse.json(SIGN_IN_FIRST, { status: 401 });
  } else if (!isOwnerRequest(req)) {
    return NextResponse.json(OWNER_ONLY, { status: 401 });
  }

  const { code, verifier, state } = body || {};
  if (typeof code !== "string" || typeof verifier !== "string" || typeof state !== "string") {
    return NextResponse.json(
      { ok: false, error: "provide `code`, `verifier`, and `state`" },
      { status: 400 }
    );
  }
  let creds;
  try {
    creds = await exchangeCode(code, verifier, state);
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e?.message || "login failed" }, { status: 400 });
  }

  if (mode === "session") {
    // Hand the credential to the API as the caller — same gate, same store as
    // a pasted token, plus the refresh token the runner renews it with.
    try {
      const auth = req.headers.get("authorization");
      const r = await fetch(`${apiBase()}/agent/auth/claude`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(auth ? { Authorization: auth } : {}),
        },
        body: JSON.stringify({
          token: creds.accessToken,
          refresh_token: creds.refreshToken,
          expires_at: creds.expiresAt, // ms; the API normalizes
          source: "oauth",
        }),
      });
      const data = await r.json().catch(() => null);
      if (!r.ok) {
        return NextResponse.json(
          { ok: false, error: data?.error || `could not store the session (${r.status})` },
          { status: r.status }
        );
      }
      return NextResponse.json({
        ok: true,
        mode,
        address,
        expiresAt: creds.expiresAt,
        subscriptionType: creds.subscriptionType,
        scopes: creds.scopes,
        agentAuth: data, // the API's fresh snapshot — the console renders it
      });
    } catch (e: any) {
      return NextResponse.json(
        { ok: false, error: `could not reach the API: ${e?.message || e}` },
        { status: 502 }
      );
    }
  }

  try {
    writeCredentials(creds);
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e?.message || "write failed" }, { status: 500 });
  }
  return NextResponse.json({
    ok: true,
    mode,
    expiresAt: creds.expiresAt,
    subscriptionType: creds.subscriptionType,
    scopes: creds.scopes,
  });
}
