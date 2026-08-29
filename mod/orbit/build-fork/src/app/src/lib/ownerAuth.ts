// Server-side owner gate for the app's own /api/* routes.
//
// These Next route handlers run as root on the host (pm2 `build-fork-app`), so
// anything they do — spawning services, writing the job runner's Claude
// credentials — is a host-level power and must prove the caller is the owner
// before it happens. The console already holds a build-API bearer token, so
// rather than invent a second session we validate THAT token here: same HMAC
// secret (~/.mod/build-fork/server.secret), same owner list, one identity.
//
// Accepted proofs, in order:
//   1. `Authorization: Bearer <build-API token>` from the owner (or a co-owner).
//   2. `x-terminal-token` — the TERMINAL tab's owner session (lib/terminalAuth).
//   3. BUILD_FORK_JOBS_LOCAL=1 — the trusted host-only dev server, no auth anywhere.
import fs from "fs";
import os from "os";
import path from "path";
import crypto from "crypto";
import { ownerAddress, verifySession } from "./terminalAuth";

const SECRET_PATH = path.join(os.homedir(), ".mod", "build-fork", "server.secret");
const OWNERS_PATH = path.join(os.homedir(), ".mod", "build-fork", "owners.json");
const TOKEN_TTL_MS = 24 * 3600 * 1000; // matches the Rust side's 86400s

export function localMode(): boolean {
  return process.env.BUILD_FORK_JOBS_LOCAL === "1";
}

// The API's HMAC secret. Absent means the API has never run — then no bearer
// token can be valid, and we fail closed rather than minting a fallback.
function serverSecret(): Buffer | null {
  try {
    const b = fs.readFileSync(SECRET_PATH);
    return b.length === 32 ? b : null;
  } catch {
    return null;
  }
}

// Co-owner wallets (~/.mod/build-fork/owners.json), same format the Rust side reads:
// a bare array or {"addresses": [...]}.
function coOwners(): string[] {
  try {
    const parsed = JSON.parse(fs.readFileSync(OWNERS_PATH, "utf8"));
    const arr = Array.isArray(parsed) ? parsed : parsed?.addresses;
    return Array.isArray(arr)
      ? arr.filter((a) => typeof a === "string").map((a) => a.toLowerCase())
      : [];
  } catch {
    return [];
  }
}

function isOwnerAddress(addr: string): boolean {
  const a = addr.toLowerCase();
  const owner = ownerAddress();
  return (!!owner && a === owner) || coOwners().includes(a);
}

// Validate a build-API bearer token (`address:timestamp:hmac`, or
// `address:timestamp:ho:hmac` for handed-off sessions) and return its address.
export function addressFromBearer(header: string | null | undefined): string | null {
  if (!header?.startsWith("Bearer ")) return null;
  const parts = header.slice(7).trim().split(":");
  let address: string, ts: string, sig: string, payload: string;
  if (parts.length === 3) {
    [address, ts, sig] = parts;
    payload = `${address}:${ts}`;
  } else if (parts.length === 4 && parts[2] === "ho") {
    [address, ts, , sig] = parts;
    payload = `${address}:${ts}:ho`;
  } else {
    return null;
  }
  const issued = Number(ts);
  if (!Number.isFinite(issued)) return null;
  if (Date.now() - issued * 1000 > TOKEN_TTL_MS) return null;

  const secret = serverSecret();
  if (!secret) return null;
  const expected = crypto.createHmac("sha256", secret).update(payload).digest("hex");
  try {
    if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  } catch {
    return null;
  }
  return address.toLowerCase();
}

/// The address behind this request's bearer token, or null when it carries
/// none (or an invalid one). Weaker than `isOwnerRequest` on purpose: some
/// routes act on the CALLER'S OWN account — connecting their Claude session,
/// say — where being signed in at all is the whole gate. Local mode has one
/// identity and no signing, matching what the Rust side calls the caller.
export function signedInAddress(req: { headers: Headers }): string | null {
  if (localMode()) return "local";
  return addressFromBearer(req.headers.get("authorization"));
}

/// True when this request carries proof that the caller is the host owner.
export function isOwnerRequest(req: { headers: Headers }): boolean {
  if (localMode()) return true;
  const addr = addressFromBearer(req.headers.get("authorization"));
  if (addr && isOwnerAddress(addr)) return true;
  return verifySession(req.headers.get("x-terminal-token"));
}

/// The 401 body these routes answer with when the gate refuses.
export const OWNER_ONLY = {
  ok: false,
  error: "owner-only — sign in with the owner wallet",
} as const;
