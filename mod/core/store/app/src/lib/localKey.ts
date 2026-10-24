/**
 * Local sign-in — a keypair the browser holds, for people with no wallet
 * extension. The store API only ever verifies a signature over the token
 * envelope, so an ethers wallet minted here is indistinguishable from MetaMask
 * to the server: same address space, same `personal_sign` (v=27/28) format.
 *
 * Trade-offs the UI must state plainly:
 *   - the private key lives in this browser's storage, so anything with access
 *     to the profile (including neighbouring modules on the shared modc2.com
 *     origin) can read it — treat it as a throwaway identity, not a vault;
 *   - clearing site data destroys it. `exportLocalKey` + `importLocalKey` are
 *     the backup path; QR handoff moves a *session* to another device.
 */

import { Wallet, scrypt, toUtf8Bytes, hexlify } from "ethers";
import { LOCAL_KEY_STORAGE, storageGet, storageRemove, storageSet } from "./safeStorage";

let cached: Wallet | null = null;

function normalizePk(pk: string): string {
  const s = pk.trim().replace(/\s/g, "");
  return s.startsWith("0x") ? s : `0x${s}`;
}

export function hasLocalKey(): boolean {
  return !!storageGet(LOCAL_KEY_STORAGE);
}

/** The stored wallet, or null if this browser has never made one. */
export function loadLocalKey(): Wallet | null {
  if (cached) return cached;
  const pk = storageGet(LOCAL_KEY_STORAGE);
  if (!pk) return null;
  try {
    cached = new Wallet(normalizePk(pk));
    return cached;
  } catch {
    // Corrupt entry — drop it so the next sign-in can mint a fresh key.
    storageRemove(LOCAL_KEY_STORAGE);
    return null;
  }
}

/**
 * Mint and persist a new local wallet. `persisted` is false when storage
 * refused the write (private mode, full quota) — the key then only lives for
 * this tab, which the caller should warn about before the user stores anything.
 */
export function createLocalKey(): { wallet: Wallet; persisted: boolean } {
  const w = Wallet.createRandom();
  const wallet = new Wallet(w.privateKey);
  storageSet(LOCAL_KEY_STORAGE, wallet.privateKey);
  cached = wallet;
  return { wallet, persisted: storageGet(LOCAL_KEY_STORAGE) === wallet.privateKey };
}

/**
 * Password → key, deterministically: the same password gives the same address
 * on any device, so the password *is* the backup. scrypt (N=2^17, r=8) keeps a
 * guess at ~1s in the browser, but a short or common password is still
 * guessable offline — callers must enforce a floor (MIN_PASSWORD) and say so.
 */
export const MIN_PASSWORD = 12;
const PASSWORD_SALT = "mod/store/password-key/v1";

export async function deriveKeyFromPassword(password: string): Promise<string> {
  const out = await scrypt(toUtf8Bytes(password.normalize("NFKC")), toUtf8Bytes(PASSWORD_SALT), 131072, 8, 1, 32);
  return hexlify(out);
}

/** Derive from a password and persist it like any other local key. */
export async function passwordLocalKey(password: string): Promise<{ wallet: Wallet; persisted: boolean }> {
  const wallet = new Wallet(await deriveKeyFromPassword(password));
  storageSet(LOCAL_KEY_STORAGE, wallet.privateKey);
  cached = wallet;
  return { wallet, persisted: storageGet(LOCAL_KEY_STORAGE) === wallet.privateKey };
}

/** Restore a key from a backup. Throws if the hex isn't a valid private key. */
export function importLocalKey(pk: string): Wallet {
  const wallet = new Wallet(normalizePk(pk)); // throws on bad input
  storageSet(LOCAL_KEY_STORAGE, wallet.privateKey);
  cached = wallet;
  return wallet;
}

export function exportLocalKey(): string | null {
  return loadLocalKey()?.privateKey ?? null;
}

/** Permanent: without a backup, everything stored under this address is orphaned. */
export function forgetLocalKey() {
  cached = null;
  storageRemove(LOCAL_KEY_STORAGE);
}

/** Signer in the shape `buildModToken` expects. */
export async function localSign(message: string): Promise<string> {
  const w = loadLocalKey();
  if (!w) throw new Error("no local key in this browser");
  return w.signMessage(message);
}
