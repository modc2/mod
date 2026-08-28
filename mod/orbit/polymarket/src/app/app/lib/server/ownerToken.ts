// Owner identity, server side.
//
// The Rust API mints `pma1.<address>.<exp>.<hmac>` tokens against a secret it
// persists at ~/.mod/polymarket/server.secret (api/src/access.rs). Two things
// in the Next server need that secret:
//
//   • the /api/hub route, to check that a caller is the owner before it reads
//     or writes the backtest cache;
//   • the background worker, which has to call the gated Rust API with no
//     browser to sign in for it, so it mints itself the same token the console
//     would have gotten.
//
// Same secret, same format, same 7-day TTL — this is a reader for the Rust
// implementation, not a second auth system.

import { createHmac, timingSafeEqual } from "crypto";
import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

const TOKEN_TTL_SECS = 7 * 24 * 3600;

/** Where the module keeps per-deployment state — mirror of `state_dir()` in
    api/src/access.rs, including the env override the API honors. */
export function stateDir(): string {
  return process.env.POLYMARKET_ACCESS_DIR || join(homedir(), ".mod", "polymarket");
}

function secret(): Buffer | null {
  try {
    const hex = readFileSync(join(stateDir(), "server.secret"), "utf8").trim();
    const buf = Buffer.from(hex, "hex");
    return buf.length === 32 ? buf : null;
  } catch {
    return null;
  }
}

/** The deployment's owner address, resolved the way the API resolves it:
    env → polymarket/owner.json → the fleet-wide claude/owner.json. */
export function ownerAddress(): string | null {
  const fromEnv = process.env.POLYMARKET_OWNER?.trim().toLowerCase();
  if (fromEnv?.startsWith("0x") && fromEnv.length === 42) return fromEnv;
  for (const p of [join(stateDir(), "owner.json"), join(homedir(), ".mod", "claude", "owner.json")]) {
    try {
      const o = String(JSON.parse(readFileSync(p, "utf8")).owner || "").trim().toLowerCase();
      if (o.startsWith("0x") && o.length === 42) return o;
    } catch {
      // next candidate
    }
  }
  return null;
}

function sign(data: string, key: Buffer): string {
  return createHmac("sha256", key).update(data).digest("hex");
}

/** Mint an owner token for the worker's own calls into the Rust API. Null when
    the deployment has no owner or no secret — the worker then does nothing,
    which is the correct behavior for an unconfigured gate. */
export function mintOwnerToken(): string | null {
  const key = secret();
  const owner = ownerAddress();
  if (!key || !owner) return null;
  const exp = Math.floor(Date.now() / 1000) + TOKEN_TTL_SECS;
  return `pma1.${owner}.${exp}.${sign(`pma1|${owner}|${exp}`, key)}`;
}

/** The address a valid, unexpired, owner-issued token belongs to. */
export function verifyOwnerToken(token: string | null | undefined): string | null {
  if (!token) return null;
  const parts = token.trim().split(".");
  if (parts.length !== 4 || parts[0] !== "pma1") return null;
  const [, addr, expRaw, sig] = parts;
  const exp = Number(expRaw);
  if (!Number.isFinite(exp) || Date.now() / 1000 >= exp) return null;
  const key = secret();
  if (!key) return null;
  const expected = sign(`pma1|${addr}|${exp}`, key);
  const a = Buffer.from(sig, "utf8");
  const b = Buffer.from(expected, "utf8");
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  // Ownership is re-checked on every request, so rotating the owner locks out
  // tokens minted for the previous one — same rule as the Rust gate.
  return ownerAddress() === addr.toLowerCase() ? addr.toLowerCase() : null;
}

/** Bearer token out of a request's Authorization header. */
export function bearer(req: Request): string | null {
  const h = req.headers.get("authorization") || "";
  return h.toLowerCase().startsWith("bearer ") ? h.slice(7).trim() : null;
}
