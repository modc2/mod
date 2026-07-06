import { SavedIndex, IndexTrader } from "./types";

// Equal-weight a list of addresses into IndexTrader rows (remainder cents go
// to the earliest entries) — shared by every "seed a strat with N traders"
// call site so weights always sum to exactly 100.
export function equalWeightTraders(addrs: string[]): IndexTrader[] {
  const n = addrs.length;
  if (n === 0) return [];
  const base = Math.floor(100 / n);
  const remainder = 100 - base * n;
  return addrs.map((address, i) => ({ address, weight: (base + (i < remainder ? 1 : 0)) / 100 }));
}

const STORAGE_KEY = "poly8bit_indexes";
const ACTIVE_KEY = "poly8bit_active_index";

// Quota recovery: when a strat write throws QuotaExceededError, the disposable
// trade/market caches are evicted so the (tiny, critical) strat data wins.
// Without this, users hit silent write failures once the cache grows past
// ~5MB and adding/removing traders appears completely broken.
const DISPOSABLE_PREFIXES = ["poly_cid_", "poly_hr_", "poly_mkt_"];

function freeDisposableCache(): number {
  if (typeof window === "undefined") return 0;
  const toRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && DISPOSABLE_PREFIXES.some((p) => k.startsWith(p))) toRemove.push(k);
  }
  for (const k of toRemove) localStorage.removeItem(k);
  return toRemove.length;
}

function isQuotaError(e: unknown): boolean {
  if (!(e instanceof Error)) return false;
  return e.name === "QuotaExceededError" || /quota/i.test(e.message);
}

function setItemSafe(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    if (!isQuotaError(e)) throw e;
    const freed = freeDisposableCache();
    // eslint-disable-next-line no-console
    console.warn(`[indexStore] localStorage full — evicted ${freed} cache entries and retrying strat write`);
    localStorage.setItem(key, value); // retry; if this throws, let it surface
  }
}

export function loadIndexes(): SavedIndex[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveIndex(index: SavedIndex): void {
  const all = loadIndexes();
  all.push(index);
  setItemSafe(STORAGE_KEY, JSON.stringify(all));
}

export function deleteIndex(id: string): void {
  const all = loadIndexes().filter((idx) => idx.id !== id);
  setItemSafe(STORAGE_KEY, JSON.stringify(all));
}

export function updateIndex(id: string, patch: Partial<SavedIndex>): void {
  const all = loadIndexes();
  const idx = all.findIndex((i) => i.id === id);
  if (idx === -1) return;
  all[idx] = { ...all[idx], ...patch };
  setItemSafe(STORAGE_KEY, JSON.stringify(all));
}

export function getActiveIndexId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}

export function setActiveIndexId(id: string | null): void {
  try {
    if (id) localStorage.setItem(ACTIVE_KEY, id);
    else localStorage.removeItem(ACTIVE_KEY);
  } catch {}
}
