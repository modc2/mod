"use client";

/**
 * Owner key — who "my strats" means, with no sign-up.
 *
 * The browser generates one random key and keeps it. It goes out on every
 * strat call as `X-Owner-Key`; the server stores only its SHA-256 and hands
 * back the first 16 hex of that hash as your FINGERPRINT — the id you give
 * someone who should be whitelisted on a private strat. There is no
 * recovery: lose the key, lose write access. That's the honest price of not
 * asking anyone to make an account, and why EXPORT exists.
 */

const KEY = "copytensor:owner_key:v1";

function generate(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The key for this browser, minted on first use. */
export function ownerKey(): string {
  if (typeof window === "undefined") return "";
  let k = "";
  try {
    k = localStorage.getItem(KEY) || "";
    if (!k) {
      k = generate();
      localStorage.setItem(KEY, k);
    }
  } catch {
    // Private mode with storage blocked: a per-tab key still lets you build
    // and backtest, it just won't be there next visit.
    k = k || generate();
  }
  return k;
}

/** Replace the key — how you carry your strats to another browser. */
export function setOwnerKey(k: string) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(KEY, k.trim());
  } catch {}
}

export function ownerHeader(): Record<string, string> {
  const k = ownerKey();
  return k ? { "X-Owner-Key": k } : {};
}
