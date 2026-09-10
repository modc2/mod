// Owner gate for the Next routes that read/write the agent's Claude
// credentials (/api/credentials, /api/credentials/oauth).
//
// Those routes hand out session metadata and — worse — WRITE the credentials
// every job runs under, so they must not be open. Rather than re-deriving the
// owner rule here (config owner + BlocTime holders + whitelist all matter, and
// the Rust API already owns that logic), we delegate: forward the caller's own
// credentials to the API's owner-only `GET /agent/auth` and require a 200.
// One source of truth, and no second copy of the HMAC/token format to drift.
//
// Fails CLOSED: if the API can't be reached the routes refuse. The escape hatch
// is CLAUDE_JOBS_LOCAL=1, the same env the Rust side uses to skip auth.
import { NextRequest, NextResponse } from "next/server";

// Server-side we always talk to the API over loopback — the browser-facing
// relative gateway path (`/api/claude`) is meaningless from inside the server.
function apiBase(): string {
  const explicit = process.env.CLAUDE_API_URL?.trim();
  if (explicit) return explicit.replace(/\/$/, "");
  const env = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (env && /^https?:\/\//.test(env)) return env.replace(/\/$/, "");
  const port = process.env.NEXT_PUBLIC_API_PORT?.trim() || "8820";
  return `http://127.0.0.1:${port}`;
}

export function localMode(): boolean {
  return process.env.CLAUDE_JOBS_LOCAL === "1";
}

/**
 * Returns null when the caller is the owner, or a ready-to-return error
 * response when they are not.
 */
export async function requireOwner(req: NextRequest): Promise<NextResponse | null> {
  if (localMode()) return null;

  const authorization = req.headers.get("authorization");
  const coreToken = req.headers.get("token");
  if (!authorization && !coreToken) {
    return NextResponse.json(
      { ok: false, error: "owner authentication required — sign in to the console first" },
      { status: 401 }
    );
  }

  const headers: Record<string, string> = {};
  if (authorization) headers["Authorization"] = authorization;
  if (coreToken) headers["token"] = coreToken;

  try {
    const res = await fetch(`${apiBase()}/agent/auth`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    if (res.ok) return null;
    const body = await res.json().catch(() => ({} as any));
    return NextResponse.json(
      { ok: false, error: body?.error || "not authorized (owner only)" },
      { status: res.status === 401 ? 401 : 403 }
    );
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: `could not verify owner with the claude API: ${e?.message || e}` },
      { status: 503 }
    );
  }
}
