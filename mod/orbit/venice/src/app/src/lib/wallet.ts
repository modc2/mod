// Wallet helpers + mod **protocol-auth** token builder.
//
// The token is a signed, time-bounded `{data, time, key, signature}`
// envelope, base64url-encoded — verified statelessly by the Rust gateway
// (src/api/src/auth.rs), which mirrors mod/core/server/auth.
//
// The signed material is `JSON.stringify({ data, time })` (compact, no spaces),
// signed via EIP-191 `personal_sign`. We keep `data` to a single key so its
// JSON serialization is unambiguous across JS / Rust / Python.
//
// Two identities can produce that signature; the gateway treats them
// identically (it only ever sees the recovered address):
//
//   1. **Wallet** — MetaMask `personal_sign`. The signer IS your real,
//      on-chain address: convenient, but it links every edit to your wallet.
//   2. **Local derivation** — a random secp256k1 keypair generated in this
//      browser (viem) and kept only in localStorage. The address is an
//      ephemeral pseudonym with no link to your wallet or any chain identity:
//      edit photos under a throwaway identity, each with its own Venice key.
//      Nothing about you leaves the browser except the (EIP-191) signature.
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import type { Hex } from "viem";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
      on?: (event: string, handler: (...args: unknown[]) => void) => void;
      removeListener?: (event: string, handler: (...args: unknown[]) => void) => void;
      isMetaMask?: boolean;
    };
  }
}

export function shortAddress(addr: string): string {
  if (!addr || addr.length < 10) return addr || "";
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export function hasWallet(): boolean {
  return typeof window !== "undefined" && !!window.ethereum;
}

export async function connectWallet(): Promise<{ address: string; chainId: number }> {
  if (!window.ethereum) throw new Error("No Ethereum wallet detected");
  const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
  const chainIdHex = (await window.ethereum.request({ method: "eth_chainId" })) as string;
  return { address: accounts[0], chainId: parseInt(chainIdHex, 16) };
}

export async function personalSign(message: string, address: string): Promise<string> {
  if (!window.ethereum) throw new Error("No Ethereum wallet detected");
  return (await window.ethereum.request({
    method: "personal_sign",
    params: [message, address],
  })) as string;
}

/**
 * Best-effort `localStorage.setItem`. Browsers throw `QuotaExceededError` when
 * the origin is full — and every modc2.com module shares one origin, so venice's
 * tiny writes can be the call that tips it over. Persistence is a convenience;
 * the live session lives in React state regardless, so a failed write must never
 * throw. Returns true iff the value was actually stored.
 */
export function safeSetItem(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

// base64url JSON, matching Python's urlsafe_b64encode(...).rstrip(b"=").
function b64urlJson(obj: unknown): string {
  const s = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(s)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Build a mod protocol-auth token. `data` defaults to `{ scope: "venice" }`.
 * Signs `JSON.stringify({ data, time })` — exactly what the gateway rebuilds
 * and verifies (sig over ["data","time"], compact separators).
 */
export async function buildModToken(
  address: string,
  data: Record<string, unknown> = { scope: "venice" }
): Promise<string> {
  const time = (Date.now() / 1000).toString();
  const sigData = JSON.stringify({ data, time });
  const signature = await personalSign(sigData, address);
  return b64urlJson({ data, time, key: address, signature });
}

// ── Local (anonymous) identity ────────────────────────────────────────────
// A secp256k1 keypair generated and held entirely in this browser. Its
// address is a pseudonym — never linked to a wallet or any chain. The private
// key lives only in localStorage; clearing it (or "Forget" in the UI) destroys
// the identity and orphans whatever Venice key was stored under it server-side.

const LOCAL_PK_KEY = "venice:local-pk";

export type LocalIdentity = { address: string; pk: Hex };

/** True if a private key is present for local sign-in (browser only). */
export function hasLocalIdentity(): boolean {
  return typeof window !== "undefined" && !!localStorage.getItem(LOCAL_PK_KEY);
}

/** Load the stored local identity, or null if none exists / not in a browser. */
export function loadLocalIdentity(): LocalIdentity | null {
  if (typeof window === "undefined") return null;
  const pk = localStorage.getItem(LOCAL_PK_KEY) as Hex | null;
  if (!pk) return null;
  try {
    return { address: privateKeyToAccount(pk).address, pk };
  } catch {
    localStorage.removeItem(LOCAL_PK_KEY); // corrupt — drop it
    return null;
  }
}

/**
 * Return the existing local identity, or mint a fresh random one and persist
 * it. `generatePrivateKey` draws from the platform CSPRNG (WebCrypto).
 */
export function getOrCreateLocalIdentity(): LocalIdentity {
  const existing = loadLocalIdentity();
  if (existing) return existing;
  const pk = generatePrivateKey();
  // If storage is full the identity still works for this session, but it can't
  // survive a reload — surfaced to the user by the caller via `persisted`.
  safeSetItem(LOCAL_PK_KEY, pk);
  return { address: privateKeyToAccount(pk).address, pk };
}

/** Permanently forget the local identity (and thus its server-side key). */
export function clearLocalIdentity(): void {
  if (typeof window !== "undefined") localStorage.removeItem(LOCAL_PK_KEY);
}

/**
 * Build a mod protocol-auth token signed by the local keypair. Produces the
 * same EIP-191 signature MetaMask would, so the gateway's verifier accepts it
 * with no special-casing — the recovered address is the local pseudonym.
 */
export async function buildLocalModToken(
  id: LocalIdentity,
  data: Record<string, unknown> = { scope: "venice" }
): Promise<string> {
  const time = (Date.now() / 1000).toString();
  const sigData = JSON.stringify({ data, time });
  const account = privateKeyToAccount(id.pk);
  const signature = await account.signMessage({ message: sigData });
  return b64urlJson({ data, time, key: id.address.toLowerCase(), signature });
}
