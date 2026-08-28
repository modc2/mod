/**
 * Quota-safe persistence for modc2.com, where every module shares ONE
 * localStorage origin. A neighbouring module can fill the quota, at which
 * point a raw `localStorage.setItem` throws and takes sign-in down with it.
 *
 * Strategy: localStorage → (evict our own stale keys, retry) → sessionStorage
 * → in-memory. Reads check the same chain, writes never throw.
 */

const memory = new Map<string, string>();

// Keys this module owns and may evict to make room for a fresh write.
const OWN_PREFIX = "store:";

// The local sign-in private key. Everything else under OWN_PREFIX is session
// state that can be re-derived; this one can't — evicting it would orphan every
// object stored under that address — so the sweep below skips it.
export const LOCAL_KEY_STORAGE = "store:localkey";

function tryGet(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function trySet(storage: Storage, key: string, value: string): boolean {
  try {
    storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function tryRemove(storage: Storage, key: string) {
  try {
    storage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function storageGet(key: string): string | null {
  if (typeof window === "undefined") return memory.get(key) ?? null;
  return (
    tryGet(window.localStorage, key) ??
    tryGet(window.sessionStorage, key) ??
    memory.get(key) ??
    null
  );
}

export function storageSet(key: string, value: string) {
  if (typeof window === "undefined") {
    memory.set(key, value);
    return;
  }
  if (trySet(window.localStorage, key, value)) {
    tryRemove(window.sessionStorage, key);
    memory.delete(key);
    return;
  }
  // Quota full (likely a neighbour module's blobs). Evict our own other keys
  // — they are all small session state — and retry once.
  try {
    for (let i = window.localStorage.length - 1; i >= 0; i--) {
      const k = window.localStorage.key(i);
      if (k && k !== key && k !== LOCAL_KEY_STORAGE && k.startsWith(OWN_PREFIX))
        tryRemove(window.localStorage, k);
    }
  } catch {
    /* ignore */
  }
  if (trySet(window.localStorage, key, value)) {
    tryRemove(window.sessionStorage, key);
    memory.delete(key);
    return;
  }
  // Fall back: survives reloads within the tab, never crashes the caller.
  if (trySet(window.sessionStorage, key, value)) {
    memory.delete(key);
    return;
  }
  memory.set(key, value);
}

export function storageRemove(key: string) {
  memory.delete(key);
  if (typeof window === "undefined") return;
  tryRemove(window.localStorage, key);
  tryRemove(window.sessionStorage, key);
}
