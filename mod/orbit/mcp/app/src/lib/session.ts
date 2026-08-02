/**
 * Session = a mod protocol token plus the address that signed it.
 *
 * Two ways in, one result: a browser wallet (MetaMask & friends) or a keypair
 * this browser derived locally. The hub verifies a signature either way, so the
 * only difference the user sees is who holds the key.
 *
 * The token is time-bounded server-side (MCP_SESSION_TTL, 7 days) — we keep it
 * in quota-safe storage and drop it the moment the API answers 401.
 */
import { api, ApiError } from "./api";
import { createLocalKey, hasLocalKey, loadLocalKey, localSign } from "./localKey";
import { ADDRESS_STORAGE, TOKEN_STORAGE, storageGet, storageRemove, storageSet } from "./safeStorage";
import { buildModToken, connectWallet, personalSign } from "./wallet";

export type SignInMode = "wallet" | "local";

export interface Session {
  address: string;
  token: string;
  mode: SignInMode;
}

export function loadSession(): Session | null {
  const token = storageGet(TOKEN_STORAGE);
  const address = storageGet(ADDRESS_STORAGE);
  if (!token || !address) return null;
  return { token, address, mode: hasLocalKey() && loadLocalKey()?.address.toLowerCase() === address.toLowerCase() ? "local" : "wallet" };
}

function save(s: Session) {
  storageSet(TOKEN_STORAGE, s.token);
  storageSet(ADDRESS_STORAGE, s.address);
}

export function clearSession() {
  storageRemove(TOKEN_STORAGE);
  storageRemove(ADDRESS_STORAGE);
}

/** Sign in with the injected wallet — one signature, no transaction, no gas. */
export async function signInWithWallet(): Promise<Session> {
  const { address } = await connectWallet();
  const token = await buildModToken(address, { mcp: "hub" }, personalSign);
  const s: Session = { address, token, mode: "wallet" };
  save(s);
  return s;
}

/**
 * Sign in with a browser-held key, minting one on first use. `persisted` is
 * false when storage refused the write — the caller warns, because publishing
 * under a key that dies with the tab means losing the slug.
 */
export async function signInWithLocalKey(): Promise<Session & { persisted: boolean }> {
  let wallet = loadLocalKey();
  let persisted = true;
  if (!wallet) {
    const made = createLocalKey();
    wallet = made.wallet;
    persisted = made.persisted;
  }
  const token = await buildModToken(wallet.address, { mcp: "hub" }, (msg) => localSign(msg));
  const s: Session = { address: wallet.address, token, mode: "local" };
  save(s);
  return { ...s, persisted };
}

/** True when an error means the session is dead and should be dropped. */
export function isExpired(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

/** Cheap liveness check: /submissions?mine=1 needs a valid token and nothing else. */
export async function verifySession(s: Session): Promise<boolean> {
  try {
    await api.submissions(s.token, true);
    return true;
  } catch (e) {
    return !isExpired(e);
  }
}
