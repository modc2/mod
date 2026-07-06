import type { SavedIndex } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";
const HMAC_SECRET = process.env.NEXT_PUBLIC_STRAT_HMAC_SECRET || "";

// ── Crypto helpers (Web Crypto API — no npm deps) ──

async function deriveKey(localToken: string): Promise<CryptoKey> {
  const enc = new TextEncoder();
  const hash = await crypto.subtle.digest("SHA-256", enc.encode(localToken));
  return crypto.subtle.importKey("raw", hash, { name: "AES-GCM" }, false, [
    "encrypt",
    "decrypt",
  ]);
}

export async function encryptStrat(
  strat: SavedIndex,
  localToken: string,
): Promise<string> {
  const key = await deriveKey(localToken);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = new TextEncoder().encode(JSON.stringify(strat));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    plaintext,
  );
  // Prepend IV to ciphertext, then base64 the whole thing
  const combined = new Uint8Array(iv.length + ciphertext.byteLength);
  combined.set(iv, 0);
  combined.set(new Uint8Array(ciphertext), iv.length);
  return btoa(String.fromCharCode(...combined));
}

export async function decryptStrat(
  blob: string,
  localToken: string,
): Promise<SavedIndex | null> {
  try {
    const key = await deriveKey(localToken);
    const raw = Uint8Array.from(atob(blob), (c) => c.charCodeAt(0));
    const iv = raw.slice(0, 12);
    const ciphertext = raw.slice(12);
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv },
      key,
      ciphertext,
    );
    return JSON.parse(new TextDecoder().decode(plaintext));
  } catch {
    return null;
  }
}

// ── HMAC transport auth ──

async function hmacSign(body: string): Promise<string> {
  if (!HMAC_SECRET) return "";
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(HMAC_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(body));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function tokenPreview(localToken: string): string {
  return localToken.slice(0, 8);
}

// ── API Client ──

interface ServerStrat {
  id: string;
  ciphertext: string;
  updated_at: number;
}

export async function fetchServerStrats(
  localToken: string,
): Promise<ServerStrat[]> {
  try {
    const tid = tokenPreview(localToken);
    const res = await fetch(`${API_URL}/strats?token_id=${tid}`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.strats || [];
  } catch {
    return [];
  }
}

export async function pushStrat(
  strat: SavedIndex,
  localToken: string,
): Promise<boolean> {
  try {
    const tid = tokenPreview(localToken);
    const ciphertext = await encryptStrat(strat, localToken);
    const body = JSON.stringify({
      token_id: tid,
      ciphertext,
      updated_at: strat.updatedAt,
    });
    const sig = await hmacSign(body);
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (sig) headers["x-strat-sig"] = sig;

    const res = await fetch(`${API_URL}/strats/${strat.id}`, {
      method: "PUT",
      headers,
      body,
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function deleteServerStrat(
  id: string,
  localToken: string,
): Promise<boolean> {
  try {
    const tid = tokenPreview(localToken);
    const sigBody = `${tid}:${id}`;
    const sig = await hmacSign(sigBody);
    const headers: Record<string, string> = {};
    if (sig) headers["x-strat-sig"] = sig;

    const res = await fetch(`${API_URL}/strats/${id}?token_id=${tid}`, {
      method: "DELETE",
      headers,
    });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Sync server strats with localStorage.
 * Decrypts server blobs, merges with local (server wins on conflict by updatedAt).
 * Returns merged list.
 */
export async function syncStrats(
  localStrats: SavedIndex[],
  localToken: string,
): Promise<SavedIndex[]> {
  const serverBlobs = await fetchServerStrats(localToken);
  const serverStrats: SavedIndex[] = [];

  for (const blob of serverBlobs) {
    const decrypted = await decryptStrat(blob.ciphertext, localToken);
    if (decrypted) serverStrats.push(decrypted);
  }

  // Merge: index by id, server wins if updatedAt is newer
  const merged = new Map<string, SavedIndex>();
  for (const s of localStrats) merged.set(s.id, s);
  for (const s of serverStrats) {
    const existing = merged.get(s.id);
    if (!existing || s.updatedAt > existing.updatedAt) {
      merged.set(s.id, s);
    }
  }

  // Push any local-only or locally-newer strats to server
  for (const s of merged.values()) {
    const onServer = serverStrats.find((ss) => ss.id === s.id);
    if (!onServer || s.updatedAt > (onServer.updatedAt || 0)) {
      pushStrat(s, localToken); // fire-and-forget
    }
  }

  return Array.from(merged.values());
}
