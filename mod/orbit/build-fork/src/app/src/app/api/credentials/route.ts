import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import os from "os";
import path from "path";
import { isOwnerRequest, OWNER_ONLY } from "@/lib/ownerAuth";

// This route runs server-side as the `node` user inside the build
// container and manages the Claude Code OAuth credentials the job runner
// spawns `claude` with. It lets an operator paste working credentials from
// the UI when the host-mounted ones are missing/expired — no shell needed.
//
// Owner-only, both ways: POST installs the account every job then bills to,
// and even GET describes the operator's live session (token preview, expiry,
// plan) — neither is a visitor's business.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CRED_PATH = path.join(os.homedir(), ".claude", ".credentials.json");

interface OAuth {
  accessToken?: string;
  refreshToken?: string;
  expiresAt?: number;
  scopes?: string[];
  subscriptionType?: string;
}

function readStatus() {
  try {
    // Resolve through the symlink the entrypoint creates so we report on the
    // file the CLI actually reads.
    const raw = fs.readFileSync(CRED_PATH, "utf8");
    const data = JSON.parse(raw);
    const oauth: OAuth = data?.claudeAiOauth || {};
    const access = oauth.accessToken;
    if (!access) {
      return { loggedIn: false, reason: "no accessToken in credentials file" };
    }
    const expiresAt = oauth.expiresAt || 0;
    const now = Date.now();
    const remainingMs = expiresAt ? expiresAt - now : null;
    const expired = remainingMs !== null && remainingMs <= 0;
    let symlinkTarget: string | null = null;
    try {
      if (fs.lstatSync(CRED_PATH).isSymbolicLink()) {
        symlinkTarget = fs.readlinkSync(CRED_PATH);
      }
    } catch {}
    return {
      loggedIn: !expired,
      expired,
      expiresAt,
      remainingMinutes: remainingMs !== null ? Math.floor(remainingMs / 60000) : null,
      subscriptionType: oauth.subscriptionType || null,
      tokenPreview: `${access.slice(0, 10)}…${access.slice(-4)}`,
      source: symlinkTarget ? `linked → ${symlinkTarget}` : "local file",
    };
  } catch (e: any) {
    if (e?.code === "ENOENT") return { loggedIn: false, reason: "no credentials file" };
    if (e?.code === "EACCES") return { loggedIn: false, reason: "credentials file not readable by node user" };
    return { loggedIn: false, reason: e?.message || "failed to read credentials" };
  }
}

export async function GET(req: NextRequest) {
  if (!isOwnerRequest(req)) return NextResponse.json(OWNER_ONLY, { status: 401 });
  return NextResponse.json(readStatus());
}

// Accepts one of:
//   { json: "<full .credentials.json contents>" }   ← paste from a working machine
//   { accessToken, refreshToken?, expiresAt?, subscriptionType? }
export async function POST(req: NextRequest) {
  if (!isOwnerRequest(req)) return NextResponse.json(OWNER_ONLY, { status: 401 });
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }

  let payload: any;

  if (typeof body?.json === "string" && body.json.trim()) {
    // Full credentials.json paste — accept the raw blob, validate shape.
    let parsed: any;
    try {
      parsed = JSON.parse(body.json);
    } catch {
      return NextResponse.json(
        { ok: false, error: "pasted text is not valid JSON" },
        { status: 400 }
      );
    }
    // Allow pasting either the whole file ({claudeAiOauth:{…}}) or just the
    // inner oauth object ({accessToken:…}).
    const oauth = parsed.claudeAiOauth || parsed;
    if (!oauth?.accessToken) {
      return NextResponse.json(
        { ok: false, error: "JSON has no claudeAiOauth.accessToken" },
        { status: 400 }
      );
    }
    payload = { claudeAiOauth: oauth };
  } else if (typeof body?.accessToken === "string" && body.accessToken.trim()) {
    const oauth: OAuth = {
      accessToken: body.accessToken.trim(),
      refreshToken: body.refreshToken?.trim() || undefined,
      expiresAt:
        typeof body.expiresAt === "number"
          ? body.expiresAt
          : body.expiresAt
          ? Number(body.expiresAt)
          : Date.now() + 8 * 3600 * 1000, // assume 8h if unknown
      subscriptionType: body.subscriptionType || "max",
    };
    payload = { claudeAiOauth: oauth };
  } else {
    return NextResponse.json(
      { ok: false, error: "provide `json` (full credentials) or `accessToken`" },
      { status: 400 }
    );
  }

  try {
    const dir = path.dirname(CRED_PATH);
    fs.mkdirSync(dir, { recursive: true });
    // Replace the entrypoint's symlink with a real, node-owned file so these
    // credentials take effect immediately and don't clobber the host's file.
    // (A container restart re-links to the host mount as the baseline.)
    try {
      if (fs.lstatSync(CRED_PATH).isSymbolicLink()) fs.unlinkSync(CRED_PATH);
    } catch {}
    fs.writeFileSync(CRED_PATH, JSON.stringify(payload, null, 2), { mode: 0o600 });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: `write failed: ${e?.message || e}` },
      { status: 500 }
    );
  }

  return NextResponse.json({ ok: true, status: readStatus() });
}
