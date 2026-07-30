// Server-side owner gate for the TERMINAL tab (host root shell). The terminal
// route runs on the host as root, so it MUST prove the caller is the configured
// owner before executing anything. Flow:
//   1. Owner signs `buildAuthMessage(addr, ts)` with the owner wallet.
//   2. /api/terminal/auth verifies the signature recovers the config owner and
//      issues an HMAC session token (server secret at ~/.mod/dev/terminal.secret).
//   3. /api/terminal requires a valid session token on every command.
import fs from "fs";
import os from "os";
import path from "path";
import crypto from "crypto";
import { verifyMessage } from "ethers";

const SECRET_PATH = path.join(os.homedir(), ".mod", "build", "terminal.secret");
const SESSION_TTL_MS = 24 * 3600 * 1000;   // session valid 24h after signing
const AUTH_MAX_SKEW_MS = 5 * 60 * 1000;    // signed timestamp must be fresh (anti-replay)

// Resolve the configured owner address (lowercase) from config.json. The host
// next-server cwd is the app root (…/build/src/app); config.json is two levels up.
export function ownerAddress(): string | null {
  const candidates = [
    path.resolve(process.cwd(), "../../config.json"),
    path.resolve(process.cwd(), "../config.json"),
    path.join(os.homedir(), "mod/mod/orbit/build/config.json"),
    "/root/mod/mod/orbit/build/config.json",
  ];
  for (const c of candidates) {
    try {
      const data = JSON.parse(fs.readFileSync(c, "utf8"));
      if (data?.owner) return String(data.owner).toLowerCase();
    } catch {}
  }
  return null;
}

// Lazily create a 0600 server secret so tokens survive across requests but
// can't be forged without host access.
function getSecret(): Buffer {
  try {
    return fs.readFileSync(SECRET_PATH);
  } catch {
    const secret = crypto.randomBytes(32);
    try {
      fs.mkdirSync(path.dirname(SECRET_PATH), { recursive: true });
      fs.writeFileSync(SECRET_PATH, secret, { mode: 0o600 });
    } catch {}
    return secret;
  }
}

function hmac(data: string): string {
  return crypto.createHmac("sha256", getSecret()).update(data).digest("hex");
}

// EXACT message the client must sign. Keep in sync with the frontend.
export function buildAuthMessage(address: string, ts: number): string {
  return [
    "MOD Terminal Authorization",
    `address: ${address.toLowerCase()}`,
    `ts: ${ts}`,
    "",
    "Sign to use the owner terminal on this server. This is a free signature, not a transaction.",
  ].join("\n");
}

type AuthResult =
  | { ok: true; token: string; expiresAt: number }
  | { ok: false; error: string };

// Verify a fresh owner signature and mint a session token.
export function authorize(address: string, ts: number, signature: string): AuthResult {
  const owner = ownerAddress();
  if (!owner) return { ok: false, error: "no owner configured" };
  if (!Number.isFinite(ts) || Math.abs(Date.now() - ts) > AUTH_MAX_SKEW_MS) {
    return { ok: false, error: "stale or invalid timestamp — try again" };
  }
  let recovered: string;
  try {
    recovered = verifyMessage(buildAuthMessage(address, ts), signature);
  } catch {
    return { ok: false, error: "bad signature" };
  }
  if (recovered.toLowerCase() !== address.toLowerCase()) {
    return { ok: false, error: "signature does not match address" };
  }
  if (recovered.toLowerCase() !== owner) {
    return { ok: false, error: "not the owner" };
  }
  const expiry = Date.now() + SESSION_TTL_MS;
  const token = `${owner}:${expiry}:${hmac(`${owner}:${expiry}`)}`;
  return { ok: true, token, expiresAt: expiry };
}

// Validate a session token presented on a terminal command.
export function verifySession(token: string | null | undefined): boolean {
  if (!token) return false;
  const parts = token.split(":");
  if (parts.length !== 3) return false;
  const [addr, expStr, sig] = parts;
  const expiry = Number(expStr);
  if (!Number.isFinite(expiry) || Date.now() > expiry) return false;
  const owner = ownerAddress();
  if (!owner || addr.toLowerCase() !== owner) return false;
  const expected = hmac(`${addr}:${expiry}`);
  try {
    return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
  } catch {
    return false;
  }
}
